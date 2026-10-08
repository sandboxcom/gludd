"""Fail-closed contracts for phase-one exact-gate batch receipts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from coverage import CoverageData
from scripts import ci_batch_receipts as receipt_module
from scripts import run_ci_shards_serial as serial_runner
from scripts.ci_batch_receipts import (
    MAX_RECEIPT_BYTES,
    MAX_RECEIPT_GENERATIONS,
    BatchReceiptRequest,
    ReceiptPublication,
    ShadowBatchReceiptWriter,
    build_batch_runtime_identity,
    canonical_json_sha256,
    normalize_junit_outcomes,
    validate_batch_receipt,
)

_SHA = "a" * 40


def _write_branch_coverage(path: Path) -> None:
    data = CoverageData(basename=str(path))
    data.add_arcs({"src/general_ludd/example.py": {(1, 2), (2, -1)}})
    data.write()
    data.close()


def _write_passing_junit(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="2" failures="0" errors="0" skipped="1">
  <testsuite name="pytest" tests="2" failures="0" errors="0" skipped="1">
    <testcase classname="tests.unit.test_example" name="test_pass" />
    <testcase classname="tests.unit.test_example" name="test_skip"><skipped /></testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )


def _identity(*, sha: str = _SHA) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": {
            "candidate_sha": sha,
            "expected_sha": sha,
            "branch": "feature",
            "clean": True,
            "exact_sha": True,
            "queries_ok": True,
            "repository_state_id": "state-1",
            "test_files": ["tests/unit/test_example.py"],
            "test_files_sha256": canonical_json_sha256(
                ["tests/unit/test_example.py"]
            ),
        },
        "plan": {
            "shard": "unit-1a1",
            "batch_index": 1,
            "max_files_per_batch": 16,
            "shard_plan_sha256": "1" * 64,
            "collection_manifest_sha256": "2" * 64,
            "execution_policy_sha256": "3" * 64,
        },
        "runner": {
            "implementation_sha256": "4" * 64,
            "attestation_sha256": "5" * 64,
            "pytest_args": ["-W", "error"],
            "heartbeat_seconds": 30.0,
            "no_progress_seconds": 600.0,
            "worker_count": 1,
            "distribution": "none",
            "cleanup_policy_version": 1,
        },
        "toolchain": {
            "python": "cpython-3.11.14",
            "executable_sha256": "6" * 64,
            "uv_version": "0.8.0",
            "lockfile_sha256": "7" * 64,
            "dependency_profile_sha256": "8" * 64,
            "installed_distributions_sha256": "9" * 64,
        },
        "plugins": {
            "inventory_sha256": "b" * 64,
            "pytest": "8.4.2",
            "pytest_cov": "7.0.0",
            "coverage": "7.10.7",
        },
        "coverage": {
            "config_sha256": "c" * 64,
            "branch": True,
            "source": ["src/general_ludd"],
            "omit": [],
            "paths": {},
            "concurrency": ["greenlet", "thread"],
            "data_schema": "coverage.py-7",
        },
        "platform": {
            "system": "Darwin",
            "release": "25.0",
            "machine": "arm64",
            "python_abi": "cpython-311",
            "resource_namespace_schema": 1,
        },
        "environment": {
            "allowlist": {
                "CI": "true",
                "GITHUB_ACTIONS": None,
                "PYTHONHASHSEED": None,
                "TZ": "UTC",
            },
            "undeclared_names_sha256": "d" * 64,
            "restricted_inputs_present": False,
            "values_outside_allowlist_stored": False,
        },
        "external_inputs": [],
    }


def _request(tmp_path: Path, **overrides: object) -> BatchReceiptRequest:
    coverage = tmp_path / "batch.coverage"
    junit = tmp_path / "junit.xml"
    _write_branch_coverage(coverage)
    _write_passing_junit(junit)
    values: dict[str, object] = {
        "action_identity": _identity(),
        "observed_action_identity": _identity(),
        "coverage_path": coverage,
        "outcome_manifest": normalize_junit_outcomes(junit),
        "originating_run_id": "run-1",
        "started_at": "2026-10-07T12:00:00Z",
        "completed_at": "2026-10-07T12:00:01Z",
        "returncode": 0,
        "cleanup_returncode": 0,
    }
    values.update(overrides)
    return BatchReceiptRequest(**values)  # type: ignore[arg-type]


def test_shadow_writer_publishes_one_content_addressed_pass_receipt(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    writer = ShadowBatchReceiptWriter(cache)
    request = _request(tmp_path)

    result = writer.publish(request)

    assert result.published is True
    assert result.reason == "published"
    assert result.path == cache / "v1" / _SHA / canonical_json_sha256(_identity())
    assert result.path is not None
    validation = validate_batch_receipt(
        result.path,
        expected_action_identity=_identity(),
    )
    assert validation.valid is True
    assert validation.reason == "valid"
    assert {path.name for path in result.path.iterdir()} == {
        "complete.json",
        "coverage.data",
        "manifest.json",
        "outcomes.json",
    }
    manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
    serialized = json.dumps(manifest, sort_keys=True).lower()
    assert "secret" not in serialized
    assert "password" not in serialized
    assert "pickle" not in serialized


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"returncode": 1}, "batch-not-passing"),
        ({"cleanup_returncode": 74}, "cleanup-incomplete"),
        ({"outcome_manifest": None}, "outcomes-invalid"),
    ],
)
def test_shadow_writer_refuses_nonpassing_or_incomplete_batch(
    tmp_path: Path,
    overrides: dict[str, object],
    reason: str,
) -> None:
    writer = ShadowBatchReceiptWriter(tmp_path / "batch-receipts")

    result = writer.publish(_request(tmp_path, **overrides))

    assert result.published is False
    assert result.reason == reason
    assert not (tmp_path / "batch-receipts").exists()


def test_shadow_writer_refuses_statement_only_coverage(tmp_path: Path) -> None:
    coverage = tmp_path / "statement-only.coverage"
    data = CoverageData(basename=str(coverage))
    data.add_lines({"src/general_ludd/example.py": {1, 2}})
    data.write()
    data.close()
    writer = ShadowBatchReceiptWriter(tmp_path / "batch-receipts")

    result = writer.publish(_request(tmp_path, coverage_path=coverage))

    assert result.published is False
    assert result.reason == "coverage-not-branch-aware"


def test_coverage_validation_refuses_missing_empty_corrupt_and_symlink_data(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.coverage"
    empty = tmp_path / "empty.coverage"
    corrupt = tmp_path / "corrupt.coverage"
    symlink = tmp_path / "linked.coverage"
    empty_branch = tmp_path / "empty-branch.coverage"
    empty.touch()
    corrupt.write_bytes(b"not sqlite")
    symlink.symlink_to(corrupt)
    empty_data = CoverageData(basename=str(empty_branch))
    empty_data.add_arcs({})
    empty_data.write()
    empty_data.close()

    missing_error = receipt_module.coverage_data_error(missing)
    assert missing_error is not None
    assert missing_error.startswith("FileNotFoundError:")
    assert receipt_module.coverage_data_error(empty) == "file is missing or empty"
    assert receipt_module.coverage_data_error(corrupt) is not None
    assert (
        receipt_module.coverage_data_error(symlink)
        == "symbolic links are not accepted"
    )
    assert (
        receipt_module.coverage_data_error(empty_branch, require_branch=True)
        == "coverage contains no measured files"
    )


def test_shadow_writer_refuses_identity_drift_without_touching_cache(
    tmp_path: Path,
) -> None:
    writer = ShadowBatchReceiptWriter(tmp_path / "batch-receipts")

    result = writer.publish(
        _request(tmp_path, observed_action_identity=_identity(sha="b" * 40))
    )

    assert result.published is False
    assert result.reason == "action-identity-drift"
    assert not (tmp_path / "batch-receipts").exists()


def test_shadow_writer_refuses_sensitive_identity_keys(tmp_path: Path) -> None:
    identity = _identity()
    environment = identity["environment"]
    assert isinstance(environment, dict)
    environment["api_token"] = "must-not-persist"
    writer = ShadowBatchReceiptWriter(tmp_path / "batch-receipts")

    result = writer.publish(
        _request(
            tmp_path,
            action_identity=identity,
            observed_action_identity=identity,
        )
    )

    assert result.published is False
    assert result.reason == "sensitive-identity-key"
    assert not (tmp_path / "batch-receipts").exists()


def test_shadow_writer_refuses_restricted_environment_without_storing_value(
    tmp_path: Path,
) -> None:
    identity = _identity()
    identity["environment"] = {
        "allowlist": {"CI": "true"},
        "restricted_inputs_present": True,
        "undeclared_names_sha256": "d" * 64,
        "values_outside_allowlist_stored": False,
    }

    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(
            tmp_path,
            action_identity=identity,
            observed_action_identity=identity,
        )
    )

    assert result.published is False
    assert result.reason == "restricted-environment"
    assert "do-not-store" not in json.dumps(identity)
    assert not (tmp_path / "batch-receipts").exists()


@pytest.mark.parametrize(
    ("request_change", "reason"),
    [
        ({"originating_run_id": "bad run id"}, "run-id-invalid"),
        ({"coverage_path": Path("missing.coverage")}, "coverage-invalid"),
    ],
)
def test_shadow_writer_refuses_invalid_run_or_coverage_evidence(
    tmp_path: Path,
    request_change: dict[str, object],
    reason: str,
) -> None:
    if request_change.get("coverage_path") == Path("missing.coverage"):
        request_change["coverage_path"] = tmp_path / "missing.coverage"

    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(tmp_path, **request_change)
    )

    assert result.published is False
    assert result.reason == reason


@pytest.mark.parametrize(
    ("identity_change", "reason"),
    [
        ({"source": "invalid"}, "source-identity-invalid"),
        ({"source": {"candidate_sha": "short", "clean": True}}, "source-ineligible"),
        ({"source": {"candidate_sha": _SHA, "clean": False}}, "source-ineligible"),
        ({"unexpected_metadata": "do-not-store"}, "action-identity-invalid"),
        ({"not_finite": float("nan")}, "action-identity-invalid"),
    ],
)
def test_shadow_writer_refuses_malformed_action_identity(
    tmp_path: Path,
    identity_change: dict[str, object],
    reason: str,
) -> None:
    identity = _identity()
    identity.update(identity_change)

    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(
            tmp_path,
            action_identity=identity,
            observed_action_identity=identity,
        )
    )

    assert result.published is False
    assert result.reason == reason


def test_shadow_writer_refuses_internally_inexact_source_identity(
    tmp_path: Path,
) -> None:
    identity = _identity()
    source = identity["source"]
    assert isinstance(source, dict)
    source["expected_sha"] = "b" * 40

    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(
            tmp_path,
            action_identity=identity,
            observed_action_identity=identity,
        )
    )

    assert result.published is False
    assert result.reason == "source-ineligible"


@pytest.mark.parametrize("corruption", ["coverage", "manifest", "completion"])
def test_receipt_validation_refuses_corruption(
    tmp_path: Path,
    corruption: str,
) -> None:
    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(tmp_path)
    )
    assert result.path is not None
    if corruption == "coverage":
        with (result.path / "coverage.data").open("ab") as handle:
            handle.write(b"corrupt")
    elif corruption == "manifest":
        (result.path / "manifest.json").write_text("{", encoding="utf-8")
    else:
        (result.path / "complete.json").write_text("{}\n", encoding="utf-8")

    validation = validate_batch_receipt(
        result.path,
        expected_action_identity=_identity(),
    )

    assert validation.valid is False
    assert validation.reason.startswith("corrupt-")


def test_receipt_validation_refuses_duplicate_json_keys(tmp_path: Path) -> None:
    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(tmp_path)
    )
    assert result.path is not None
    manifest_path = result.path / "manifest.json"
    original = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        '{"schema_version":1,' + original.removeprefix("{"),
        encoding="utf-8",
    )
    completion_path = result.path / "complete.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["manifest_sha256"] = receipt_module.file_sha256(manifest_path)
    completion_path.write_bytes(receipt_module.canonical_json_bytes(completion) + b"\n")

    validation = validate_batch_receipt(
        result.path,
        expected_action_identity=_identity(),
    )

    assert validation.valid is False
    assert validation.reason == "corrupt-json-ValueError"


@pytest.mark.parametrize("mode", [0o644, 0o4600])
def test_receipt_validation_refuses_unsafe_artifact_mode(
    tmp_path: Path,
    mode: int,
) -> None:
    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(tmp_path)
    )
    assert result.path is not None
    os.chmod(result.path / "manifest.json", mode)

    validation = validate_batch_receipt(
        result.path,
        expected_action_identity=_identity(),
    )

    assert validation.valid is False
    assert validation.reason == "corrupt-manifest.json-unsafe-mode"


def test_receipt_validation_refuses_extra_artifact_and_foreign_identity(
    tmp_path: Path,
) -> None:
    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(tmp_path)
    )
    assert result.path is not None
    extra = result.path / "extra.json"
    extra.write_text("{}\n", encoding="utf-8")
    os.chmod(extra, 0o600)
    assert (
        validate_batch_receipt(
            result.path,
            expected_action_identity=_identity(),
        ).reason
        == "corrupt-layout"
    )
    extra.unlink()
    assert (
        validate_batch_receipt(
            result.path,
            expected_action_identity=_identity(sha="b" * 40),
        ).reason
        == "corrupt-action-identity"
    )


def test_receipt_validation_rechecks_coverage_semantics_not_only_digest(
    tmp_path: Path,
) -> None:
    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(tmp_path)
    )
    assert result.path is not None
    coverage = result.path / "coverage.data"
    coverage.unlink()
    data = CoverageData(basename=str(coverage))
    data.add_lines({"src/general_ludd/example.py": {1}})
    data.write()
    data.close()
    os.chmod(coverage, 0o600)
    manifest_path = result.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["coverage"]["bytes"] = coverage.stat().st_size
    manifest["coverage"]["sha256"] = receipt_module.file_sha256(coverage)
    manifest_path.write_bytes(receipt_module.canonical_json_bytes(manifest) + b"\n")
    os.chmod(manifest_path, 0o600)
    completion_path = result.path / "complete.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["manifest_sha256"] = receipt_module.file_sha256(manifest_path)
    completion_path.write_bytes(receipt_module.canonical_json_bytes(completion) + b"\n")
    os.chmod(completion_path, 0o600)

    validation = validate_batch_receipt(
        result.path,
        expected_action_identity=_identity(),
    )

    assert validation.valid is False
    assert validation.reason == "corrupt-coverage-data"


def test_receipt_validation_refuses_changed_completion_digest(tmp_path: Path) -> None:
    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(tmp_path)
    )
    assert result.path is not None
    completion_path = result.path / "complete.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["manifest_sha256"] = "0" * 64
    completion_path.write_bytes(receipt_module.canonical_json_bytes(completion) + b"\n")
    os.chmod(completion_path, 0o600)

    validation = validate_batch_receipt(
        result.path,
        expected_action_identity=_identity(),
    )

    assert validation.valid is False
    assert validation.reason == "corrupt-completion-digest"


@pytest.mark.parametrize(
    ("max_generations", "max_bytes"),
    [(0, MAX_RECEIPT_BYTES), (3, MAX_RECEIPT_BYTES), (2, 0), (2, MAX_RECEIPT_BYTES + 1)],
)
def test_shadow_writer_rejects_bounds_wider_than_phase_one(
    tmp_path: Path,
    max_generations: int,
    max_bytes: int,
) -> None:
    with pytest.raises(ValueError):
        ShadowBatchReceiptWriter(
            tmp_path / "batch-receipts",
            max_generations=max_generations,
            max_bytes=max_bytes,
        )


def test_shadow_writer_enforces_two_generation_bound(tmp_path: Path) -> None:
    cache = tmp_path / "batch-receipts"
    for sha in ("b" * 40, "c" * 40):
        generation = cache / "v1" / sha
        generation.mkdir(parents=True)
        os.chmod(cache, 0o700)
        os.chmod(cache / "v1", 0o700)
        os.chmod(generation, 0o700)
    writer = ShadowBatchReceiptWriter(cache)

    result = writer.publish(_request(tmp_path))

    assert MAX_RECEIPT_GENERATIONS == 2
    assert result.published is False
    assert result.reason == "generation-limit"
    assert len(tuple((cache / "v1").iterdir())) == 2


def test_shadow_writer_enforces_total_byte_bound(tmp_path: Path) -> None:
    writer = ShadowBatchReceiptWriter(
        tmp_path / "batch-receipts",
        max_bytes=1,
    )

    result = writer.publish(_request(tmp_path))

    assert MAX_RECEIPT_BYTES == 2 * 1024**3
    assert result.published is False
    assert result.reason == "byte-limit"


def test_shadow_writer_refuses_symlinked_cache_root_without_following_it(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    cache = tmp_path / "batch-receipts"
    cache.symlink_to(outside, target_is_directory=True)

    result = ShadowBatchReceiptWriter(cache).publish(_request(tmp_path))

    assert result.published is False
    assert result.reason == "cache-root-symlink"
    assert outside.stat().st_mode & 0o777 == 0o755
    assert tuple(outside.iterdir()) == ()


def test_shadow_writer_refuses_symlinked_cache_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "ci-shards"
    parent.symlink_to(outside, target_is_directory=True)

    result = ShadowBatchReceiptWriter(parent / "batch-receipts").publish(
        _request(tmp_path)
    )

    assert result.published is False
    assert result.reason == "cache-parent-symlink"
    assert tuple(outside.iterdir()) == ()


def test_shadow_writer_refuses_symlinked_generation_without_following_it(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    version = cache / "v1"
    outside = tmp_path / "outside"
    version.mkdir(parents=True, mode=0o700)
    outside.mkdir(mode=0o755)
    os.chmod(cache, 0o700)
    os.chmod(version, 0o700)
    (version / _SHA).symlink_to(outside, target_is_directory=True)

    result = ShadowBatchReceiptWriter(cache).publish(_request(tmp_path))

    assert result.published is False
    assert result.reason == "cache-layout-invalid"
    assert outside.stat().st_mode & 0o777 == 0o755
    assert tuple(outside.iterdir()) == ()


def test_shadow_writer_refuses_unsafe_or_unknown_cache_layout(tmp_path: Path) -> None:
    cache = tmp_path / "batch-receipts"
    cache.mkdir(mode=0o755)
    assert (
        ShadowBatchReceiptWriter(cache).publish(_request(tmp_path)).reason
        == "cache-root-unsafe-mode"
    )
    os.chmod(cache, 0o700)
    version = cache / "v1"
    version.mkdir(mode=0o700)
    (version / "not-a-generation").mkdir(mode=0o700)

    result = ShadowBatchReceiptWriter(cache).publish(_request(tmp_path))

    assert result.published is False
    assert result.reason == "cache-layout-invalid"


def test_shadow_writer_refuses_unknown_symlink_during_size_walk(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    generation = cache / "v1" / _SHA
    generation.mkdir(parents=True, mode=0o700)
    os.chmod(cache, 0o700)
    os.chmod(cache / "v1", 0o700)
    os.chmod(generation, 0o700)
    (generation / "stale-link").symlink_to(tmp_path / "outside")

    result = ShadowBatchReceiptWriter(cache).publish(_request(tmp_path))

    assert result.published is False
    assert result.reason == "cache-size-unknown"


def test_shadow_writer_never_overwrites_existing_action(tmp_path: Path) -> None:
    writer = ShadowBatchReceiptWriter(tmp_path / "batch-receipts")
    request = _request(tmp_path)
    first = writer.publish(request)

    second = writer.publish(request)

    assert first.published is True
    assert second.published is False
    assert second.reason == "already-present"
    assert second.path == first.path


def test_shadow_writer_removes_staging_tree_after_publication_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "batch-receipts"
    monkeypatch.setattr(
        receipt_module,
        "_copy_private",
        lambda *_args: (_ for _ in ()).throw(OSError("disk")),
    )

    result = ShadowBatchReceiptWriter(cache).publish(_request(tmp_path))

    assert result.published is False
    assert result.reason == "publication-failed"
    generation = cache / "v1" / _SHA
    assert not generation.exists()


def test_shadow_writer_refuses_failed_internal_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        receipt_module,
        "validate_batch_receipt",
        lambda *_args, **_kwargs: receipt_module.ReceiptValidation(
            False,
            "corrupt-test",
        ),
    )

    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(tmp_path)
    )

    assert result.published is False
    assert result.reason == "self-validation-corrupt-test"


def test_shadow_writer_removes_receipt_that_fails_post_publish_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validations = iter(
        (
            receipt_module.ReceiptValidation(True, "valid"),
            receipt_module.ReceiptValidation(False, "corrupt-after-rename"),
        )
    )
    monkeypatch.setattr(
        receipt_module,
        "validate_batch_receipt",
        lambda *_args, **_kwargs: next(validations),
    )

    result = ShadowBatchReceiptWriter(tmp_path / "batch-receipts").publish(
        _request(tmp_path)
    )

    assert result.published is False
    assert result.reason == "post-publish-corrupt-after-rename"
    generation = tmp_path / "batch-receipts" / "v1" / _SHA
    assert not generation.exists()


def test_junit_normalization_keeps_only_content_free_terminal_identity(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<testsuite tests="1" failures="0" errors="0" skipped="0">
<testcase classname="tests.unit.test_example" name="test_token">
<system-out>api_token=do-not-store</system-out>
</testcase></testsuite>""",
        encoding="utf-8",
    )

    outcomes = normalize_junit_outcomes(junit)

    serialized = json.dumps(outcomes, sort_keys=True)
    assert outcomes["schema_version"] == 1
    assert outcomes["counts"] == {
        "errors": 0,
        "failures": 0,
        "passed": 1,
        "skipped": 0,
        "tests": 1,
    }
    assert outcomes["node_id_sha256"]
    assert "do-not-store" not in serialized
    assert "api_token" not in serialized


def test_junit_normalization_accepts_long_pytest_parameter_ids(
    tmp_path: Path,
) -> None:
    parameter_id = "word " * 1_000
    testcase_name = f"test_words[{parameter_id}]"
    assert len(testcase_name) > 4_096
    junit = tmp_path / "junit.xml"
    junit.write_text(
        f'<testsuite><testcase classname="case" name="{testcase_name}" /></testsuite>',
        encoding="utf-8",
    )

    outcomes = normalize_junit_outcomes(junit)

    assert outcomes["counts"] == {
        "errors": 0,
        "failures": 0,
        "passed": 1,
        "skipped": 0,
        "tests": 1,
    }


def test_junit_normalization_refuses_hostile_testcase_name_size(
    tmp_path: Path,
) -> None:
    testcase_name = "x" * (receipt_module.MAX_JUNIT_TESTCASE_NAME_CHARS + 1)
    junit = tmp_path / "junit.xml"
    junit.write_text(
        f'<testsuite><testcase classname="case" name="{testcase_name}" /></testsuite>',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identity is missing or oversized"):
        normalize_junit_outcomes(junit)


@pytest.mark.parametrize(
    "xml",
    [
        "<",
        "<unsupported />",
        "<testsuite />",
        "<testsuite><testcase classname='case' /></testsuite>",
        (
            "<testsuite><testcase classname='case' name='ambiguous'>"
            "<failure /><skipped /></testcase></testsuite>"
        ),
        (
            "<testsuite>"
            "<testcase classname='case' name='same' />"
            "<testcase classname='case' name='same' />"
            "</testsuite>"
        ),
    ],
)
def test_junit_normalization_refuses_malformed_or_ambiguous_evidence(
    tmp_path: Path,
    xml: str,
) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(xml, encoding="utf-8")

    with pytest.raises(ValueError):
        normalize_junit_outcomes(junit)


def test_junit_normalization_classifies_failure_and_error_outcomes(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<testsuite>
<testcase classname="case" name="failed"><failure /></testcase>
<testcase classname="case" name="errored"><error /></testcase>
</testsuite>""",
        encoding="utf-8",
    )

    outcomes = normalize_junit_outcomes(junit)

    assert outcomes["counts"] == {
        "errors": 1,
        "failures": 1,
        "passed": 0,
        "skipped": 0,
        "tests": 2,
    }
    assert receipt_module._outcome_manifest_error(outcomes) == "terminal-outcome"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"schema_version": 2}, "schema"),
        ({"counts": {}}, "counts"),
        (
            {
                "counts": {
                    "errors": 0,
                    "failures": 0,
                    "passed": -1,
                    "skipped": 0,
                    "tests": -1,
                }
            },
            "counts",
        ),
        (
            {
                "counts": {
                    "errors": 0,
                    "failures": 0,
                    "passed": 1,
                    "skipped": 0,
                    "tests": 2,
                }
            },
            "counts",
        ),
        ({"node_id_sha256": "bad"}, "digest"),
    ],
)
def test_outcome_manifest_validation_is_strict(
    tmp_path: Path,
    mutation: dict[str, object],
    reason: str,
) -> None:
    junit = tmp_path / "junit.xml"
    _write_passing_junit(junit)
    outcomes = normalize_junit_outcomes(junit)
    outcomes.update(mutation)

    assert receipt_module._outcome_manifest_error(outcomes) == reason


def test_junit_normalization_refuses_missing_empty_and_symlinked_files(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty.xml"
    linked = tmp_path / "linked.xml"
    empty.touch()
    linked.symlink_to(empty)

    for path in (tmp_path / "missing.xml", empty, linked):
        with pytest.raises(ValueError):
            normalize_junit_outcomes(path)


def test_runtime_identity_hashes_restricted_environment_names_without_values() -> None:
    root = Path(__file__).resolve().parents[2]

    identity = build_batch_runtime_identity(
        root=root,
        scripts=root / "scripts",
        runner=root / "scripts" / "run_ci_shards_serial.py",
        coverage_config=root / ".coveragerc-greenlet",
        interpreter={
            "implementation": sys.implementation.name,
            "version": ".".join(map(str, sys.version_info[:3])),
            "executable": str(Path(sys.executable).resolve(strict=True)),
        },
        include_uv_probe=False,
        environment={"CI": "true", "API_TOKEN": "do-not-store", "TZ": "UTC"},
    )

    serialized = json.dumps(identity, sort_keys=True)
    assert identity["runner"]["receipt_schema"] == 1
    assert identity["coverage"]["branch"] is True
    assert identity["environment"]["restricted_inputs_present"] is True
    assert identity["environment"]["values_outside_allowlist_stored"] is False
    assert "API_TOKEN" not in serialized
    assert "do-not-store" not in serialized


def test_runtime_identity_records_bounded_uv_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="uv 0.8.0\n",
        ),
    )

    identity = build_batch_runtime_identity(
        root=root,
        scripts=root / "scripts",
        runner=root / "scripts" / "run_ci_shards_serial.py",
        coverage_config=root / ".coveragerc-greenlet",
        interpreter={
            "implementation": sys.implementation.name,
            "version": ".".join(map(str, sys.version_info[:3])),
            "executable": str(Path(sys.executable).resolve(strict=True)),
        },
        include_uv_probe=True,
        environment={},
    )

    assert identity["toolchain"]["uv_version"] == "uv 0.8.0"


def test_runtime_identity_fails_closed_when_uv_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    with pytest.raises(RuntimeError, match="uv version probe failed"):
        build_batch_runtime_identity(
            root=root,
            scripts=root / "scripts",
            runner=root / "scripts" / "run_ci_shards_serial.py",
            coverage_config=root / ".coveragerc-greenlet",
            interpreter={
                "implementation": sys.implementation.name,
                "version": ".".join(map(str, sys.version_info[:3])),
                "executable": str(Path(sys.executable).resolve(strict=True)),
            },
            include_uv_probe=True,
            environment={},
        )


def test_pytest_receipt_outcomes_are_opt_in_without_changing_worker_policy(
    tmp_path: Path,
) -> None:
    command = serial_runner._pytest_command(
        "unit-1a1",
        ["tests/unit/test_example.py"],
        tmp_path / "owned",
        ["-W", "error"],
        junit_xml=tmp_path / "batch" / "junit.xml",
    )

    assert f"--junitxml={tmp_path / 'batch' / 'junit.xml'}" in command
    assert command.count("--override-ini") == 1
    assert "junit_family=xunit2" in command
    assert "-n" not in command
    assert serial_runner.MAX_FILES_PER_BATCH == 16


def test_release_receipt_eligibility_requires_full_exact_sha() -> None:
    identity = {
        "head_sha": _SHA,
        "expected_sha": _SHA,
        "clean": True,
        "exact_sha": True,
        "queries_ok": True,
    }

    assert serial_runner._identity_is_release_eligible(identity) is True
    assert serial_runner._identity_is_release_eligible(
        {**identity, "head_sha": "abc123", "expected_sha": "abc123"}
    ) is False


def test_shadow_session_reobserves_uv_toolchain_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_identity = {
        "head_sha": _SHA,
        "expected_sha": _SHA,
        "branch": "feature",
        "clean": True,
        "exact_sha": True,
        "queries_ok": True,
    }
    uv_versions = iter(("uv expected", "uv drifted"))
    probe_modes: list[bool] = []

    def fake_runtime_identity(
        *,
        include_uv_probe: bool,
        **_kwargs: object,
    ) -> dict[str, dict[str, object]]:
        probe_modes.append(include_uv_probe)
        return {
            "runner": {},
            "toolchain": {"uv_version": next(uv_versions)},
            "plugins": {},
            "coverage": {},
            "platform": {},
            "environment": {},
        }

    monkeypatch.setattr(
        serial_runner,
        "_plan_shards",
        lambda *_args, **_kwargs: [
            ("unit-1a1", [["tests/unit/test_example.py"]])
        ],
    )
    monkeypatch.setattr(serial_runner, "repository_state_id", lambda *_args, **_kwargs: "state-1")
    monkeypatch.setattr(serial_runner, "_interpreter_identity", lambda: {"python": "same"})
    monkeypatch.setattr(serial_runner, "build_batch_runtime_identity", fake_runtime_identity)
    monkeypatch.setattr(
        serial_runner,
        "_repository_identity",
        lambda **_kwargs: repository_identity,
    )
    monkeypatch.setattr(
        serial_runner,
        "_resource_paths",
        lambda: serial_runner.ResourcePaths(
            root=tmp_path,
            coverage_shards=tmp_path / "coverage-fragments",
            coverage_json=tmp_path / "coverage.json",
            coverage_audit=tmp_path / "coverage-audit.json",
            attestation=tmp_path / "attestation.json",
            resume=tmp_path / "resume.json",
        ),
    )

    session = serial_runner._create_shadow_receipt_session(
        repository_identity=repository_identity,
        shards=["unit-1a1"],
        pytest_args=["-W", "error"],
        max_files_per_batch=16,
        heartbeat_seconds=30.0,
        no_progress_seconds=600.0,
    )
    expected = session.expected_identity(
        "unit-1a1",
        1,
        ["tests/unit/test_example.py"],
    )
    observed = session.observed_identity(
        "unit-1a1",
        1,
        ["tests/unit/test_example.py"],
    )

    assert probe_modes == [True, True]
    assert canonical_json_sha256(expected) != canonical_json_sha256(observed)


@pytest.mark.parametrize("receipt_headroom", [True, False])
def test_serial_runner_publishes_only_after_coverage_and_owned_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    receipt_headroom: bool,
) -> None:
    coverage_shards = tmp_path / "coverage-fragments"
    resource_root = tmp_path / "resources"
    serial_runner.COVERAGE_SHARDS = coverage_shards
    serial_runner.COVERAGE_JSON = tmp_path / "coverage.json"
    serial_runner.COVERAGE_AUDIT = tmp_path / "coverage-audit.json"
    monkeypatch.setattr(
        serial_runner,
        "_resource_paths",
        lambda: serial_runner.ResourcePaths(
            root=resource_root,
            coverage_shards=coverage_shards,
            coverage_json=tmp_path / "coverage.json",
            coverage_audit=tmp_path / "coverage-audit.json",
            attestation=tmp_path / "attestation.json",
            resume=tmp_path / "resume.json",
        ),
    )
    monkeypatch.setattr(
        serial_runner,
        "expand_shard",
        lambda _shard: ["tests/unit/test_example.py"],
    )
    monkeypatch.setattr(serial_runner, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(serial_runner, "_git_output", lambda *_args: (0, _SHA))
    monkeypatch.setattr(serial_runner, "_interpreter_identity", lambda: {"python": "same"})
    monkeypatch.setattr(
        serial_runner,
        "_interpreter_is_unchanged",
        lambda *_args, **_kwargs: True,
    )
    headroom_contexts: list[str] = []

    def record_headroom(_path: Path, *, context: str) -> bool:
        headroom_contexts.append(context)
        return len(headroom_contexts) == 1 or receipt_headroom

    monkeypatch.setattr(serial_runner, "_disk_headroom_available", record_headroom)

    owned_roots: list[Path] = []

    def fake_owned_tmpdir(_label: str) -> Path:
        owned = tmp_path / f"owned-{len(owned_roots) + 1}"
        owned.mkdir()
        owned_roots.append(owned)
        return owned

    monkeypatch.setattr(serial_runner, "_owned_socket_safe_tmpdir", fake_owned_tmpdir)

    def fake_run_owned(command: list[str], **_kwargs: object) -> int:
        junit = next(
            Path(argument.removeprefix("--junitxml="))
            for argument in command
            if argument.startswith("--junitxml=")
        )
        junit.parent.mkdir(parents=True, exist_ok=True)
        _write_passing_junit(junit)
        return 0

    monkeypatch.setattr(serial_runner, "_run_owned_pytest", fake_run_owned)

    def fake_save_coverage(
        shard: str,
        batch_index: int,
        _basetemp: Path,
        _env: dict[str, str],
    ) -> bool:
        coverage_shards.mkdir(parents=True, exist_ok=True)
        _write_branch_coverage(
            coverage_shards / f".coverage.{shard}.batch-{batch_index:03d}"
        )
        return True

    monkeypatch.setattr(serial_runner, "_save_shard_coverage", fake_save_coverage)

    def cleanup_tmpdir(path: Path, **_kwargs: object) -> int:
        shutil.rmtree(path)
        return 0

    monkeypatch.setattr(
        serial_runner,
        "_cleanup_owned_tmpdir_safely",
        cleanup_tmpdir,
    )

    published: list[BatchReceiptRequest] = []

    class RecordingWriter:
        def publish(self, request: BatchReceiptRequest) -> ReceiptPublication:
            assert all(not path.exists() for path in owned_roots)
            assert not any((resource_root / "workspaces").glob("*/batch-001"))
            published.append(request)
            return ReceiptPublication(
                True,
                "published",
                path=tmp_path / "receipt",
                action_digest="d" * 64,
            )

    session = serial_runner.BatchReceiptSession(
        writer=RecordingWriter(),  # type: ignore[arg-type]
        run_id="run-1",
        expected_identity=lambda *_args: _identity(),
        observed_identity=lambda *_args: _identity(),
    )

    result = serial_runner.run(
        ["unit-1a1"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
        receipt_session=session,
    )

    assert result == 0
    assert len(published) == int(receipt_headroom)
    if published:
        assert published[0].returncode == 0
        assert published[0].cleanup_returncode == 0
        assert published[0].outcome_manifest is not None
    else:
        output = capsys.readouterr().out
        assert "status=refused" in output
        assert "reason=disk-headroom" in output
    assert headroom_contexts == [
        "unit-1a1:batch-001:before",
        "unit-1a1:batch-001:receipt-write",
    ]


def test_cli_operator_off_switch_never_creates_shadow_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    resources = serial_runner.ResourcePaths(
        root=tmp_path,
        coverage_shards=tmp_path / "coverage-fragments",
        coverage_json=tmp_path / "coverage.json",
        coverage_audit=tmp_path / "coverage-audit.json",
        attestation=tmp_path / "attestation.json",
    )
    identity = {
        "head_sha": _SHA,
        "expected_sha": _SHA,
        "branch": "feature",
        "clean": True,
        "exact_sha": True,
        "queries_ok": True,
    }
    received: list[object] = []
    monkeypatch.setattr(serial_runner, "_resource_paths", lambda: resources)
    monkeypatch.setattr(
        serial_runner,
        "_repository_identity",
        lambda **_kwargs: identity,
    )
    monkeypatch.setattr(
        serial_runner,
        "_create_shadow_receipt_session",
        lambda **_kwargs: pytest.fail("operator-off must bypass receipt setup"),
    )
    def record_run(*_args: object, **kwargs: object) -> int:
        received.append(kwargs.get("receipt_session"))
        return 0

    monkeypatch.setattr(serial_runner, "run", record_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-1a1",
            "--skip-isolated",
            "--skip-aggregate",
            "--no-shadow-batch-receipts",
        ],
    )

    assert serial_runner.main() == 0
    assert received == [None]
    assert "status=disabled reason=operator-off" in capsys.readouterr().out
