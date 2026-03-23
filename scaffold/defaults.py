"""Auto-generate default values and check required keys from scaffold.config.yml.

Supports both the new scaffold.config.yml and legacy scaffold-defaults.yml.

scaffold.config.yml format:
    auto:
      SESSION_SECRET:
        type: secret
        length: 32
    required:
      ANTHROPIC_API_KEY:
        description: "Claude API key"
        url: https://console.anthropic.com/settings/keys
    optional:
      DEVBOT_URL:
        description: "Devbot server URL"
        default: ""
"""

from __future__ import annotations

import secrets
import uuid
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.prompt import Prompt

CONFIG_NAMES = [
    "scaffold.config.yml",
    "scaffold.config.yaml",
    "scaffold-defaults.yml",
    "scaffold-defaults.yaml",
]

err_console = Console(stderr=True)


def find_config_file(project_dir: Path | None = None) -> Path | None:
    """Find scaffold.config.yml (or legacy scaffold-defaults.yml)."""
    search_dir = project_dir or Path.cwd()
    for name in CONFIG_NAMES:
        path = search_dir / name
        if path.exists():
            return path
    return None


def load_config(path: Path) -> dict[str, dict[str, Any]]:
    """Load scaffold.config.yml. Returns the full parsed dict."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not raw or not isinstance(raw, dict):
        return {}

    return raw


def generate_value(spec: dict[str, Any]) -> str:
    """Generate a value according to its spec."""
    typ = spec.get("type", "secret")

    if typ == "secret":
        length = spec.get("length", 32)
        return secrets.token_hex(length // 2 + length % 2)[:length]

    if typ == "uuid":
        return str(uuid.uuid4())

    if typ == "string":
        default = spec.get("default")
        if default is None:
            raise ValueError("type 'string' requires a 'default' field")
        return str(default)

    raise ValueError(f"Unknown default type: {typ}")


def _load_existing_env(project_dir: Path) -> dict[str, str | None]:
    """Load existing .scaffold/.env values."""
    env_path = project_dir / ".scaffold" / ".env"
    if not env_path.exists():
        return {}
    from dotenv import dotenv_values
    return dotenv_values(env_path)


def _append_to_env(project_dir: Path, values: dict[str, str]) -> None:
    """Append key=value pairs to .scaffold/.env."""
    if not values:
        return
    env_path = project_dir / ".scaffold" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with open(env_path, "a") as f:
        for k, v in values.items():
            f.write(f"{k}={v}\n")


def apply_defaults(project_dir: Path | None = None) -> dict[str, str]:
    """Process scaffold.config.yml: auto-generate secrets, prompt for required keys.

    Returns dict of all newly set key-value pairs.
    """
    project_dir = project_dir or Path.cwd()
    config_path = find_config_file(project_dir)
    if config_path is None:
        return {}

    config = load_config(config_path)
    if not config:
        return {}

    existing = _load_existing_env(project_dir)
    all_new: dict[str, str] = {}

    # 1. Auto-generate secrets/UUIDs
    auto = config.get("auto", {})
    if isinstance(auto, dict):
        for key, spec in auto.items():
            if not isinstance(spec, dict):
                continue
            if existing.get(key):
                continue
            value = generate_value(spec)
            all_new[key] = value
            err_console.print(f"  [dim]Generated: {key}[/dim]")

    # 2. Check required keys — prompt if missing and interactive
    required = config.get("required", {})
    if isinstance(required, dict):
        missing_required = []
        for key, spec in required.items():
            if not isinstance(spec, dict):
                continue
            if existing.get(key) or all_new.get(key):
                continue
            missing_required.append((key, spec))

        if missing_required:
            err_console.print()
            err_console.print(
                "[bold]Required keys missing[/bold] — "
                "set these in .scaffold/.env or enter them now:"
            )
            for key, spec in missing_required:
                desc = spec.get("description", key)
                url = spec.get("url", "")
                label = f"  {desc}"
                if url:
                    label += f" ({url})"
                value = Prompt.ask(label, console=err_console)
                if value:
                    all_new[key] = value
                    err_console.print(f"  [green]Set: {key}[/green]")
                else:
                    err_console.print(
                        f"  [yellow]Skipped: {key} — "
                        f"add to .scaffold/.env before deploying[/yellow]"
                    )

    # 3. Check optional keys — just note what's available
    optional = config.get("optional", {})
    if isinstance(optional, dict):
        missing_optional = []
        for key, spec in optional.items():
            if not isinstance(spec, dict):
                continue
            if existing.get(key) or all_new.get(key):
                continue
            # Apply defaults for optional keys that have them
            default = spec.get("default")
            if default:
                all_new[key] = str(default)
                err_console.print(f"  [dim]Default: {key}={default}[/dim]")
            else:
                missing_optional.append((key, spec))

        if missing_optional:
            err_console.print()
            err_console.print("[dim]Optional keys (not set):[/dim]")
            for key, spec in missing_optional:
                desc = spec.get("description", key)
                err_console.print(f"  [dim]  {key} — {desc}[/dim]")

    # Write all new values to .scaffold/.env
    _append_to_env(project_dir, all_new)

    return all_new


# Backwards compat aliases
find_defaults_file = find_config_file
load_defaults = load_config
