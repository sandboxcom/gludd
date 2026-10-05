#!/usr/bin/env python3
"""Verify exact core/collection ownership for direct Python dependencies."""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tomllib
from dataclasses import dataclass
from importlib.metadata import packages_distributions
from pathlib import Path
from typing import cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

INVENTORY_PATH = Path("config/core-python-dependency-ownership.json")


@dataclass(frozen=True)
class RuntimeEvidence:
    """One exact non-import runtime dependency reference."""

    path: str
    token: str


@dataclass(frozen=True)
class OwnershipRecord:
    """Expected observations for one direct dependency."""

    disposition: str
    import_roots: tuple[str, ...]
    core_import_paths: tuple[str, ...]
    collection_import_paths: tuple[str, ...]
    collection_requirement_paths: tuple[str, ...]
    runtime_evidence: tuple[RuntimeEvidence, ...]


def _table(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be an object with string keys")
    return cast(dict[str, object], value)


def _strings(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{context} must be a list of strings")
    return tuple(cast(list[str], value))


def _direct_dependencies(root: Path) -> tuple[dict[str, object], set[str]]:
    metadata = cast(
        dict[str, object],
        tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")),
    )
    project = _table(metadata.get("project"), "project")
    raw_dependencies = project.get("dependencies")
    if not isinstance(raw_dependencies, list) or not all(
        isinstance(item, str) for item in raw_dependencies
    ):
        raise ValueError("project.dependencies must be a list of strings")
    dependencies: set[str] = {
        str(canonicalize_name(Requirement(item).name))
        for item in cast(list[str], raw_dependencies)
    }
    return metadata, dependencies


def _load_inventory(root: Path) -> dict[str, OwnershipRecord]:
    path = root / INVENTORY_PATH
    raw = _table(json.loads(path.read_text(encoding="utf-8")), "inventory")
    if raw.get("schema_version") != 1:
        raise ValueError("inventory.schema_version must be 1")
    dependencies = _table(raw.get("dependencies"), "inventory.dependencies")
    records: dict[str, OwnershipRecord] = {}
    for dependency, value in dependencies.items():
        record = _table(value, f"dependencies.{dependency}")
        raw_evidence = record.get("runtime_evidence")
        if not isinstance(raw_evidence, list):
            raise ValueError(
                f"dependencies.{dependency}.runtime_evidence must be a list"
            )
        evidence: list[RuntimeEvidence] = []
        for index, item in enumerate(raw_evidence):
            evidence_item = _table(
                item,
                f"dependencies.{dependency}.runtime_evidence[{index}]",
            )
            evidence_path = evidence_item.get("path")
            token = evidence_item.get("token")
            if not isinstance(evidence_path, str) or not isinstance(token, str):
                raise ValueError(
                    f"dependencies.{dependency}.runtime_evidence[{index}] "
                    "requires string path and token"
                )
            evidence.append(RuntimeEvidence(path=evidence_path, token=token))
        disposition = record.get("disposition")
        if not isinstance(disposition, str):
            raise ValueError(f"dependencies.{dependency}.disposition must be a string")
        records[canonicalize_name(dependency)] = OwnershipRecord(
            disposition=disposition,
            import_roots=_strings(
                record.get("import_roots"),
                f"dependencies.{dependency}.import_roots",
            ),
            core_import_paths=_strings(
                record.get("core_import_paths"),
                f"dependencies.{dependency}.core_import_paths",
            ),
            collection_import_paths=_strings(
                record.get("collection_import_paths"),
                f"dependencies.{dependency}.collection_import_paths",
            ),
            collection_requirement_paths=_strings(
                record.get("collection_requirement_paths"),
                f"dependencies.{dependency}.collection_requirement_paths",
            ),
            runtime_evidence=tuple(evidence),
        )
    return records


def _python_files(path: Path) -> tuple[Path, ...]:
    if not path.exists():
        return ()
    return tuple(sorted(file for file in path.rglob("*.py") if file.is_file()))


def _collection_python_files(path: Path) -> tuple[Path, ...]:
    """Return collection runtime files while excluding test-only consumers."""
    return tuple(
        file
        for file in _python_files(path)
        if "tests" not in file.relative_to(path).parts
    )


def _static_imports(root: Path, files: tuple[Path, ...]) -> dict[str, set[str]]:
    imports: dict[str, set[str]] = {}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            module_names: tuple[str, ...]
            if isinstance(node, ast.Import):
                module_names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_names = (node.module,)
            else:
                continue
            for module_name in module_names:
                import_root = module_name.split(".", 1)[0]
                imports.setdefault(import_root, set()).add(relative)
    return imports


def _collection_requirements(root: Path) -> dict[str, set[str]]:
    requirements: dict[str, set[str]] = {}
    collection_root = root / "collections/ansible_collections"
    if not collection_root.exists():
        return requirements
    for path in sorted(collection_root.rglob("meta/ee-requirements.txt")):
        relative = path.relative_to(root).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            candidate = line.strip()
            if not candidate or candidate.startswith("#"):
                continue
            try:
                name = canonicalize_name(Requirement(candidate).name)
            except InvalidRequirement as exc:
                raise ValueError(f"invalid requirement in {relative}: {candidate}") from exc
            requirements.setdefault(name, set()).add(relative)
    return requirements


def _observed_paths(
    imports: dict[str, set[str]], import_roots: tuple[str, ...]
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                path
                for import_root in import_roots
                for path in imports.get(import_root, set())
            }
        )
    )


def _runtime_evidence_errors(
    root: Path, dependency: str, evidence: tuple[RuntimeEvidence, ...]
) -> list[str]:
    errors: list[str] = []
    for item in evidence:
        path = root / item.path
        if not path.is_file():
            errors.append(f"{dependency}: runtime evidence path is missing: {item.path}")
        elif item.token not in path.read_text(encoding="utf-8"):
            errors.append(
                f"{dependency}: runtime evidence token {item.token!r} is missing "
                f"from {item.path}"
            )
    return errors


def audit_repository(root: Path) -> list[str]:
    """Return exact dependency-ownership violations for ``root``.

    Args:
        root: Repository root containing project metadata and the ownership inventory.

    Returns:
        Sorted human-readable violations; an empty list means the boundary is exact.
    """
    inventory_path = root / INVENTORY_PATH
    if not inventory_path.is_file():
        return [f"ownership inventory is missing: {INVENTORY_PATH.as_posix()}"]
    try:
        _metadata, direct_dependencies = _direct_dependencies(root)
        inventory = _load_inventory(root)
        core_imports = _static_imports(root, _python_files(root / "src/general_ludd"))
        collection_imports = _static_imports(
            root,
            _collection_python_files(root / "collections/ansible_collections"),
        )
        collection_requirements = _collection_requirements(root)
    except (OSError, SyntaxError, ValueError, tomllib.TOMLDecodeError) as exc:
        return [f"dependency ownership audit could not load repository: {exc}"]

    errors: list[str] = []
    missing = sorted(direct_dependencies - inventory.keys())
    stale = sorted(inventory.keys() - direct_dependencies)
    if missing:
        errors.append(f"direct dependencies missing from inventory: {missing}")
    if stale:
        errors.append(f"inventory entries are not direct dependencies: {stale}")

    for dependency in sorted(direct_dependencies & inventory.keys()):
        record = inventory[dependency]
        if record.disposition != "retain-core":
            errors.append(
                f"{dependency}: direct dependency disposition must be retain-core, "
                f"got {record.disposition!r}"
            )
        if not record.import_roots:
            errors.append(f"{dependency}: import_roots must not be empty")
            continue

        observed_core = _observed_paths(core_imports, record.import_roots)
        observed_collection = _observed_paths(collection_imports, record.import_roots)
        observed_requirements = tuple(
            sorted(collection_requirements.get(dependency, set()))
        )
        if observed_core != tuple(sorted(record.core_import_paths)):
            errors.append(
                f"{dependency}: core imports differ; expected "
                f"{list(record.core_import_paths)}, observed {list(observed_core)}"
            )
        if observed_collection != tuple(sorted(record.collection_import_paths)):
            errors.append(
                f"{dependency}: collection imports differ; expected "
                f"{list(record.collection_import_paths)}, "
                f"observed {list(observed_collection)}"
            )
        if observed_requirements != tuple(sorted(record.collection_requirement_paths)):
            errors.append(
                f"{dependency}: collection requirements differ; expected "
                f"{list(record.collection_requirement_paths)}, "
                f"observed {list(observed_requirements)}"
            )

        evidence_errors = _runtime_evidence_errors(
            root,
            dependency,
            record.runtime_evidence,
        )
        errors.extend(evidence_errors)
        has_runtime_evidence = bool(record.runtime_evidence) and not evidence_errors
        if not observed_core and not has_runtime_evidence:
            if observed_collection or observed_requirements:
                errors.append(
                    f"{dependency}: collection-only dependency must leave core metadata"
                )
            else:
                errors.append(f"{dependency}: no verified core runtime consumer")
    return sorted(errors)


def _deptry_import_roots(metadata: dict[str, object]) -> dict[str, tuple[str, ...]]:
    tool = _table(metadata.get("tool", {}), "tool")
    deptry = _table(tool.get("deptry", {}), "tool.deptry")
    raw_mapping = _table(
        deptry.get("package_module_name_map", {}),
        "tool.deptry.package_module_name_map",
    )
    mappings: dict[str, tuple[str, ...]] = {}
    for dependency, value in raw_mapping.items():
        roots = (
            (value,)
            if isinstance(value, str)
            else _strings(value, f"package_module_name_map.{dependency}")
        )
        mappings[canonicalize_name(dependency)] = roots
    return mappings


def _metadata_import_roots() -> dict[str, set[str]]:
    roots: dict[str, set[str]] = {}
    for import_root, distributions in packages_distributions().items():
        for distribution in distributions:
            roots.setdefault(canonicalize_name(distribution), set()).add(import_root)
    return roots


def observed_inventory(root: Path) -> dict[str, object]:
    """Build a deterministic inventory skeleton from current repository evidence.

    Args:
        root: Repository root to scan mechanically.

    Returns:
        JSON-serializable inventory with exact static consumers and empty indirect
        evidence for human adjudication.
    """
    metadata, dependencies = _direct_dependencies(root)
    configured_roots = _deptry_import_roots(metadata)
    metadata_roots = _metadata_import_roots()
    core_imports = _static_imports(root, _python_files(root / "src/general_ludd"))
    collection_imports = _static_imports(
        root,
        _collection_python_files(root / "collections/ansible_collections"),
    )
    collection_requirements = _collection_requirements(root)
    observed_import_roots = core_imports.keys() | collection_imports.keys()
    records: dict[str, object] = {}
    for dependency in sorted(dependencies):
        roots = configured_roots.get(dependency)
        if roots is None:
            metadata_candidates = metadata_roots.get(dependency, set())
            imported_candidates = metadata_candidates & observed_import_roots
            roots = tuple(sorted(imported_candidates or metadata_candidates))
        if not roots:
            roots = (dependency.replace("-", "_"),)
        records[dependency] = {
            "disposition": "retain-core",
            "import_roots": list(roots),
            "core_import_paths": list(_observed_paths(core_imports, roots)),
            "collection_import_paths": list(
                _observed_paths(collection_imports, roots)
            ),
            "collection_requirement_paths": sorted(
                collection_requirements.get(dependency, set())
            ),
            "runtime_evidence": [],
        }
    return {"schema_version": 1, "dependencies": records}


def main(argv: list[str] | None = None) -> int:
    """Run the ownership audit or print a mechanically observed inventory.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero when the requested operation succeeds, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--print-observed", action="store_true")
    args = parser.parse_args(argv)
    if args.print_observed:
        print(json.dumps(observed_inventory(args.root), indent=2, sort_keys=True))
        return 0
    errors = audit_repository(args.root)
    if errors:
        for error in errors:
            print(f"CORE_DEPENDENCY_OWNERSHIP_ERROR {error}", file=sys.stderr)
        return 1
    print("CORE_DEPENDENCY_OWNERSHIP_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
