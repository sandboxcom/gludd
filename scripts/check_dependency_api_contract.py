#!/usr/bin/env python3
"""Fail closed when dependency metadata cannot provide imported public APIs.

The bounded JSON inventory ties each application import to the dependency
version that introduced it.  The checker independently verifies the direct
declaration, every exact version in ``uv.lock``, the configured consumer import,
and the installed runtime surface.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

CONFIG_PATH = Path("config/dependency_api_contracts.json")
MAX_CONFIG_BYTES = 65_536
MAX_PROJECT_BYTES = 1_048_576
MAX_LOCK_BYTES = 16_777_216
MAX_CONSUMER_BYTES = 2_097_152
MAX_CONTRACTS = 64
MAX_PUBLIC_APIS = 64
MAX_CONSUMERS = 64
MAX_NAME_LENGTH = 256

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_DOTTED_IDENTIFIER_RE = re.compile(
    r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z",
    flags=re.ASCII,
)
_ROOT_KEYS = frozenset({"schema_version", "contracts"})
_CONTRACT_KEYS = frozenset(
    {
        "distribution",
        "module",
        "introduced_version",
        "public_apis",
        "consumers",
    }
)


@dataclass(frozen=True)
class DependencyApiContract:
    """One versioned public-API cohort and its exact source consumers."""

    distribution: str
    module: str
    introduced_version: Version
    public_apis: tuple[str, ...]
    consumers: tuple[str, ...]


def _read_bounded(path: Path, limit: int, label: str) -> str:
    """Read UTF-8 text without allowing an input to exceed ``limit`` bytes."""
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    try:
        with path.open("rb") as stream:
            payload = stream.read(limit + 1)
    except OSError as exc:
        raise ValueError(f"could not read {label}: {exc}") from exc
    if len(payload) > limit:
        raise ValueError(f"{label} exceeds {limit}-byte limit")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not UTF-8") from exc


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be an object with string keys")
    return cast(dict[str, object], value)


def _exact_keys(value: dict[str, object], expected: frozenset[str], context: str) -> None:
    unknown = sorted(value.keys() - expected)
    missing = sorted(expected - value.keys())
    if unknown:
        raise ValueError(f"{context} has unknown keys: {unknown}")
    if missing:
        raise ValueError(f"{context} is missing keys: {missing}")


def _bounded_strings(
    value: object,
    *,
    context: str,
    limit: int,
    validator: re.Pattern[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{context} must be a non-empty list of strings")
    strings = tuple(cast(list[str], value))
    if len(strings) > limit:
        raise ValueError(f"{context} permits at most {limit} entries")
    if len(set(strings)) != len(strings):
        raise ValueError(f"{context} must not contain duplicates")
    for item in strings:
        if not item or len(item) > MAX_NAME_LENGTH:
            raise ValueError(
                f"{context} entries must be between 1 and {MAX_NAME_LENGTH} characters"
            )
        if validator is not None and validator.fullmatch(item) is None:
            raise ValueError(f"{context} contains invalid name {item!r}")
    return strings


def _consumer_paths(value: object, context: str) -> tuple[str, ...]:
    paths = _bounded_strings(value, context=context, limit=MAX_CONSUMERS)
    for raw_path in paths:
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
            raise ValueError(f"{context} contains unsafe Python path {raw_path!r}")
    return paths


def load_contracts(root: Path) -> tuple[DependencyApiContract, ...]:
    """Load and strictly validate the bounded dependency/API inventory."""
    raw_text = _read_bounded(root / CONFIG_PATH, MAX_CONFIG_BYTES, "dependency API contract metadata")
    try:
        parsed = json.loads(raw_text, object_pairs_hook=_object_without_duplicates)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    metadata = _object(parsed, "metadata")
    _exact_keys(metadata, _ROOT_KEYS, "metadata")
    if metadata["schema_version"] != 1:
        raise ValueError("metadata.schema_version must be 1")
    entries = metadata["contracts"]
    if not isinstance(entries, list):
        raise ValueError("metadata.contracts must be a list")
    if not entries:
        raise ValueError("metadata.contracts must not be empty")
    if len(entries) > MAX_CONTRACTS:
        raise ValueError(f"metadata.contracts permits at most {MAX_CONTRACTS} entries")

    contracts: list[DependencyApiContract] = []
    identities: set[tuple[str, str, Version]] = set()
    for index, raw_entry in enumerate(entries):
        context = f"metadata.contracts[{index}]"
        entry = _object(raw_entry, context)
        _exact_keys(entry, _CONTRACT_KEYS, context)

        distribution = entry["distribution"]
        module = entry["module"]
        introduced = entry["introduced_version"]
        if (
            not isinstance(distribution, str)
            or len(distribution) > MAX_NAME_LENGTH
            or _NAME_RE.fullmatch(distribution) is None
        ):
            raise ValueError(f"{context}.distribution is invalid")
        if (
            not isinstance(module, str)
            or len(module) > MAX_NAME_LENGTH
            or _DOTTED_IDENTIFIER_RE.fullmatch(module) is None
        ):
            raise ValueError(f"{context}.module is invalid")
        try:
            introduced_version = Version(introduced) if isinstance(introduced, str) else None
        except InvalidVersion as exc:
            raise ValueError(f"{context}.introduced_version is invalid") from exc
        if introduced_version is None or introduced_version.is_prerelease:
            raise ValueError(f"{context}.introduced_version is invalid")

        public_apis = _bounded_strings(
            entry["public_apis"],
            context=f"{context}.public_apis",
            limit=MAX_PUBLIC_APIS,
            validator=_DOTTED_IDENTIFIER_RE,
        )
        consumers = _consumer_paths(entry["consumers"], f"{context}.consumers")
        canonical_distribution = canonicalize_name(distribution)
        identity = (canonical_distribution, module, introduced_version)
        if identity in identities:
            raise ValueError(f"{context} duplicates an existing contract")
        identities.add(identity)
        contracts.append(
            DependencyApiContract(
                distribution=canonical_distribution,
                module=module,
                introduced_version=introduced_version,
                public_apis=public_apis,
                consumers=consumers,
            )
        )
    return tuple(contracts)


def _project_requirements(root: Path) -> dict[str, tuple[Requirement, ...]]:
    content = _read_bounded(root / "pyproject.toml", MAX_PROJECT_BYTES, "pyproject.toml")
    try:
        metadata = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid pyproject.toml: {exc}") from exc
    project = _object(metadata.get("project"), "project")
    raw_dependencies = project.get("dependencies")
    if not isinstance(raw_dependencies, list) or not all(
        isinstance(item, str) for item in raw_dependencies
    ):
        raise ValueError("project.dependencies must be a list of strings")
    grouped: dict[str, list[Requirement]] = {}
    for raw_requirement in cast(list[str], raw_dependencies):
        try:
            requirement = Requirement(raw_requirement)
        except InvalidRequirement as exc:
            raise ValueError(f"invalid dependency requirement {raw_requirement!r}") from exc
        grouped.setdefault(canonicalize_name(requirement.name), []).append(requirement)
    return {name: tuple(requirements) for name, requirements in grouped.items()}


def _locked_versions(root: Path) -> dict[str, tuple[Version, ...]]:
    content = _read_bounded(root / "uv.lock", MAX_LOCK_BYTES, "uv.lock")
    try:
        metadata = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid uv.lock: {exc}") from exc
    packages = metadata.get("package")
    if not isinstance(packages, list):
        raise ValueError("uv.lock package must be a list")
    grouped: dict[str, list[Version]] = {}
    for index, raw_package in enumerate(packages):
        package = _object(raw_package, f"uv.lock package[{index}]")
        name = package.get("name")
        raw_version = package.get("version")
        if not isinstance(name, str) or not isinstance(raw_version, str):
            raise ValueError(f"uv.lock package[{index}] requires string name and version")
        try:
            version = Version(raw_version)
        except InvalidVersion as exc:
            raise ValueError(f"invalid locked version {raw_version!r} for {name}") from exc
        versions = grouped.setdefault(canonicalize_name(name), [])
        if version not in versions:
            versions.append(version)
    return {name: tuple(versions) for name, versions in grouped.items()}


def _guaranteed_floor(requirement: Requirement) -> Version | None:
    floors: list[Version] = []
    for specifier in requirement.specifier:
        if specifier.operator not in {">=", ">", "~=", "==", "==="}:
            continue
        raw_version = specifier.version.removesuffix(".*")
        try:
            floors.append(Version(raw_version))
        except InvalidVersion:
            return None
    return max(floors) if floors else None


def _consumer_imports(root: Path, contract: DependencyApiContract) -> list[str]:
    errors: list[str] = []
    for relative in contract.consumers:
        path = root / relative
        try:
            source = _read_bounded(path, MAX_CONSUMER_BYTES, f"consumer {relative}")
            tree = ast.parse(source, filename=relative)
        except (SyntaxError, ValueError) as exc:
            errors.append(f"{contract.distribution}: could not parse consumer {relative}: {exc}")
            continue
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == contract.module
            for alias in node.names
        }
        for public_api in contract.public_apis:
            top_level = public_api.split(".", 1)[0]
            if top_level not in imported:
                errors.append(
                    f"{contract.distribution}: {relative} does not import public API "
                    f"{public_api} from {contract.module}"
                )
    return errors


def _runtime_api_errors(
    contract: DependencyApiContract,
    importer: Callable[[str], object],
) -> list[str]:
    try:
        module = importer(contract.module)
    except Exception as exc:
        return [
            f"{contract.distribution}: could not import runtime module "
            f"{contract.module}: {exc}"
        ]
    errors: list[str] = []
    for public_api in contract.public_apis:
        current = module
        try:
            for component in public_api.split("."):
                current = getattr(current, component)
        except AttributeError:
            errors.append(
                f"{contract.distribution}: runtime module {contract.module} "
                f"lacks public API {public_api}"
            )
    return errors


def _metadata_error(prefix: str, operation: Callable[[], object]) -> tuple[object | None, list[str]]:
    try:
        return operation(), []
    except (OSError, ValueError) as exc:
        return None, [f"{prefix}: {exc}"]


def audit_repository(
    root: Path,
    *,
    importer: Callable[[str], object] = importlib.import_module,
) -> list[str]:
    """Return violations linking imports to declarations, locks, and runtime APIs."""
    contracts_raw, errors = _metadata_error(
        "could not load dependency API contract metadata",
        lambda: load_contracts(root),
    )
    if errors:
        return errors
    requirements_raw, errors = _metadata_error(
        "could not load project dependency metadata",
        lambda: _project_requirements(root),
    )
    if errors:
        return errors
    versions_raw, errors = _metadata_error(
        "could not load locked dependency metadata",
        lambda: _locked_versions(root),
    )
    if errors:
        return errors

    contracts = cast(tuple[DependencyApiContract, ...], contracts_raw)
    requirements = cast(dict[str, tuple[Requirement, ...]], requirements_raw)
    locked_versions = cast(dict[str, tuple[Version, ...]], versions_raw)
    violations: list[str] = []
    for contract in contracts:
        declarations = requirements.get(contract.distribution, ())
        versions = locked_versions.get(contract.distribution, ())
        if not declarations:
            violations.append(f"{contract.distribution}: direct dependency declaration is missing")
        else:
            for declaration in declarations:
                floor = _guaranteed_floor(declaration)
                if floor is None or floor < contract.introduced_version:
                    violations.append(
                        f"{contract.distribution}: declaration {declaration} does not guarantee "
                        f"public APIs introduced in {contract.introduced_version.public}"
                    )
        if not versions:
            violations.append(f"{contract.distribution}: locked package is missing")
        else:
            for version in versions:
                if version < contract.introduced_version:
                    violations.append(
                        f"{contract.distribution}: locked version {version.public} predates "
                        f"public APIs introduced in {contract.introduced_version.public}"
                    )
                elif declarations and not any(
                    declaration.specifier.contains(version, prereleases=True)
                    for declaration in declarations
                ):
                    violations.append(
                        f"{contract.distribution}: locked version {version.public} does not "
                        "satisfy any direct declaration"
                    )
        violations.extend(_consumer_imports(root, contract))
        violations.extend(_runtime_api_errors(contract, importer))
    return sorted(violations)


def main(argv: Iterable[str] | None = None) -> int:
    """Audit a repository root and emit deterministic gate markers."""
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if len(arguments) > 1:
        print("DEPENDENCY-API-CONTRACT FAIL usage: [repository-root]")
        return 2
    root = Path(arguments[0]).resolve() if arguments else Path(__file__).resolve().parent.parent
    violations = audit_repository(root)
    if violations:
        for violation in violations:
            print(f"DEPENDENCY-API-CONTRACT VIOLATION {violation}")
        print(f"DEPENDENCY-API-CONTRACT FAIL violations={len(violations)}")
        return 1
    print("DEPENDENCY-API-CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
