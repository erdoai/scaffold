"""scaffold logs — stream logs from a service."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console

from scaffold.config.tokens import resolve_tokens
from scaffold.providers.railway import RailwayProvider
from scaffold.state.store import StateStore

console = Console()


def run_logs(service: str, follow: bool = False) -> None:
    """Stream logs from a service."""
    project_dir = Path.cwd()
    state = StateStore(project_dir)

    resource = state.get_resource(service)
    if not resource:
        console.print(f"[red]Service '{service}' not found in state.[/red]")
        console.print(f"[dim]Available: {', '.join(state.state['resources'].keys())}[/dim]")
        return

    tokens = resolve_tokens(project_dir)
    railway = RailwayProvider(tokens)

    output = asyncio.run(railway.get_logs(resource, follow=follow))
    console.print(output)
