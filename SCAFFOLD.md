# SCAFFOLD.md — Agent Reference

Complete reference for using scaffold programmatically. Read this file to understand the schema, CLI, and patterns.

## scaffold.yml Schema

```yaml
project: string           # required — project name (used for Railway project + domain generation)
region: string             # optional — default: us-west1

services:                  # map of service name → config
  <name>:
    provider: string       # railway | vercel (default: railway)
    runtime: string        # python | node | docker (Railway only)
    framework: string      # nextjs | remix (Vercel only)
    source: string         # source directory (default: ".")
    start: string          # start command (Railway only)
    health_check: string   # health check path, e.g. /health
    replicas: int          # number of replicas (default: 1)
    env:                   # environment variables
      KEY: "value"         # literal value
      KEY: "${{ref}}"      # reference (see Reference Syntax below)

databases:                 # map of database name → config
  <name>:
    provider: string       # railway (default: railway)
    plugin: string         # required — postgres | redis | mysql | mongodb
    extensions: [string]   # optional — e.g. [pgvector]

domain:                    # map of service name → domain config
  <name>:
    host: string           # full hostname, e.g. api.myapp.erdo.ai
    auth: string           # none | cloudflare-zero-trust | basic (default: none)
```

## Reference Syntax — ${{ref}}

References create implicit dependencies and are resolved during provisioning:

| Reference | Resolves to |
|-----------|-------------|
| `${{postgres.url}}` | PostgreSQL connection URL |
| `${{redis.url}}` | Redis connection URL |
| `${{server.url}}` | Deployed service URL |
| `${{env.VAR_NAME}}` | Environment variable from host |

References determine provision order via topological sort. Databases are provisioned before services that reference them.

## CLI Commands

### scaffold init
One-time interactive setup. Creates `~/.scaffold/config.yml` with provider tokens.

### scaffold plan "<description>"
Generate scaffold.yml from natural language using Claude.
```bash
scaffold plan "FastAPI server with Postgres and pgvector on Railway"
# → writes scaffold.yml
```

### scaffold up [--json] [--dry-run] [--file PATH]
Provision all resources. Idempotent — updates existing, creates new.
```bash
scaffold up --json
```
Returns:
```json
{
  "status": "ok",
  "project": "my-project",
  "resources": {
    "postgres": {"url": "postgresql://...", "provider": "railway"},
    "server": {"url": "https://...", "provider": "railway"}
  }
}
```

### scaffold down [SERVICE] [--keep-db] [--json]
Tear down resources. Optionally target a single service or keep databases.
```bash
scaffold down              # tear down everything
scaffold down worker       # tear down just the worker
scaffold down --keep-db    # tear down services, keep databases
```

### scaffold dev [--file PATH]
Run services locally, pointing at provisioned Railway DB. No local Postgres needed.
```bash
scaffold dev
# Starts each service locally, resolves ${{postgres.url}} to real Railway URL
```

### scaffold status [--json]
Show provisioned resources with URLs and health check results.
```bash
scaffold status --json
```
Returns:
```json
{
  "status": "provisioned",
  "project": "my-project",
  "resources": {
    "postgres": {"provider": "railway", "url": "postgresql://...", "health": "—"},
    "server": {"provider": "railway", "url": "https://...", "health": "ok"}
  }
}
```

### scaffold env sync
Push env vars from scaffold.yml to providers.

### scaffold env pull [--stdout] [--json]
Pull env vars from providers into local .env file.
```bash
scaffold env pull              # writes .env
scaffold env pull --json       # outputs as JSON
```

### scaffold logs <service> [--follow]
Stream logs from a deployed service.

### scaffold docs-path
Print the path to this file (SCAFFOLD.md). Use this to find and read the reference.
```bash
cat $(scaffold docs-path)
```

## Token Setup

Scaffold resolves tokens in priority order:

1. **Environment variables** (highest priority):
   - `SCAFFOLD_RAILWAY_TOKEN`
   - `SCAFFOLD_VERCEL_TOKEN`
   - `SCAFFOLD_SUPABASE_TOKEN`
   - `SCAFFOLD_CLOUDFLARE_API_TOKEN`
   - `SCAFFOLD_CLOUDFLARE_ACCOUNT_ID`
   - `SCAFFOLD_CLOUDFLARE_ZONE_ID`
   - `SCAFFOLD_ANTHROPIC_API_KEY`

2. **Project .scaffold/.env** — auto-read from project directory

3. **Global config** — `~/.scaffold/config.yml`

For agent-to-agent token handoff, write tokens to `.scaffold/.env`:
```bash
cat > .scaffold/.env << 'EOF'
SCAFFOLD_RAILWAY_TOKEN=rw_...
SCAFFOLD_CLOUDFLARE_API_TOKEN=cf_...
EOF
```

## Common Patterns

### FastAPI + Postgres
```yaml
project: my-api
services:
  server:
    provider: railway
    runtime: python
    source: .
    start: "uvicorn app:app --host 0.0.0.0 --port $PORT"
    health_check: /health
    env:
      DATABASE_URL: "${{postgres.url}}"
databases:
  postgres:
    provider: railway
    plugin: postgres
```

### FastAPI + Postgres + pgvector + Workers
```yaml
project: agent-system
services:
  server:
    provider: railway
    runtime: python
    source: .
    start: "uvicorn app:app --host 0.0.0.0 --port $PORT"
    health_check: /health
    env:
      DATABASE_URL: "${{postgres.url}}"
      REDIS_URL: "${{redis.url}}"
  worker:
    provider: railway
    runtime: python
    source: .
    start: "python -m worker"
    replicas: 2
    env:
      DATABASE_URL: "${{postgres.url}}"
      REDIS_URL: "${{redis.url}}"
databases:
  postgres:
    provider: railway
    plugin: postgres
    extensions: [pgvector]
  redis:
    provider: railway
    plugin: redis
```

### Next.js + FastAPI + Postgres
```yaml
project: fullstack-app
services:
  api:
    provider: railway
    runtime: python
    source: ./api
    start: "uvicorn app:app --host 0.0.0.0 --port $PORT"
    env:
      DATABASE_URL: "${{postgres.url}}"
  frontend:
    provider: vercel
    framework: nextjs
    source: ./frontend
    env:
      NEXT_PUBLIC_API_URL: "${{api.url}}"
databases:
  postgres:
    provider: railway
    plugin: postgres
```

## Error Format

All errors are returned as structured JSON when `--json` is used:
```json
{
  "status": "error",
  "error": "Token 'railway' not found. Set SCAFFOLD_RAILWAY_TOKEN in environment, .scaffold/.env, or ~/.scaffold/config.yml"
}
```

## State File

Scaffold tracks provisioned resources in `.scaffold/state.json`. This file:
- Makes `up` idempotent (update, not recreate)
- Makes `down` possible (knows what to destroy)
- Makes `dev` possible (knows DB URLs without re-querying)
- Should be committed to version control (URLs are not secrets)
