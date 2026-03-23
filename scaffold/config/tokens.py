"""Token resolution: env vars → .scaffold/.env → ~/.scaffold/config.yml → CLI configs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


ENV_VAR_MAP = {
    "railway": "SCAFFOLD_RAILWAY_TOKEN",
    "vercel": "SCAFFOLD_VERCEL_TOKEN",
    "supabase": "SCAFFOLD_SUPABASE_TOKEN",
    "neon": "SCAFFOLD_NEON_TOKEN",
    "cloudflare_api_token": "SCAFFOLD_CLOUDFLARE_API_TOKEN",
    "cloudflare_account_id": "SCAFFOLD_CLOUDFLARE_ACCOUNT_ID",
    "cloudflare_zone_id": "SCAFFOLD_CLOUDFLARE_ZONE_ID",
    "anthropic": "SCAFFOLD_ANTHROPIC_API_KEY",
}


@dataclass
class ResolvedTokens:
    railway: str | None = None
    vercel: str | None = None
    supabase: str | None = None
    neon: str | None = None
    cloudflare_api_token: str | None = None
    cloudflare_account_id: str | None = None
    cloudflare_zone_id: str | None = None
    anthropic: str | None = None

    def require(self, key: str) -> str:
        """Get a token or raise with a helpful message."""
        val = getattr(self, key, None)
        if not val:
            env_var = ENV_VAR_MAP.get(key, f"SCAFFOLD_{key.upper()}")
            hints = [f"Set {env_var} in environment, .scaffold/.env, or ~/.scaffold/config.yml"]
            if key == "railway":
                hints.append("Or run `railway login` to authenticate the Railway CLI")
            raise ValueError(f"Token '{key}' not found. {' '.join(hints)}")
        return val


def resolve_tokens(project_dir: Path | None = None) -> ResolvedTokens:
    """Resolve tokens from env vars → .scaffold/.env → ~/.scaffold/config.yml → CLI configs.

    Higher priority sources override lower ones.
    """
    tokens = ResolvedTokens()

    # Priority 4 (lowest): provider CLI configs (Railway, Vercel, etc.)
    _load_cli_tokens(tokens)

    # Priority 3: global config
    _load_global_config(tokens)

    # Priority 2: project .scaffold/.env
    if project_dir:
        _load_project_env(tokens, project_dir)

    # Priority 1 (highest): environment variables
    _load_env_vars(tokens)

    return tokens


def _load_global_config(tokens: ResolvedTokens) -> None:
    """Load tokens from ~/.scaffold/config.yml."""
    config_path = Path.home() / ".scaffold" / "config.yml"
    if not config_path.exists():
        return

    try:
        import yaml

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        return

    token_section = config.get("tokens", {})
    if not token_section:
        return

    for key in ("railway", "vercel", "supabase", "neon", "anthropic"):
        if val := token_section.get(key):
            setattr(tokens, key, val)

    # Cloudflare has nested structure
    cf = token_section.get("cloudflare", {})
    if isinstance(cf, dict):
        if val := cf.get("api_token"):
            tokens.cloudflare_api_token = val
        if val := cf.get("account_id"):
            tokens.cloudflare_account_id = val
        if val := cf.get("zone_id"):
            tokens.cloudflare_zone_id = val


def _load_project_env(tokens: ResolvedTokens, project_dir: Path) -> None:
    """Load tokens from .scaffold/.env in the project directory."""
    env_path = project_dir / ".scaffold" / ".env"
    if not env_path.exists():
        return

    values = dotenv_values(env_path)
    for attr, env_var in ENV_VAR_MAP.items():
        if val := values.get(env_var):
            setattr(tokens, attr, val)


def _load_env_vars(tokens: ResolvedTokens) -> None:
    """Load tokens from environment variables (highest priority)."""
    for attr, env_var in ENV_VAR_MAP.items():
        if val := os.environ.get(env_var):
            setattr(tokens, attr, val)


def _load_cli_tokens(tokens: ResolvedTokens) -> None:
    """Load tokens from provider CLI configs (lowest priority fallback).

    Supports:
    - Railway CLI: ~/.railway/config.json → user.token
    """
    # Railway CLI session token
    if not tokens.railway:
        railway_config = Path.home() / ".railway" / "config.json"
        if railway_config.exists():
            try:
                with open(railway_config) as f:
                    config = json.load(f)
                token = config.get("user", {}).get("token")
                if token:
                    tokens.railway = token
            except Exception:
                pass
