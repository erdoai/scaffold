"""Auth middleware code generation — LLM-powered, framework-aware.

Scans the codebase, calls Claude to generate auth middleware tailored to the
specific project. Returns a plan (file writes + wiring instructions).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console

from scaffold.config.tokens import resolve_tokens
from scaffold.manifest.schema import AuthConfig
from scaffold.planner.agent import scan_codebase

err_console = Console(stderr=True)

AUTH_SYSTEM_PROMPT = """\
You are an auth middleware generator. Given a codebase summary and auth requirements, \
generate the minimal auth middleware and login routes for the project.

You MUST output valid JSON with this exact structure:
{
  "framework": "fastapi | express | nextjs | flask | django | hono | other",
  "files": [
    {
      "path": "relative/path/to/file.py",
      "content": "full file content as a string",
      "description": "what this file does"
    }
  ],
  "wiring": [
    "Step-by-step instructions for wiring the middleware into the existing app"
  ]
}

Rules:
- Detect the web framework from the codebase summary
- Generate a JWT-verifying auth middleware appropriate for that framework
- Generate auth routes: POST /auth/login (accept email, send magic link), GET /auth/verify (exchange token for JWT)
- The middleware should check Authorization: Bearer <jwt> header and scaffold_auth cookie
- Skip auth for public paths: /auth/*, /health, /docs, /openapi.json
- JWT verification uses HS256 with AUTH_JWT_SECRET env var — implement inline (no PyJWT dep)
- Magic link sending uses httpx to call Resend API (AUTH_EMAIL_API_KEY env var). If no API key, show link directly (dev mode)
- Read allowed emails from AUTH_ALLOWED_EMAILS env var (comma-separated glob patterns like *@company.com)
- Auth config from scaffold.yml is provided — use allowed_emails and token_ttl from it
- Put auth files in a scaffold_auth/ directory at the project root (or appropriate location for the framework)
- Make the middleware attach the user email to the request (request.state.user_email for Python, req.user for Node)
- Keep it minimal — no database tables, no user model, just email + JWT
- Only output the JSON, no explanation
"""


def generate_auth_plan(
    project_dir: Path,
    service_name: str,
    auth_config: AuthConfig,
) -> dict[str, Any]:
    """Scan the codebase and generate an auth middleware plan via Claude.

    Returns:
        {
            "framework": str,
            "files": [{"path": str, "content": str, "description": str}],
            "wiring": [str],
        }
    """
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "Auth middleware generation requires the anthropic package. "
            "Install it with: pip install scaffold[plan]"
        )

    tokens = resolve_tokens(project_dir)
    api_key = tokens.require("anthropic")

    summary = scan_codebase(project_dir)

    auth_context = (
        f"Auth config for service '{service_name}':\n"
        f"  mode: middleware\n"
        f"  allowed_emails: {auth_config.allowed_emails}\n"
        f"  token_ttl: {auth_config.token_ttl}\n"
        f"  email_provider: {auth_config.email_provider}\n"
    )

    user_msg = (
        f"Generate auth middleware for this codebase:\n\n"
        f"{summary}\n\n"
        f"{auth_context}\n\n"
        f"Output the JSON plan."
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        system=AUTH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = message.content[0].text

    # Extract JSON from response (may be wrapped in code fences)
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]

    return json.loads(text.strip())


def apply_auth_plan(project_dir: Path, plan: dict[str, Any]) -> list[str]:
    """Write the generated auth files to disk.

    Returns list of file paths written.
    """
    written: list[str] = []
    for file_info in plan.get("files", []):
        rel_path = file_info["path"]
        content = file_info["content"]
        full_path = project_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        written.append(rel_path)
    return written


def print_auth_plan(plan: dict[str, Any], json_output: bool = False) -> None:
    """Print the auth plan to the console."""
    console = Console()

    if json_output:
        console.print(json.dumps(plan, indent=2))
        return

    console.print(f"\n[bold]Auth middleware plan[/bold] (framework: {plan.get('framework', '?')})\n")

    console.print("[bold]Files to generate:[/bold]")
    for f in plan.get("files", []):
        console.print(f"  [green]{f['path']}[/green] — {f.get('description', '')}")

    console.print("\n[bold]Wiring instructions:[/bold]")
    for i, step in enumerate(plan.get("wiring", []), 1):
        console.print(f"  {i}. {step}")

    console.print("\n[dim]Run with --apply to write these files.[/dim]\n")
