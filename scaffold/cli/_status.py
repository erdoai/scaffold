"""scaffold status — show provisioned resources."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from scaffold.config.tokens import resolve_tokens
from scaffold.manifest.loader import load_manifest
from scaffold.manifest.schema import AuthConfig
from scaffold.providers.railway import RailwayProvider
from scaffold.state.store import StateStore

console = Console()


def run_status(json_output: bool = False) -> None:
    """Show provisioned resources, URLs, and health checks."""
    project_dir = Path.cwd()
    state = StateStore(project_dir)

    if not state.is_provisioned:
        if json_output:
            console.print(json.dumps({"status": "not_provisioned", "resources": {}}))
        else:
            console.print("[yellow]Nothing provisioned. Run `scaffold up` first.[/yellow]")
        return

    # Try to load manifest for health check paths and auth config
    health_checks: dict[str, str] = {}
    auth_domains: dict[str, AuthConfig] = {}
    try:
        manifest = load_manifest()
        for name, svc in manifest.services.items():
            if svc.health_check:
                health_checks[name] = svc.health_check
        for name, domain in manifest.domain.items():
            auth = domain.auth
            if isinstance(auth, AuthConfig) and auth.mode != "none":
                auth_domains[name] = auth
    except Exception:
        pass

    result = asyncio.run(_check_status(state, health_checks))

    if json_output:
        # Include auth info in JSON output
        if auth_domains:
            result["auth"] = {
                name: {
                    "mode": auth.mode,
                    "allowed_emails": auth.allowed_emails,
                    "proxy": f"{name}-auth-proxy",
                }
                for name, auth in auth_domains.items()
            }
        console.print(json.dumps(result, indent=2))
    else:
        table = Table(title=f"Project: {state.state.get('project', '?')}")
        table.add_column("Resource", style="bold")
        table.add_column("Provider")
        table.add_column("URL")
        table.add_column("Health")

        for name, info in result["resources"].items():
            health = info.get("health", "—")
            health_style = "green" if health == "ok" else "red" if health == "fail" else "dim"
            # Mark auth sidecars
            display_name = name
            res_data = state.get_resource(name) or {}
            if res_data.get("type") == "auth-sidecar":
                display_name = f"{name} (auth)"
            table.add_row(
                display_name,
                info.get("provider", "—"),
                info.get("url", "—"),
                f"[{health_style}]{health}[/{health_style}]",
            )

        console.print(table)

        # Show auth summary
        if auth_domains:
            console.print("\n[bold]Auth:[/bold]")
            for name, auth in auth_domains.items():
                emails = ", ".join(auth.allowed_emails) if auth.allowed_emails else "*"
                proxy_url = state.get_url(f"{name}-auth-proxy") or "not deployed"
                console.print(f"  {name}: [yellow]{auth.mode}[/yellow] — {emails}")
                console.print(f"    proxy: {proxy_url}")
            console.print(f"  [dim]JWT secret: see .scaffold/.env (AUTH_JWT_SECRET)[/dim]")

        console.print(f"\n[dim]Provisioned at: {state.state.get('provisioned_at', '?')}[/dim]")


async def _check_status(state: StateStore, health_checks: dict[str, str]) -> dict:
    """Check health of all resources."""
    tokens = resolve_tokens(Path.cwd())
    railway = RailwayProvider(tokens)

    resources = {}
    for name, data in state.state["resources"].items():
        url = data.get("url")
        health = "—"

        if name in health_checks and url:
            healthy = await railway.health_check(url, health_checks[name])
            health = "ok" if healthy else "fail"

        resources[name] = {
            "provider": data.get("provider"),
            "url": url,
            "health": health,
        }

    return {
        "status": "provisioned",
        "project": state.state.get("project"),
        "provisioned_at": state.state.get("provisioned_at"),
        "resources": resources,
    }
