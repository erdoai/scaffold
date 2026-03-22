"""Codebase-aware manifest generation — scans the project and generates scaffold.yml."""

from __future__ import annotations

from pathlib import Path

from scaffold.config.tokens import resolve_tokens


SYSTEM_PROMPT = """You are a deployment infrastructure planner. You analyze codebases and generate deployment manifests.

Given a summary of a project's codebase (files, dependencies, entry points, imports), generate a valid scaffold.yml that would deploy it.

The manifest format is:

```yaml
project: <project-name>
region: us-west1

services:
  <service-name>:
    provider: railway | vercel
    runtime: python | node | docker    # for Railway
    framework: nextjs | remix          # for Vercel
    source: .                          # source directory
    start: "<start command>"           # for Railway services
    health_check: /health              # optional health check path
    replicas: 1                        # number of replicas
    env:
      VAR_NAME: "value"
      DB_URL: "${{postgres.url}}"      # reference a database
      OTHER: "${{env.MY_VAR}}"         # reference an env var
      API: "${{server.url}}"           # reference another service

databases:
  <db-name>:
    provider: railway
    plugin: postgres | redis | mysql | mongodb
    extensions: [pgvector]             # optional extensions

domain:
  <service-name>:
    host: api.example.com
    auth: none | cloudflare-zero-trust | basic
```

Rules:
- Infer services from entry points, CLI commands, Procfile, Dockerfile, etc.
- Infer databases from imports (sqlalchemy, asyncpg, psycopg → postgres; redis-py → redis; pgvector → postgres with pgvector extension)
- Infer the start command from pyproject.toml scripts, Procfile, Dockerfile CMD, or package.json scripts
- Infer runtime from the project type (pyproject.toml → python, package.json → node)
- If there's a frontend dir with next.config, use Vercel with nextjs framework
- Use ${{resource.url}} for database refs, ${{env.VAR_NAME}} for secrets the user must provide
- Detect worker processes, background jobs, cron-like services as separate services
- Use the directory name as the project name
- Only output the YAML manifest, no explanation or commentary
- Be opinionated — pick sensible defaults rather than leaving things ambiguous
"""


def scan_codebase(project_dir: Path | None = None) -> str:
    """Scan the codebase and build a summary for the LLM."""
    root = project_dir or Path.cwd()
    parts: list[str] = []

    parts.append(f"Project directory: {root.name}")

    # File tree (top 2 levels, skip common noise)
    parts.append("\n## File tree")
    skip_dirs = {
        ".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
        ".ruff_cache", ".pytest_cache", "dist", "build", ".egg-info", ".scaffold",
        ".next", ".vercel", ".turbo",
    }
    tree_lines: list[str] = []
    _walk_tree(root, root, skip_dirs, tree_lines, max_depth=3)
    parts.append("\n".join(tree_lines[:200]))  # cap at 200 lines

    # Key config files
    config_files = [
        "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
        "Procfile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "requirements.txt", "Pipfile",
        "next.config.js", "next.config.mjs", "next.config.ts",
        "vercel.json", "railway.json", "railway.toml",
        ".env.example", ".env.sample",
    ]
    for name in config_files:
        path = root / name
        if path.exists():
            content = _read_capped(path, max_lines=80)
            parts.append(f"\n## {name}\n```\n{content}\n```")

    # Check subdirectories for their own config files (monorepo)
    for subdir in sorted(root.iterdir()):
        if subdir.is_dir() and subdir.name not in skip_dirs and not subdir.name.startswith("."):
            for name in ["pyproject.toml", "package.json", "Dockerfile", "next.config.js", "next.config.mjs"]:
                path = subdir / name
                if path.exists():
                    content = _read_capped(path, max_lines=60)
                    parts.append(f"\n## {subdir.name}/{name}\n```\n{content}\n```")

    # Look for entry points and imports
    parts.append("\n## Key source files (first 30 lines)")
    entry_patterns = [
        "app.py", "main.py", "server.py", "worker.py", "cli.py",
        "manage.py", "wsgi.py", "asgi.py",
        "index.ts", "index.js", "server.ts", "server.js",
        "src/main.py", "src/app.py", "src/index.ts",
    ]
    for pattern in entry_patterns:
        path = root / pattern
        if path.exists():
            content = _read_capped(path, max_lines=30)
            parts.append(f"\n### {pattern}\n```\n{content}\n```")

    # Search for __main__.py files (Python CLI entry points)
    for main_file in root.rglob("__main__.py"):
        if any(skip in main_file.parts for skip in skip_dirs):
            continue
        rel = main_file.relative_to(root)
        content = _read_capped(main_file, max_lines=30)
        parts.append(f"\n### {rel}\n```\n{content}\n```")

    # Grep for database/infra imports
    parts.append("\n## Detected infrastructure imports")
    infra_keywords = [
        "sqlalchemy", "asyncpg", "psycopg", "pgvector", "databases",
        "redis", "aioredis", "celery", "dramatiq", "arq",
        "fastapi", "flask", "django", "uvicorn", "gunicorn",
        "nextjs", "next/", "express", "koa", "hono",
        "boto3", "s3", "anthropic", "openai",
    ]
    found_imports: set[str] = set()
    for py_file in root.rglob("*.py"):
        if any(skip in py_file.parts for skip in skip_dirs):
            continue
        try:
            text = py_file.read_text(errors="ignore")
            for kw in infra_keywords:
                if f"import {kw}" in text or f"from {kw}" in text:
                    found_imports.add(kw)
        except Exception:
            pass
    for ts_file in list(root.rglob("*.ts")) + list(root.rglob("*.js")):
        if any(skip in ts_file.parts for skip in skip_dirs):
            continue
        try:
            text = ts_file.read_text(errors="ignore")
            for kw in infra_keywords:
                if kw in text:
                    found_imports.add(kw)
        except Exception:
            pass

    if found_imports:
        parts.append(", ".join(sorted(found_imports)))
    else:
        parts.append("(none detected)")

    return "\n".join(parts)


def _walk_tree(
    root: Path, current: Path, skip: set[str], lines: list[str], max_depth: int, depth: int = 0,
) -> None:
    """Build an indented file tree."""
    if depth > max_depth:
        return
    try:
        entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except PermissionError:
        return

    for entry in entries:
        if entry.name in skip or entry.name.startswith("."):
            continue
        prefix = "  " * depth
        if entry.is_dir():
            lines.append(f"{prefix}{entry.name}/")
            _walk_tree(root, entry, skip, lines, max_depth, depth + 1)
        else:
            lines.append(f"{prefix}{entry.name}")


def _read_capped(path: Path, max_lines: int = 60) -> str:
    """Read a file, capped at N lines."""
    try:
        lines = path.read_text(errors="ignore").splitlines()[:max_lines]
        text = "\n".join(lines)
        if len(path.read_text(errors="ignore").splitlines()) > max_lines:
            text += f"\n... ({len(path.read_text(errors='ignore').splitlines()) - max_lines} more lines)"
        return text
    except Exception:
        return "(could not read)"


def generate_manifest(project_dir: Path | None = None, description: str | None = None) -> str:
    """Scan the codebase and generate a scaffold.yml.

    Args:
        project_dir: Directory to scan. Defaults to cwd.
        description: Optional extra context from the user.
    """
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "scaffold plan requires the anthropic package. "
            "Install it with: pip install scaffold[plan]"
        )

    tokens = resolve_tokens(project_dir)
    api_key = tokens.require("anthropic")

    # Scan the codebase
    summary = scan_codebase(project_dir)

    user_msg = f"Analyze this codebase and generate a scaffold.yml to deploy it:\n\n{summary}"
    if description:
        user_msg += f"\n\nAdditional context from the user: {description}"

    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_msg},
        ],
    )

    # Extract YAML from response
    text = message.content[0].text

    # Strip markdown code fences if present
    if "```yaml" in text:
        text = text.split("```yaml", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]

    return text.strip() + "\n"
