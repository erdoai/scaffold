"""Send magic link emails via Resend or Postmark."""

from __future__ import annotations

import httpx


async def send_magic_link(
    to_email: str,
    magic_url: str,
    *,
    provider: str = "resend",
    api_key: str,
    from_email: str = "auth@scaffold.dev",
) -> bool:
    """Send a magic link email. Returns True on success."""
    if provider == "resend":
        return await _send_resend(to_email, magic_url, api_key, from_email)
    elif provider == "postmark":
        return await _send_postmark(to_email, magic_url, api_key, from_email)
    raise ValueError(f"Unknown email provider: {provider}")


async def _send_resend(to: str, url: str, api_key: str, from_email: str) -> bool:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": from_email,
                "to": [to],
                "subject": "Your login link",
                "html": (
                    f"<p>Click to log in:</p>"
                    f'<p><a href="{url}">{url}</a></p>'
                    f"<p>This link expires in 10 minutes.</p>"
                ),
            },
            timeout=10,
        )
        return resp.status_code in (200, 201)


async def _send_postmark(to: str, url: str, api_key: str, from_email: str) -> bool:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.postmarkapp.com/email",
            headers={
                "X-Postmark-Server-Token": api_key,
                "Content-Type": "application/json",
            },
            json={
                "From": from_email,
                "To": to,
                "Subject": "Your login link",
                "HtmlBody": (
                    f"<p>Click to log in:</p>"
                    f'<p><a href="{url}">{url}</a></p>'
                    f"<p>This link expires in 10 minutes.</p>"
                ),
            },
            timeout=10,
        )
        return resp.status_code == 200
