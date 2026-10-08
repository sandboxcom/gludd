"""Tests for the collection/role interoperability release gate."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from scripts.check_collection_interop import (
    audit_collection_interop,
    main,
    resolve_ansible_collections_root,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _collection(
    root: Path,
    name: str,
    *,
    dependencies: dict[str, str] | None = None,
) -> Path:
    collection = root / "ansible_collections" / "general_ludd" / name
    _write_yaml(
        collection / "galaxy.yml",
        {
            "namespace": "general_ludd",
            "name": name,
            "version": "0.1.0",
            "readme": "README.md",
            "authors": ["Gludd"],
            "dependencies": dependencies or {},
        },
    )
    (collection / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    return collection


def _role(collection: Path, name: str) -> None:
    _write_yaml(
        collection / "roles" / name / "tasks" / "main.yml",
        [{"name": "No-op", "ansible.builtin.debug": {"msg": name}}],
    )


def _module(collection: Path, name: str) -> None:
    path = collection / "plugins" / "modules" / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('"""Fixture module."""\n', encoding="utf-8")


def _codes(root: Path) -> set[str]:
    return {issue.code for issue in audit_collection_interop(root).issues}


def test_resolves_repository_collections_and_packaged_roots(tmp_path: Path) -> None:
    collection = _collection(tmp_path, "alpha")

    assert resolve_ansible_collections_root(tmp_path) == (
        tmp_path / "ansible_collections"
    )
    assert resolve_ansible_collections_root(tmp_path / "ansible_collections") == (
        tmp_path / "ansible_collections"
    )
    assert resolve_ansible_collections_root(collection) == collection


def test_accepts_declared_existing_role_and_module_edges(tmp_path: Path) -> None:
    alpha = _collection(
        tmp_path,
        "alpha",
        dependencies={"general_ludd.beta": ">=0.1.0"},
    )
    beta = _collection(tmp_path, "beta")
    _role(beta, "worker")
    _module(beta, "calculate")
    _write_yaml(
        alpha / "roles" / "orchestrator" / "tasks" / "main.yml",
        [
            {
                "name": "Call role",
                "ansible.builtin.include_role": {
                    "name": "general_ludd.beta.worker",
                },
            },
            {
                "name": "Call module",
                "general_ludd.beta.calculate": {"value": 1},
            },
        ],
    )

    report = audit_collection_interop(tmp_path)

    assert report.issues == ()
    assert report.edges == (("general_ludd.alpha", "general_ludd.beta"),)


def test_reports_missing_role_and_module_targets(tmp_path: Path) -> None:
    alpha = _collection(
        tmp_path,
        "alpha",
        dependencies={"general_ludd.beta": ">=0.1.0"},
    )
    _collection(tmp_path, "beta")
    _write_yaml(
        alpha / "roles" / "orchestrator" / "tasks" / "main.yml",
        [
            {
                "ansible.builtin.import_role": {
                    "name": "general_ludd.beta.absent_role",
                }
            },
            {"general_ludd.beta.absent_module": {}},
        ],
    )

    assert _codes(tmp_path) == {"missing-module", "missing-role"}


def test_reports_undeclared_cross_collection_edge(tmp_path: Path) -> None:
    alpha = _collection(tmp_path, "alpha")
    beta = _collection(tmp_path, "beta")
    _role(beta, "worker")
    _write_yaml(
        alpha / "roles" / "orchestrator" / "tasks" / "main.yml",
        [
            {
                "ansible.builtin.include_role": {
                    "name": "general_ludd.beta.worker",
                }
            }
        ],
    )

    assert _codes(tmp_path) == {"undeclared-dependency"}


def test_reports_dependency_cycles(tmp_path: Path) -> None:
    _collection(
        tmp_path,
        "alpha",
        dependencies={"general_ludd.beta": ">=0.1.0"},
    )
    _collection(
        tmp_path,
        "beta",
        dependencies={"general_ludd.alpha": ">=0.1.0"},
    )

    assert _codes(tmp_path) == {"dependency-cycle"}


def test_validates_module_redirect_target_and_declared_edge(tmp_path: Path) -> None:
    alpha = _collection(
        tmp_path,
        "alpha",
        dependencies={"general_ludd.beta": ">=0.1.0"},
    )
    beta = _collection(tmp_path, "beta")
    _module(beta, "calculate")
    _write_yaml(
        alpha / "meta" / "runtime.yml",
        {
            "requires_ansible": ">=2.15.0",
            "plugin_routing": {
                "modules": {
                    "legacy_calculate": {
                        "redirect": "general_ludd.beta.calculate",
                    }
                }
            },
        },
    )

    assert audit_collection_interop(tmp_path).issues == ()
    (beta / "plugins" / "modules" / "calculate.py").unlink()
    assert _codes(tmp_path) == {"missing-module"}


def test_reports_missing_packaged_general_ludd_dependency(tmp_path: Path) -> None:
    _collection(
        tmp_path,
        "alpha",
        dependencies={"general_ludd.not_packaged": ">=0.1.0"},
    )

    assert _codes(tmp_path) == {"missing-dependency-collection"}


def test_rejects_short_and_dynamic_role_names(tmp_path: Path) -> None:
    alpha = _collection(tmp_path, "alpha")
    _write_yaml(
        alpha / "roles" / "orchestrator" / "tasks" / "main.yml",
        [
            {"ansible.builtin.include_role": {"name": "worker"}},
            {
                "ansible.builtin.include_role": {
                    "name": "{{ selected_collection }}.worker",
                }
            },
        ],
    )

    assert _codes(tmp_path) == {"dynamic-role-name", "short-role-name"}


def test_failed_candidate_audit_preserves_active_tree_for_zdd_rollback(
    tmp_path: Path,
) -> None:
    active = tmp_path / "active"
    candidate = tmp_path / "candidate"
    active_collection = _collection(active, "alpha")
    _role(active_collection, "stable")
    candidate_collection = _collection(candidate, "alpha")
    _write_yaml(
        candidate_collection / "roles" / "orchestrator" / "tasks" / "main.yml",
        [{"ansible.builtin.include_role": {"name": "missing"}}],
    )
    active_before = {
        path.relative_to(active): path.read_bytes()
        for path in active.rglob("*")
        if path.is_file()
    }

    report = audit_collection_interop(candidate)

    assert report.issues
    assert active_before == {
        path.relative_to(active): path.read_bytes()
        for path in active.rglob("*")
        if path.is_file()
    }


def test_repository_collection_graph_is_valid() -> None:
    report = audit_collection_interop(REPO_ROOT)

    assert report.issues == (), report.format_issues()


def test_cli_reports_success_and_candidate_issues(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    alpha = _collection(tmp_path, "alpha")

    assert main([str(tmp_path)]) == 0
    assert "collection-interop: PASS" in capsys.readouterr().out

    _write_yaml(
        alpha / "roles" / "orchestrator" / "tasks" / "main.yml",
        [{"ansible.builtin.include_role": {"name": "worker"}}],
    )
    assert main([str(tmp_path)]) == 1
    assert "short-role-name" in capsys.readouterr().out


def test_cli_converts_resolution_errors_to_usage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main([str(tmp_path / "not-a-package")])

    assert raised.value.code == 2
    assert "no Ansible collection root found" in capsys.readouterr().err
