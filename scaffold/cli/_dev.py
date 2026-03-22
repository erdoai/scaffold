"""scaffold dev — run services locally with Railway DB."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from scaffold.manifest.loader import load_manifest
from scaffold.manifest.resolve import resolve_refs
from scaffold.state.store import StateStore

console = Console()


def run_dev(manifest_path: Path | None = None) -> None:
    """Run services locally, pointing at provisioned databases."""
    project_dir = Path.cwd()
    manifest = load_manifest(manifest_path)
    state = StateStore(project_dir)

    if not state.is_provisioned:
        console.print(
            "[yellow]No resources provisioned. Run `scaffold up` first.[/yellow]"
        )
        return

    resolved_urls = state.get_all_urls()

    # For dev mode, services resolve to localhost
    port = 8000
    local_urls: dict[str, str] = {}
    for name in manifest.services:
        local_urls[name] = f"http://localhost:{port}"
        port += 1

    # Merge: databases use real URLs, services use localhost
    all_urls = {**resolved_urls, **local_urls}

    processes: list[subprocess.Popen] = []

    try:
        port = 8000
        for name, svc in manifest.services.items():
            if not svc.start:
                console.print(f"  [dim]Skipping {name} (no start command)[/dim]")
                continue

            # Resolve env vars
            resolved_env = {}
            for k, v in svc.env.items():
                resolved_env[k] = resolve_refs(v, all_urls)

            env = {**os.environ, **resolved_env, "PORT": str(port)}

            console.print(f"  [green]Starting {name}[/green] on port {port}")
            proc = subprocess.Popen(
                svc.start,
                shell=True,
                env=env,
                cwd=svc.source if svc.source != "." else None,
            )
            processes.append(proc)
            port += 1

        console.print(f"\n[bold]Running {len(processes)} service(s). Ctrl+C to stop.[/bold]\n")

        # Wait for any process to exit
        while processes:
            for proc in processes:
                ret = proc.poll()
                if ret is not None:
                    console.print(f"[yellow]Process exited with code {ret}[/yellow]")
                    _shutdown(processes)
                    return
            import time
            time.sleep(0.5)

    except KeyboardInterrupt:
        console.print("\n[dim]Shutting down...[/dim]")
        _shutdown(processes)


def _shutdown(processes: list[subprocess.Popen]) -> None:
    """Gracefully shut down all processes."""
    for proc in processes:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)

    for proc in processes:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
