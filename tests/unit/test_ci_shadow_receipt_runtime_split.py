"""Structural and behavioral contracts for the serial-runner receipt split."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from scripts import ci_shadow_receipt_runtime as receipt_runtime
from scripts import run_ci_shards_serial as serial_runner
from scripts.ci_batch_receipts import ShadowBatchReceiptWriter
from scripts.ci_batch_replay_audit import (
    ReplayAuditRequest,
    ReplayAuditResult,
    ShadowReplayAuditor,
)
from scripts.ci_gate_progress import ProgressBatch

_SHA = "a" * 40


class _StaticAuditor:
    def __init__(self, result: ReplayAuditResult) -> None:
        self._result = result

    def audit(self, _request: ReplayAuditRequest) -> ReplayAuditResult:
        return self._result


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
