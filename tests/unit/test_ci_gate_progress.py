"""Fail-closed contracts for bounded shadow gate progress and ETA evidence."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from coverage import CoverageData
from scripts import run_ci_shards_serial as serial_runner
from scripts.ci_batch_receipts import (
    BatchReceiptRequest,
    ShadowBatchReceiptWriter,
    canonical_json_sha256,
)
from scripts.ci_gate_progress import (
    MAX_PROGRESS_BATCHES,
    MAX_PROGRESS_OUTPUT_BYTES,
    MAX_TIMING_SAMPLES,
    ProgressBatch,
    ProgressExecution,
    ShadowGateProgress,
)
from scripts.ci_receipt_auth import ReceiptSigner, ReceiptTrustPolicy

_SHA = "a" * 40
_AUTH_KEY = bytes(range(32))
_AUTH_SIGNER = ReceiptSigner(_AUTH_KEY, 1_000, 300)
_AUTH_POLICY = ReceiptTrustPolicy(
    trusted_keys={_AUTH_SIGNER.signer_id: _AUTH_KEY},
    verification_epoch=1_100,
)


def _identity(batch_index: int, *, candidate_sha: str = _SHA) -> dict[str, object]:
    files = [f"tests/unit/test_batch_{batch_index}.py"]
    files_digest = canonical_json_sha256(files)
    return {
        "schema_version": 1,
        "source": {
            "candidate_sha": candidate_sha,
            "expected_sha": candidate_sha,
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
            "batch_index": batch_index,
            "max_files_per_batch": 16,
            "shard_plan_sha256": "1" * 64,
            "complete_plan_sha256": "2" * 64,
            "collection_manifest_kind": "canonical-test-file-plan-v1",
            "collection_manifest_sha256": files_digest,
            "execution_policy_sha256": "4" * 64,
        },
        "runner": {"implementation_sha256": "5" * 64},
        "toolchain": {"installed_distributions_sha256": "6" * 64},
        "plugins": {"inventory_sha256": "7" * 64},
        "coverage": {"branch": True, "config_sha256": "8" * 64},
        "platform": {"system": "Darwin"},
        "environment": {
            "allowlist": {"CI": "true"},
            "undeclared_names_sha256": "9" * 64,
            "restricted_inputs_present": False,
            "values_outside_allowlist_stored": False,
        },
        "external_inputs": [],
    }


def _batch(batch_index: int) -> ProgressBatch:
    identity = _identity(batch_index)
    source = identity["source"]
    plan = identity["plan"]
    assert isinstance(source, dict)
    assert isinstance(plan, dict)
    return ProgressBatch(
        shard="unit-1a1",
        batch_index=batch_index,
        test_files_sha256=str(source["test_files_sha256"]),
        collection_manifest_sha256=str(plan["collection_manifest_sha256"]),
    )


def _write_coverage(path: Path) -> None:
    data = CoverageData(basename=str(path))
    data.add_arcs({"src/general_ludd/example.py": {(1, 2), (2, -1)}})
    data.write()
    data.close()


def _outcomes(batch_index: int) -> dict[str, object]:
    node = canonical_json_sha256(f"node-{batch_index}")
    return {
        "schema_version": 1,
        "counts": {
            "errors": 0,
            "failures": 0,
            "passed": 1,
            "skipped": 0,
            "tests": 1,
        },
        "node_id_sha256": canonical_json_sha256([node]),
        "terminal_outcome_sha256": canonical_json_sha256(
            [{"node_sha256": node, "outcome": "passed"}]
        ),
    }


def _publish(
    cache: Path,
    tmp_path: Path,
    batch_index: int,
    *,
    started_at: str,
    completed_at: str,
    candidate_sha: str = _SHA,
) -> Path:
    identity = _identity(batch_index, candidate_sha=candidate_sha)
    coverage = tmp_path / f"coverage-{candidate_sha[:4]}-{batch_index}"
    _write_coverage(coverage)
    publication = ShadowBatchReceiptWriter(cache, signer=_AUTH_SIGNER).publish(
        BatchReceiptRequest(
            action_identity=identity,
            observed_action_identity=identity,
            coverage_path=coverage,
            outcome_manifest=_outcomes(batch_index),
            originating_run_id=f"run-{batch_index}",
            started_at=started_at,
            completed_at=completed_at,
            returncode=0,
            cleanup_returncode=0,
        )
    )
    assert publication.published is True
    assert publication.path is not None
    return publication.path


def _tracker(**kwargs: Any) -> ShadowGateProgress:
    return ShadowGateProgress(trust_policy=_AUTH_POLICY, **kwargs)


def test_progress_eta_uses_only_completed_receipt_durations_and_is_labeled_estimate(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "receipts"
    first = _publish(
        cache,
        tmp_path,
        1,
        started_at="2026-10-07T00:00:00Z",
        completed_at="2026-10-07T00:00:10Z",
    )
    second = _publish(
        cache,
        tmp_path,
        2,
        started_at="2026-10-07T00:01:00Z",
        completed_at="2026-10-07T00:01:20Z",
    )
    tracker = _tracker(
        candidate_sha=_SHA,
        batches=[_batch(1), _batch(2), _batch(3)],
        completed_receipts=[first, second],
    )

    summary = tracker.record(
        ProgressExecution(
            batch=_batch(1),
            passed=True,
            receipt_status="eligible",
        )
    )

    assert summary["progress"] == {
        "executed": 1,
        "failed": 0,
        "failure_receipts": 0,
        "ineligible": 0,
        "missing": 0,
        "passed": 1,
        "receipt_candidates": 1,
        "remaining": 2,
        "total": 3,
    }
    assert summary["timing"] == {
        "basis": "completed-shadow-receipts",
        "authentication_status": "verified",
        "eta_kind": "estimate",
        "eta_seconds_estimate": 35.0,
        "evidence_status": "valid",
        "samples_retained": 2,
        "samples_truncated": False,
    }
    assert summary["gate_result"] == "unknown"
    assert summary["overall_green"] is None
    assert summary["terminal_phases_complete"] is False
    assert summary["skips"] == 0
    rendered = tracker.render(summary)
    assert len(rendered.encode("utf-8")) <= MAX_PROGRESS_OUTPUT_BYTES
    assert json.loads(rendered) == summary


def test_progress_distinguishes_pass_fail_missing_and_ineligible_without_green(
    tmp_path: Path,
) -> None:
    tracker = _tracker(
        candidate_sha=_SHA,
        batches=[_batch(1), _batch(2), _batch(3)],
    )

    tracker.record(
        ProgressExecution(batch=_batch(1), passed=False, receipt_status="missing")
    )
    tracker.record(
        ProgressExecution(batch=_batch(2), passed=True, receipt_status="ineligible")
    )
    summary = tracker.record(
        ProgressExecution(batch=_batch(3), passed=True, receipt_status="eligible")
    )

    assert summary["progress"] == {
        "executed": 3,
        "failed": 1,
        "failure_receipts": 0,
        "ineligible": 1,
        "missing": 1,
        "passed": 2,
        "receipt_candidates": 1,
        "remaining": 0,
        "total": 3,
    }
    assert summary["timing"]["eta_seconds_estimate"] is None
    assert summary["gate_result"] == "unknown"
    assert summary["overall_green"] is None
    assert summary["terminal_phases_complete"] is False
    assert summary["skips"] == 0


@pytest.mark.parametrize(
    ("evidence", "reason"),
    [
        ("mixed-candidate", "rejected-mixed-candidate"),
        ("malformed", "rejected-malformed-receipt"),
        ("foreign-plan", "rejected-foreign-plan"),
        ("ambiguous", "rejected-ambiguous-receipts"),
    ],
)
def test_progress_rejects_untrusted_or_ambiguous_timing_evidence(
    tmp_path: Path,
    evidence: str,
    reason: str,
) -> None:
    cache = tmp_path / "receipts"
    first = _publish(
        cache,
        tmp_path,
        1 if evidence != "foreign-plan" else 3,
        started_at="2026-10-07T00:00:00Z",
        completed_at="2026-10-07T00:00:10Z",
        candidate_sha="b" * 40 if evidence == "mixed-candidate" else _SHA,
    )
    receipts = [first]
    if evidence == "malformed":
        (first / "manifest.json").write_text("{}\n", encoding="utf-8")
    elif evidence == "ambiguous":
        second_identity = copy.deepcopy(_identity(1))
        runner = second_identity["runner"]
        assert isinstance(runner, dict)
        runner["implementation_sha256"] = "f" * 64
        coverage = tmp_path / "coverage-ambiguous"
        _write_coverage(coverage)
        publication = ShadowBatchReceiptWriter(cache, signer=_AUTH_SIGNER).publish(
            BatchReceiptRequest(
                action_identity=second_identity,
                observed_action_identity=second_identity,
                coverage_path=coverage,
                outcome_manifest=_outcomes(1),
                originating_run_id="ambiguous-run",
                started_at="2026-10-07T00:02:00Z",
                completed_at="2026-10-07T00:02:12Z",
                returncode=0,
                cleanup_returncode=0,
            )
        )
        assert publication.path is not None
        receipts.append(publication.path)

    tracker = _tracker(
        candidate_sha=_SHA,
        batches=[_batch(1), _batch(2)],
        completed_receipts=receipts,
    )
    summary = tracker.record(
        ProgressExecution(batch=_batch(1), passed=True, receipt_status="ineligible")
    )

    assert summary["timing"]["evidence_status"] == reason
    assert summary["timing"]["samples_retained"] == 0
    assert summary["timing"]["eta_seconds_estimate"] is None


def test_progress_caps_plan_samples_and_machine_output(tmp_path: Path) -> None:
    cache = tmp_path / "receipts"
    receipts = [
        _publish(
            cache,
            tmp_path,
            index,
            started_at=f"2026-10-07T00:0{index}:00Z",
            completed_at=f"2026-10-07T00:0{index}:{index:02d}Z",
        )
        for index in range(1, 4)
    ]
    tracker = _tracker(
        candidate_sha=_SHA,
        batches=[_batch(1), _batch(2), _batch(3)],
        completed_receipts=receipts,
        max_timing_samples=2,
    )

    summary = tracker.record(
        ProgressExecution(batch=_batch(1), passed=True, receipt_status="eligible")
    )

    assert MAX_TIMING_SAMPLES == 64
    assert MAX_PROGRESS_BATCHES == 512
    assert summary["timing"]["samples_retained"] == 2
    assert summary["timing"]["samples_truncated"] is True
    assert len(tracker.render(summary).encode("utf-8")) <= MAX_PROGRESS_OUTPUT_BYTES


@pytest.mark.parametrize(
    ("candidate_sha", "batches", "max_timing_samples"),
    [
        ("short", [_batch(1)], 1),
        (_SHA, [], 1),
        (_SHA, [_batch(1)] * (MAX_PROGRESS_BATCHES + 1), 1),
        (_SHA, [_batch(1)], 0),
        (_SHA, [_batch(1)], MAX_TIMING_SAMPLES + 1),
        (_SHA, [_batch(1), _batch(1)], 1),
    ],
)
def test_progress_rejects_invalid_or_unbounded_plans(
    candidate_sha: str,
    batches: list[ProgressBatch],
    max_timing_samples: int,
) -> None:
    with pytest.raises(ValueError):
        _tracker(
            candidate_sha=candidate_sha,
            batches=batches,
            max_timing_samples=max_timing_samples,
        )


def test_progress_rejects_unknown_duplicate_or_invalid_execution_status() -> None:
    tracker = _tracker(candidate_sha=_SHA, batches=[_batch(1)])
    valid = ProgressExecution(batch=_batch(1), passed=True, receipt_status="eligible")
    tracker.record(valid)

    with pytest.raises(ValueError, match="already recorded"):
        tracker.record(valid)
    with pytest.raises(ValueError, match="not in the canonical plan"):
        tracker.record(
            ProgressExecution(
                batch=_batch(2),
                passed=True,
                receipt_status="missing",
            )
        )
    with pytest.raises(ValueError, match="receipt_status"):
        ProgressExecution(
            batch=_batch(1),
            passed=True,
            receipt_status=cast(Any, "unknown"),
        )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"shard": "../escape"}, "shard"),
        ({"batch_index": 0}, "batch_index"),
        ({"test_files_sha256": "short"}, "test_files_sha256"),
        ({"collection_manifest_sha256": "short"}, "collection_manifest_sha256"),
    ],
)
def test_progress_rejects_invalid_batch_coordinates(
    kwargs: dict[str, Any],
    match: str,
) -> None:
    values: dict[str, Any] = {
        "shard": "unit-1a1",
        "batch_index": 1,
        "test_files_sha256": "1" * 64,
        "collection_manifest_sha256": "2" * 64,
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=match):
        ProgressBatch(**values)


def test_progress_rejects_non_boolean_execution_result() -> None:
    with pytest.raises(ValueError, match="passed"):
        ProgressExecution(
            batch=_batch(1),
            passed=cast(bool, 1),
            receipt_status="eligible",
        )


@pytest.mark.parametrize("shape", ["missing-receipt", "missing-manifest", "invalid-json", "non-object"])
def test_progress_rejects_malformed_receipt_shapes(
    tmp_path: Path,
    shape: str,
) -> None:
    receipt = tmp_path / "receipt"
    if shape != "missing-receipt":
        receipt.mkdir(mode=0o700)
    if shape in {"invalid-json", "non-object"}:
        manifest = receipt / "manifest.json"
        manifest.write_text("not-json\n" if shape == "invalid-json" else "[]\n", encoding="utf-8")
        manifest.chmod(0o600)

    tracker = _tracker(
        candidate_sha=_SHA,
        batches=[_batch(1)],
        completed_receipts=[receipt],
    )

    assert tracker.summary()["timing"]["evidence_status"] == "rejected-malformed-receipt"
    assert tracker.summary()["timing"]["eta_seconds_estimate"] is None


@pytest.mark.parametrize(
    ("started_at", "completed_at"),
    [
        ("not-a-time", "2026-10-07T00:00:01Z"),
        ("2026-13-07T00:00:00Z", "2026-10-07T00:00:01Z"),
        ("2026-10-07T00:00:02Z", "2026-10-07T00:00:01Z"),
        ("2026-10-07T00:00:00Z", "2026-10-09T00:00:01Z"),
    ],
)
def test_progress_rejects_invalid_or_unbounded_receipt_durations(
    tmp_path: Path,
    started_at: str,
    completed_at: str,
) -> None:
    receipt = _publish(
        tmp_path / "receipts",
        tmp_path,
        1,
        started_at=started_at,
        completed_at=completed_at,
    )

    tracker = _tracker(
        candidate_sha=_SHA,
        batches=[_batch(1)],
        completed_receipts=[receipt],
    )

    assert tracker.summary()["timing"]["evidence_status"] == "rejected-malformed-receipt"
    assert tracker.summary()["timing"]["eta_seconds_estimate"] is None


def test_progress_rejects_unbounded_receipt_enumeration(tmp_path: Path) -> None:
    receipt = _publish(
        tmp_path / "receipts",
        tmp_path,
        1,
        started_at="2026-10-07T00:00:00Z",
        completed_at="2026-10-07T00:00:01Z",
    )

    tracker = _tracker(
        candidate_sha=_SHA,
        batches=[_batch(1)],
        completed_receipts=[receipt] * (MAX_PROGRESS_BATCHES + 1),
    )

    assert tracker.summary()["timing"]["evidence_status"] == "rejected-unbounded-evidence"
    assert tracker.summary()["timing"]["samples_retained"] == 0


def test_progress_requires_new_receipt_to_match_executed_batch(tmp_path: Path) -> None:
    receipt = _publish(
        tmp_path / "receipts",
        tmp_path,
        2,
        started_at="2026-10-07T00:00:00Z",
        completed_at="2026-10-07T00:00:01Z",
    )
    tracker = _tracker(
        candidate_sha=_SHA,
        batches=[_batch(1), _batch(2)],
    )

    summary = tracker.record(
        ProgressExecution(batch=_batch(1), passed=True, receipt_status="missing"),
        completed_receipt=receipt,
    )

    assert summary["timing"]["evidence_status"] == "rejected-execution-receipt-mismatch"
    assert summary["timing"]["eta_seconds_estimate"] is None


def test_progress_accepts_matching_new_receipt_and_ignores_exact_duplicate(
    tmp_path: Path,
) -> None:
    receipt = _publish(
        tmp_path / "receipts",
        tmp_path,
        1,
        started_at="2026-10-07T00:00:00Z",
        completed_at="2026-10-07T00:00:03Z",
    )
    tracker = _tracker(
        candidate_sha=_SHA,
        batches=[_batch(1), _batch(2)],
        completed_receipts=[receipt],
    )

    summary = tracker.record(
        ProgressExecution(batch=_batch(1), passed=True, receipt_status="eligible"),
        completed_receipt=receipt,
    )

    assert summary["timing"]["evidence_status"] == "valid"
    assert summary["timing"]["samples_retained"] == 1
    assert summary["timing"]["eta_seconds_estimate"] == 3.0


def test_progress_render_refuses_oversized_payload() -> None:
    tracker = _tracker(candidate_sha=_SHA, batches=[_batch(1)])

    with pytest.raises(ValueError, match="output exceeds"):
        tracker.render({"padding": "x" * MAX_PROGRESS_OUTPUT_BYTES})


def test_progress_output_contains_no_receipt_paths_test_names_or_secrets(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "private-token-path"
    receipt = _publish(
        cache,
        tmp_path,
        1,
        started_at="2026-10-07T00:00:00Z",
        completed_at="2026-10-07T00:00:10Z",
    )
    tracker = _tracker(
        candidate_sha=_SHA,
        batches=[_batch(1)],
        completed_receipts=[receipt],
    )

    rendered = tracker.render(
        tracker.record(
            ProgressExecution(
                batch=_batch(1),
                passed=True,
                receipt_status="eligible",
            )
        )
    )

    assert "private-token-path" not in rendered
    assert "test_batch_1" not in rendered
    assert "secret" not in rendered.lower()
    assert "pickle" not in rendered.lower()


def test_runner_emits_one_bounded_machine_readable_progress_marker(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tracker = _tracker(candidate_sha=_SHA, batches=[_batch(1)])
    session = serial_runner.BatchReceiptSession(
        writer=object(),  # type: ignore[arg-type]
        run_id="run-1",
        expected_identity=lambda *_args: _identity(1),
        observed_identity=lambda *_args: _identity(1),
        progress=tracker,
    )

    serial_runner._report_shadow_gate_progress(
        session,
        batch=_batch(1),
        passed=True,
        receipt_status="missing",
        completed_receipt=None,
    )

    marker = capsys.readouterr().out.strip()
    prefix = "GATE-PROGRESS-SHADOW "
    assert marker.startswith(prefix)
    payload = json.loads(marker.removeprefix(prefix))
    assert payload["progress"]["executed"] == 1
    assert payload["progress"]["missing"] == 1
    assert payload["timing"]["eta_kind"] == "estimate"
    assert payload["gate_result"] == "unknown"
    assert payload["overall_green"] is None
    assert payload["skips"] == 0
    assert len(marker.encode("utf-8")) <= len(prefix) + MAX_PROGRESS_OUTPUT_BYTES


def test_cli_can_disable_progress_without_disabling_receipts_or_replay_audit(
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
    received: list[tuple[bool, bool]] = []
    session = object()

    def create_session(**kwargs: object) -> object:
        replay_enabled = kwargs["replay_audit_enabled"]
        progress_enabled = kwargs["progress_enabled"]
        assert isinstance(replay_enabled, bool)
        assert isinstance(progress_enabled, bool)
        received.append((replay_enabled, progress_enabled))
        return session

    monkeypatch.setattr(
        serial_runner,
        "_repository_identity",
        lambda **_kwargs: identity,
    )
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
            "--no-shadow-gate-progress",
        ],
    )

    assert serial_runner.main() == 0
    assert received == [(True, False)]
    output = capsys.readouterr().out
    assert "GATE-PROGRESS-SHADOW status=disabled reason=operator-off skips=0" in output
    assert "BATCH-REPLAY-SHADOW status=enabled mode=audit-only" in output
