"""Railway provider — CLI + GraphQL API wrapper."""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

import httpx

from scaffold.config.tokens import ResolvedTokens
from scaffold.providers.base import Provider


RAILWAY_API = "https://backboard.railway.app/graphql/v2"


class RailwayProvider(Provider):
    """Provisions services and databases on Railway."""

    def __init__(self, tokens: ResolvedTokens):
        super().__init__(tokens)
        self._token = tokens.require("railway")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _gql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a Railway GraphQL query."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                RAILWAY_API,
                json={"query": query, "variables": variables or {}},
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if errors := data.get("errors"):
                raise RuntimeError(f"Railway API error: {errors}")
            return data.get("data", {})

    async def create_project(self, name: str) -> str:
        """Create a Railway project. Returns project ID."""
        query = """
        mutation($name: String!) {
            projectCreate(input: { name: $name }) {
                id
            }
        }
        """
        data = await self._gql(query, {"name": name})
        return data["projectCreate"]["id"]

    async def provision_database(
        self,
        name: str,
        project_id: str,
        plugin: str,
        extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Provision a database plugin on Railway."""
        # Create a service for the database
        query = """
        mutation($projectId: String!, $name: String!, $image: String!) {
            serviceCreate(input: {
                projectId: $projectId,
                name: $name,
                source: { image: $image }
            }) {
                id
            }
        }
        """
        image_map = {
            "postgres": "postgres:16",
            "redis": "redis:7",
            "mysql": "mysql:8",
            "mongodb": "mongo:7",
        }
        image = image_map.get(plugin, f"{plugin}:latest")
        data = await self._gql(query, {
            "projectId": project_id,
            "name": name,
            "image": image,
        })
        service_id = data["serviceCreate"]["id"]

        # For postgres with extensions, we'd set them via env/init scripts
        # Railway's template databases handle this; for custom images we'd
        # use a startup SQL script

        # Get the connection URL from the service variables
        # Railway auto-generates these for database templates
        url = await self._get_database_url(project_id, service_id, plugin)

        return {
            "provider": "railway",
            "railway_project_id": project_id,
            "railway_service_id": service_id,
            "url": url,
            "plugin": plugin,
            "extensions": extensions or [],
        }

    async def _get_database_url(
        self, project_id: str, service_id: str, plugin: str
    ) -> str:
        """Get the connection URL for a database service."""
        query = """
        query($projectId: String!, $serviceId: String!) {
            variables(projectId: $projectId, serviceId: $serviceId) {
                key
                value
            }
        }
        """
        # Railway may take a moment to provision — poll briefly
        for _ in range(10):
            try:
                data = await self._gql(query, {
                    "projectId": project_id,
                    "serviceId": service_id,
                })
                variables = data.get("variables", [])
                url_key = {
                    "postgres": "DATABASE_URL",
                    "redis": "REDIS_URL",
                    "mysql": "MYSQL_URL",
                    "mongodb": "MONGODB_URL",
                }.get(plugin, "DATABASE_URL")

                for var in variables:
                    if var["key"] == url_key:
                        return var["value"]
            except Exception:
                pass
            await asyncio.sleep(2)

        return f"pending://{plugin}-url-not-yet-available"

    async def provision_service(
        self,
        name: str,
        project_id: str,
        source: str,
        start_command: str | None = None,
        env: dict[str, str] | None = None,
        runtime: str | None = None,
    ) -> dict[str, Any]:
        """Deploy a service to Railway."""
        # Create the service
        query = """
        mutation($projectId: String!, $name: String!) {
            serviceCreate(input: {
                projectId: $projectId,
                name: $name
            }) {
                id
            }
        }
        """
        data = await self._gql(query, {"projectId": project_id, "name": name})
        service_id = data["serviceCreate"]["id"]

        # Set start command if provided
        if start_command:
            await self._set_service_config(service_id, start_command=start_command)

        # Set env vars
        if env:
            await self.set_env_vars(
                {"railway_service_id": service_id, "railway_project_id": project_id},
                env,
            )

        # Get the generated URL
        url = await self.get_service_url(
            {"railway_service_id": service_id, "railway_project_id": project_id}
        )

        return {
            "provider": "railway",
            "railway_project_id": project_id,
            "railway_service_id": service_id,
            "url": url,
        }

    async def _set_service_config(
        self, service_id: str, start_command: str | None = None
    ) -> None:
        """Set service configuration (start command, etc.)."""
        query = """
        mutation($serviceId: String!, $startCommand: String) {
            serviceUpdate(id: $serviceId, input: {
                startCommand: $startCommand
            }) {
                id
            }
        }
        """
        await self._gql(query, {
            "serviceId": service_id,
            "startCommand": start_command,
        })

    async def destroy_service(self, name: str, resource_state: dict[str, Any]) -> None:
        """Delete a Railway service."""
        service_id = resource_state.get("railway_service_id")
        if not service_id:
            return

        query = """
        mutation($serviceId: String!) {
            serviceDelete(id: $serviceId)
        }
        """
        await self._gql(query, {"serviceId": service_id})

    async def destroy_database(self, name: str, resource_state: dict[str, Any]) -> None:
        """Delete a Railway database (same as destroying a service)."""
        await self.destroy_service(name, resource_state)

    async def get_service_url(self, resource_state: dict[str, Any]) -> str | None:
        """Get the public URL for a Railway service."""
        service_id = resource_state.get("railway_service_id")
        if not service_id:
            return None

        query = """
        query($serviceId: String!) {
            service(id: $serviceId) {
                domains {
                    domain
                }
            }
        }
        """
        try:
            data = await self._gql(query, {"serviceId": service_id})
            domains = data.get("service", {}).get("domains", [])
            if domains:
                return f"https://{domains[0]['domain']}"
        except Exception:
            pass

        return None

    async def set_env_vars(
        self, resource_state: dict[str, Any], env: dict[str, str]
    ) -> None:
        """Set env vars on a Railway service."""
        service_id = resource_state.get("railway_service_id")
        project_id = resource_state.get("railway_project_id")
        if not service_id or not project_id:
            return

        query = """
        mutation($projectId: String!, $serviceId: String!, $variables: Json!) {
            variableUpsert(input: {
                projectId: $projectId,
                serviceId: $serviceId,
                variables: $variables
            })
        }
        """
        await self._gql(query, {
            "projectId": project_id,
            "serviceId": service_id,
            "variables": env,
        })

    async def get_env_vars(self, resource_state: dict[str, Any]) -> dict[str, str]:
        """Pull env vars from a Railway service."""
        service_id = resource_state.get("railway_service_id")
        project_id = resource_state.get("railway_project_id")
        if not service_id or not project_id:
            return {}

        query = """
        query($projectId: String!, $serviceId: String!) {
            variables(projectId: $projectId, serviceId: $serviceId) {
                key
                value
            }
        }
        """
        data = await self._gql(query, {
            "projectId": project_id,
            "serviceId": service_id,
        })
        return {v["key"]: v["value"] for v in data.get("variables", [])}

    async def health_check(self, url: str, path: str) -> bool:
        """Check service health via HTTP GET."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{url}{path}", timeout=10)
                return resp.status_code == 200
        except Exception:
            return False

    async def get_logs(self, resource_state: dict[str, Any], follow: bool = False) -> str:
        """Get logs via Railway CLI."""
        service_id = resource_state.get("railway_service_id")
        if not service_id:
            return "No service ID found"

        cmd = ["railway", "logs", "--service", service_id]
        if follow:
            cmd.append("--follow")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                env={"RAILWAY_TOKEN": self._token},
            )
            return result.stdout or result.stderr
        except subprocess.TimeoutExpired:
            return "Log fetch timed out"
