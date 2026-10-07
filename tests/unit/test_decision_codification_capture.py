"""Signed, privacy-preserving producer capture for reusable agent decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from general_ludd.decision_codification.capture import (
    DecisionCaptureError,
    DecisionCaptureReceipt,
    DecisionOutcomeRecorder,
)
from general_ludd.decision_codification.schema import DecisionKind, VerifiedOutcome
from general_ludd.decision_codification.service import (
    AnalysisRejectionReason,
    DecisionLogAnalyzer,
)
from general_ludd.replay.schema import (
    ModelIdentityV1,
    RuntimeIdentityV1,
    SourceIdentityV1,
    canonical_replay_json,
)
from general_ludd.replay.store import RunBundleStore

KEY_ID = "decision-capture-test-key"
KEY = b"decision-capture-test-key-material"
POLICY_DIGEST = "sha256:" + "a" * 64
CONFIG_DIGEST = "sha256:" + "b" * 64
NOW = datetime(2026, 10, 7, 20, tzinfo=UTC)


def _features(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "work_type": "code",
        "queue": "batch",
        "risk_band": "low",
        "resource_profile": "cpu",
        "provider_class": "local",
        "operation_class": "review",
        "status": "succeeded",
        "approval_required": False,
        "fallback_allowed": True,
        "reversible": True,
        "retry_count": 0,
        "estimated_cost_microusd": 10_000,
        "latency_ms": 250,
        "required_evidence": True,
    }
    values.update(changes)
    return values


def _store(root: Path) -> RunBundleStore:
    return RunBundleStore(
        root,
        verification_keys={KEY_ID: KEY},
        active_key_id=KEY_ID,
    )


def _recorder(root: Path, **changes: object) -> DecisionOutcomeRecorder:
    values: dict[str, object] = {
        "store": _store(root),
        "project_id": "project-1",
        "policy_digest": POLICY_DIGEST,
        "correlation_key": KEY,
        "source": SourceIdentityV1(
            repository_url_sha256="sha256:" + "c" * 64,
            commit_sha="d" * 40,
            tree_sha="e" * 40,
            branch="development",
            dirty=False,
        ),
        "runtime": RuntimeIdentityV1(
            gludd_version="0.1.1",
            python_version="3.14.0",
            os="darwin",
            architecture="arm64",
            config_sha256=CONFIG_DIGEST,
            feature_flags={"decision_codification": True},
        ),
        "model": ModelIdentityV1(
            provider="openai",
            profile="review",
            model="gpt-6",
            request_parameters={},
            provider_revision=None,
        ),
        "retention_days": 30,
        "max_total_bytes": 1_048_576,
        "scan_limit": 100,
    }
    values.update(changes)
    return DecisionOutcomeRecorder(**values)  # type: ignore[arg-type]


def _capture(
    recorder: DecisionOutcomeRecorder, **changes: object
) -> DecisionCaptureReceipt:
    values: dict[str, object] = {
        "capture_id": "return-review:RET-PRIVATE-001",
        "root_task_id": "TODO-PRIVATE-001",
        "decision_kind": DecisionKind.REVIEW,
        "features": _features(),
        "decision": "approve",
        "outcome": VerifiedOutcome.SUCCESS,
        "occurred_at": NOW,
    }
    values.update(changes)
    return recorder.capture(**values)  # type: ignore[arg-type]


def test_capture_finalizes_a_signed_analyzable_pair_without_raw_ids(
    tmp_path: Path,
) -> None:
    root = tmp_path / "replays"
    receipt = _capture(_recorder(root))
    store = _store(root)
    bundle = store.read_verified(receipt.run_id)

    assert bundle.manifest.integrity == "signed"
    assert bundle.manifest.event_count == 2
    assert bundle.manifest.retention.expires_at is not None
    assert [event.type for event in bundle.events] == [
        "review.decided",
        "decision.outcome",
    ]
    decision, outcome = bundle.events
    assert outcome.payload["decision_event_digest"] == decision.digest
    assert receipt.decision_event_digest == decision.digest
    assert receipt.outcome_event_digest == outcome.digest
    serialized = canonical_replay_json(
        [event.model_dump(mode="json", by_alias=True) for event in bundle.events]
    )
    assert "RET-PRIVATE-001" not in serialized
    assert "TODO-PRIVATE-001" not in serialized

    analysis = DecisionLogAnalyzer(store).analyze(
        (receipt.run_id,),
        project_id="project-1",
        current_policy_digest=POLICY_DIGEST,
        training_recipe_digest="sha256:" + "f" * 64,
        dependency_lock_digest="sha256:" + "1" * 64,
        created_at=NOW,
        expires_at=datetime(2026, 11, 7, tzinfo=UTC),
        maximum_use_count=100,
    )
    assert analysis.events_eligible == 1
    assert analysis.rejection_counts == (
        (AnalysisRejectionReason.NO_SAFE_GROUP, 1),
    )


def test_capture_is_idempotent_and_rejects_same_identity_with_new_content(
    tmp_path: Path,
) -> None:
    recorder = _recorder(tmp_path / "replays")

    first = _capture(recorder)
    duplicate = _capture(recorder)

    assert duplicate == first
    with pytest.raises(DecisionCaptureError, match="conflicts"):
        _capture(recorder, decision="reject")


@pytest.mark.parametrize(
    "changes",
    [
        {"features": _features(risk_band="high")},
        {"features": _features(secret_token="must-not-persist")},
        {"capture_id": ""},
        {"root_task_id": "x" * 1025},
        {"decision": "invented"},
        {"outcome": VerifiedOutcome.UNKNOWN},
        {"occurred_at": datetime(2026, 10, 7, 20)},
    ],
)
def test_capture_rejects_unsafe_or_unbounded_input_before_writing(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    root = tmp_path / "replays"
    recorder = _recorder(root)

    with pytest.raises(DecisionCaptureError):
        _capture(recorder, **changes)

    assert not tuple((root / "runs-v1").iterdir())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"store": object()}, "replay store"),
        ({"project_id": "unsafe/project"}, "scope"),
        ({"correlation_key": b"short"}, "correlation key"),
        ({"source": object()}, "source identity"),
        ({"runtime": object()}, "runtime identity"),
        ({"model": object()}, "model identity"),
        ({"retention_days": True}, "retention"),
        ({"max_total_bytes": 1024}, "storage bound"),
        ({"scan_limit": 0}, "scan bound"),
    ],
)
def test_recorder_rejects_invalid_scope_identity_and_resource_bounds(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(DecisionCaptureError, match=message):
        _recorder(tmp_path / "replays", **changes)


def test_capture_wraps_storage_failures_without_leaking_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _recorder(tmp_path / "replays")

    def fail_retention(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("sensitive-storage-detail")

    monkeypatch.setattr(RunBundleStore, "enforce_retention", fail_retention)

    with pytest.raises(DecisionCaptureError, match="failed closed") as error:
        _capture(recorder)

    assert "sensitive-storage-detail" not in str(error.value)
