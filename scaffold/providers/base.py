"""Provider ABC — interface all providers must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from scaffold.config.tokens import ResolvedTokens


class Provider(ABC):
    """Base class for infrastructure providers."""

    def __init__(self, tokens: ResolvedTokens):
        self.tokens = tokens

    @abstractmethod
    async def provision_database(
        self,
        name: str,
        project_id: str,
        plugin: str,
        extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Provision a database. Returns resource state dict with url, ids, etc."""
        ...

    @abstractmethod
    async def provision_service(
        self,
        name: str,
        project_id: str,
        source: str,
        start_command: str | None = None,
        env: dict[str, str] | None = None,
        runtime: str | None = None,
    ) -> dict[str, Any]:
        """Deploy a service. Returns resource state dict with url, ids, etc."""
        ...

    @abstractmethod
    async def destroy_service(self, name: str, resource_state: dict[str, Any]) -> None:
        """Tear down a service."""
        ...

    @abstractmethod
    async def destroy_database(self, name: str, resource_state: dict[str, Any]) -> None:
        """Tear down a database."""
        ...

    @abstractmethod
    async def get_service_url(self, resource_state: dict[str, Any]) -> str | None:
        """Get the current URL for a deployed service."""
        ...

    @abstractmethod
    async def set_env_vars(
        self, resource_state: dict[str, Any], env: dict[str, str]
    ) -> None:
        """Set environment variables on a service."""
        ...

    @abstractmethod
    async def health_check(self, url: str, path: str) -> bool:
        """Check if a service is healthy."""
        ...

    @abstractmethod
    async def get_logs(self, resource_state: dict[str, Any], follow: bool = False) -> str:
        """Get logs from a service."""
        ...

    @abstractmethod
    async def create_project(self, name: str) -> str:
        """Create a provider project. Returns project ID."""
        ...

    @abstractmethod
    async def get_env_vars(self, resource_state: dict[str, Any]) -> dict[str, str]:
        """Pull env vars from a deployed service."""
        ...
