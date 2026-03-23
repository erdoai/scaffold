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
    provider: string       # railway | supabase | neon (default: railway)
    plugin: string         # required — postgres | redis | mysql | mongodb
    extensions: [string]   # optional — e.g. [pgvector]
    project_ref: string    # optional — existing Supabase project ref
    branch: string         # optional — Neon branch name

domain:                    # map of service name → domain config
  <name>:
    host: string           # full hostname, e.g. api.myapp.erdo.ai
    auth: string | object  # "none" (default) or auth config object:
      mode: string         # none | sidecar | middleware
      allowed_emails: [string]  # email patterns: ["*@co.com", "user@x.com"]
      token_ttl: int       # JWT lifetime in seconds (default: 86400)
      email_provider: string    # resend | postmark (default: resend)
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
Interactive setup that auto-fetches tokens from provider CLIs via OAuth. Opens your browser for Railway, Vercel, and Cloudflare login, reads the tokens from their CLI config files, and saves everything to `~/.scaffold/config.yml` + `~/.scaffold/.env`. Falls back to manual paste if a CLI isn't installed.

### scaffold plan [DESCRIPTION] [--source PATH] [--output PATH]
Scans the codebase automatically and generates scaffold.yml. Reads pyproject.toml/package.json, detects frameworks (FastAPI, Next.js, etc.), spots database imports (sqlalchemy, pgvector, redis), finds entry points and worker processes.
```bash
scaffold plan                  # scan cwd, generate scaffold.yml
scaffold plan -s ../my-app     # scan a different directory
scaffold plan "also needs redis for queues"  # scan + extra hint
```

### scaffold up [--json] [--dry-run] [--apply] [--file PATH]
Provision all resources. Idempotent — updates existing, creates new.
If `auth.mode: middleware` is set, shows the generated auth plan. Use `--apply` to write the auth files.
```bash
scaffold up --json
scaffold up --apply        # also write auth middleware files
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
   - `SCAFFOLD_NEON_TOKEN`
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

## Auth

Scaffold supports email-based authentication for deployed services. Two modes:

### Sidecar mode (`auth.mode: sidecar`)
Deploys a reverse proxy in front of the service that handles magic link email login and JWT issuance. No code changes needed.

- Users visit `/auth/login` to get a magic link email
- Magic link → `/auth/verify` → JWT (cookie + JSON response)
- Authenticated requests are proxied to the upstream service
- API consumers use `Authorization: Bearer <jwt>` header

Requires `SCAFFOLD_EMAIL_API_KEY` (Resend or Postmark) in `.scaffold/.env` or env. Without it, the sidecar runs in dev mode (login link shown directly, no email sent).

### Middleware mode (`auth.mode: middleware`)
Scaffold scans the repo, calls Claude to generate auth middleware tailored to the specific codebase and framework. Supports FastAPI, Express, Next.js, Flask, Django, Hono, and others.

```bash
scaffold up                # shows the auth plan (files + wiring instructions)
scaffold up --apply        # writes the auth files into the project
```

The LLM generates:
- JWT-verifying middleware for the detected framework
- Login/verify routes (magic link flow)
- Wiring instructions for integrating into the existing app

Requires `SCAFFOLD_ANTHROPIC_API_KEY` (same as `scaffold plan`).

### Auth env vars
- `AUTH_JWT_SECRET` — auto-generated by `scaffold up`, stored in `.scaffold/.env`
- `SCAFFOLD_EMAIL_API_KEY` — user-provided API key for Resend/Postmark
- `SCAFFOLD_EMAIL_FROM` — sender email address (default: `auth@scaffold.dev`)

### Example
```yaml
project: my-api
services:
  server:
    provider: railway
    runtime: python
    start: "uvicorn app:app --host 0.0.0.0 --port $PORT"
    env:
      DATABASE_URL: "${{postgres.url}}"
databases:
  postgres:
    plugin: postgres
domain:
  server:
    host: api.myapp.com
    auth:
      mode: sidecar
      allowed_emails: ["*@mycompany.com", "ceo@gmail.com"]
      token_ttl: 86400
```

After `scaffold up`, the output includes the auth proxy URL and JWT secret location.

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

### FastAPI + Supabase Postgres
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
    provider: supabase
    plugin: postgres
    extensions: [pgvector]
```

### FastAPI + Neon Postgres
```yaml
project: my-api
services:
  server:
    provider: railway
    runtime: python
    source: .
    start: "uvicorn app:app --host 0.0.0.0 --port $PORT"
    env:
      DATABASE_URL: "${{postgres.url}}"
databases:
  postgres:
    provider: neon
    plugin: postgres
    extensions: [pgvector]
```

## Auto-Generated Defaults — scaffold-defaults.yml

Create a `scaffold-defaults.yml` in your project root (committed to git) to auto-generate secrets and other values that don't exist yet in `.scaffold/.env`. On every `scaffold up`, missing values are generated locally and appended to `.scaffold/.env`, then pushed to providers via the normal `${{env.VAR}}` resolution.

```yaml
auto:
  SESSION_SECRET:
    type: secret
    length: 32
  WORKER_API_KEY:
    type: secret
    length: 24
  INSTANCE_ID:
    type: uuid
```

### Supported types

| Type | Fields | Description |
|------|--------|-------------|
| `secret` | `length` (default: 32) | Cryptographic hex string |
| `uuid` | — | Random UUID v4 |
| `string` | `default` (required) | Literal default value |

**Key behavior:**
- Existing values in `.scaffold/.env` are never overwritten
- Generated secrets never leave the machine — they're written locally and pushed to providers
- The file is idempotent: running `scaffold up` twice won't regenerate anything

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
