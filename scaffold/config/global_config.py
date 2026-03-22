"""~/.scaffold/config.yml loader — defaults, domain_suffix, etc."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


CONFIG_PATH = Path.home() / ".scaffold" / "config.yml"


@dataclass
class GlobalConfig:
    region: str = "us-west1"
    domain_suffix: str | None = None

    @classmethod
    def load(cls) -> GlobalConfig:
        """Load global config from ~/.scaffold/config.yml."""
        if not CONFIG_PATH.exists():
            return cls()

        try:
            import yaml

            with open(CONFIG_PATH) as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return cls()

        defaults = data.get("defaults", {})
        return cls(
            region=defaults.get("region", "us-west1"),
            domain_suffix=defaults.get("domain_suffix"),
        )

    @classmethod
    def save_initial(cls, tokens: dict, defaults: dict | None = None) -> Path:
        """Create initial ~/.scaffold/config.yml during `scaffold init`."""
        import yaml

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        config: dict = {"tokens": tokens}
        if defaults:
            config["defaults"] = defaults

        with open(CONFIG_PATH, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        return CONFIG_PATH
