"""Structural and behavioral contracts for the serial-runner receipt split."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from scripts import ci_shadow_receipt_runtime as receipt_runtime
from scripts import run_ci_shards_serial as serial_runner
from scripts.ci_batch_receipts import (
    ShadowBatchReceiptWriter,
    ShadowFailureReceiptWriter,
)
from scripts.ci_batch_replay_audit import (
    ReplayAuditRequest,
    ReplayAuditResult,
    ShadowReplayAuditor,
)
from scripts.ci_gate_progress import ProgressBatch, ShadowGateProgress

_SHA = "a" * 40


class _StaticAuditor:
    def __init__(self, result: ReplayAuditResult) -> None:
        self._result = result

    def audit(self, _request: ReplayAuditRequest) -> ReplayAuditResult:
        return self._result


class _RaisingAuditor:
    def audit(self, _request: ReplayAuditRequest) -> ReplayAuditResult:
        raise ValueError("invalid replay evidence")


class _RaisingFailureWriter:
    def publish(self, _request: object) -> object:
        raise RuntimeError("publication failed")


class _RaisingProgress:
    def record(self, *_args: object, **_kwargs: object) -> object:
        raise OSError("progress unavailable")


def _receipt_session(*, auditor: object | None = None) -> receipt_runtime.BatchReceiptSession:
    def identity(
        _shard: str,
        _batch_index: int,
        _files: list[str],
    ) -> dict[str, object]:
        return {}

    return receipt_runtime.BatchReceiptSession(
        writer=cast(ShadowBatchReceiptWriter, object()),
        run_id="run-id",
        expected_identity=identity,
        observed_identity=identity,
        auditor=(None if auditor is None else cast(ShadowReplayAuditor, auditor)),
    )


def test_receipt_runtime_split_keeps_runner_bounded_and_compatibility_exports() -> None:
    runner_path = Path(serial_runner.__file__)

    assert len(runner_path.read_text(encoding="utf-8").splitlines()) < 2_500
    assert serial_runner.BatchReceiptSession is receipt_runtime.BatchReceiptSession
    assert (
        serial_runner._report_shadow_replay_eligibility
        is receipt_runtime.report_shadow_replay_eligibility
    )
    assert (
        serial_runner._report_shadow_gate_progress
        is receipt_runtime.report_shadow_gate_progress
    )


def test_shadow_configuration_preserves_session_factory_monkeypatch_seam(
    capsys: pytest.CaptureFixture[str],
) -> None:
    created: list[dict[str, object]] = []
    sentinel = object()

    def create_session(**kwargs: object) -> object:
        created.append(kwargs)
        return sentinel

    result = receipt_runtime.configure_shadow_receipts(
        source_release_eligible=True,
        writer_enabled=True,
        create_session=create_session,
        repository_identity={"expected_sha": _SHA},
        shards=["unit-1a1"],
        pytest_args=["-W", "error"],
        max_files_per_batch=16,
        heartbeat_seconds=30.0,
        no_progress_seconds=600.0,
        replay_audit_enabled=True,
        progress_enabled=True,
        failure_receipts_enabled=True,
        receipt_authentication_enabled=True,
    )

    assert result is sentinel
    assert created == [
        {
            "repository_identity": {"expected_sha": _SHA},
            "shards": ["unit-1a1"],
            "pytest_args": ["-W", "error"],
            "max_files_per_batch": 16,
            "heartbeat_seconds": 30.0,
            "no_progress_seconds": 600.0,
            "replay_audit_enabled": True,
            "progress_enabled": True,
            "failure_receipts_enabled": True,
            "receipt_authentication_enabled": True,
            "receipt_admission_enabled": False,
            "batch_workers": 1,
        }
    ]
    output = capsys.readouterr().out
    assert "BATCH-RECEIPT-SHADOW status=enabled" in output
    assert "BATCH-RECEIPT-AUTH-SHADOW status=enabled" in output
    assert "BATCH-FAILURE-RECEIPT-SHADOW status=enabled" in output
    assert "BATCH-REPLAY-SHADOW status=enabled" in output
    assert "GATE-PROGRESS-SHADOW status=enabled" in output
    assert "skips=0" in output


def test_shadow_configuration_writer_off_never_calls_factory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden_factory(**_kwargs: object) -> object:
        raise AssertionError("writer-off must not construct a session")

    result = receipt_runtime.configure_shadow_receipts(
        source_release_eligible=True,
        writer_enabled=False,
        create_session=forbidden_factory,
        repository_identity={"expected_sha": _SHA},
        shards=["unit-1a1"],
        pytest_args=[],
        max_files_per_batch=16,
        heartbeat_seconds=30.0,
        no_progress_seconds=600.0,
        replay_audit_enabled=True,
        progress_enabled=True,
        failure_receipts_enabled=True,
        receipt_authentication_enabled=True,
    )

    assert result is None
    output = capsys.readouterr().out
    assert "BATCH-RECEIPT-SHADOW status=disabled reason=operator-off" in output
    assert "BATCH-REPLAY-SHADOW status=disabled reason=writer-off skips=0" in output
    assert "GATE-PROGRESS-SHADOW status=disabled reason=writer-off skips=0" in output


def test_shadow_configuration_fails_closed_when_factory_rejects_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def rejecting_factory(**_kwargs: object) -> object:
        raise ValueError("unsafe identity")

    result = receipt_runtime.configure_shadow_receipts(
        source_release_eligible=True,
        writer_enabled=True,
        create_session=rejecting_factory,
        repository_identity={"expected_sha": _SHA},
        shards=["unit-1a1"],
        pytest_args=[],
        max_files_per_batch=16,
        heartbeat_seconds=30.0,
        no_progress_seconds=600.0,
        replay_audit_enabled=True,
        progress_enabled=True,
        failure_receipts_enabled=True,
        receipt_authentication_enabled=True,
    )

    assert result is None
    output = capsys.readouterr().out
    assert "BATCH-RECEIPT-SHADOW status=disabled reason=identity-ValueError" in output
    assert "BATCH-REPLAY-SHADOW status=disabled reason=identity-ValueError skips=0" in output
    assert "future_eligible=0 skips=0" in output
    assert "GATE-PROGRESS-SHADOW status=disabled reason=identity-ValueError skips=0" in output


def test_shadow_progress_without_tracker_is_a_quiet_noop(
    capsys: pytest.CaptureFixture[str],
) -> None:
    receipt_runtime.report_shadow_gate_progress(
        _receipt_session(),
        batch=ProgressBatch(
            shard="unit-1a1",
            batch_index=1,
            test_files_sha256="b" * 64,
            collection_manifest_sha256="c" * 64,
        ),
        passed=True,
        receipt_status="missing",
        completed_receipt=None,
    )

    assert capsys.readouterr().out == ""


def test_gate_owner_probe_fails_closed_without_masking_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not receipt_runtime.gate_owner_is_alive({"GLUDD_GATE_OWNER_PID": "bad"})
    assert not receipt_runtime.gate_owner_is_alive({"GLUDD_GATE_OWNER_PID": "1"})

    monkeypatch.setattr(receipt_runtime.os, "kill", lambda *_args: (_ for _ in ()).throw(ProcessLookupError()))
    assert not receipt_runtime.gate_owner_is_alive({"GLUDD_GATE_OWNER_PID": "42"})

    monkeypatch.setattr(receipt_runtime.os, "kill", lambda *_args: (_ for _ in ()).throw(PermissionError()))
    assert receipt_runtime.gate_owner_is_alive({"GLUDD_GATE_OWNER_PID": "42"})


def test_identity_helpers_reject_non_object_runtime_sections() -> None:
    bindings = replace(
        serial_runner._shadow_receipt_bindings(),
        interpreter_identity=lambda: {},
        build_runtime_identity=lambda **_kwargs: {"runner": []},
    )
    with pytest.raises(TypeError, match="runtime runner identity"):
        receipt_runtime.receipt_base_identity(
            bindings,
            repository_identity={},
            repository_state_identifier="state",
            pytest_args=[],
            max_files_per_batch=1,
            heartbeat_seconds=1.0,
            no_progress_seconds=2.0,
            include_uv_probe=False,
        )

    with pytest.raises(TypeError, match="source and plan"):
        receipt_runtime.batch_receipt_action_identity(
            bindings,
            {"source": [], "plan": {}},
            {"unit-1a1": "digest"},
            "complete",
            "unit-1a1",
            1,
            ["tests/unit/test_example.py"],
        )


def test_extracted_reporters_contain_dependency_exceptions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    replay_status = receipt_runtime.report_shadow_replay_eligibility(
        _receipt_session(auditor=_RaisingAuditor()),
        shard="unit-1a1",
        batch_index=1,
        action_identity={},
        observed_action_identity={},
        coverage_path=Path("unused-coverage"),
        outcome_manifest=None,
        returncode=0,
        cleanup_returncode=0,
    )
    progress_session = replace(
        _receipt_session(),
        progress=cast(ShadowGateProgress, _RaisingProgress()),
    )
    receipt_runtime.report_shadow_gate_progress(
        progress_session,
        batch=ProgressBatch(
            shard="unit-1a1",
            batch_index=1,
            test_files_sha256="b" * 64,
            collection_manifest_sha256="c" * 64,
        ),
        passed=False,
        receipt_status="ineligible",
        completed_receipt=None,
    )

    assert replay_status == "ineligible"
    output = capsys.readouterr().out
    assert "reason=auditor-ValueError" in output
    assert '"error":"progress-OSError"' in output


def test_failure_receipt_preconditions_and_exceptions_fail_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    base_bindings = replace(
        serial_runner._shadow_receipt_bindings(),
        resource_root=lambda: Path("unused-resource-root"),
        disk_headroom=lambda *_args, **_kwargs: True,
    )
    writer = cast(ShadowFailureReceiptWriter, object())
    session = replace(_receipt_session(), failure_writer=writer)
    arguments = {
        "shard": "unit-1a1",
        "batch_index": 1,
        "files": ["tests/unit/test_example.py"],
        "failure_node_metadata": None,
        "elapsed_seconds": 1.0,
        "returncode": 1,
    }

    assert not receipt_runtime.publish_shadow_failure_receipt(
        base_bindings,
        _receipt_session(),
        cleanup_returncode=0,
        **arguments,
    )
    assert not receipt_runtime.publish_shadow_failure_receipt(
        base_bindings,
        session,
        cleanup_returncode=1,
        **arguments,
    )

    def invalid_identity(
        _shard: str,
        _batch_index: int,
        _files: list[str],
    ) -> dict[str, object]:
        raise ValueError("invalid identity")

    invalid_session = replace(session, expected_identity=invalid_identity)
    assert not receipt_runtime.publish_shadow_failure_receipt(
        base_bindings,
        invalid_session,
        cleanup_returncode=0,
        **arguments,
    )
    no_disk_bindings = replace(
        base_bindings,
        disk_headroom=lambda *_args, **_kwargs: False,
    )
    assert not receipt_runtime.publish_shadow_failure_receipt(
        no_disk_bindings,
        session,
        cleanup_returncode=0,
        **arguments,
    )
    raising_session = replace(
        session,
        failure_writer=cast(ShadowFailureReceiptWriter, _RaisingFailureWriter()),
    )
    assert not receipt_runtime.publish_shadow_failure_receipt(
        base_bindings,
        raising_session,
        cleanup_returncode=0,
        **arguments,
    )

    output = capsys.readouterr().out
    assert "reason=cleanup-incomplete" in output
    assert "reason=identity-ValueError" in output
    assert "reason=disk-headroom" in output
    assert "reason=publisher-RuntimeError" in output


@pytest.mark.parametrize(
    ("result", "expected_status", "marker"),
    [
        (
            ReplayAuditResult(
                eligible=False,
                reason="unsafe-auditor-result",
                skip_authorized=True,
            ),
            "ineligible",
            "status=refused",
        ),
        (
            ReplayAuditResult(eligible=False, reason="no-prior-candidate"),
            "missing",
            "status=miss",
        ),
        (
            ReplayAuditResult(eligible=False, reason="candidate-drift"),
            "ineligible",
            "status=refused",
        ),
    ],
)
def test_extracted_replay_reporter_preserves_fail_closed_statuses(
    capsys: pytest.CaptureFixture[str],
    result: ReplayAuditResult,
    expected_status: str,
    marker: str,
) -> None:
    status = receipt_runtime.report_shadow_replay_eligibility(
        _receipt_session(auditor=_StaticAuditor(result)),
        shard="unit-1a1",
        batch_index=1,
        action_identity={},
        observed_action_identity={},
        coverage_path=Path("unused-coverage"),
        outcome_manifest=None,
        returncode=0,
        cleanup_returncode=0,
    )

    assert status == expected_status
    output = capsys.readouterr().out
    assert marker in output
    assert f"reason={result.reason}" in output or "skip-authorization-forbidden" in output
    assert "skips=0" in output
