"""Railway provider — GraphQL API wrapper.

Tested GQL types/mutations:
- variableCollectionUpsert: uses EnvironmentVariables! type (not Json/JSON)
- serviceInstanceUpdate: for start command (needs environmentId)
- serviceUpdate: does NOT support startCommand
- volumeCreate: for persistent storage
- tcpProxyCreate: for database public access
- serviceDomainCreate: for HTTP service public URLs
"""

from __future__ import annotations

import asyncio
import secrets
import string
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
            try:
                data = resp.json()
            except Exception:
                resp.raise_for_status()
                return {}

            if errors := data.get("errors"):
                msgs = "; ".join(e.get("message", str(e)) for e in errors)
                raise RuntimeError(f"Railway API error: {msgs}")

            resp.raise_for_status()
            return data.get("data", {})

    # ── Project / workspace ───────────────────────────────────────────────

    async def _get_workspace_id(self) -> str:
        """Get the user's default workspace ID."""
        data = await self._gql("""
            query { me { id workspaces { id name } } }
        """)
        me = data.get("me", {})
        workspaces = me.get("workspaces", [])
        if workspaces:
            return workspaces[0]["id"]
        return me["id"]

    async def _get_environment_id(self, project_id: str) -> str:
        """Get the default (production) environment ID for a project."""
        data = await self._gql("""
            query($pid: String!) {
                project(id: $pid) {
                    environments { edges { node { id name } } }
                }
            }
        """, {"pid": project_id})
        edges = data["project"]["environments"]["edges"]
        return edges[0]["node"]["id"]

    async def create_project(self, name: str) -> str:
        """Create a Railway project. Returns project ID."""
        workspace_id = await self._get_workspace_id()
        data = await self._gql("""
            mutation($name: String!, $wsId: String!) {
                projectCreate(input: { name: $name, workspaceId: $wsId }) { id }
            }
        """, {"name": name, "wsId": workspace_id})
        return data["projectCreate"]["id"]

    # ── Database provisioning ─────────────────────────────────────────────

    async def provision_database(
        self,
        name: str,
        project_id: str,
        plugin: str,
        extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Provision a database on Railway with password, volume, and TCP proxy."""
        image_map = {
            "postgres": "postgres:16",
            "redis": "redis:7",
            "mysql": "mysql:8",
            "mongodb": "mongo:7",
        }
        image = image_map.get(plugin, f"{plugin}:latest")

        # Create the database service
        data = await self._gql("""
            mutation($projectId: String!, $name: String!, $image: String!) {
                serviceCreate(input: {
                    projectId: $projectId, name: $name, source: { image: $image }
                }) { id }
            }
        """, {"projectId": project_id, "name": name, "image": image})
        service_id = data["serviceCreate"]["id"]

        env_id = await self._get_environment_id(project_id)

        # Generate credentials and set env vars
        password = _generate_password()
        db_env = _get_db_env(plugin, password)

        await self._set_env_vars_raw(project_id, service_id, env_id, db_env)

        # Add persistent volume
        mount_map = {
            "postgres": "/var/lib/postgresql/data",
            "redis": "/data",
            "mysql": "/var/lib/mysql",
            "mongodb": "/data/db",
        }
        mount_path = mount_map.get(plugin, "/data")
        await self._gql("""
            mutation($pid: String!, $svcId: String!, $envId: String!) {
                volumeCreate(input: {
                    projectId: $pid, serviceId: $svcId,
                    environmentId: $envId, mountPath: "%s"
                }) { id }
            }
        """ % mount_path, {"pid": project_id, "svcId": service_id, "envId": env_id})

        # Wait for Railway to initialise the service before creating TCP proxy
        await asyncio.sleep(3)

        # Create TCP proxy for public access (databases need TCP, not HTTP)
        port_map = {"postgres": 5432, "redis": 6379, "mysql": 3306, "mongodb": 27017}
        app_port = port_map.get(plugin, 5432)

        proxy = None
        for attempt in range(5):
            try:
                proxy = await self._gql("""
                    mutation($svcId: String!, $envId: String!) {
                        tcpProxyCreate(input: {
                            serviceId: $svcId, environmentId: $envId, applicationPort: %d
                        }) { domain proxyPort }
                    }
                """ % app_port, {"svcId": service_id, "envId": env_id})
                break
            except RuntimeError:
                if attempt < 4:
                    await asyncio.sleep(3)
                else:
                    raise

        domain = proxy["tcpProxyCreate"]["domain"]
        proxy_port = proxy["tcpProxyCreate"]["proxyPort"]
        url = _build_db_url(plugin, password, domain, proxy_port)

        return {
            "provider": "railway",
            "railway_project_id": project_id,
            "railway_service_id": service_id,
            "railway_environment_id": env_id,
            "url": url,
            "plugin": plugin,
            "extensions": extensions or [],
        }

    # ── Service provisioning ──────────────────────────────────────────────

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
        data = await self._gql("""
            mutation($projectId: String!, $name: String!) {
                serviceCreate(input: { projectId: $projectId, name: $name }) { id }
            }
        """, {"projectId": project_id, "name": name})
        service_id = data["serviceCreate"]["id"]

        env_id = await self._get_environment_id(project_id)

        # Set start command via serviceInstanceUpdate (not serviceUpdate)
        if start_command:
            await self._gql("""
                mutation($svcId: String!, $envId: String!, $cmd: String!) {
                    serviceInstanceUpdate(
                        serviceId: $svcId, environmentId: $envId,
                        input: { startCommand: $cmd }
                    )
                }
            """, {"svcId": service_id, "envId": env_id, "cmd": start_command})

        # Set env vars
        if env:
            await self._set_env_vars_raw(project_id, service_id, env_id, env)

        # Generate a public domain for HTTP services
        url = await self._create_service_domain(service_id, env_id)

        return {
            "provider": "railway",
            "railway_project_id": project_id,
            "railway_service_id": service_id,
            "railway_environment_id": env_id,
            "url": url,
        }

    async def provision_image_service(
        self,
        name: str,
        project_id: str,
        image: str,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Deploy a service from a Docker image (e.g. auth sidecar)."""
        data = await self._gql("""
            mutation($projectId: String!, $name: String!, $image: String!) {
                serviceCreate(input: {
                    projectId: $projectId, name: $name, source: { image: $image }
                }) { id }
            }
        """, {"projectId": project_id, "name": name, "image": image})
        service_id = data["serviceCreate"]["id"]

        env_id = await self._get_environment_id(project_id)

        if env:
            await self._set_env_vars_raw(project_id, service_id, env_id, env)

        url = await self._create_service_domain(service_id, env_id)

        return {
            "provider": "railway",
            "railway_project_id": project_id,
            "railway_service_id": service_id,
            "railway_environment_id": env_id,
            "url": url,
            "type": "auth-sidecar",
        }

    async def _create_service_domain(self, service_id: str, env_id: str) -> str | None:
        """Create a Railway-generated domain for a service."""
        try:
            data = await self._gql("""
                mutation($svcId: String!, $envId: String!) {
                    serviceDomainCreate(input: {
                        serviceId: $svcId, environmentId: $envId
                    }) { domain }
                }
            """, {"svcId": service_id, "envId": env_id})
            domain = data["serviceDomainCreate"]["domain"]
            return f"https://{domain}"
        except Exception:
            return None

    # ── Env vars ──────────────────────────────────────────────────────────

    async def _set_env_vars_raw(
        self, project_id: str, service_id: str, env_id: str, env: dict[str, str]
    ) -> None:
        """Set env vars using the correct EnvironmentVariables type."""
        await self._gql("""
            mutation($pid: String!, $svcId: String!, $envId: String!, $vars: EnvironmentVariables!) {
                variableCollectionUpsert(input: {
                    projectId: $pid, serviceId: $svcId,
                    environmentId: $envId, variables: $vars
                })
            }
        """, {"pid": project_id, "svcId": service_id, "envId": env_id, "vars": env})

    async def update_start_command(
        self, resource_state: dict[str, Any], start_command: str
    ) -> None:
        """Update the start command on an existing Railway service."""
        svc_id = resource_state.get("railway_service_id")
        env_id = resource_state.get("railway_environment_id")
        pid = resource_state.get("railway_project_id")
        if not svc_id:
            return
        if not env_id and pid:
            env_id = await self._get_environment_id(pid)
        if not env_id:
            return
        await self._gql("""
            mutation($svcId: String!, $envId: String!, $cmd: String!) {
                serviceInstanceUpdate(
                    serviceId: $svcId, environmentId: $envId,
                    input: { startCommand: $cmd }
                )
            }
        """, {"svcId": svc_id, "envId": env_id, "cmd": start_command})

    async def set_env_vars(
        self, resource_state: dict[str, Any], env: dict[str, str]
    ) -> None:
        """Set env vars on a Railway service."""
        pid = resource_state.get("railway_project_id")
        svc_id = resource_state.get("railway_service_id")
        env_id = resource_state.get("railway_environment_id")
        if not all([pid, svc_id, env_id]):
            # Try to get env_id if missing (older state)
            if pid and svc_id and not env_id:
                env_id = await self._get_environment_id(pid)
            else:
                return
        await self._set_env_vars_raw(pid, svc_id, env_id, env)

    async def get_env_vars(self, resource_state: dict[str, Any]) -> dict[str, str]:
        """Pull env vars from a Railway service."""
        pid = resource_state.get("railway_project_id")
        svc_id = resource_state.get("railway_service_id")
        env_id = resource_state.get("railway_environment_id")
        if not all([pid, svc_id]):
            return {}

        if not env_id:
            env_id = await self._get_environment_id(pid)

        data = await self._gql("""
            query($pid: String!, $svcId: String!, $envId: String!) {
                variables(projectId: $pid, serviceId: $svcId, environmentId: $envId)
            }
        """, {"pid": pid, "svcId": svc_id, "envId": env_id})

        # variables returns a JSON object of key:value pairs
        vars_data = data.get("variables", {})
        if isinstance(vars_data, dict):
            return vars_data
        return {}

    # ── Redeploy ──────────────────────────────────────────────────────────

    async def redeploy_service(self, resource_state: dict[str, Any]) -> None:
        """Trigger a redeploy on an existing Railway service."""
        svc_id = resource_state.get("railway_service_id")
        env_id = resource_state.get("railway_environment_id")
        pid = resource_state.get("railway_project_id")
        if not svc_id:
            return
        if not env_id and pid:
            env_id = await self._get_environment_id(pid)
        if not env_id:
            return
        await self._gql("""
            mutation($svcId: String!, $envId: String!) {
                serviceInstanceRedeploy(serviceId: $svcId, environmentId: $envId)
            }
        """, {"svcId": svc_id, "envId": env_id})

    # ── Destroy ───────────────────────────────────────────────────────────

    async def destroy_service(self, name: str, resource_state: dict[str, Any]) -> None:
        """Delete a Railway service."""
        service_id = resource_state.get("railway_service_id")
        if not service_id:
            return
        await self._gql("""
            mutation($id: String!) { serviceDelete(id: $id) }
        """, {"id": service_id})

    async def destroy_database(self, name: str, resource_state: dict[str, Any]) -> None:
        """Delete a Railway database (same as destroying a service)."""
        await self.destroy_service(name, resource_state)

    # ── URL / health ──────────────────────────────────────────────────────

    async def get_service_url(self, resource_state: dict[str, Any]) -> str | None:
        return resource_state.get("url")

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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _generate_password(length: int = 24) -> str:
    """Generate a secure password for database credentials."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _get_db_env(plugin: str, password: str) -> dict[str, str]:
    """Get the environment variables needed for a database image."""
    if plugin == "postgres":
        return {
            "POSTGRES_PASSWORD": password,
            "POSTGRES_USER": "scaffold",
            "POSTGRES_DB": "scaffold",
            "PGDATA": "/var/lib/postgresql/data/pgdata",
        }
    elif plugin == "mysql":
        return {
            "MYSQL_ROOT_PASSWORD": password,
            "MYSQL_DATABASE": "scaffold",
        }
    elif plugin == "mongodb":
        return {
            "MONGO_INITDB_ROOT_USERNAME": "scaffold",
            "MONGO_INITDB_ROOT_PASSWORD": password,
        }
    # Redis doesn't need a password env by default
    return {}


def _build_db_url(plugin: str, password: str, host: str, port: int) -> str:
    """Build a connection URL for a database."""
    if plugin == "postgres":
        return f"postgresql://scaffold:{password}@{host}:{port}/scaffold"
    elif plugin == "redis":
        return f"redis://{host}:{port}"
    elif plugin == "mysql":
        return f"mysql://root:{password}@{host}:{port}/scaffold"
    elif plugin == "mongodb":
        return f"mongodb://scaffold:{password}@{host}:{port}"
    return f"{plugin}://{host}:{port}"
