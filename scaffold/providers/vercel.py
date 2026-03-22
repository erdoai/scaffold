"""Vercel provider — REST API wrapper."""

from __future__ import annotations

import json
from typing import Any

import httpx

from scaffold.config.tokens import ResolvedTokens
from scaffold.providers.base import Provider


VERCEL_API = "https://api.vercel.com"


class VercelProvider(Provider):
    """Provisions frontend deployments on Vercel."""

    def __init__(self, tokens: ResolvedTokens):
        super().__init__(tokens)
        self._token = tokens.require("vercel")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _api(
        self, method: str, path: str, data: dict | None = None
    ) -> dict:
        """Make a Vercel API request."""
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method,
                f"{VERCEL_API}{path}",
                json=data,
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def create_project(self, name: str) -> str:
        """Create a Vercel project. Returns project ID."""
        data = await self._api("POST", "/v10/projects", {
            "name": name,
            "framework": None,
        })
        return data["id"]

    async def provision_database(
        self,
        name: str,
        project_id: str,
        plugin: str,
        extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Vercel doesn't directly provision databases — use Vercel Postgres or external."""
        raise NotImplementedError(
            "Use Railway for databases. Vercel provider handles frontend deployments only."
        )

    async def provision_service(
        self,
        name: str,
        project_id: str,
        source: str,
        start_command: str | None = None,
        env: dict[str, str] | None = None,
        runtime: str | None = None,
    ) -> dict[str, Any]:
        """Deploy a frontend to Vercel."""
        # Set env vars first
        if env:
            for key, value in env.items():
                await self._api("POST", f"/v10/projects/{project_id}/env", {
                    "key": key,
                    "value": value,
                    "type": "plain",
                    "target": ["production", "preview", "development"],
                })

        # Trigger deployment via CLI (more reliable for source uploads)
        import subprocess

        cmd = ["npx", "vercel", "--yes", "--token", self._token, "--cwd", source]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        url = result.stdout.strip() if result.returncode == 0 else None

        return {
            "provider": "vercel",
            "vercel_project_id": project_id,
            "url": url,
        }

    async def destroy_service(self, name: str, resource_state: dict[str, Any]) -> None:
        """Delete a Vercel project."""
        project_id = resource_state.get("vercel_project_id")
        if project_id:
            await self._api("DELETE", f"/v9/projects/{project_id}")

    async def destroy_database(self, name: str, resource_state: dict[str, Any]) -> None:
        raise NotImplementedError("Vercel does not manage databases")

    async def get_service_url(self, resource_state: dict[str, Any]) -> str | None:
        project_id = resource_state.get("vercel_project_id")
        if not project_id:
            return None

        try:
            data = await self._api("GET", f"/v9/projects/{project_id}")
            targets = data.get("targets", {})
            production = targets.get("production", {})
            if alias := production.get("alias"):
                return f"https://{alias[0]}"
            if url := production.get("url"):
                return f"https://{url}"
        except Exception:
            pass

        return resource_state.get("url")

    async def set_env_vars(
        self, resource_state: dict[str, Any], env: dict[str, str]
    ) -> None:
        project_id = resource_state.get("vercel_project_id")
        if not project_id:
            return

        for key, value in env.items():
            await self._api("POST", f"/v10/projects/{project_id}/env", {
                "key": key,
                "value": value,
                "type": "plain",
                "target": ["production", "preview", "development"],
            })

    async def get_env_vars(self, resource_state: dict[str, Any]) -> dict[str, str]:
        project_id = resource_state.get("vercel_project_id")
        if not project_id:
            return {}

        data = await self._api("GET", f"/v9/projects/{project_id}/env")
        return {e["key"]: e.get("value", "") for e in data.get("envs", [])}

    async def health_check(self, url: str, path: str) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{url}{path}", timeout=10)
                return resp.status_code == 200
        except Exception:
            return False

    async def get_logs(self, resource_state: dict[str, Any], follow: bool = False) -> str:
        return "Use `vercel logs` CLI for Vercel service logs."
