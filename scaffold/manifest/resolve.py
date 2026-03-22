"""${{ref}} resolution and topological sort for dependency ordering."""

from __future__ import annotations

import re
from collections import defaultdict

from scaffold.manifest.schema import Manifest


REF_PATTERN = re.compile(r"\$\{\{(\w+)\.(\w+)\}\}")


def extract_refs(value: str) -> list[tuple[str, str]]:
    """Extract all ${{resource.field}} references from a string."""
    return REF_PATTERN.findall(value)


def build_dependency_graph(manifest: Manifest) -> dict[str, set[str]]:
    """Build a dependency graph from ${{ref}} usage in env vars.

    Returns a dict mapping resource name → set of resource names it depends on.
    """
    deps: dict[str, set[str]] = defaultdict(set)
    all_resources = set(manifest.databases.keys()) | set(manifest.services.keys())

    for name, svc in manifest.services.items():
        deps.setdefault(name, set())
        for env_val in svc.env.values():
            for ref_resource, ref_field in extract_refs(env_val):
                if ref_resource in all_resources and ref_resource != name:
                    deps[name].add(ref_resource)

    # Databases have no deps on other resources
    for name in manifest.databases:
        deps.setdefault(name, set())

    return dict(deps)


def topological_sort(deps: dict[str, set[str]]) -> list[str]:
    """Return resources in dependency order (provisions dependencies first).

    Raises ValueError on circular dependencies.
    """
    in_degree: dict[str, int] = {node: 0 for node in deps}
    for node, node_deps in deps.items():
        for dep in node_deps:
            if dep not in in_degree:
                in_degree[dep] = 0
            in_degree[dep]  # ensure dep exists
        # Actually we need reverse: in_degree counts how many things depend on you
        # No — in_degree[node] = number of things node depends on
        pass

    # Reset and compute properly
    in_degree = {node: len(node_deps) for node, node_deps in deps.items()}
    # Add nodes that are depended on but not in deps dict
    for node_deps in deps.values():
        for dep in node_deps:
            if dep not in in_degree:
                in_degree[dep] = 0

    # Kahn's algorithm
    queue = [node for node, degree in in_degree.items() if degree == 0]
    result: list[str] = []

    while queue:
        queue.sort()  # deterministic ordering
        node = queue.pop(0)
        result.append(node)

        for dependent, dep_set in deps.items():
            if node in dep_set:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

    if len(result) != len(in_degree):
        missing = set(in_degree.keys()) - set(result)
        raise ValueError(f"Circular dependency detected involving: {missing}")

    return result


def resolve_refs(
    value: str,
    resolved_urls: dict[str, str],
    env_vars: dict[str, str] | None = None,
) -> str:
    """Resolve ${{resource.field}} references in a string.

    Args:
        value: String potentially containing ${{ref}} patterns.
        resolved_urls: Map of resource_name → resolved URL.
        env_vars: Map of env var name → value (for ${{env.VAR}} refs).
    """
    def replace_ref(match: re.Match) -> str:
        resource, ref_field = match.group(1), match.group(2)

        if resource == "env":
            # ${{env.VAR_NAME}} → environment variable
            if env_vars and ref_field in env_vars:
                return env_vars[ref_field]
            import os
            return os.environ.get(ref_field, match.group(0))

        # ${{resource.url}} → resolved URL
        if resource in resolved_urls:
            return resolved_urls[resource]

        return match.group(0)  # unresolved — leave as-is

    return REF_PATTERN.sub(replace_ref, value)


def get_provision_order(manifest: Manifest) -> list[str]:
    """Get the order in which resources should be provisioned."""
    deps = build_dependency_graph(manifest)
    return topological_sort(deps)
