"""Supabase provider — managed Postgres via REST API."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from scaffold.config.tokens import ResolvedTokens
from scaffold.providers.base import Provider


SUPABASE_API = "https://api.supabase.com/v1"


class SupabaseProvider(Provider):
    """Provisions databases on Supabase."""

    def __init__(self, tokens: ResolvedTokens):
        super().__init__(tokens)
        self._token = tokens.require("supabase")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _api(
        self, method: str, path: str, data: dict | None = None
    ) -> dict | list:
        """Make a Supabase Management API request."""
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method,
                f"{SUPABASE_API}{path}",
                json=data,
                headers=self._headers,
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def create_project(self, name: str) -> str:
        """Create a Supabase project. Returns project ref."""
        # Get the user's organization
        orgs = await self._api("GET", "/organizations")
        if not orgs:
            raise RuntimeError("No Supabase organizations found. Create one at supabase.com/dashboard.")
        org_id = orgs[0]["id"]

        data = await self._api("POST", "/projects", {
            "name": name,
            "organization_id": org_id,
            "region": "us-west-1",
            "plan": "free",
            "db_pass": _generate_db_password(),
        })
        project_ref = data["id"]

        # Wait for project to be ready
        await self._wait_for_project(project_ref)
        return project_ref

    async def _wait_for_project(self, project_ref: str, timeout: int = 120) -> None:
        """Wait for a Supabase project to become active."""
        for _ in range(timeout // 3):
            project = await self._api("GET", f"/projects/{project_ref}")
            if project.get("status") == "ACTIVE_HEALTHY":
                return
            await asyncio.sleep(3)
        raise RuntimeError(f"Supabase project {project_ref} did not become ready in {timeout}s")

    async def provision_database(
        self,
        name: str,
        project_id: str,
        plugin: str,
        extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Provision a Postgres database on Supabase."""
        if plugin != "postgres":
            raise ValueError(f"Supabase only supports postgres, not {plugin}. Use Railway for {plugin}.")

        # If project_id is an existing project ref, use it; otherwise create new
        if not project_id:
            project_id = await self.create_project(name)

        # Get the connection string
        settings = await self._api("GET", f"/projects/{project_id}/settings")
        db = settings.get("db", {})

        # Build the connection URL
        host = db.get("host", f"db.{project_id}.supabase.co")
        port = db.get("port", 5432)
        db_name = db.get("name", "postgres")

        # The pooler URL is more reliable for app connections
        url = f"postgresql://postgres.{project_id}:{db.get('pass', '')}@aws-0-us-west-1.pooler.supabase.com:6543/{db_name}"

        # Enable requested extensions
        if extensions:
            await self._enable_extensions(project_id, extensions)

        return {
            "provider": "supabase",
            "supabase_project_ref": project_id,
            "url": url,
            "plugin": plugin,
            "extensions": extensions or [],
            "host": host,
        }

    async def _enable_extensions(self, project_ref: str, extensions: list[str]) -> None:
        """Enable Postgres extensions via the API."""
        for ext in extensions:
            try:
                await self._api("POST", f"/projects/{project_ref}/extensions", {
                    "name": ext,
                    "schema": "extensions",
                })
            except Exception:
                # Extension might already be enabled or not available
                pass

    async def provision_service(self, **kwargs) -> dict[str, Any]:
        raise NotImplementedError("Supabase is a database provider. Use Railway or Vercel for services.")

    async def destroy_service(self, name: str, resource_state: dict[str, Any]) -> None:
        raise NotImplementedError("Supabase is a database provider.")

    async def destroy_database(self, name: str, resource_state: dict[str, Any]) -> None:
        """Delete a Supabase project (and its database)."""
        project_ref = resource_state.get("supabase_project_ref")
        if project_ref:
            await self._api("DELETE", f"/projects/{project_ref}")

    async def get_service_url(self, resource_state: dict[str, Any]) -> str | None:
        return resource_state.get("url")

    async def set_env_vars(self, resource_state: dict[str, Any], env: dict[str, str]) -> None:
        pass  # Supabase doesn't have service env vars

    async def get_env_vars(self, resource_state: dict[str, Any]) -> dict[str, str]:
        """Return the database URL as an env var."""
        url = resource_state.get("url")
        return {"DATABASE_URL": url} if url else {}

    async def health_check(self, url: str, path: str) -> bool:
        """Check if the Supabase project API is responding."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=10)
                return resp.status_code < 500
        except Exception:
            return False

    async def get_logs(self, resource_state: dict[str, Any], follow: bool = False) -> str:
        return "Use Supabase dashboard for database logs."


def _generate_db_password() -> str:
    """Generate a secure database password."""
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(32))
