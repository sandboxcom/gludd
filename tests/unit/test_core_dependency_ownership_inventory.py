"""Exact ownership contracts for every direct Gludd core dependency."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pytest import CaptureFixture, MonkeyPatch
from scripts import check_core_dependency_ownership
from scripts.check_core_dependency_ownership import (
    audit_repository,
    observed_inventory,
)

ROOT = Path(__file__).resolve().parents[2]


def _write_fixture(
    root: Path,
    *,
    dependency: str = "requests>=2",
    dependency_name: str = "requests",
    core_source: str = "",
    collection_source: str = "",
    record: dict[str, object],
) -> None:
    (root / "src/general_ludd").mkdir(parents=True)
    (root / "src/general_ludd/core.py").write_text(core_source, encoding="utf-8")
    collection = (
        root
        / "collections/ansible_collections/general_ludd/example/plugins/module_utils"
    )
    collection.mkdir(parents=True)
    (collection / "adapter.py").write_text(collection_source, encoding="utf-8")
    (root / "config").mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "fixture"\n'
        f'dependencies = ["{dependency}"]\n',
        encoding="utf-8",
    )
    inventory = {
        "schema_version": 1,
        "dependencies": {dependency_name: record},
    }
    (root / "config/core-python-dependency-ownership.json").write_text(
        json.dumps(inventory),
        encoding="utf-8",
    )


def test_inventory_exactly_proves_every_direct_core_dependency() -> None:
    assert audit_repository(ROOT) == []


def test_checker_rejects_collection_only_base_dependency(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        collection_source="import requests\n",
        record={
            "disposition": "retain-core",
            "import_roots": ["requests"],
            "core_import_paths": [],
            "collection_import_paths": [
                "collections/ansible_collections/general_ludd/example/plugins/module_utils/adapter.py"
            ],
            "collection_requirement_paths": [],
            "runtime_evidence": [],
        },
    )

    errors = audit_repository(tmp_path)

    assert any("collection-only" in error for error in errors)


def test_checker_rejects_uninventoried_and_stale_consumers(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        core_source="import requests\n",
        record={
            "disposition": "retain-core",
            "import_roots": ["requests"],
            "core_import_paths": ["src/general_ludd/stale.py"],
            "collection_import_paths": [],
            "collection_requirement_paths": [],
            "runtime_evidence": [],
        },
    )

    errors = audit_repository(tmp_path)

    assert any("core imports differ" in error for error in errors)


def test_checker_accepts_verified_indirect_runtime_evidence(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        core_source='DATABASE_URL = "sqlite+aiosqlite:///state.db"\n',
        dependency="aiosqlite>=0.20",
        dependency_name="aiosqlite",
        record={
            "disposition": "retain-core",
            "import_roots": ["aiosqlite"],
            "core_import_paths": [],
            "collection_import_paths": [],
            "collection_requirement_paths": [],
            "runtime_evidence": [
                {
                    "path": "src/general_ludd/core.py",
                    "token": "sqlite+aiosqlite",
                }
            ],
        },
    )

    assert audit_repository(tmp_path) == []


def test_checker_ignores_collection_test_only_imports(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        core_source="import requests\n",
        record={
            "disposition": "retain-core",
            "import_roots": ["requests"],
            "core_import_paths": ["src/general_ludd/core.py"],
            "collection_import_paths": [],
            "collection_requirement_paths": [],
            "runtime_evidence": [],
        },
    )
    collection_test = (
        tmp_path
        / "collections/ansible_collections/general_ludd/example/tests/unit/test_adapter.py"
    )
    collection_test.parent.mkdir(parents=True)
    collection_test.write_text("import requests\n", encoding="utf-8")

    assert audit_repository(tmp_path) == []


def test_observed_inventory_excludes_unimported_metadata_roots(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _write_fixture(
        tmp_path,
        core_source="import requests\n",
        record={
            "disposition": "retain-core",
            "import_roots": ["requests"],
            "core_import_paths": ["src/general_ludd/core.py"],
            "collection_import_paths": [],
            "collection_requirement_paths": [],
            "runtime_evidence": [],
        },
    )
    monkeypatch.setattr(
        check_core_dependency_ownership,
        "_metadata_import_roots",
        lambda: {"requests": {"_requests_native", "requests"}},
    )

    inventory = observed_inventory(tmp_path)
    dependencies = cast(dict[str, dict[str, object]], inventory["dependencies"])

    assert dependencies["requests"]["import_roots"] == ["requests"]


def test_checker_fails_closed_on_invalid_project_requirement(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        dependency="not a requirement ???",
        record={
            "disposition": "retain-core",
            "import_roots": ["requests"],
            "core_import_paths": [],
            "collection_import_paths": [],
            "collection_requirement_paths": [],
            "runtime_evidence": [],
        },
    )

    errors = audit_repository(tmp_path)

    assert len(errors) == 1
    assert errors[0].startswith(
        "dependency ownership audit could not load repository:"
    )
    assert "not a requirement ???" in errors[0]


def test_checker_reports_inventory_key_and_disposition_drift(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        core_source="import requests\n",
        record={
            "disposition": "move-to-collection",
            "import_roots": ["requests"],
            "core_import_paths": ["src/general_ludd/core.py"],
            "collection_import_paths": [],
            "collection_requirement_paths": [],
            "runtime_evidence": [],
        },
    )
    inventory_path = tmp_path / "config/core-python-dependency-ownership.json"
    inventory = cast(
        dict[str, object], json.loads(inventory_path.read_text(encoding="utf-8"))
    )
    dependencies = cast(dict[str, object], inventory["dependencies"])
    dependencies["stale-package"] = dependencies.pop("requests")
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    errors = audit_repository(tmp_path)

    assert "direct dependencies missing from inventory: ['requests']" in errors
    assert "inventory entries are not direct dependencies: ['stale-package']" in errors


def test_checker_rejects_empty_roots_and_unverified_runtime_evidence(
    tmp_path: Path,
) -> None:
    missing_evidence = tmp_path / "missing-evidence"
    _write_fixture(
        missing_evidence,
        record={
            "disposition": "retain-core",
            "import_roots": [],
            "core_import_paths": [],
            "collection_import_paths": [],
            "collection_requirement_paths": [],
            "runtime_evidence": [],
        },
    )
    wrong_token = tmp_path / "wrong-token"
    _write_fixture(
        wrong_token,
        core_source='PROVIDER = "requests"\n',
        record={
            "disposition": "retain-core",
            "import_roots": ["requests"],
            "core_import_paths": [],
            "collection_import_paths": [],
            "collection_requirement_paths": [],
            "runtime_evidence": [
                {"path": "src/general_ludd/core.py", "token": "missing-token"}
            ],
        },
    )

    assert audit_repository(missing_evidence) == [
        "requests: import_roots must not be empty"
    ]
    wrong_token_errors = audit_repository(wrong_token)
    assert any("runtime evidence token" in error for error in wrong_token_errors)
    assert any("no verified core runtime consumer" in error for error in wrong_token_errors)


def test_checker_tracks_collection_runtime_requirements(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        core_source="import requests\n",
        record={
            "disposition": "retain-core",
            "import_roots": ["requests"],
            "core_import_paths": ["src/general_ludd/core.py"],
            "collection_import_paths": [],
            "collection_requirement_paths": [
                "collections/ansible_collections/general_ludd/example/meta/ee-requirements.txt"
            ],
            "runtime_evidence": [],
        },
    )
    requirements = (
        tmp_path
        / "collections/ansible_collections/general_ludd/example/meta/ee-requirements.txt"
    )
    requirements.parent.mkdir(parents=True)
    requirements.write_text("# controller dependency\n\nrequests>=2\n", encoding="utf-8")

    assert audit_repository(tmp_path) == []

    requirements.write_text("not a requirement ???\n", encoding="utf-8")
    errors = audit_repository(tmp_path)
    assert len(errors) == 1
    assert "invalid requirement" in errors[0]


def test_checker_fails_closed_on_malformed_inventory(tmp_path: Path) -> None:
    record: dict[str, object] = {
        "disposition": "retain-core",
        "import_roots": ["requests"],
        "core_import_paths": [],
        "collection_import_paths": [],
        "collection_requirement_paths": [],
        "runtime_evidence": [],
    }
    malformed_payloads: tuple[object, ...] = (
        [],
        {"schema_version": 2, "dependencies": {"requests": record}},
        {
            "schema_version": 1,
            "dependencies": {"requests": {**record, "runtime_evidence": "bad"}},
        },
        {
            "schema_version": 1,
            "dependencies": {
                "requests": {
                    **record,
                    "runtime_evidence": [{"path": 1, "token": "bad"}],
                }
            },
        },
        {
            "schema_version": 1,
            "dependencies": {"requests": {**record, "disposition": None}},
        },
        {
            "schema_version": 1,
            "dependencies": {"requests": {**record, "import_roots": "requests"}},
        },
    )
    for index, payload in enumerate(malformed_payloads):
        case_root = tmp_path / str(index)
        _write_fixture(case_root, record=record)
        inventory_path = case_root / "config/core-python-dependency-ownership.json"
        inventory_path.write_text(json.dumps(payload), encoding="utf-8")

        errors = audit_repository(case_root)

        assert len(errors) == 1
        assert errors[0].startswith(
            "dependency ownership audit could not load repository:"
        )


def test_observed_inventory_uses_deptry_mapping_and_normalized_fallback(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _write_fixture(
        tmp_path,
        dependency="fixture-pkg>=1",
        dependency_name="fixture-pkg",
        record={
            "disposition": "retain-core",
            "import_roots": ["fixture_pkg"],
            "core_import_paths": [],
            "collection_import_paths": [],
            "collection_requirement_paths": [],
            "runtime_evidence": [],
        },
    )
    monkeypatch.setattr(
        check_core_dependency_ownership,
        "_metadata_import_roots",
        lambda: {},
    )

    inventory = observed_inventory(tmp_path)
    dependencies = cast(dict[str, dict[str, object]], inventory["dependencies"])
    assert dependencies["fixture-pkg"]["import_roots"] == ["fixture_pkg"]

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + "\n[tool.deptry]\n"
        + 'package_module_name_map = { fixture-pkg = "custom_root" }\n',
        encoding="utf-8",
    )
    inventory = observed_inventory(tmp_path)
    dependencies = cast(dict[str, dict[str, object]], inventory["dependencies"])
    assert dependencies["fixture-pkg"]["import_roots"] == ["custom_root"]


def test_command_reports_pass_errors_and_observed_inventory(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    _write_fixture(
        tmp_path,
        core_source="import requests\n",
        record={
            "disposition": "retain-core",
            "import_roots": ["requests"],
            "core_import_paths": ["src/general_ludd/core.py"],
            "collection_import_paths": [],
            "collection_requirement_paths": [],
            "runtime_evidence": [],
        },
    )

    assert check_core_dependency_ownership.main(["--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "CORE_DEPENDENCY_OWNERSHIP_PASS\n"
    assert (
        check_core_dependency_ownership.main(
            ["--root", str(tmp_path), "--print-observed"]
        )
        == 0
    )
    observed_output = capsys.readouterr().out
    assert '"schema_version": 1' in observed_output

    missing_root = tmp_path / "missing"
    assert check_core_dependency_ownership.main(["--root", str(missing_root)]) == 1
    assert "CORE_DEPENDENCY_OWNERSHIP_ERROR" in capsys.readouterr().err
