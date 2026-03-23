"""Scaffold CLI — deploy any service stack to Railway/Vercel."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from scaffold.__version__ import __version__

app = typer.Typer(
    name="scaffold",
    help="Deploy any service stack to Railway/Vercel.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

env_app = typer.Typer(help="Manage environment variables.")
app.add_typer(env_app, name="env")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version"),
):
    if version:
        console.print(f"scaffold {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


# ── init ──────────────────────────────────────────────────────────────────────


@app.command()
def init():
    """Interactive setup — auto-fetch tokens from provider CLIs."""
    from scaffold.cli._init import run_init

    run_init()


# ── plan ──────────────────────────────────────────────────────────────────────


@app.command()
def plan(
    description: str = typer.Argument(None, help="Optional extra context (codebase is scanned automatically)"),
    source: Path = typer.Option(None, "--source", "-s", help="Project directory to scan (default: cwd)"),
    output: Path = typer.Option(Path("scaffold.yml"), "--output", "-o", help="Output path"),
):
    """Scan the codebase and generate scaffold.yml automatically."""
    from scaffold.planner.agent import generate_manifest

    project_dir = source or Path.cwd()
    console.print(f"[dim]Scanning {project_dir} ...[/dim]")
    manifest_yaml = generate_manifest(project_dir=project_dir, description=description)

    output.write_text(manifest_yaml)
    console.print(f"[green]Manifest written to {output}[/green]")
    console.print("[dim]Review and edit as needed, then run: scaffold up[/dim]")


# ── up ────────────────────────────────────────────────────────────────────────


@app.command()
def up(
    manifest_path: Path = typer.Option(None, "--file", "-f", help="Path to scaffold.yml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show execution plan without provisioning"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    apply_auth: bool = typer.Option(False, "--apply", help="Write auth middleware files (for auth.mode: middleware)"),
):
    """Provision everything defined in scaffold.yml (idempotent)."""
    from scaffold.cli._up import run_up

    run_up(manifest_path, dry_run=dry_run, json_output=json_output, apply_auth=apply_auth)


# ── down ──────────────────────────────────────────────────────────────────────


@app.command()
def down(
    service: str = typer.Argument(None, help="Specific service to tear down (all if omitted)"),
    keep_db: bool = typer.Option(False, "--keep-db", help="Preserve databases"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Tear down provisioned resources."""
    from scaffold.cli._down import run_down

    run_down(service, keep_db=keep_db, yes=yes, json_output=json_output)


# ── dev ───────────────────────────────────────────────────────────────────────


@app.command()
def dev(
    manifest_path: Path = typer.Option(None, "--file", "-f", help="Path to scaffold.yml"),
):
    """Run services locally, pointing at the provisioned Railway DB."""
    from scaffold.cli._dev import run_dev

    run_dev(manifest_path)


# ── status ────────────────────────────────────────────────────────────────────


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show provisioned resources, URLs, and health checks."""
    from scaffold.cli._status import run_status

    run_status(json_output=json_output)


# ── env ───────────────────────────────────────────────────────────────────────


@env_app.command("sync")
def env_sync():
    """Push env vars from scaffold.yml to providers."""
    from scaffold.cli._env import run_env_sync

    run_env_sync()


@env_app.command("pull")
def env_pull(
    stdout: bool = typer.Option(False, "--stdout", help="Print to stdout instead of .env"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Pull env vars from providers → local .env."""
    from scaffold.cli._env import run_env_pull

    run_env_pull(stdout=stdout, json_output=json_output)


# ── logs ──────────────────────────────────────────────────────────────────────


@app.command()
def logs(
    service: str = typer.Argument(..., help="Service name to stream logs from"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
):
    """Stream logs from a service."""
    from scaffold.cli._logs import run_logs

    run_logs(service, follow=follow)


# ── docs-path ─────────────────────────────────────────────────────────────────


@app.command("docs-path")
def docs_path():
    """Print path to SCAFFOLD.md (for agents to read)."""
    scaffold_md = Path(__file__).parent.parent.parent / "SCAFFOLD.md"
    if not scaffold_md.exists():
        # Try installed package location
        import importlib.resources as pkg_resources
        try:
            scaffold_md = Path(str(pkg_resources.files("scaffold").joinpath("../SCAFFOLD.md")))
        except Exception:
            pass

    console.print(str(scaffold_md))
