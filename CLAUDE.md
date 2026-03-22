# Scaffold

Deploy any service stack to Railway/Vercel — zero-touch, agent-friendly.

## Project structure

```
scaffold/
├── pyproject.toml          # hatchling build, typer CLI
├── SCAFFOLD.md             # agent-readable reference doc
├── scaffold/
│   ├── cli/                # Typer CLI commands
│   │   ├── main.py         # app entry + init, plan, docs-path
│   │   ├── _up.py          # scaffold up
│   │   ├── _down.py        # scaffold down
│   │   ├── _dev.py         # scaffold dev (local runner)
│   │   ├── _status.py      # scaffold status
│   │   ├── _env.py         # scaffold env sync/pull
│   │   └── _logs.py        # scaffold logs
│   ├── manifest/
│   │   ├── schema.py       # Pydantic models for scaffold.yml
│   │   ├── resolve.py      # ${{ref}} resolution + topo sort
│   │   └── loader.py       # YAML loader + validation
│   ├── providers/
│   │   ├── base.py         # Provider ABC
│   │   ├── railway.py      # Railway GraphQL API
│   │   └── vercel.py       # Vercel REST API
│   ├── planner/
│   │   └── agent.py        # Claude-based manifest generation
│   ├── config/
│   │   ├── tokens.py       # Token resolution (env → .scaffold/.env → global config)
│   │   └── global_config.py
│   └── state/
│       └── store.py        # .scaffold/state.json management
```

## Key patterns

- **Token resolution**: env vars > `.scaffold/.env` > `~/.scaffold/config.yml`
- **${{ref}} syntax**: `${{postgres.url}}`, `${{server.url}}`, `${{env.VAR}}`
- **Idempotent provisioning**: state.json tracks what exists, `up` updates not recreates
- **All commands support `--json`** for agent consumption

## Development

```bash
pip install -e ".[dev]"
scaffold --help
```

## Testing

```bash
pytest tests/
```
