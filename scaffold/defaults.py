"""Auto-generate default values (secrets, UUIDs) from scaffold-defaults.yml."""

from __future__ import annotations

import secrets
import uuid
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

DEFAULTS_NAMES = ["scaffold-defaults.yml", "scaffold-defaults.yaml"]

err_console = Console(stderr=True)


def find_defaults_file(project_dir: Path | None = None) -> Path | None:
    """Find scaffold-defaults.yml if it exists. Returns None if not found."""
    search_dir = project_dir or Path.cwd()
    for name in DEFAULTS_NAMES:
        path = search_dir / name
        if path.exists():
            return path
    return None


def load_defaults(path: Path) -> dict[str, dict[str, Any]]:
    """Load and validate scaffold-defaults.yml.

    Expected format:
        auto:
          SESSION_SECRET:
            type: secret
            length: 32
          WORKER_API_KEY:
            type: secret
            length: 24
          INSTANCE_ID:
            type: uuid

    Returns the 'auto' dict, or empty dict if missing.
    """
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not raw or not isinstance(raw, dict):
        return {}

    auto = raw.get("auto", {})
    if not isinstance(auto, dict):
        return {}

    return auto


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


def apply_defaults(project_dir: Path | None = None) -> dict[str, str]:
    """Check scaffold-defaults.yml against .scaffold/.env and generate missing values.

    Returns dict of newly generated key-value pairs (empty if nothing new).
    """
    project_dir = project_dir or Path.cwd()
    defaults_path = find_defaults_file(project_dir)
    if defaults_path is None:
        return {}

    auto = load_defaults(defaults_path)
    if not auto:
        return {}

    # Load existing .scaffold/.env values
    env_path = project_dir / ".scaffold" / ".env"
    existing: dict[str, str | None] = {}
    if env_path.exists():
        from dotenv import dotenv_values
        existing = dotenv_values(env_path)

    # Generate missing values
    generated: dict[str, str] = {}
    for key, spec in auto.items():
        if not isinstance(spec, dict):
            continue
        if existing.get(key):
            continue  # already set, skip
        generated[key] = generate_value(spec)

    if not generated:
        return {}

    # Append to .scaffold/.env
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with open(env_path, "a") as f:
        for k, v in generated.items():
            f.write(f"{k}={v}\n")

    for k in generated:
        err_console.print(f"  [dim]Generated default: {k}[/dim]")

    return generated
