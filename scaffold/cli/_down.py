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
    yes: bool = False,
    json_output: bool = False,
) -> None:
    """Tear down resources."""
    from rich.prompt import Confirm

    project_dir = Path.cwd()
    state = StateStore(project_dir)

    if not state.is_provisioned:
        console.print("[yellow]Nothing provisioned.[/yellow]")
        return

    # Show what will be destroyed and confirm
    project = state.state.get("project", "?")
    to_destroy: list[str] = []
    to_keep: list[str] = []

    for name, data in state.state["resources"].items():
        if service and name != service:
            to_keep.append(name)
            continue
        is_db = data.get("plugin") is not None
        if is_db and keep_db:
            to_keep.append(name)
        else:
            to_destroy.append(name)

    if not to_destroy:
        console.print("[yellow]Nothing to destroy.[/yellow]")
        return

    if not yes:
        console.print(f"\n[bold red]This will destroy the following resources in '{project}':[/bold red]\n")
        for name in to_destroy:
            data = state.state["resources"][name]
            provider = data.get("provider", "?")
            is_db = data.get("plugin") is not None
            label = f"db ({data['plugin']})" if is_db else "service"
            console.print(f"  [red]✗[/red] {name} — {label} on {provider}")
        if to_keep:
            for name in to_keep:
                console.print(f"  [dim]  {name} — kept[/dim]")
        console.print()

        if not Confirm.ask("Are you sure?", default=False):
            console.print("[dim]Aborted.[/dim]")
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
