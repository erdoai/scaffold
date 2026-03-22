"""Neon provider — serverless Postgres via REST API."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from scaffold.config.tokens import ResolvedTokens
from scaffold.providers.base import Provider


NEON_API = "https://console.neon.tech/api/v2"


class NeonProvider(Provider):
    """Provisions serverless Postgres databases on Neon."""

    def __init__(self, tokens: ResolvedTokens):
        super().__init__(tokens)
        self._token = tokens.require("neon")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _api(
        self, method: str, path: str, data: dict | None = None
    ) -> dict:
        """Make a Neon API request."""
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method,
                f"{NEON_API}{path}",
                json=data,
                headers=self._headers,
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def create_project(self, name: str) -> str:
        """Create a Neon project. Returns project ID."""
        data = await self._api("POST", "/projects", {
            "project": {
                "name": name,
                "pg_version": 16,
            },
        })
        return data["project"]["id"]

    async def provision_database(
        self,
        name: str,
        project_id: str,
        plugin: str,
        extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Provision a Postgres database on Neon."""
        if plugin != "postgres":
            raise ValueError(f"Neon only supports postgres, not {plugin}. Use Railway for {plugin}.")

        if not project_id:
            project_id = await self.create_project(name)

        # Get the connection URI from the project
        data = await self._api("GET", f"/projects/{project_id}/connection_uri", None)
        connection_uri = data.get("uri", "")

        # If no URI yet, get it from branches/endpoints
        if not connection_uri:
            branches = await self._api("GET", f"/projects/{project_id}/branches")
            endpoints = await self._api("GET", f"/projects/{project_id}/endpoints")
            if endpoints.get("endpoints"):
                endpoint = endpoints["endpoints"][0]
                host = endpoint.get("host", "")
                # Neon connection string format
                connection_uri = f"postgresql://neondb_owner@{host}/neondb?sslmode=require"

        return {
            "provider": "neon",
            "neon_project_id": project_id,
            "url": connection_uri,
            "plugin": plugin,
            "extensions": extensions or [],
        }

    async def provision_service(self, **kwargs) -> dict[str, Any]:
        raise NotImplementedError("Neon is a database provider. Use Railway or Vercel for services.")

    async def destroy_service(self, name: str, resource_state: dict[str, Any]) -> None:
        raise NotImplementedError("Neon is a database provider.")

    async def destroy_database(self, name: str, resource_state: dict[str, Any]) -> None:
        """Delete a Neon project."""
        project_id = resource_state.get("neon_project_id")
        if project_id:
            await self._api("DELETE", f"/projects/{project_id}")

    async def get_service_url(self, resource_state: dict[str, Any]) -> str | None:
        return resource_state.get("url")

    async def set_env_vars(self, resource_state: dict[str, Any], env: dict[str, str]) -> None:
        pass

    async def get_env_vars(self, resource_state: dict[str, Any]) -> dict[str, str]:
        url = resource_state.get("url")
        return {"DATABASE_URL": url} if url else {}

    async def health_check(self, url: str, path: str) -> bool:
        return True  # Neon is serverless, always "up"

    async def get_logs(self, resource_state: dict[str, Any], follow: bool = False) -> str:
        return "Use Neon dashboard for database logs."
