"""Pin domain Python code and dependencies outside the Gludd core wheel."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import cast

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]

PHYSICS_MODULES = (
    "convex_hull",
    "entropy",
    "fft",
    "interpolation",
    "kmeans",
    "simplex",
    "wavelet",
)
SECURITY_MODULES = ("asn1", "salsa20", "shamir", "srp")
CORE_ASN1_OID_SYMBOLS = {
    "CertManager",
    "KNOWN_OIDS",
    "OIDInfo",
    "asn1_roundtrip_verify",
    "oid_generate",
    "oid_lookup",
}


def _pyproject() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _table(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return cast(dict[str, object], value)


def _items(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _requirement_names(path: Path) -> set[str]:
    return {
        Requirement(line).name.lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _defined_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _imported_names(tree: ast.Module) -> set[str]:
    return {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }


@pytest.mark.parametrize("module_name", PHYSICS_MODULES)
def test_numerical_adapters_are_owned_by_physics_collection(module_name: str) -> None:
    collection_path = (
        ROOT
        / "collections/ansible_collections/general_ludd/physics/plugins/module_utils"
        / f"{module_name}.py"
    )

    assert collection_path.is_file()
    assert not (ROOT / "src/general_ludd/algorithms" / f"{module_name}.py").exists()


@pytest.mark.parametrize("module_name", SECURITY_MODULES)
def test_protocol_adapters_are_owned_by_security_collection(module_name: str) -> None:
    collection_path = (
        ROOT
        / "collections/ansible_collections/general_ludd/security/plugins/module_utils"
        / f"{module_name}.py"
    )

    assert collection_path.is_file()
    assert not (ROOT / "src/general_ludd/algorithms" / f"{module_name}.py").exists()


def test_base_core_excludes_collection_domain_dependencies() -> None:
    project = _table(_pyproject()["project"])
    dependencies = {
        str(item).split("[", 1)[0].split(">", 1)[0].lower()
        for item in _items(project["dependencies"])
    }

    assert dependencies.isdisjoint(
        {"numpy", "scipy", "pywavelets", "pycryptodome", "shamir", "srptools"}
    )


def test_asn1_oid_implementation_has_one_security_collection_owner() -> None:
    collection_module = (
        ROOT
        / "collections/ansible_collections/general_ludd/security/plugins/module_utils/asn1.py"
    )
    core_module = ROOT / "src/general_ludd/ssl_agent/cert_manager.py"
    core_package = ROOT / "src/general_ludd/ssl_agent/__init__.py"

    assert {"encode_der", "generate_oid", "lookup_oid", "parse_der"} <= _defined_names(
        _tree(collection_module)
    )
    assert CORE_ASN1_OID_SYMBOLS.isdisjoint(_defined_names(_tree(core_module)))
    assert CORE_ASN1_OID_SYMBOLS.isdisjoint(_imported_names(_tree(core_package)))


def test_asn1_oid_collection_uses_only_stdlib_python() -> None:
    collection_module = (
        ROOT
        / "collections/ansible_collections/general_ludd/security/plugins/module_utils/asn1.py"
    )
    import_roots = {
        (node.module or "").split(".", 1)[0]
        if isinstance(node, ast.ImportFrom)
        else alias.name.split(".", 1)[0]
        for node in _tree(collection_module).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert import_roots <= {"__future__", "hashlib", "time", "typing", "uuid"}


def test_development_and_game_extras_retain_declared_test_runtimes() -> None:
    metadata = _pyproject()
    project = _table(metadata["project"])
    optional = _table(project["optional-dependencies"])
    dependency_groups = _table(metadata["dependency-groups"])
    dev = "\n".join(str(item) for item in _items(dependency_groups["dev"])).lower()
    game = "\n".join(str(item) for item in _items(optional["game-e2e"])).lower()

    for requirement in ("numpy", "scipy", "pywavelets", "pycryptodome", "shamir", "srptools"):
        assert requirement in dev
    assert "numpy" in game


@pytest.mark.parametrize(
    ("collection", "requirements"),
    [
        ("physics", {"numpy", "scipy", "pywavelets"}),
        ("radio", {"numpy", "scipy"}),
        ("forensics", {"numpy", "scipy", "pillow"}),
        ("security", {"pycryptodome", "shamir", "srptools"}),
    ],
)
def test_collection_execution_environment_owns_python_dependencies(
    collection: str,
    requirements: set[str],
) -> None:
    collection_root = ROOT / "collections/ansible_collections/general_ludd" / collection
    metadata = (collection_root / "meta/execution-environment.yml").read_text(encoding="utf-8")
    requirement_path = collection_root / "meta/ee-requirements.txt"

    assert "python: meta/ee-requirements.txt" in metadata
    assert requirements <= _requirement_names(requirement_path)
