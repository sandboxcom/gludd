#!/usr/bin/env python3
"""Fail-closed verifier for Gludd's Ansible collection dependency graph.

The verifier intentionally operates on source trees *and* extracted Galaxy
artifacts.  It is read-only so a rejected candidate cannot mutate the active
collection tree during a zero-downtime promotion.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROLE_ACTIONS = {
    "include_role",
    "import_role",
    "ansible.builtin.include_role",
    "ansible.builtin.import_role",
}
FQCN_PATTERN = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
DYNAMIC_PATTERN = re.compile(r"{{|{%|{#")


@dataclass(frozen=True, slots=True, order=True)
class InteropIssue:
    """One deterministic collection interoperability violation."""

    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class CollectionInfo:
    """Installed content and dependency metadata for one collection."""

    fqcn: str
    path: Path
    dependencies: frozenset[str]
    roles: frozenset[str]
    modules: frozenset[str]
    module_redirects: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class InteropReport:
    """Stable audit result suitable for a release gate or JSON wrapper."""

    collections_root: Path
    collections: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    issues: tuple[InteropIssue, ...]

    @property
    def ok(self) -> bool:
        """Return whether the candidate is safe to promote."""
        return not self.issues

    def format_issues(self) -> str:
        """Render bounded, actionable diagnostics for CI and operators."""
        return "\n".join(
            f"[{issue.code}] {issue.path}: {issue.message}" for issue in self.issues
        )


def resolve_ansible_collections_root(root: Path) -> Path:
    """Resolve a repository, install root, or extracted collection artifact."""
    candidate = root.resolve()
    if (candidate / "galaxy.yml").is_file():
        return candidate
    for relative in (
        Path("collections/ansible_collections"),
        Path("ansible_collections"),
    ):
        resolved = candidate / relative
        if resolved.is_dir():
            return resolved
    if candidate.name == "ansible_collections" and candidate.is_dir():
        return candidate
    raise ValueError(
        f"no Ansible collection root found below {candidate}; expected "
        "collections/ansible_collections, ansible_collections, or galaxy.yml"
    )


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        documents = list(yaml.safe_load_all(stream))
    if len(documents) == 1:
        return documents[0]
    return documents


def _collection_paths(collections_root: Path) -> tuple[Path, ...]:
    if (collections_root / "galaxy.yml").is_file():
        return (collections_root,)
    return tuple(sorted(collections_root.glob("*/*/galaxy.yml")))


def _plugin_names(collection: Path, plugin_type: str) -> set[str]:
    base = collection / "plugins" / plugin_type
    if not base.is_dir():
        return set()
    return {
        ".".join(path.relative_to(base).with_suffix("").parts)
        for path in base.rglob("*.py")
        if path.name != "__init__.py"
    }


def _module_redirects(collection: Path) -> dict[str, str]:
    runtime = collection / "meta" / "runtime.yml"
    if not runtime.is_file():
        return {}
    data = _load_yaml(runtime)
    if not isinstance(data, dict):
        return {}
    routing = data.get("plugin_routing")
    if not isinstance(routing, dict):
        return {}
    modules = routing.get("modules")
    if not isinstance(modules, dict):
        return {}
    redirects: dict[str, str] = {}
    for alias, metadata in modules.items():
        if not isinstance(alias, str) or not isinstance(metadata, dict):
            continue
        target = metadata.get("redirect")
        if isinstance(target, str):
            redirects[alias] = target
    return redirects


def _read_collection(path_or_galaxy: Path) -> CollectionInfo:
    collection = path_or_galaxy.parent if path_or_galaxy.name == "galaxy.yml" else path_or_galaxy
    galaxy_path = collection / "galaxy.yml"
    data = _load_yaml(galaxy_path)
    if not isinstance(data, dict):
        raise ValueError(f"{galaxy_path} must contain a YAML mapping")
    namespace = data.get("namespace")
    name = data.get("name")
    if not isinstance(namespace, str) or not isinstance(name, str):
        raise ValueError(f"{galaxy_path} must define string namespace and name")
    dependencies = data.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise ValueError(f"{galaxy_path} dependencies must be a mapping")
    roles_dir = collection / "roles"
    roles = (
        {entry.name for entry in roles_dir.iterdir() if entry.is_dir()}
        if roles_dir.is_dir()
        else set()
    )
    redirects = _module_redirects(collection)
    modules = (
        _plugin_names(collection, "modules")
        | _plugin_names(collection, "action")
        | set(redirects)
    )
    return CollectionInfo(
        fqcn=f"{namespace}.{name}",
        path=collection,
        dependencies=frozenset(str(key) for key in dependencies),
        roles=frozenset(roles),
        modules=frozenset(modules),
        module_redirects=tuple(sorted(redirects.items())),
    )


def _task_files(collection: Path) -> tuple[Path, ...]:
    files: set[Path] = set()
    for section in ("tasks", "handlers"):
        files.update(collection.glob(f"roles/*/{section}/**/*.yml"))
        files.update(collection.glob(f"roles/*/{section}/**/*.yaml"))
    for directory in ("playbooks", "molecule"):
        files.update((collection / directory).rglob("*.yml"))
        files.update((collection / directory).rglob("*.yaml"))
    for molecule in collection.glob("roles/*/molecule"):
        files.update(molecule.rglob("*.yml"))
        files.update(molecule.rglob("*.yaml"))
    return tuple(sorted(files))


def _walk_mappings(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_mappings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_mappings(nested)


def _role_name(value: Any) -> Any:
    return value.get("name") if isinstance(value, dict) else value


def _module_fqcns(task: dict[str, Any]) -> Iterable[str]:
    for key in task:
        if isinstance(key, str) and key.startswith("general_ludd."):
            yield key
    for action_key in ("action", "local_action"):
        action = task.get(action_key)
        if isinstance(action, str):
            yield action.split(maxsplit=1)[0]
        elif isinstance(action, dict):
            module = action.get("module")
            if isinstance(module, str):
                yield module


def _looks_like_task(value: dict[str, Any]) -> bool:
    """Distinguish tasks from nested module argument mappings.

    Module arguments commonly contain an ``action`` key (for example
    ``action: list``).  Treating every recursive mapping as a task would turn
    those arguments into false short-module findings.
    """
    keys = {key for key in value if isinstance(key, str)}
    if ROLE_ACTIONS.intersection(keys):
        return True
    if any(
        key.startswith(("ansible.", "general_ludd."))
        for key in keys
    ):
        return True
    return "name" in keys and bool({"action", "local_action"}.intersection(keys))


def _split_fqcn(value: str) -> tuple[str, str] | None:
    parts = value.split(".", maxsplit=2)
    if len(parts) != 3:
        return None
    return ".".join(parts[:2]), parts[2]


def _append_edge_issue(
    issues: set[InteropIssue],
    *,
    owner: CollectionInfo,
    target: str,
    relative_path: str,
) -> None:
    if target != owner.fqcn and target not in owner.dependencies:
        issues.add(
            InteropIssue(
                "undeclared-dependency",
                relative_path,
                f"{owner.fqcn} calls {target} but galaxy.yml does not declare it",
            )
        )


def _scan_collection_calls(
    owner: CollectionInfo,
    catalog: dict[str, CollectionInfo],
    issues: set[InteropIssue],
) -> None:
    for path in _task_files(owner.path):
        relative = f"{owner.fqcn}/{path.relative_to(owner.path).as_posix()}"
        try:
            data = _load_yaml(path)
        except yaml.YAMLError as exc:
            issues.add(InteropIssue("invalid-yaml", relative, str(exc).splitlines()[0]))
            continue
        for task in _walk_mappings(data):
            if not _looks_like_task(task):
                continue
            for action in ROLE_ACTIONS.intersection(task):
                name = _role_name(task[action])
                if not isinstance(name, str):
                    issues.add(
                        InteropIssue(
                            "dynamic-role-name",
                            relative,
                            f"{action} name must be a literal collection FQCN",
                        )
                    )
                    continue
                if DYNAMIC_PATTERN.search(name):
                    issues.add(
                        InteropIssue(
                            "dynamic-role-name",
                            relative,
                            f"dynamic role {name!r} cannot be verified before promotion",
                        )
                    )
                    continue
                if not FQCN_PATTERN.fullmatch(name):
                    issues.add(
                        InteropIssue(
                            "short-role-name",
                            relative,
                            f"role {name!r} must use namespace.collection.role",
                        )
                    )
                    continue
                target_fqcn, role = _split_fqcn(name) or ("", "")
                _append_edge_issue(
                    issues,
                    owner=owner,
                    target=target_fqcn,
                    relative_path=relative,
                )
                target = catalog.get(target_fqcn)
                if target is None:
                    issues.add(
                        InteropIssue(
                            "missing-collection",
                            relative,
                            f"role target collection {target_fqcn} is not packaged",
                        )
                    )
                elif role not in target.roles:
                    issues.add(
                        InteropIssue(
                            "missing-role",
                            relative,
                            f"role {name} does not exist in the packaged collection",
                        )
                    )

            for fqcn in _module_fqcns(task):
                split = _split_fqcn(fqcn)
                if split is None:
                    issues.add(
                        InteropIssue(
                            "invalid-module-fqcn",
                            relative,
                            f"module {fqcn!r} must use namespace.collection.module",
                        )
                    )
                    continue
                target_fqcn, module = split
                _append_edge_issue(
                    issues,
                    owner=owner,
                    target=target_fqcn,
                    relative_path=relative,
                )
                target = catalog.get(target_fqcn)
                if target is None:
                    issues.add(
                        InteropIssue(
                            "missing-collection",
                            relative,
                            f"module target collection {target_fqcn} is not packaged",
                        )
                    )
                elif module not in target.modules:
                    issues.add(
                        InteropIssue(
                            "missing-module",
                            relative,
                            f"module {fqcn} does not exist in the packaged collection",
                        )
                    )


def _scan_module_redirects(
    owner: CollectionInfo,
    catalog: dict[str, CollectionInfo],
    issues: set[InteropIssue],
) -> None:
    relative = f"{owner.fqcn}/meta/runtime.yml"
    for alias, fqcn in owner.module_redirects:
        split = _split_fqcn(fqcn)
        if split is None:
            issues.add(
                InteropIssue(
                    "invalid-module-fqcn",
                    relative,
                    f"redirect {alias!r} has invalid target {fqcn!r}",
                )
            )
            continue
        target_fqcn, module = split
        _append_edge_issue(
            issues,
            owner=owner,
            target=target_fqcn,
            relative_path=relative,
        )
        target = catalog.get(target_fqcn)
        if target is None:
            issues.add(
                InteropIssue(
                    "missing-collection",
                    relative,
                    f"redirect target collection {target_fqcn} is not packaged",
                )
            )
        elif module not in target.modules:
            issues.add(
                InteropIssue(
                    "missing-module",
                    relative,
                    f"redirect {alias!r} targets missing module {fqcn}",
                )
            )


def _dependency_cycles(graph: dict[str, set[str]]) -> set[tuple[str, ...]]:
    cycles: set[tuple[str, ...]] = set()
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            cycle = visiting[visiting.index(node) :]
            rotations = [tuple(cycle[index:] + cycle[:index]) for index in range(len(cycle))]
            cycles.add(min(rotations))
            return
        if node in visited:
            return
        visiting.append(node)
        for target in sorted(graph.get(node, set())):
            visit(target)
        visiting.pop()
        visited.add(node)

    for node in sorted(graph):
        visit(node)
    return cycles


def audit_collection_interop(root: Path) -> InteropReport:
    """Audit one candidate tree without modifying candidate or active state."""
    collections_root = resolve_ansible_collections_root(root)
    catalog: dict[str, CollectionInfo] = {}
    for path in _collection_paths(collections_root):
        info = _read_collection(path)
        if info.fqcn in catalog:
            raise ValueError(f"duplicate collection {info.fqcn}")
        catalog[info.fqcn] = info

    issues: set[InteropIssue] = set()
    for info in catalog.values():
        _scan_collection_calls(info, catalog, issues)
        _scan_module_redirects(info, catalog, issues)
        for dependency in sorted(info.dependencies):
            if dependency.startswith("general_ludd.") and dependency not in catalog:
                issues.add(
                    InteropIssue(
                        "missing-dependency-collection",
                        f"{info.fqcn}/galaxy.yml",
                        f"declared dependency {dependency} is not packaged",
                    )
                )

    graph = {
        fqcn: {dependency for dependency in info.dependencies if dependency in catalog}
        for fqcn, info in catalog.items()
    }
    for cycle in _dependency_cycles(graph):
        chain = " -> ".join((*cycle, cycle[0]))
        issues.add(
            InteropIssue(
                "dependency-cycle",
                "galaxy.yml",
                f"collection dependency graph contains cycle: {chain}",
            )
        )

    edges = tuple(
        sorted((source, target) for source, targets in graph.items() for target in targets)
    )
    return InteropReport(
        collections_root=collections_root,
        collections=tuple(sorted(catalog)),
        edges=edges,
        issues=tuple(sorted(issues)),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by the beta4 release gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        report = audit_collection_interop(args.root)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        parser.error(str(exc))
    if not report.ok:
        print(report.format_issues())
        return 1
    print(
        "collection-interop: PASS — "
        f"{len(report.collections)} collections, {len(report.edges)} declared edges"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
