"""Dependency-version/public-API contract guard tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.check_dependency_api_contract import (
    CONFIG_PATH,
    MAX_CONTRACTS,
    audit_repository,
    load_contracts,
    main,
)

ROOT = Path(__file__).resolve().parents[2]


def _write_repository(
    root: Path,
    *,
    requirement: str = "filelock>=3.30.0",
    locked_versions: tuple[str, ...] = ("3.30.0",),
    symbols: tuple[str, ...] = ("lock_descriptor", "unlock_descriptor"),
    consumer_source: str = (
        "from filelock import lock_descriptor, unlock_descriptor\n"
    ),
) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "fixture"\nversion = "0"\n'
        f'dependencies = ["{requirement}"]\n',
        encoding="utf-8",
    )
    locked_content = "".join(
            f'[[package]]\nname = "filelock"\nversion = "{version}"\n'
            for version in locked_versions
        )
    if not locked_content:
        locked_content = '[[package]]\nname = "other"\nversion = "1.0"\n'
    (root / "uv.lock").write_text(
        locked_content,
        encoding="utf-8",
    )
    (root / "scripts" / "consumer.py").write_text(
        consumer_source,
        encoding="utf-8",
    )
    (root / CONFIG_PATH).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "contracts": [
                    {
                        "distribution": "filelock",
                        "module": "filelock",
                        "introduced_version": "3.30.0",
                        "public_apis": list(symbols),
                        "consumers": ["scripts/consumer.py"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _filelock_importer(module: str) -> object:
    assert module == "filelock"
    return SimpleNamespace(
        lock_descriptor=lambda descriptor: descriptor,
        unlock_descriptor=lambda descriptor: None,
    )


def test_repository_filelock_descriptor_contract_is_green() -> None:
    contracts = load_contracts(ROOT)
    filelock_contract = next(
        contract for contract in contracts if contract.distribution == "filelock"
    )

    assert filelock_contract.introduced_version.public == "3.30.0"
    assert filelock_contract.public_apis == (
        "lock_descriptor",
        "unlock_descriptor",
    )
    assert "scripts/ci_batch_receipts.py" in filelock_contract.consumers
    assert audit_repository(ROOT) == []


def test_valid_contract_accepts_declared_locked_and_runtime_api(tmp_path: Path) -> None:
    _write_repository(tmp_path)

    assert audit_repository(tmp_path, importer=_filelock_importer) == []


def test_declared_floor_must_guarantee_api_version(tmp_path: Path) -> None:
    _write_repository(tmp_path, requirement="filelock>=3.29.7")

    errors = audit_repository(tmp_path, importer=_filelock_importer)

    assert errors == [
        "filelock: declaration filelock>=3.29.7 does not guarantee public APIs "
        "introduced in 3.30.0"
    ]


def test_every_locked_candidate_must_contain_api(tmp_path: Path) -> None:
    _write_repository(tmp_path, locked_versions=("3.29.7", "3.30.0"))

    errors = audit_repository(tmp_path, importer=_filelock_importer)

    assert errors == [
        "filelock: locked version 3.29.7 predates public APIs introduced in 3.30.0"
    ]


def test_missing_declaration_and_lock_fail_closed(tmp_path: Path) -> None:
    _write_repository(tmp_path, requirement="other>=1", locked_versions=())

    errors = audit_repository(tmp_path, importer=_filelock_importer)

    assert errors == [
        "filelock: direct dependency declaration is missing",
        "filelock: locked package is missing",
    ]


def test_runtime_public_api_must_exist(tmp_path: Path) -> None:
    _write_repository(tmp_path)

    errors = audit_repository(
        tmp_path,
        importer=lambda _module: SimpleNamespace(lock_descriptor=lambda: None),
    )

    assert errors == ["filelock: runtime module filelock lacks public API unlock_descriptor"]


def test_runtime_import_failure_is_a_violation(tmp_path: Path) -> None:
    _write_repository(tmp_path)

    def unavailable(_module: str) -> object:
        raise ImportError("not installed")

    errors = audit_repository(tmp_path, importer=unavailable)

    assert errors == ["filelock: could not import runtime module filelock: not installed"]


def test_unexpected_runtime_import_failure_also_fails_closed(tmp_path: Path) -> None:
    _write_repository(tmp_path)

    def broken(_module: str) -> object:
        raise RuntimeError("module initialization failed")

    assert audit_repository(tmp_path, importer=broken) == [
        "filelock: could not import runtime module filelock: module initialization failed"
    ]


def test_configured_consumer_must_import_every_public_api(tmp_path: Path) -> None:
    _write_repository(
        tmp_path,
        consumer_source="from filelock import lock_descriptor\n",
    )

    errors = audit_repository(tmp_path, importer=_filelock_importer)

    assert errors == [
        "filelock: scripts/consumer.py does not import public API unlock_descriptor "
        "from filelock"
    ]


def test_malformed_json_and_duplicate_keys_fail_closed(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    (tmp_path / CONFIG_PATH).write_text('{"schema_version": 1,', encoding="utf-8")
    assert "could not load dependency API contract metadata" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]

    (tmp_path / CONFIG_PATH).write_text(
        '{"schema_version":1,"schema_version":1,"contracts":[]}',
        encoding="utf-8",
    )
    assert "duplicate key" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]


def test_unknown_or_missing_contract_metadata_fails_closed(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    metadata = json.loads((tmp_path / CONFIG_PATH).read_text(encoding="utf-8"))
    metadata["unexpected"] = True
    (tmp_path / CONFIG_PATH).write_text(json.dumps(metadata), encoding="utf-8")
    assert "unknown keys" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]

    (tmp_path / CONFIG_PATH).unlink()
    assert "could not load dependency API contract metadata" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]


def test_invalid_contract_fields_fail_closed(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    metadata = json.loads((tmp_path / CONFIG_PATH).read_text(encoding="utf-8"))
    contract = metadata["contracts"][0]
    contract["introduced_version"] = "not-a-version"
    contract["public_apis"] = []
    (tmp_path / CONFIG_PATH).write_text(json.dumps(metadata), encoding="utf-8")

    assert "introduced_version is invalid" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("schema", "schema_version must be 1"),
        ("contracts-object", "contracts must be a list"),
        ("contracts-empty", "contracts must not be empty"),
        ("missing-root-key", "is missing keys"),
        ("entry-not-object", "must be an object"),
        ("distribution", "distribution is invalid"),
        ("module", "module is invalid"),
        ("introduced-type", "introduced_version is invalid"),
        ("introduced-prerelease", "introduced_version is invalid"),
        ("apis-empty", "public_apis must be a non-empty list"),
        ("apis-duplicate", "public_apis must not contain duplicates"),
        ("apis-invalid", "public_apis contains invalid name"),
        ("apis-too-many", "public_apis permits at most 64 entries"),
        ("consumer-unsafe", "contains unsafe Python path"),
        ("contract-duplicate", "duplicates an existing contract"),
    ],
)
def test_contract_schema_is_strict_and_bounded(
    tmp_path: Path,
    case: str,
    expected: str,
) -> None:
    _write_repository(tmp_path)
    metadata = json.loads((tmp_path / CONFIG_PATH).read_text(encoding="utf-8"))
    contract = metadata["contracts"][0]
    if case == "schema":
        metadata["schema_version"] = 2
    elif case == "contracts-object":
        metadata["contracts"] = {}
    elif case == "contracts-empty":
        metadata["contracts"] = []
    elif case == "missing-root-key":
        del metadata["schema_version"]
    elif case == "entry-not-object":
        metadata["contracts"] = [1]
    elif case == "distribution":
        contract["distribution"] = "bad requirement!"
    elif case == "module":
        contract["module"] = "not-a-module"
    elif case == "introduced-type":
        contract["introduced_version"] = 330
    elif case == "introduced-prerelease":
        contract["introduced_version"] = "3.30.0rc1"
    elif case == "apis-empty":
        contract["public_apis"] = []
    elif case == "apis-duplicate":
        contract["public_apis"] = ["lock_descriptor", "lock_descriptor"]
    elif case == "apis-invalid":
        contract["public_apis"] = ["not-an-identifier"]
    elif case == "apis-too-many":
        contract["public_apis"] = [f"api_{index}" for index in range(65)]
    elif case == "consumer-unsafe":
        contract["consumers"] = ["../outside.py"]
    elif case == "contract-duplicate":
        metadata["contracts"].append(dict(contract))
    (tmp_path / CONFIG_PATH).write_text(json.dumps(metadata), encoding="utf-8")

    assert expected in audit_repository(tmp_path, importer=_filelock_importer)[0]


def test_non_utf8_or_symlinked_contract_metadata_fails_closed(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    metadata_path = tmp_path / CONFIG_PATH
    metadata_path.write_bytes(b"\xff")
    assert "is not UTF-8" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]

    metadata_path.unlink()
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    metadata_path.symlink_to(target)
    assert "must not be a symlink" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]


def test_contract_count_and_metadata_size_are_bounded(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    metadata = json.loads((tmp_path / CONFIG_PATH).read_text(encoding="utf-8"))
    metadata["contracts"] *= MAX_CONTRACTS + 1
    (tmp_path / CONFIG_PATH).write_text(json.dumps(metadata), encoding="utf-8")
    assert f"at most {MAX_CONTRACTS} entries" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]

    (tmp_path / CONFIG_PATH).write_text(" " * 70_000, encoding="utf-8")
    assert "exceeds 65536-byte limit" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]


def test_malformed_project_and_lock_metadata_fail_closed(tmp_path: Path) -> None:
    _write_repository(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project", encoding="utf-8")
    assert "could not load project dependency metadata" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]

    _write_repository(tmp_path)
    (tmp_path / "uv.lock").write_text('package = [{name = "filelock"}]', encoding="utf-8")
    assert "could not load locked dependency metadata" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]

    _write_repository(tmp_path)
    (tmp_path / "uv.lock").write_text("[[package]", encoding="utf-8")
    assert "invalid uv.lock" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]

    _write_repository(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = "filelock>=3.30.0"\n',
        encoding="utf-8",
    )
    assert "project.dependencies must be a list" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]


def test_invalid_requirement_version_and_consumer_syntax_fail_closed(
    tmp_path: Path,
) -> None:
    _write_repository(tmp_path, requirement="not valid !!!")
    assert "invalid dependency requirement" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]

    _write_repository(tmp_path, locked_versions=("not-a-version",))
    assert "invalid locked version" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]

    _write_repository(tmp_path, consumer_source="from filelock import (\n")
    assert "could not parse consumer" in audit_repository(
        tmp_path, importer=_filelock_importer
    )[0]


def test_locked_version_must_satisfy_a_direct_declaration(tmp_path: Path) -> None:
    _write_repository(
        tmp_path,
        requirement="filelock>=3.30.0,<4",
        locked_versions=("4.0.12",),
    )

    assert audit_repository(tmp_path, importer=_filelock_importer) == [
        "filelock: locked version 4.0.12 does not satisfy any direct declaration"
    ]


def test_cli_returns_bounded_pass_failure_and_usage_markers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_repository(tmp_path)
    assert main([str(tmp_path)]) == 0
    assert capsys.readouterr().out == "DEPENDENCY-API-CONTRACT PASS\n"

    _write_repository(tmp_path, requirement="filelock>=3.29.7")
    assert main([str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "DEPENDENCY-API-CONTRACT VIOLATION" in output
    assert "DEPENDENCY-API-CONTRACT FAIL violations=1" in output

    assert main([str(tmp_path), "unexpected"]) == 2
    assert "FAIL usage" in capsys.readouterr().out
