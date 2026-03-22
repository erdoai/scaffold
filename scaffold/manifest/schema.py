"""Pydantic models for scaffold.yml."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServiceConfig(BaseModel):
    """A service to deploy (backend, worker, frontend, etc.)."""

    provider: str = "railway"  # railway | vercel
    runtime: str | None = None  # python | node | docker
    framework: str | None = None  # nextjs | remix | etc. (for Vercel)
    source: str = "."
    start: str | None = None
    health_check: str | None = None
    replicas: int = 1
    env: dict[str, str] = Field(default_factory=dict)


class DatabaseConfig(BaseModel):
    """A database or data store to provision."""

    provider: str = "railway"
    plugin: str  # postgres | redis | mysql | mongodb
    extensions: list[str] = Field(default_factory=list)


class DomainConfig(BaseModel):
    """Domain/auth config for a service."""

    host: str
    auth: str = "none"  # none | cloudflare-zero-trust | basic


class Manifest(BaseModel):
    """The full scaffold.yml manifest."""

    project: str
    region: str = "us-west1"
    services: dict[str, ServiceConfig] = Field(default_factory=dict)
    databases: dict[str, DatabaseConfig] = Field(default_factory=dict)
    domain: dict[str, DomainConfig] = Field(default_factory=dict)
