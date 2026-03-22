""".scaffold/state.json — tracks provisioned resources."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATE_DIR = ".scaffold"
STATE_FILE = "state.json"


class StateStore:
    """Manages .scaffold/state.json for tracking provisioned resources."""

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self.state_dir = self.project_dir / STATE_DIR
        self.state_path = self.state_dir / STATE_FILE
        self._state: dict[str, Any] | None = None

    @property
    def state(self) -> dict[str, Any]:
        if self._state is None:
            self._state = self._load()
        return self._state

    def _load(self) -> dict[str, Any]:
        """Load state from disk or return empty state."""
        if self.state_path.exists():
            with open(self.state_path) as f:
                return json.load(f)
        return {"project": None, "provisioned_at": None, "resources": {}}

    def save(self) -> None:
        """Persist state to disk."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(self.state, f, indent=2)

    def set_project(self, project: str) -> None:
        self.state["project"] = project
        self.state["provisioned_at"] = datetime.now(timezone.utc).isoformat()

    def get_resource(self, name: str) -> dict[str, Any] | None:
        return self.state["resources"].get(name)

    def set_resource(self, name: str, data: dict[str, Any]) -> None:
        self.state["resources"][name] = data

    def remove_resource(self, name: str) -> dict[str, Any] | None:
        return self.state["resources"].pop(name, None)

    def get_url(self, name: str) -> str | None:
        """Get the URL for a provisioned resource."""
        resource = self.get_resource(name)
        if resource:
            return resource.get("url")
        return None

    def get_all_urls(self) -> dict[str, str]:
        """Get all resource URLs."""
        urls: dict[str, str] = {}
        for name, data in self.state["resources"].items():
            if url := data.get("url"):
                urls[name] = url
        return urls

    @property
    def is_provisioned(self) -> bool:
        return bool(self.state["resources"])

    def clear(self) -> None:
        self._state = {"project": None, "provisioned_at": None, "resources": {}}
        if self.state_path.exists():
            self.state_path.unlink()
