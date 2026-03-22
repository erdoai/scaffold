"""Claude-based manifest generation from natural language."""

from __future__ import annotations

from pathlib import Path

from scaffold.config.tokens import resolve_tokens
from scaffold.manifest.schema import Manifest


SYSTEM_PROMPT = """You are a deployment manifest generator. Given a natural language description of a service stack, generate a valid scaffold.yml manifest.

The manifest format is:

```yaml
project: <project-name>
region: us-west1

services:
  <service-name>:
    provider: railway | vercel
    runtime: python | node | docker    # for Railway
    framework: nextjs | remix          # for Vercel
    source: .                          # source directory
    start: "<start command>"           # for Railway services
    health_check: /health              # optional health check path
    replicas: 1                        # number of replicas
    env:
      VAR_NAME: "value"
      DB_URL: "${{postgres.url}}"      # reference a database
      OTHER: "${{env.MY_VAR}}"         # reference an env var
      API: "${{server.url}}"           # reference another service

databases:
  <db-name>:
    provider: railway
    plugin: postgres | redis | mysql | mongodb
    extensions: [pgvector]             # optional extensions

domain:
  <service-name>:
    host: api.example.com
    auth: none | cloudflare-zero-trust | basic
```

Rules:
- Use ${{resource.url}} to reference database URLs and service URLs
- Use ${{env.VAR_NAME}} for environment variables that should come from the user's env
- Services that depend on databases will have those refs resolved automatically
- Only output the YAML manifest, no explanation
- Use sensible defaults for anything not specified
"""


def generate_manifest(description: str) -> str:
    """Generate a scaffold.yml from a natural language description."""
    import anthropic

    tokens = resolve_tokens()
    api_key = tokens.require("anthropic")

    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": description},
        ],
    )

    # Extract YAML from response
    text = message.content[0].text

    # Strip markdown code fences if present
    if "```yaml" in text:
        text = text.split("```yaml", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]

    return text.strip() + "\n"
