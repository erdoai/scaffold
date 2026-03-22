"""scaffold init — interactive setup that auto-fetches tokens from provider CLIs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()

# ── Provider token locations ──────────────────────────────────────────────────

PROVIDERS = {
    "railway": {
        "name": "Railway",
        "cli": "railway",
        "login_cmd": ["railway", "login"],
        "whoami_cmd": ["railway", "whoami"],
        "token_file": Path.home() / ".railway" / "config.json",
        "token_path": ("user", "token"),
        "env_var": "SCAFFOLD_RAILWAY_TOKEN",
        "required": True,
    },
    "vercel": {
        "name": "Vercel",
        "cli": "vercel",
        "login_cmd": ["vercel", "login"],
        "whoami_cmd": ["vercel", "whoami"],
        "token_file": Path.home() / ".vercel" / "auth.json",
        "token_path": ("token",),
        "env_var": "SCAFFOLD_VERCEL_TOKEN",
        "required": False,
    },
    "cloudflare": {
        "name": "Cloudflare",
        "cli": "wrangler",
        "login_cmd": ["wrangler", "login"],
        "whoami_cmd": ["wrangler", "whoami"],
        "token_file": Path.home() / ".wrangler" / "config" / "default.toml",
        "token_key_toml": "oauth_token",
        "env_var": "SCAFFOLD_CLOUDFLARE_API_TOKEN",
        "required": False,
    },
}


def run_init() -> None:
    """Interactive setup — auto-fetch tokens from provider CLIs."""
    console.print()
    console.print(
        Panel.fit(
            "[bold]scaffold init[/bold]\n\n"
            "Connects to your cloud providers and saves tokens locally.\n"
            "Opens your browser for OAuth where supported.",
            border_style="blue",
        )
    )
    console.print()

    collected_tokens: dict[str, str] = {}
    collected_meta: dict[str, dict] = {}  # extra provider-specific info

    # ── Step 1: Railway (required) ────────────────────────────────────────

    console.print("[bold blue]1/4[/bold blue] [bold]Railway[/bold] — services & databases")
    railway_token = _setup_railway()
    if railway_token:
        collected_tokens["SCAFFOLD_RAILWAY_TOKEN"] = railway_token
        console.print("  [green]Railway connected.[/green]\n")
    else:
        console.print("  [yellow]Skipped. You'll need SCAFFOLD_RAILWAY_TOKEN to deploy.[/yellow]\n")

    # ── Step 2: Vercel (optional) ─────────────────────────────────────────

    console.print("[bold blue]2/4[/bold blue] [bold]Vercel[/bold] — frontend deployments [dim](optional)[/dim]")
    if Confirm.ask("  Set up Vercel?", default=True):
        vercel_token = _setup_vercel()
        if vercel_token:
            collected_tokens["SCAFFOLD_VERCEL_TOKEN"] = vercel_token
            console.print("  [green]Vercel connected.[/green]\n")
        else:
            console.print("  [yellow]Skipped.[/yellow]\n")
    else:
        console.print("  [dim]Skipped.[/dim]\n")

    # ── Step 3: Cloudflare (optional) ─────────────────────────────────────

    console.print("[bold blue]3/4[/bold blue] [bold]Cloudflare[/bold] — DNS & Zero Trust [dim](optional)[/dim]")
    if Confirm.ask("  Set up Cloudflare?", default=True):
        cf_tokens = _setup_cloudflare()
        collected_tokens.update(cf_tokens)
        if cf_tokens:
            console.print("  [green]Cloudflare connected.[/green]\n")
        else:
            console.print("  [yellow]Skipped.[/yellow]\n")
    else:
        console.print("  [dim]Skipped.[/dim]\n")

    # ── Step 4: Anthropic (optional, for `scaffold plan`) ─────────────────

    console.print("[bold blue]4/4[/bold blue] [bold]Anthropic[/bold] — AI manifest generation [dim](optional)[/dim]")
    if Confirm.ask("  Set up Anthropic API key? (for `scaffold plan`)", default=True):
        api_key = Prompt.ask("  API key", password=True)
        if api_key:
            collected_tokens["SCAFFOLD_ANTHROPIC_API_KEY"] = api_key
            console.print("  [green]Anthropic key saved.[/green]\n")
    else:
        console.print("  [dim]Skipped.[/dim]\n")

    # ── Defaults ──────────────────────────────────────────────────────────

    region = Prompt.ask("Default region", default="us-west1")
    domain_suffix = Prompt.ask("Domain suffix [dim](e.g. erdo.ai, blank to skip)[/dim]", default="")

    # ── Save ──────────────────────────────────────────────────────────────

    if not collected_tokens:
        console.print("[red]No tokens collected. Nothing to save.[/red]")
        return

    _save_config(collected_tokens, region, domain_suffix or None)
    _show_summary(collected_tokens, region, domain_suffix)


# ── Railway ───────────────────────────────────────────────────────────────────


def _setup_railway() -> str | None:
    """Log in to Railway and extract the token."""
    # Check if CLI is installed
    if not shutil.which("railway"):
        console.print("  [yellow]Railway CLI not found.[/yellow]")
        console.print("  Install: [dim]brew install railway[/dim] or [dim]npm i -g @railway/cli[/dim]")
        if Confirm.ask("  Paste a token manually instead?", default=True):
            return Prompt.ask("  Railway token", password=True) or None
        return None

    # Check if already logged in
    existing = _read_railway_token()
    if existing:
        # Verify it works
        if _cli_works("railway", ["railway", "whoami"]):
            console.print(f"  [dim]Already logged in.[/dim]")
            if Confirm.ask("  Use existing Railway session?", default=True):
                return existing

    # Run login flow
    console.print("  [dim]Opening browser for Railway login...[/dim]")
    result = subprocess.run(
        ["railway", "login"],
        timeout=120,
    )

    if result.returncode != 0:
        console.print("  [red]Railway login failed.[/red]")
        if Confirm.ask("  Paste a token manually instead?", default=True):
            return Prompt.ask("  Railway token", password=True) or None
        return None

    # Read token from config
    token = _read_railway_token()
    if not token:
        console.print("  [yellow]Could not read token from ~/.railway/config.json[/yellow]")
        if Confirm.ask("  Paste a token manually instead?", default=True):
            return Prompt.ask("  Railway token", password=True) or None
    return token


def _read_railway_token() -> str | None:
    """Read Railway token from ~/.railway/config.json."""
    config_path = Path.home() / ".railway" / "config.json"
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text())
        return data.get("user", {}).get("token")
    except Exception:
        return None


# ── Vercel ────────────────────────────────────────────────────────────────────


def _setup_vercel() -> str | None:
    """Log in to Vercel and extract the token."""
    if not shutil.which("vercel"):
        console.print("  [yellow]Vercel CLI not found.[/yellow]")
        console.print("  Install: [dim]npm i -g vercel[/dim]")
        if Confirm.ask("  Paste a token manually instead?", default=True):
            return Prompt.ask("  Vercel token", password=True) or None
        return None

    existing = _read_vercel_token()
    if existing:
        if _cli_works("vercel", ["vercel", "whoami"]):
            console.print(f"  [dim]Already logged in.[/dim]")
            if Confirm.ask("  Use existing Vercel session?", default=True):
                return existing

    console.print("  [dim]Opening browser for Vercel login...[/dim]")
    result = subprocess.run(["vercel", "login"], timeout=120)

    if result.returncode != 0:
        console.print("  [red]Vercel login failed.[/red]")
        if Confirm.ask("  Paste a token manually instead?", default=True):
            return Prompt.ask("  Vercel token", password=True) or None
        return None

    token = _read_vercel_token()
    if not token:
        console.print("  [yellow]Could not read token from ~/.vercel/auth.json[/yellow]")
        if Confirm.ask("  Paste a token manually instead?", default=True):
            return Prompt.ask("  Vercel token", password=True) or None
    return token


def _read_vercel_token() -> str | None:
    """Read Vercel token from ~/.vercel/auth.json."""
    auth_path = Path.home() / ".vercel" / "auth.json"
    if not auth_path.exists():
        return None
    try:
        data = json.loads(auth_path.read_text())
        return data.get("token")
    except Exception:
        return None


# ── Cloudflare ────────────────────────────────────────────────────────────────


def _setup_cloudflare() -> dict[str, str]:
    """Log in to Cloudflare and collect API token + IDs."""
    tokens: dict[str, str] = {}

    if not shutil.which("wrangler"):
        console.print("  [yellow]Wrangler CLI not found.[/yellow]")
        console.print("  Install: [dim]npm i -g wrangler[/dim]")
        console.print("  [dim]Falling back to manual entry.[/dim]")
        return _cloudflare_manual()

    # Try wrangler login (browser OAuth)
    if _cli_works("wrangler", ["wrangler", "whoami"]):
        console.print("  [dim]Already logged in to Cloudflare.[/dim]")
        if not Confirm.ask("  Re-authenticate?", default=False):
            # Read existing token
            cf_token = _read_cloudflare_token()
            if cf_token:
                tokens["SCAFFOLD_CLOUDFLARE_API_TOKEN"] = cf_token
    else:
        console.print("  [dim]Opening browser for Cloudflare login...[/dim]")
        result = subprocess.run(["wrangler", "login"], timeout=120)
        if result.returncode == 0:
            cf_token = _read_cloudflare_token()
            if cf_token:
                tokens["SCAFFOLD_CLOUDFLARE_API_TOKEN"] = cf_token

    if "SCAFFOLD_CLOUDFLARE_API_TOKEN" not in tokens:
        console.print("  [dim]Could not auto-detect token, falling back to manual.[/dim]")
        return _cloudflare_manual()

    # Account ID and Zone ID — these can't be auto-detected from CLI easily
    account_id = Prompt.ask("  Cloudflare account ID", default="")
    if account_id:
        tokens["SCAFFOLD_CLOUDFLARE_ACCOUNT_ID"] = account_id

    zone_id = Prompt.ask("  Cloudflare zone ID [dim](for DNS)[/dim]", default="")
    if zone_id:
        tokens["SCAFFOLD_CLOUDFLARE_ZONE_ID"] = zone_id

    return tokens


def _cloudflare_manual() -> dict[str, str]:
    """Manual Cloudflare token entry."""
    tokens: dict[str, str] = {}
    api_token = Prompt.ask("  Cloudflare API token", password=True, default="")
    if api_token:
        tokens["SCAFFOLD_CLOUDFLARE_API_TOKEN"] = api_token
    account_id = Prompt.ask("  Cloudflare account ID", default="")
    if account_id:
        tokens["SCAFFOLD_CLOUDFLARE_ACCOUNT_ID"] = account_id
    zone_id = Prompt.ask("  Cloudflare zone ID", default="")
    if zone_id:
        tokens["SCAFFOLD_CLOUDFLARE_ZONE_ID"] = zone_id
    return tokens


def _read_cloudflare_token() -> str | None:
    """Read Cloudflare token from wrangler config."""
    # Wrangler stores OAuth tokens in ~/.wrangler/config/default.toml
    config_path = Path.home() / ".wrangler" / "config" / "default.toml"
    if not config_path.exists():
        return None
    try:
        text = config_path.read_text()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("oauth_token"):
                # oauth_token = "..."
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return None
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _cli_works(name: str, cmd: list[str]) -> bool:
    """Check if a CLI command succeeds (e.g. `railway whoami`)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _save_config(
    tokens: dict[str, str],
    region: str,
    domain_suffix: str | None,
) -> None:
    """Save tokens to ~/.scaffold/config.yml and optionally echo the .env format."""
    import yaml

    from scaffold.config.global_config import CONFIG_PATH

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Build config.yml structure
    token_section: dict = {}

    token_map = {
        "SCAFFOLD_RAILWAY_TOKEN": "railway",
        "SCAFFOLD_VERCEL_TOKEN": "vercel",
        "SCAFFOLD_ANTHROPIC_API_KEY": "anthropic",
    }
    for env_var, key in token_map.items():
        if val := tokens.get(env_var):
            token_section[key] = val

    # Cloudflare nests
    cf: dict = {}
    if val := tokens.get("SCAFFOLD_CLOUDFLARE_API_TOKEN"):
        cf["api_token"] = val
    if val := tokens.get("SCAFFOLD_CLOUDFLARE_ACCOUNT_ID"):
        cf["account_id"] = val
    if val := tokens.get("SCAFFOLD_CLOUDFLARE_ZONE_ID"):
        cf["zone_id"] = val
    if cf:
        token_section["cloudflare"] = cf

    config: dict = {"tokens": token_section}
    defaults: dict = {"region": region}
    if domain_suffix:
        defaults["domain_suffix"] = domain_suffix
    config["defaults"] = defaults

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    # Also write a .env-style file for easy agent consumption
    env_path = CONFIG_PATH.parent / ".env"
    lines = [f"{k}={v}" for k, v in tokens.items()]
    env_path.write_text("\n".join(lines) + "\n")


def _show_summary(tokens: dict[str, str], region: str, domain_suffix: str) -> None:
    """Show what was saved."""
    from scaffold.config.global_config import CONFIG_PATH

    console.print()
    table = Table(title="Setup complete", border_style="green")
    table.add_column("Provider", style="bold")
    table.add_column("Status")

    provider_status = {
        "Railway": "SCAFFOLD_RAILWAY_TOKEN" in tokens,
        "Vercel": "SCAFFOLD_VERCEL_TOKEN" in tokens,
        "Cloudflare": "SCAFFOLD_CLOUDFLARE_API_TOKEN" in tokens,
        "Anthropic": "SCAFFOLD_ANTHROPIC_API_KEY" in tokens,
    }
    for name, connected in provider_status.items():
        status = "[green]Connected[/green]" if connected else "[dim]Not configured[/dim]"
        table.add_row(name, status)

    console.print(table)
    console.print()
    console.print(f"  Config:   [dim]{CONFIG_PATH}[/dim]")
    console.print(f"  Env file: [dim]{CONFIG_PATH.parent / '.env'}[/dim]")
    console.print(f"  Region:   [dim]{region}[/dim]")
    if domain_suffix:
        console.print(f"  Domain:   [dim]{domain_suffix}[/dim]")
    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print("  1. Create a [bold]scaffold.yml[/bold] or run [bold]scaffold plan[/bold]")
    console.print("  2. Run [bold]scaffold up[/bold] to deploy")
    console.print()
