"""scaffold up — provision everything."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from rich.console import Console

from scaffold.config.tokens import ResolvedTokens, resolve_tokens
from scaffold.defaults import apply_defaults
from scaffold.manifest.loader import load_manifest
from scaffold.manifest.resolve import get_provision_order, resolve_refs
from scaffold.manifest.schema import Manifest
from scaffold.providers.railway import RailwayProvider
from scaffold.providers.vercel import VercelProvider
from scaffold.state.store import StateStore

console = Console()
err_console = Console(stderr=True)


def run_up(
    manifest_path: Path | None = None,
    dry_run: bool = False,
    json_output: bool = False,
) -> None:
    """Provision all resources in dependency order."""
    project_dir = Path.cwd()
    manifest = load_manifest(manifest_path)
    order = get_provision_order(manifest)
    tokens = resolve_tokens(project_dir)
    state = StateStore(project_dir)

    # Auto-generate missing defaults (secrets, etc.) before provisioning
    apply_defaults(project_dir)

    if dry_run:
        _show_plan(manifest, order, json_output)
        return

    result = asyncio.run(_provision_all(manifest, order, tokens, state))

    if json_output:
        console.print(json.dumps(result, indent=2))
    else:
        console.print("\n[bold green]All resources provisioned.[/bold green]\n")
        for name, info in result.get("resources", {}).items():
            url = info.get("url", "—")
            console.print(f"  [bold]{name}[/bold]: {url}")
        console.print()


def _show_plan(manifest: Manifest, order: list[str], json_output: bool) -> None:
    """Show what would be provisioned."""
    plan = {
        "project": manifest.project,
        "provision_order": order,
        "databases": {k: {"provider": v.provider, "plugin": v.plugin} for k, v in manifest.databases.items()},
        "services": {k: {"provider": v.provider, "source": v.source} for k, v in manifest.services.items()},
        "domains": {k: {"host": v.host, "auth": v.auth} for k, v in manifest.domain.items()},
    }
    if json_output:
        console.print(json.dumps(plan, indent=2))
    else:
        console.print(f"\n[bold]Project:[/bold] {manifest.project}")
        console.print(f"[bold]Provision order:[/bold] {' → '.join(order)}\n")
        for name in order:
            if name in manifest.databases:
                db = manifest.databases[name]
                console.print(f"  [blue]db[/blue]  {name}: {db.plugin} on {db.provider}")
            elif name in manifest.services:
                svc = manifest.services[name]
                console.print(f"  [green]svc[/green] {name}: {svc.runtime or svc.framework} on {svc.provider}")
        console.print()


def _get_db_provider(provider_name: str, tokens: ResolvedTokens) -> Any:
    """Get the right provider instance for a database."""
    if provider_name == "supabase":
        from scaffold.providers.supabase import SupabaseProvider
        return SupabaseProvider(tokens)
    elif provider_name == "neon":
        from scaffold.providers.neon import NeonProvider
        return NeonProvider(tokens)
    else:
        return RailwayProvider(tokens)


async def _provision_all(
    manifest: Manifest,
    order: list[str],
    tokens: ResolvedTokens,
    state: StateStore,
) -> dict:
    """Provision resources in topological order."""
    # Load .scaffold/.env into os.environ so ${{env.VAR}} refs resolve
    scaffold_env = Path.cwd() / ".scaffold" / ".env"
    if scaffold_env.exists():
        from dotenv import dotenv_values
        for k, v in dotenv_values(scaffold_env).items():
            if v is not None:
                os.environ.setdefault(k, v)

    state.set_project(manifest.project)
    resolved_urls: dict[str, str] = {}

    # Get or create Railway project (only if any resource uses Railway)
    railway_project_id = None
    railway: RailwayProvider | None = None
    needs_railway = any(
        s.provider == "railway"
        for s in list(manifest.services.values()) + list(manifest.databases.values())
    )
    if needs_railway:
        railway = RailwayProvider(tokens)
        # Check if project already exists in state
        for res in state.state["resources"].values():
            if pid := res.get("railway_project_id"):
                railway_project_id = pid
                break
        if not railway_project_id:
            console.print(f"[dim]Creating Railway project: {manifest.project}[/dim]")
            railway_project_id = await railway.create_project(manifest.project)

    needs_vercel = any(s.provider == "vercel" for s in manifest.services.values())
    vercel = VercelProvider(tokens) if needs_vercel else None

    for name in order:
        existing = state.get_resource(name)

        if name in manifest.databases:
            if existing:
                console.print(f"  [dim]Database {name} already provisioned[/dim]")
                resolved_urls[name] = existing.get("url", "")
                continue

            db = manifest.databases[name]
            console.print(f"  [blue]Provisioning database: {name} ({db.plugin} on {db.provider})[/blue]")

            db_provider = _get_db_provider(db.provider, tokens)

            if db.provider == "railway":
                result = await db_provider.provision_database(
                    name, railway_project_id, db.plugin, db.extensions
                )
            elif db.provider == "supabase":
                result = await db_provider.provision_database(
                    name, db.project_ref or "", db.plugin, db.extensions
                )
            elif db.provider == "neon":
                result = await db_provider.provision_database(
                    name, "", db.plugin, db.extensions
                )
            else:
                raise ValueError(f"Unknown database provider: {db.provider}")

            state.set_resource(name, result)
            resolved_urls[name] = result.get("url", "")

        elif name in manifest.services:
            svc = manifest.services[name]

            # Resolve env var references
            resolved_env = {}
            for k, v in svc.env.items():
                resolved_env[k] = resolve_refs(v, resolved_urls)

            if svc.provider == "railway":
                provider = railway
                project_id = railway_project_id
            elif svc.provider == "vercel":
                provider = vercel
                project_id = None
            else:
                raise ValueError(f"Unknown service provider: {svc.provider}")

            if existing and not existing.get("needs_redeploy"):
                console.print(f"  [dim]Service {name} already provisioned, updating env[/dim]")
                await provider.set_env_vars(existing, resolved_env)
                if svc.start:
                    await provider.update_start_command(existing, svc.start)
                resolved_urls[name] = existing.get("url", "")
                continue

            if svc.provider == "vercel" and not project_id:
                console.print(f"  [green]Creating Vercel project: {name}[/green]")
                project_id = await vercel.create_project(f"{manifest.project}-{name}")

            console.print(f"  [green]Deploying service: {name} on {svc.provider}[/green]")
            result = await provider.provision_service(
                name=name,
                project_id=project_id,
                source=svc.source,
                start_command=svc.start,
                env=resolved_env,
                runtime=svc.runtime,
            )
            state.set_resource(name, result)
            resolved_urls[name] = result.get("url", "")

    state.save()

    return {
        "status": "ok",
        "project": manifest.project,
        "resources": {
            name: {
                "url": data.get("url"),
                "provider": data.get("provider"),
            }
            for name, data in state.state["resources"].items()
        },
    }
