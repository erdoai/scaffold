"""Auth sidecar — Starlette ASGI app that sits in front of a service.

Handles magic link login, JWT issuance, and reverse-proxies authenticated requests.

Env vars (set by scaffold):
    AUTH_JWT_SECRET       — HMAC secret for signing JWTs
    AUTH_UPSTREAM_URL     — internal URL of the protected service
    AUTH_ALLOWED_EMAILS   — comma-separated email patterns ("*@co.com,user@x.com")
    AUTH_TOKEN_TTL        — JWT lifetime in seconds (default 86400)
    AUTH_EMAIL_PROVIDER   — "resend" or "postmark" (default "resend")
    AUTH_EMAIL_API_KEY    — API key for the email provider
    AUTH_EMAIL_FROM       — sender address (default "auth@scaffold.dev")
    PORT                  — port to listen on (default 8000)
"""

from __future__ import annotations

import fnmatch
import json
import os
import secrets
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from scaffold.auth.sidecar.email_send import send_magic_link
from scaffold.auth.sidecar.jwt_utils import create_jwt, verify_jwt
from scaffold.auth.sidecar.proxy import proxy_request

# ── Config from env ───────────────────────────────────────────────────────────

JWT_SECRET = os.environ.get("AUTH_JWT_SECRET", "")
UPSTREAM_URL = os.environ.get("AUTH_UPSTREAM_URL", "")
ALLOWED_EMAILS = [
    e.strip() for e in os.environ.get("AUTH_ALLOWED_EMAILS", "*").split(",") if e.strip()
]
TOKEN_TTL = int(os.environ.get("AUTH_TOKEN_TTL", "86400"))
EMAIL_PROVIDER = os.environ.get("AUTH_EMAIL_PROVIDER", "resend")
EMAIL_API_KEY = os.environ.get("AUTH_EMAIL_API_KEY", "")
EMAIL_FROM = os.environ.get("AUTH_EMAIL_FROM", "auth@scaffold.dev")

# In-memory magic link tokens: {token: {"email": str, "expires": float}}
_pending_tokens: dict[str, dict] = {}

# ── Helpers ───────────────────────────────────────────────────────────────────


def _email_allowed(email: str) -> bool:
    """Check if an email matches the allowed patterns."""
    email = email.lower().strip()
    for pattern in ALLOWED_EMAILS:
        if fnmatch.fnmatch(email, pattern.lower()):
            return True
    return False


def _cleanup_expired() -> None:
    """Remove expired magic link tokens."""
    now = time.time()
    expired = [k for k, v in _pending_tokens.items() if v["expires"] < now]
    for k in expired:
        del _pending_tokens[k]


def _extract_bearer(request: Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


# ── Auth routes ───────────────────────────────────────────────────────────────


LOGIN_HTML = """<!DOCTYPE html>
<html>
<head><title>Login</title>
<style>
  body { font-family: system-ui; max-width: 400px; margin: 80px auto; padding: 0 20px; }
  input, button { display: block; width: 100%; padding: 10px; margin: 8px 0; box-sizing: border-box; }
  button { background: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer; }
  .msg { color: #059669; margin-top: 12px; }
  .err { color: #dc2626; margin-top: 12px; }
</style>
</head>
<body>
<h2>Login</h2>
<form method="POST" action="/auth/login">
  <label>Email</label>
  <input type="email" name="email" required autofocus>
  <button type="submit">Send magic link</button>
</form>
{message}
</body>
</html>
"""


async def login_page(request: Request) -> Response:
    """GET /auth/login — show login form."""
    return HTMLResponse(LOGIN_HTML.format(message=""))


async def login_submit(request: Request) -> Response:
    """POST /auth/login — send magic link email."""
    # Accept form data or JSON
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        body = await request.json()
        email = body.get("email", "").strip().lower()
    else:
        form = await request.form()
        email = str(form.get("email", "")).strip().lower()

    if not email:
        return HTMLResponse(
            LOGIN_HTML.format(message='<p class="err">Email is required.</p>'),
            status_code=400,
        )

    if not _email_allowed(email):
        return HTMLResponse(
            LOGIN_HTML.format(message='<p class="err">Email not allowed.</p>'),
            status_code=403,
        )

    # Generate magic link token
    _cleanup_expired()
    token = secrets.token_urlsafe(32)
    _pending_tokens[token] = {"email": email, "expires": time.time() + 600}  # 10 min

    # Build verify URL
    base = str(request.base_url).rstrip("/")
    verify_url = f"{base}/auth/verify?token={token}"

    if EMAIL_API_KEY:
        sent = await send_magic_link(
            email, verify_url,
            provider=EMAIL_PROVIDER, api_key=EMAIL_API_KEY, from_email=EMAIL_FROM,
        )
        if sent:
            msg = '<p class="msg">Check your email for a login link.</p>'
        else:
            msg = '<p class="err">Failed to send email. Try again.</p>'
    else:
        # No email provider configured — return the link directly (dev mode)
        msg = f'<p class="msg">Dev mode — <a href="{verify_url}">click to verify</a></p>'

    # JSON response for API consumers
    if "json" in content_type:
        return JSONResponse({"status": "sent", "email": email})

    return HTMLResponse(LOGIN_HTML.format(message=msg))


async def verify_token(request: Request) -> Response:
    """GET /auth/verify?token=... — exchange magic link for JWT."""
    token = request.query_params.get("token", "")
    _cleanup_expired()

    pending = _pending_tokens.pop(token, None)
    if not pending:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)

    email = pending["email"]
    jwt = create_jwt(email, JWT_SECRET, ttl=TOKEN_TTL)

    # Set cookie + return JSON with the JWT
    response = JSONResponse({
        "status": "ok",
        "email": email,
        "token": jwt,
        "expires_in": TOKEN_TTL,
    })
    response.set_cookie(
        "scaffold_auth", jwt,
        max_age=TOKEN_TTL, httponly=True, samesite="lax", secure=True,
    )
    return response


async def auth_health(request: Request) -> Response:
    """GET /auth/health — health check."""
    return JSONResponse({"status": "ok"})


# ── Catch-all proxy ──────────────────────────────────────────────────────────


async def catch_all(request: Request) -> Response:
    """All non-auth routes: verify JWT then proxy to upstream."""
    # Try Bearer token first, then cookie
    token = _extract_bearer(request) or request.cookies.get("scaffold_auth")

    if not token:
        # No auth — redirect browsers to login, 401 for APIs
        accept = request.headers.get("accept", "")
        if "html" in accept:
            return HTMLResponse(
                '<meta http-equiv="refresh" content="0;url=/auth/login">',
                status_code=302,
                headers={"location": "/auth/login"},
            )
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    payload = verify_jwt(token, JWT_SECRET)
    if not payload:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)

    if not UPSTREAM_URL:
        return JSONResponse({"error": "No upstream configured"}, status_code=502)

    return await proxy_request(request, UPSTREAM_URL)


# ── App ───────────────────────────────────────────────────────────────────────

routes = [
    Route("/auth/login", login_page, methods=["GET"]),
    Route("/auth/login", login_submit, methods=["POST"]),
    Route("/auth/verify", verify_token, methods=["GET"]),
    Route("/auth/health", auth_health, methods=["GET"]),
    Route("/{path:path}", catch_all, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]),
    Route("/", catch_all, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]),
]

app = Starlette(routes=routes)
