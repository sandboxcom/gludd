"""Fail-closed contracts for sanitized, non-reusable batch failures."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from coverage import CoverageData
from scripts import ci_batch_receipts as receipt_module
from scripts import run_ci_shards_serial as serial_runner
from scripts.ci_batch_receipts import (
    MAX_FAILURE_NODES,
    MAX_FAILURE_RECEIPTS_PER_GENERATION,
    MAX_RECEIPT_BYTES,
    BatchReceiptRequest,
    FailureReceiptRequest,
    ReceiptPublication,
    ShadowBatchReceiptWriter,
    ShadowFailureReceiptWriter,
    canonical_json_sha256,
    failure_class_for_returncode,
    normalize_junit_failure_metadata,
    normalize_junit_outcomes,
    validate_failure_receipt,
)
from scripts.ci_batch_replay_audit import ReplayAuditRequest, ShadowReplayAuditor
from scripts.ci_gate_progress import ProgressBatch, ProgressExecution, ShadowGateProgress

_SHA = "a" * 40


def _identity(*, sha: str = _SHA) -> dict[str, object]:
    files = ["tests/unit/test_failure_example.py"]
    files_digest = canonical_json_sha256(files)
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
            "test_files": files,
            "test_files_sha256": files_digest,
        },
        "plan": {
            "shard": "unit-1a1",
            "batch_index": 1,
            "max_files_per_batch": 16,
            "shard_plan_sha256": "1" * 64,
            "complete_plan_sha256": "2" * 64,
            "collection_manifest_kind": "canonical-test-file-plan-v1",
            "collection_manifest_sha256": files_digest,
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
        "plugins": {"inventory_sha256": "b" * 64},
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
            "allowlist": {"CI": "true"},
            "undeclared_names_sha256": "d" * 64,
            "restricted_inputs_present": False,
            "values_outside_allowlist_stored": False,
        },
        "external_inputs": [],
    }


def _write_failure_junit(path: Path, *, count: int = 1) -> None:
    cases = "".join(
        f"""<testcase classname="secret.suite" file="/private/token-{index}.py"
 name="test_api_token_{index}"><failure>password=do-not-store-{index}</failure></testcase>"""
        for index in range(count)
    )
    path.write_text(f"<testsuite>{cases}</testsuite>", encoding="utf-8")


def _write_passing_junit(path: Path) -> None:
    path.write_text(
        "<testsuite><testcase classname='suite' name='test_pass' /></testsuite>",
        encoding="utf-8",
    )


def _write_branch_coverage(path: Path) -> None:
    data = CoverageData(basename=str(path))
    data.add_arcs({"src/general_ludd/example.py": {(1, 2), (2, -1)}})
    data.write()
    data.close()


def _failure_request(
    tmp_path: Path,
    **overrides: object,
) -> FailureReceiptRequest:
    junit = tmp_path / f"failure-{len(tuple(tmp_path.glob('failure-*.xml')))}.xml"
    _write_failure_junit(junit)
    values: dict[str, Any] = {
        "action_identity": _identity(),
        "observed_action_identity": _identity(),
        "failure_node_metadata": normalize_junit_failure_metadata(junit),
        "originating_run_id": "run-1",
        "elapsed_seconds": 1.25,
        "returncode": 1,
        "cleanup_returncode": 0,
    }
    values.update(overrides)
    return FailureReceiptRequest(**values)


def _progress_batch() -> ProgressBatch:
    identity = _identity()
    source = cast(dict[str, object], identity["source"])
    plan = cast(dict[str, object], identity["plan"])
    return ProgressBatch(
        shard="unit-1a1",
        batch_index=1,
        test_files_sha256=str(source["test_files_sha256"]),
        collection_manifest_sha256=str(plan["collection_manifest_sha256"]),
    )


def test_failure_metadata_is_bounded_and_contains_no_payload_or_path(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "junit.xml"
    _write_failure_junit(junit, count=MAX_FAILURE_NODES + 8)

    metadata = normalize_junit_failure_metadata(junit)

    assert metadata["failing_node_count"] == MAX_FAILURE_NODES + 8
    assert len(cast(list[object], metadata["nodes"])) == MAX_FAILURE_NODES
    assert metadata["nodes_truncated"] is True
    serialized = json.dumps(metadata, sort_keys=True).lower()
    assert "do-not-store" not in serialized
    assert "api_token" not in serialized
    assert "/private/" not in serialized
    assert "password" not in serialized


def test_failure_writer_publishes_sanitized_non_reusable_receipt(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    request = _failure_request(tmp_path)

    publication = ShadowFailureReceiptWriter(cache).publish(request)

    assert publication.published is True
    assert publication.path is not None
    assert publication.path.parent.name == "failures"
    assert publication.path.parent.parent.name == _SHA
    validation = validate_failure_receipt(
        publication.path,
        expected_action_digest=canonical_json_sha256(_identity()),
    )
    assert validation.valid is True
    manifest = json.loads(
        (publication.path / "manifest.json").read_text(encoding="utf-8")
    )
    failure = json.loads(
        (publication.path / "failure.json").read_text(encoding="utf-8")
    )
    assert manifest["kind"] == "non-reusable-failure"
    assert manifest["reusable"] is False
    assert manifest["skip_authorized"] is False
    assert failure["failure_class"] == "test-failure"
    assert failure["elapsed_seconds"] == 1.25
    assert set(manifest["batch_identity"]) == {
        "action_digest",
        "batch_index",
        "candidate_sha",
        "collection_manifest_sha256",
        "shard",
        "test_files_sha256",
    }
    serialized = json.dumps({"manifest": manifest, "failure": failure}).lower()
    assert "test_failure_example.py" not in serialized
    assert "/private/" not in serialized
    assert "do-not-store" not in serialized
    assert "pickle" not in serialized


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [
        (1, "test-failure"),
        (2, "interrupted"),
        (3, "pytest-internal-error"),
        (4, "pytest-usage-error"),
        (5, "no-tests-collected"),
        (74, "runner-nonzero"),
        (130, "interrupted"),
    ],
)
def test_failure_class_is_stable(returncode: int, expected: str) -> None:
    assert failure_class_for_returncode(returncode) == expected


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"returncode": 0}, "batch-not-failing"),
        ({"cleanup_returncode": 74}, "cleanup-incomplete"),
        ({"elapsed_seconds": -1.0}, "elapsed-invalid"),
        ({"elapsed_seconds": math.inf}, "elapsed-invalid"),
        ({"originating_run_id": "unsafe run"}, "run-id-invalid"),
    ],
)
def test_failure_writer_refuses_ineligible_evidence(
    tmp_path: Path,
    overrides: dict[str, object],
    reason: str,
) -> None:
    result = ShadowFailureReceiptWriter(tmp_path / "receipts").publish(
        _failure_request(tmp_path, **overrides)
    )

    assert result.published is False
    assert result.reason == reason


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("max_generations", 0),
        ("max_generations", 3),
        ("max_bytes", 0),
        ("max_bytes", MAX_RECEIPT_BYTES + 1),
        ("max_failure_receipts", 0),
        ("max_failure_receipts", MAX_FAILURE_RECEIPTS_PER_GENERATION + 1),
    ],
)
def test_failure_writer_rejects_configuration_outside_hard_bounds(
    tmp_path: Path,
    keyword: str,
    value: int,
) -> None:
    with pytest.raises(ValueError, match="outside the receipt bound"):
        ShadowFailureReceiptWriter(
            tmp_path / "receipts",
            **cast(Any, {keyword: value}),
        )


def test_failure_writer_accepts_unavailable_nodes_and_refuses_drift(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "receipts"
    unavailable = ShadowFailureReceiptWriter(cache).publish(
        _failure_request(tmp_path, failure_node_metadata=None)
    )
    assert unavailable.published is True
    assert unavailable.path is not None
    failure = json.loads(
        (unavailable.path / "failure.json").read_text(encoding="utf-8")
    )
    assert failure["failing_nodes"] == {
        "failing_node_count": 0,
        "failing_nodes_sha256": hashlib.sha256().hexdigest(),
        "nodes": [],
        "nodes_truncated": False,
        "schema_version": 1,
        "status": "unavailable",
    }

    drift = ShadowFailureReceiptWriter(tmp_path / "drift").publish(
        _failure_request(
            tmp_path,
            observed_action_identity=_identity(sha="b" * 40),
        )
    )
    assert drift.reason == "action-identity-drift"

    invalid_nodes = ShadowFailureReceiptWriter(tmp_path / "invalid-nodes").publish(
        _failure_request(tmp_path, failure_node_metadata={"status": "available"})
    )
    assert invalid_nodes.reason == "failure-nodes-invalid"


def test_failure_writer_refuses_invalid_batch_identity(tmp_path: Path) -> None:
    invalid = _identity()
    plan = cast(dict[str, object], invalid["plan"])
    plan["shard"] = "../escape"

    result = ShadowFailureReceiptWriter(tmp_path / "receipts").publish(
        _failure_request(
            tmp_path,
            action_identity=invalid,
            observed_action_identity=invalid,
        )
    )

    assert result.reason == "batch-identity-invalid"


def test_failure_writer_reuses_valid_receipt_and_rejects_corrupt_existing(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "receipts"
    request = _failure_request(tmp_path)
    first = ShadowFailureReceiptWriter(cache).publish(request)
    assert first.published is True
    assert first.path is not None

    duplicate = ShadowFailureReceiptWriter(cache).publish(request)
    assert duplicate.published is False
    assert duplicate.reason == "already-present"
    assert duplicate.path == first.path

    (first.path / "failure.json").write_text("{}\n", encoding="utf-8")
    corrupt = ShadowFailureReceiptWriter(cache).publish(request)
    assert corrupt.published is False
    assert corrupt.reason == "existing-failure-corrupt"


@pytest.mark.parametrize(
    "mutation",
    [
        "complete-json",
        "failure-json",
        "manifest-json",
        "unsafe-mode",
        "unexpected-artifact",
    ],
)
def test_failure_validator_refuses_corrupt_or_unsafe_artifacts(
    tmp_path: Path,
    mutation: str,
) -> None:
    publication = ShadowFailureReceiptWriter(tmp_path / "receipts").publish(
        _failure_request(tmp_path)
    )
    assert publication.published is True
    assert publication.path is not None
    receipt = publication.path
    if mutation == "complete-json":
        (receipt / "complete.json").write_text("{}\n", encoding="utf-8")
    elif mutation == "failure-json":
        (receipt / "failure.json").write_text("{}\n", encoding="utf-8")
    elif mutation == "manifest-json":
        (receipt / "manifest.json").write_text("{}\n", encoding="utf-8")
    elif mutation == "unsafe-mode":
        (receipt / "manifest.json").chmod(0o644)
    else:
        unexpected = receipt / "payload.txt"
        unexpected.write_text("must never be retained\n", encoding="utf-8")
        unexpected.chmod(0o600)

    validation = validate_failure_receipt(
        receipt,
        expected_action_digest=canonical_json_sha256(_identity()),
    )

    assert validation.valid is False


@pytest.mark.parametrize(
    ("layout", "expected_reason"),
    [
        ("cache-symlink", "cache-root-symlink"),
        ("parent-symlink", "cache-parent-symlink"),
        ("unsafe-cache", "cache-root-unsafe-mode"),
        ("version-symlink", "cache-layout-invalid"),
        ("invalid-generation", "cache-layout-invalid"),
        ("failures-symlink", "cache-size-unknown"),
        ("invalid-failure", "failure-layout-invalid"),
    ],
)
def test_failure_writer_refuses_ambiguous_or_unsafe_layouts(
    tmp_path: Path,
    layout: str,
    expected_reason: str,
) -> None:
    cache = tmp_path / "receipts"
    target = tmp_path / "symlink-target"
    target.mkdir()
    target.chmod(0o700)
    if layout == "cache-symlink":
        cache.symlink_to(target, target_is_directory=True)
    elif layout == "parent-symlink":
        parent = tmp_path / "linked-parent"
        parent.symlink_to(target, target_is_directory=True)
        cache = parent / "receipts"
    else:
        cache.mkdir(mode=0o700)
        if layout == "unsafe-cache":
            cache.chmod(0o755)
        elif layout == "version-symlink":
            (cache / "v1").symlink_to(target, target_is_directory=True)
        else:
            version = cache / "v1"
            version.mkdir(mode=0o700)
            if layout == "invalid-generation":
                (version / "not-a-sha").mkdir(mode=0o700)
            else:
                generation = version / _SHA
                generation.mkdir(mode=0o700)
                failures = generation / "failures"
                if layout == "failures-symlink":
                    failures.symlink_to(target, target_is_directory=True)
                else:
                    failures.mkdir(mode=0o700)
                    (failures / "not-a-digest").mkdir(mode=0o700)

    result = ShadowFailureReceiptWriter(cache).publish(_failure_request(tmp_path))

    assert result.published is False
    assert result.reason == expected_reason


def test_failure_writer_removes_partial_atomic_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "receipts"

    def fail_write(_path: Path, _payload: object) -> None:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(receipt_module, "_write_json", fail_write)
    result = ShadowFailureReceiptWriter(cache).publish(_failure_request(tmp_path))

    assert result.published is False
    assert result.reason == "publication-failed"
    assert not (cache / "v1" / _SHA).exists()


def test_failure_writer_shares_generation_byte_and_count_bounds(tmp_path: Path) -> None:
    limited_bytes = ShadowFailureReceiptWriter(
        tmp_path / "byte-receipts",
        max_bytes=1,
    ).publish(_failure_request(tmp_path))
    assert MAX_RECEIPT_BYTES == 2 * 1024**3
    assert limited_bytes.reason == "byte-limit"

    cache = tmp_path / "generation-receipts"
    for sha in ("b" * 40, "c" * 40):
        generation = cache / "v1" / sha
        generation.mkdir(parents=True, mode=0o700)
        cache.chmod(0o700)
        (cache / "v1").chmod(0o700)
        generation.chmod(0o700)
    generation_result = ShadowFailureReceiptWriter(cache).publish(
        _failure_request(tmp_path)
    )
    assert generation_result.reason == "generation-limit"

    count_cache = tmp_path / "count-receipts"
    writer = ShadowFailureReceiptWriter(count_cache, max_failure_receipts=1)
    assert writer.publish(_failure_request(tmp_path)).published is True
    count_result = writer.publish(
        _failure_request(tmp_path, elapsed_seconds=2.0)
    )
    assert MAX_FAILURE_RECEIPTS_PER_GENERATION == 256
    assert count_result.reason == "failure-receipt-limit"


def test_replay_auditor_always_rejects_failure_receipt(tmp_path: Path) -> None:
    cache = tmp_path / "batch-receipts"
    publication = ShadowFailureReceiptWriter(cache).publish(
        _failure_request(tmp_path)
    )
    assert publication.published is True
    coverage = tmp_path / "coverage.data"
    junit = tmp_path / "passing.xml"
    _write_branch_coverage(coverage)
    _write_passing_junit(junit)
    pass_outcomes = normalize_junit_outcomes(junit)
    pass_publication = ShadowBatchReceiptWriter(cache).publish(
        BatchReceiptRequest(
            action_identity=_identity(),
            observed_action_identity=_identity(),
            coverage_path=coverage,
            outcome_manifest=pass_outcomes,
            originating_run_id="passing-run",
            started_at="2026-10-07T00:00:00Z",
            completed_at="2026-10-07T00:00:01Z",
            returncode=0,
            cleanup_returncode=0,
        )
    )
    assert pass_publication.published is True

    result = ShadowReplayAuditor(cache, candidate_sha=_SHA).audit(
        ReplayAuditRequest(
            action_identity=_identity(),
            observed_action_identity=_identity(),
            coverage_path=coverage,
            outcome_manifest=pass_outcomes,
            returncode=0,
            cleanup_returncode=0,
        )
    )

    assert result.eligible is False
    assert result.reason == "prior-failure-non-reusable"
    assert result.skip_authorized is False


def test_progress_surfaces_failure_receipt_without_claiming_green() -> None:
    tracker = ShadowGateProgress(candidate_sha=_SHA, batches=[_progress_batch()])

    summary = tracker.record(
        ProgressExecution(
            batch=_progress_batch(),
            passed=False,
            receipt_status="failure",
        )
    )

    assert summary["progress"]["executed"] == 1
    assert summary["progress"]["failed"] == 1
    assert summary["progress"]["failure_receipts"] == 1
    assert summary["progress"]["ineligible"] == 0
    assert summary["gate_result"] == "unknown"
    assert summary["overall_green"] is None
    assert summary["terminal_phases_complete"] is False
    assert summary["skips"] == 0


def test_serial_runner_publishes_failure_only_after_owned_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
        lambda _shard: ["tests/unit/test_failure_example.py"],
    )
    monkeypatch.setattr(serial_runner, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(serial_runner, "_git_output", lambda *_args: (0, _SHA))
    monkeypatch.setattr(serial_runner, "_interpreter_identity", lambda: {"python": "same"})
    monkeypatch.setattr(
        serial_runner,
        "_interpreter_is_unchanged",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        serial_runner,
        "_disk_headroom_available",
        lambda *_args, **_kwargs: True,
    )
    owned_roots: list[Path] = []

    def owned_tmpdir(_label: str) -> Path:
        owned = tmp_path / f"owned-{len(owned_roots)}"
        owned.mkdir()
        owned_roots.append(owned)
        return owned

    def failed_pytest(command: list[str], **_kwargs: object) -> int:
        junit = next(
            Path(argument.removeprefix("--junitxml="))
            for argument in command
            if argument.startswith("--junitxml=")
        )
        junit.parent.mkdir(parents=True, exist_ok=True)
        _write_failure_junit(junit)
        return 1

    def cleanup(path: Path, **_kwargs: object) -> int:
        shutil.rmtree(path)
        return 0

    monkeypatch.setattr(serial_runner, "_owned_socket_safe_tmpdir", owned_tmpdir)
    monkeypatch.setattr(serial_runner, "_run_owned_pytest", failed_pytest)
    monkeypatch.setattr(serial_runner, "_save_shard_coverage", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(serial_runner, "_cleanup_owned_tmpdir_safely", cleanup)
    published: list[FailureReceiptRequest] = []

    class RecordingFailureWriter:
        def publish(self, request: FailureReceiptRequest) -> ReceiptPublication:
            assert all(not path.exists() for path in owned_roots)
            assert not any((resource_root / "workspaces").glob("*/batch-001"))
            published.append(request)
            return ReceiptPublication(
                True,
                "published",
                path=tmp_path / "failure-receipt",
                action_digest=canonical_json_sha256(_identity()),
            )

    progress = ShadowGateProgress(candidate_sha=_SHA, batches=[_progress_batch()])
    session = serial_runner.BatchReceiptSession(
        writer=ShadowBatchReceiptWriter(tmp_path / "pass-receipts"),
        failure_writer=cast(ShadowFailureReceiptWriter, RecordingFailureWriter()),
        run_id="run-1",
        expected_identity=lambda *_args: _identity(),
        observed_identity=lambda *_args: _identity(),
        progress=progress,
    )

    result = serial_runner.run(
        ["unit-1a1"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
        receipt_session=session,
    )

    assert result == 1
    assert len(published) == 1
    assert published[0].returncode == 1
    assert published[0].cleanup_returncode == 0
    assert published[0].elapsed_seconds >= 0
    output = capsys.readouterr().out
    assert "BATCH-FAILURE-RECEIPT-SHADOW status=published" in output
    progress_line = next(
        line for line in output.splitlines() if line.startswith("GATE-PROGRESS-SHADOW {")
    )
    payload = json.loads(progress_line.removeprefix("GATE-PROGRESS-SHADOW "))
    assert payload["progress"]["failed"] == 1
    assert payload["progress"]["failure_receipts"] == 1
    assert payload["overall_green"] is None
    assert payload["skips"] == 0


def test_cli_can_disable_failure_receipts_without_disabling_pass_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = {
        "head_sha": _SHA,
        "expected_sha": _SHA,
        "branch": "feature",
        "clean": True,
        "exact_sha": True,
        "queries_ok": True,
    }
    received: list[bool] = []
    session = object()

    def create_session(**kwargs: object) -> object:
        enabled = kwargs["failure_receipts_enabled"]
        assert isinstance(enabled, bool)
        received.append(enabled)
        return session

    monkeypatch.setattr(serial_runner, "_repository_identity", lambda **_kwargs: identity)
    monkeypatch.setattr(serial_runner, "_attestation_pairing", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(serial_runner, "_create_shadow_receipt_session", create_session)
    monkeypatch.setattr(serial_runner, "run", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        serial_runner,
        "_write_terminal_attestation",
        lambda *_args, **_kwargs: None,
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
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-1a1",
            "--skip-isolated",
            "--skip-aggregate",
            "--no-shadow-failure-receipts",
        ],
    )

    assert serial_runner.main() == 0
    assert received == [False]
    output = capsys.readouterr().out
    assert (
        "BATCH-FAILURE-RECEIPT-SHADOW status=disabled "
        "reason=operator-off skips=0"
    ) in output
    assert "BATCH-RECEIPT-SHADOW status=enabled" in output
