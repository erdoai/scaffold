"""Pydantic models for scaffold.yml."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class AuthConfig(BaseModel):
    """Authentication config for a service."""

    mode: str = "none"  # none | sidecar | middleware
    allowed_emails: list[str] = Field(default_factory=list)  # ["*@co.com", "user@x.com"]
    token_ttl: int = 86400  # JWT TTL in seconds (default 24h)
    email_provider: str = "resend"  # resend | postmark


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

    provider: str = "railway"  # railway | supabase | neon
    plugin: str  # postgres | redis | mysql | mongodb
    extensions: list[str] = Field(default_factory=list)
    # Supabase-specific
    project_ref: str | None = None  # existing supabase project ref
    # Neon-specific
    branch: str | None = None  # neon branch name


class DomainConfig(BaseModel):
    """Domain/auth config for a service."""

    host: str
    auth: str | AuthConfig = "none"  # "none" | AuthConfig

    @model_validator(mode="before")
    @classmethod
    def normalize_auth(cls, data: dict) -> dict:
        """Accept auth as a string shorthand or full AuthConfig dict."""
        if not isinstance(data, dict):
            return data
        auth = data.get("auth", "none")
        if isinstance(auth, str):
            data["auth"] = AuthConfig(mode=auth)
        elif isinstance(auth, dict):
            data["auth"] = AuthConfig(**auth)
        return data


class Manifest(BaseModel):
    """The full scaffold.yml manifest."""

    project: str
    region: str = "us-west1"
    services: dict[str, ServiceConfig] = Field(default_factory=dict)
    databases: dict[str, DatabaseConfig] = Field(default_factory=dict)
    domain: dict[str, DomainConfig] = Field(default_factory=dict)
