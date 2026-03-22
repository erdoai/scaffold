"""scaffold down — tear down provisioned resources."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rich.console import Console

from scaffold.config.tokens import resolve_tokens
from scaffold.providers.railway import RailwayProvider
from scaffold.providers.vercel import VercelProvider
from scaffold.state.store import StateStore

console = Console()


def run_down(
    service: str | None = None,
    keep_db: bool = False,
    json_output: bool = False,
) -> None:
    """Tear down resources."""
    project_dir = Path.cwd()
    state = StateStore(project_dir)

    if not state.is_provisioned:
        console.print("[yellow]Nothing provisioned.[/yellow]")
        return

    tokens = resolve_tokens(project_dir)
    result = asyncio.run(_destroy(state, tokens, service, keep_db))

    if json_output:
        console.print(json.dumps(result, indent=2))
    else:
        for name, status in result.get("destroyed", {}).items():
            console.print(f"  [red]Destroyed:[/red] {name} ({status})")
        for name in result.get("kept", []):
            console.print(f"  [dim]Kept:[/dim] {name}")
        console.print()


async def _destroy(
    state: StateStore,
    tokens,
    target: str | None,
    keep_db: bool,
) -> dict:
    """Destroy resources."""
    destroyed = {}
    kept = []

    # Lazy-init providers only when needed
    providers: dict = {}

    def _get_provider(name: str):
        if name not in providers:
            if name == "railway":
                providers[name] = RailwayProvider(tokens)
            elif name == "vercel":
                providers[name] = VercelProvider(tokens)
            elif name == "supabase":
                from scaffold.providers.supabase import SupabaseProvider
                providers[name] = SupabaseProvider(tokens)
            elif name == "neon":
                from scaffold.providers.neon import NeonProvider
                providers[name] = NeonProvider(tokens)
        return providers[name]

    for name, data in list(state.state["resources"].items()):
        if target and name != target:
            kept.append(name)
            continue

        is_db = data.get("plugin") is not None
        if is_db and keep_db:
            kept.append(name)
            continue

        provider_name = data.get("provider", "railway")
        provider = _get_provider(provider_name)

        try:
            if is_db:
                await provider.destroy_database(name, data)
            else:
                await provider.destroy_service(name, data)
            state.remove_resource(name)
            destroyed[name] = "ok"
        except Exception as e:
            destroyed[name] = f"error: {e}"

    state.save()
    return {"status": "ok", "destroyed": destroyed, "kept": kept}
