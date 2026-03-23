"""scaffold up — provision everything."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Any

from rich.console import Console

from scaffold.config.tokens import ResolvedTokens, resolve_tokens
from scaffold.defaults import apply_defaults
from scaffold.manifest.loader import load_manifest
from scaffold.manifest.resolve import get_provision_order, resolve_refs
from scaffold.manifest.schema import AuthConfig, Manifest
from scaffold.providers.railway import RailwayProvider
from scaffold.providers.vercel import VercelProvider
from scaffold.state.store import StateStore

console = Console()
err_console = Console(stderr=True)

# Docker image for the auth sidecar (published separately)
AUTH_SIDECAR_IMAGE = "ghcr.io/erdo/scaffold-auth:latest"


def run_up(
    manifest_path: Path | None = None,
    dry_run: bool = False,
    json_output: bool = False,
    apply_auth: bool = False,
) -> None:
    """Provision all resources in dependency order."""
    project_dir = Path.cwd()
    manifest = load_manifest(manifest_path)
    order = get_provision_order(manifest)
    tokens = resolve_tokens(project_dir)
    state = StateStore(project_dir)

    # Auto-generate missing defaults (secrets, etc.) before provisioning
    apply_defaults(project_dir)

    # ── Middleware auth codegen (runs before provisioning) ─────────────
    _handle_middleware_auth(manifest, project_dir, apply_auth, json_output)

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
            label = f"  [bold]{name}[/bold]: {url}"
            if info.get("type") == "auth-sidecar":
                label += " [yellow](auth sidecar)[/yellow]"
            console.print(label)

        # Show auth info
        if auth_info := result.get("auth"):
            console.print("\n[bold]Auth:[/bold]")
            for svc_name, info in auth_info.items():
                emails = ", ".join(info.get("allowed_emails", []))
                console.print(f"  [bold]{svc_name}[/bold]: {info['mode']} — allowed: {emails}")
                if proxy_url := info.get("proxy_url"):
                    console.print(f"    proxy: {proxy_url}")
            console.print(f"\n  [dim]JWT secret in .scaffold/.env (AUTH_JWT_SECRET)[/dim]")

        console.print()


def _show_plan(manifest: Manifest, order: list[str], json_output: bool) -> None:
    """Show what would be provisioned."""
    plan = {
        "project": manifest.project,
        "provision_order": order,
        "databases": {k: {"provider": v.provider, "plugin": v.plugin} for k, v in manifest.databases.items()},
        "services": {k: {"provider": v.provider, "source": v.source} for k, v in manifest.services.items()},
        "domains": {k: {"host": v.host, "auth": v.auth.mode if hasattr(v.auth, "mode") else v.auth} for k, v in manifest.domain.items()},
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


def _handle_middleware_auth(
    manifest: Manifest, project_dir: Path, apply: bool, json_output: bool,
) -> None:
    """Run LLM codegen for any services with auth.mode == 'middleware'."""
    from scaffold.manifest.schema import AuthConfig

    middleware_services: list[tuple[str, AuthConfig]] = []
    for svc_name, domain_cfg in manifest.domain.items():
        auth: AuthConfig = domain_cfg.auth  # type: ignore[assignment]
        if auth.mode == "middleware":
            middleware_services.append((svc_name, auth))

    if not middleware_services:
        return

    from scaffold.auth.codegen import apply_auth_plan, generate_auth_plan, print_auth_plan

    # Ensure JWT secret exists before codegen
    scaffold_env = project_dir / ".scaffold" / ".env"
    _ensure_jwt_secret(scaffold_env)

    for svc_name, auth in middleware_services:
        console.print(f"\n[yellow]Generating auth middleware for {svc_name}...[/yellow]")
        plan = generate_auth_plan(project_dir, svc_name, auth)

        if apply:
            written = apply_auth_plan(project_dir, plan)
            for f in written:
                console.print(f"  [green]Wrote {f}[/green]")
            if wiring := plan.get("wiring"):
                console.print("\n[bold]Wire it up:[/bold]")
                for i, step in enumerate(wiring, 1):
                    console.print(f"  {i}. {step}")
            console.print()
        else:
            print_auth_plan(plan, json_output=json_output)


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

            # Resolve ${{ref}} in start command
            resolved_start = (
                resolve_refs(svc.start, resolved_urls)
                if svc.start
                else None
            )

            if existing and not existing.get("needs_redeploy"):
                console.print(f"  [dim]Service {name} already provisioned, updating env[/dim]")
                await provider.set_env_vars(existing, resolved_env)
                if resolved_start:
                    await provider.update_start_command(
                        existing, resolved_start
                    )
                # Connect to GitHub if not already connected
                if svc.provider == "railway" and hasattr(provider, "connect_repo"):
                    from scaffold.providers.railway import _detect_github_repo
                    repo = _detect_github_repo()
                    if repo:
                        try:
                            await provider.connect_repo(existing, repo)
                        except Exception:
                            pass  # non-fatal
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
                start_command=resolved_start,
                env=resolved_env,
                runtime=svc.runtime,
            )
            state.set_resource(name, result)
            resolved_urls[name] = result.get("url", "")

    # ── Auth sidecar provisioning ────────────────────────────────────────
    auth_info: dict[str, Any] = {}
    for svc_name, domain_cfg in manifest.domain.items():
        auth: AuthConfig = domain_cfg.auth  # type: ignore[assignment]
        if auth.mode != "sidecar":
            continue

        proxy_name = f"{svc_name}-auth-proxy"
        existing_proxy = state.get_resource(proxy_name)
        upstream_url = resolved_urls.get(svc_name, "")

        if existing_proxy:
            console.print(f"  [dim]Auth proxy {proxy_name} already provisioned, updating env[/dim]")
            # Update env vars on existing sidecar
            proxy_env = _build_sidecar_env(auth, upstream_url, scaffold_env)
            if railway:
                await railway.set_env_vars(existing_proxy, proxy_env)
            auth_info[svc_name] = {
                "mode": "sidecar",
                "proxy_url": existing_proxy.get("url"),
                "allowed_emails": auth.allowed_emails,
            }
            continue

        if not railway or not railway_project_id:
            err_console.print(
                f"  [red]Auth sidecar for {svc_name} requires Railway — skipping[/red]"
            )
            continue

        # Generate JWT secret if not already present
        jwt_secret = _ensure_jwt_secret(scaffold_env)

        proxy_env = _build_sidecar_env(auth, upstream_url, scaffold_env)

        console.print(f"  [yellow]Deploying auth sidecar: {proxy_name}[/yellow]")
        result = await railway.provision_image_service(
            name=proxy_name,
            project_id=railway_project_id,
            image=AUTH_SIDECAR_IMAGE,
            env=proxy_env,
        )
        state.set_resource(proxy_name, result)

        auth_info[svc_name] = {
            "mode": "sidecar",
            "proxy_url": result.get("url"),
            "allowed_emails": auth.allowed_emails,
        }

    state.save()

    output: dict[str, Any] = {
        "status": "ok",
        "project": manifest.project,
        "resources": {
            name: {
                "url": data.get("url"),
                "provider": data.get("provider"),
                "type": data.get("type"),
            }
            for name, data in state.state["resources"].items()
        },
    }
    if auth_info:
        output["auth"] = auth_info
    return output


def _ensure_jwt_secret(env_path: Path) -> str:
    """Ensure AUTH_JWT_SECRET exists in .scaffold/.env. Returns the secret."""
    # Check env first
    if existing := os.environ.get("AUTH_JWT_SECRET"):
        return existing

    # Check .scaffold/.env
    if env_path.exists():
        from dotenv import dotenv_values
        vals = dotenv_values(env_path)
        if existing := vals.get("AUTH_JWT_SECRET"):
            os.environ.setdefault("AUTH_JWT_SECRET", existing)
            return existing

    # Generate new secret
    secret = secrets.token_hex(32)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with open(env_path, "a") as f:
        f.write(f"AUTH_JWT_SECRET={secret}\n")
    os.environ["AUTH_JWT_SECRET"] = secret
    err_console.print("  [dim]Generated AUTH_JWT_SECRET[/dim]")
    return secret


def _build_sidecar_env(auth: AuthConfig, upstream_url: str, env_path: Path) -> dict[str, str]:
    """Build env vars for the auth sidecar service."""
    jwt_secret = os.environ.get("AUTH_JWT_SECRET", "")
    if not jwt_secret:
        jwt_secret = _ensure_jwt_secret(env_path)

    env: dict[str, str] = {
        "AUTH_JWT_SECRET": jwt_secret,
        "AUTH_UPSTREAM_URL": upstream_url,
        "AUTH_ALLOWED_EMAILS": ",".join(auth.allowed_emails) if auth.allowed_emails else "*",
        "AUTH_TOKEN_TTL": str(auth.token_ttl),
        "AUTH_EMAIL_PROVIDER": auth.email_provider,
    }

    # Pass through email API key if available
    if email_key := os.environ.get("SCAFFOLD_EMAIL_API_KEY"):
        env["AUTH_EMAIL_API_KEY"] = email_key
    if email_from := os.environ.get("SCAFFOLD_EMAIL_FROM"):
        env["AUTH_EMAIL_FROM"] = email_from

    return env
