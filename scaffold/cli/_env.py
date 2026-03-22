"""scaffold env sync/pull — manage environment variables."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rich.console import Console

from scaffold.config.tokens import resolve_tokens
from scaffold.manifest.loader import load_manifest
from scaffold.manifest.resolve import resolve_refs
from scaffold.providers.railway import RailwayProvider
from scaffold.providers.vercel import VercelProvider
from scaffold.state.store import StateStore

console = Console()


def run_env_sync() -> None:
    """Push env vars from scaffold.yml to providers."""
    project_dir = Path.cwd()
    manifest = load_manifest()
    state = StateStore(project_dir)
    tokens = resolve_tokens(project_dir)

    if not state.is_provisioned:
        console.print("[yellow]Nothing provisioned. Run `scaffold up` first.[/yellow]")
        return

    resolved_urls = state.get_all_urls()

    asyncio.run(_sync_env(manifest, state, tokens, resolved_urls))
    console.print("[green]Environment variables synced.[/green]")


async def _sync_env(manifest, state, tokens, resolved_urls):
    railway = RailwayProvider(tokens)

    for name, svc in manifest.services.items():
        resource = state.get_resource(name)
        if not resource:
            continue

        resolved_env = {}
        for k, v in svc.env.items():
            resolved_env[k] = resolve_refs(v, resolved_urls)

        provider = railway  # extend for vercel
        await provider.set_env_vars(resource, resolved_env)
        console.print(f"  [dim]Synced {len(resolved_env)} vars → {name}[/dim]")


def run_env_pull(stdout: bool = False, json_output: bool = False) -> None:
    """Pull env vars from providers → local .env."""
    project_dir = Path.cwd()
    state = StateStore(project_dir)
    tokens = resolve_tokens(project_dir)

    if not state.is_provisioned:
        console.print("[yellow]Nothing provisioned.[/yellow]")
        return

    all_vars = asyncio.run(_pull_env(state, tokens))

    if json_output:
        console.print(json.dumps(all_vars, indent=2))
    elif stdout:
        for k, v in all_vars.items():
            console.print(f"{k}={v}")
    else:
        env_path = project_dir / ".env"
        lines = [f"{k}={v}" for k, v in all_vars.items()]
        env_path.write_text("\n".join(lines) + "\n")
        console.print(f"[green]Wrote {len(all_vars)} vars to .env[/green]")


async def _pull_env(state, tokens) -> dict[str, str]:
    railway = RailwayProvider(tokens)
    all_vars: dict[str, str] = {}

    for name, data in state.state["resources"].items():
        if data.get("provider") != "railway":
            continue
        try:
            env = await railway.get_env_vars(data)
            all_vars.update(env)
        except Exception:
            pass

    return all_vars
