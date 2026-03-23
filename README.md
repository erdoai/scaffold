# scaffold

Deploy any service stack to Railway/Vercel — zero-touch, agent-friendly.

One YAML manifest. One command. Infrastructure appears.

## Install

```bash
pipx install scaffold
# or
uv tool install scaffold
```

**Prerequisites:** [Railway CLI](https://docs.railway.app/guides/cli) and optionally [Vercel CLI](https://vercel.com/docs/cli).

## Quickstart

```bash
# One-time setup — opens browser for provider OAuth, saves tokens
scaffold init

# Scan codebase and auto-generate scaffold.yml
scaffold plan

# Deploy everything
scaffold up

# Run locally with the same production DB
scaffold dev
```

## scaffold.yml

```yaml
project: my-app
region: us-west1

services:
  server:
    provider: railway
    runtime: python
    source: .
    start: "uvicorn app:app"
    health_check: /health
    env:
      DATABASE_URL: "${{postgres.url}}"
      APP_CONFIG: "${{file:config.yml}}"   # inject file contents as env var

databases:
  postgres:
    provider: railway      # or supabase, neon
    plugin: postgres
    extensions: [pgvector]
```

### References

| Syntax | Description |
|--------|-------------|
| `${{postgres.url}}` | Resolved URL of a provisioned resource |
| `${{server.url}}` | Resolved URL of a deployed service |
| `${{env.VAR}}` | Environment variable (from `.scaffold/.env` or shell) |
| `${{file:path}}` | Contents of a local file, injected as the env var value |

Database providers: **Railway** (default, provisions in-project), **Supabase** (managed Postgres + auth), **Neon** (serverless Postgres).

## Commands

| Command | Description |
|---------|-------------|
| `scaffold init` | Interactive provider login (Railway, Supabase, Vercel, Cloudflare) |
| `scaffold plan` | Scan codebase and auto-generate scaffold.yml |
| `scaffold up` | Provision everything (idempotent) |
| `scaffold dev` | Run locally with production DB |
| `scaffold status` | Show resources + health checks |
| `scaffold down` | Tear down resources |
| `scaffold env pull` | Pull env vars from providers |
| `scaffold logs <svc>` | Stream service logs |

All commands support `--json` for machine-readable output.

## Agent Integration

Scaffold is designed to be used by coding agents (Claude Code, etc.):

```bash
# Agent reads the reference doc
cat $(scaffold docs-path)

# Auto-generate manifest from the codebase
scaffold plan

# Or the agent writes scaffold.yml directly, then deploys
scaffold up --json
```

See [SCAFFOLD.md](SCAFFOLD.md) for the full agent reference.

## License

MIT
