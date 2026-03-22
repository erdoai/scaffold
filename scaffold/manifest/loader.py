"""Load and validate scaffold.yml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from scaffold.manifest.schema import Manifest


MANIFEST_NAMES = ["scaffold.yml", "scaffold.yaml"]


def find_manifest(project_dir: Path | None = None) -> Path:
    """Find scaffold.yml in the given or current directory."""
    search_dir = project_dir or Path.cwd()

    for name in MANIFEST_NAMES:
        path = search_dir / name
        if path.exists():
            return path

    raise FileNotFoundError(
        f"No scaffold.yml found in {search_dir}. "
        "Create one or run `scaffold plan` to generate it."
    )


def load_manifest(path: Path | None = None) -> Manifest:
    """Load and validate a scaffold.yml file.

    Args:
        path: Path to scaffold.yml. If None, searches current directory.

    Returns:
        Validated Manifest object.

    Raises:
        FileNotFoundError: If no manifest found.
        ValueError: If manifest is invalid.
    """
    if path is None:
        path = find_manifest()

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError(f"Empty manifest: {path}")

    try:
        return Manifest.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Invalid scaffold.yml: {e}") from e
