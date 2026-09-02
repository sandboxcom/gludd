"""Deterministic lifecycle-trace checks for offline self-improvement replay."""

from __future__ import annotations

from pathlib import Path

import pytest
import scripts.replay_self_improve_failure_corpus as corpus

ROOT = Path(__file__).resolve().parents[2]
TRACKED_CORPUS = ROOT / "config/self-improve/failure-corpus.json"


def _event(phase: str, cause: str | None = None) -> dict[str, object]:
    return {"phase": phase, "cause": cause}


def test_refused_eviction_cannot_fall_through_to_a_phantom_proposal() -> None:
    verdict = corpus.check_acquisition_trace(
        (
            _event("eviction_planned"),
            _event("eviction_refused", "no_safe_reclaim"),
            _event("proposal_error"),
            _event("next_attempt_empty"),
        )
    )

    assert verdict.accepted is False
    assert verdict.outcome == "refused"
    assert verdict.feedback == (
        "protocol=self-improve-validation-retry-v3 "
        "type=acquisition_refused source=acquisition_trace "
        "detail=model cache has no safe reclaim candidate"
    )
    assert "proposal_error" not in verdict.feedback
    assert "next_attempt_empty" not in verdict.feedback


def test_completed_acquisition_requires_download_lease_and_release() -> None:
    verdict = corpus.check_acquisition_trace(
        (
            _event("eviction_planned"),
            _event("eviction_completed"),
            _event("download_completed"),
            _event("lease_acquired"),
            _event("lease_released"),
        )
    )

    assert verdict.accepted is True
    assert verdict.outcome == "completed"
    assert verdict.feedback == ""


def test_terminal_refusal_is_an_accepted_explicit_outcome() -> None:
    verdict = corpus.check_acquisition_trace(
        (
            _event("eviction_planned"),
            _event("eviction_refused", "no_safe_reclaim"),
            _event("terminal_refusal", "no_safe_reclaim"),
        )
    )

    assert verdict.accepted is True
    assert verdict.outcome == "refused"
    assert "type=acquisition_refused" in verdict.feedback
    assert "detail=model cache has no safe reclaim candidate" in verdict.feedback


@pytest.mark.parametrize(
    "trace",
    (
        (
            _event("eviction_planned"),
            _event("eviction_refused"),
            _event("terminal_refusal"),
        ),
        (
            _event("eviction_planned"),
            _event("eviction_refused", "raw /Users/operator/private.gguf"),
            _event("terminal_refusal", "raw /Users/operator/private.gguf"),
        ),
        (
            _event("eviction_planned"),
            _event("eviction_refused", "timeout"),
            _event("terminal_refusal", "validation"),
        ),
    ),
)
def test_refusal_requires_one_matching_typed_safe_acquisition_cause(
    trace: tuple[dict[str, object], ...],
) -> None:
    with pytest.raises(ValueError, match="typed safe acquisition cause"):
        corpus.check_acquisition_trace(trace)


@pytest.mark.parametrize(
    "trace",
    (
        (),
        (_event("download_completed"),),
        (
            _event("eviction_planned"),
            _event("download_completed"),
            _event("lease_acquired"),
            _event("lease_released"),
        ),
        (
            _event("eviction_planned"),
            _event("eviction_completed"),
            _event("download_completed"),
            _event("lease_released"),
        ),
        (
            _event("eviction_planned"),
            _event("eviction_completed"),
            _event("download_completed"),
            _event("lease_acquired"),
        ),
    ),
)
def test_incomplete_or_out_of_order_acquisition_is_rejected(
    trace: tuple[dict[str, object], ...],
) -> None:
    with pytest.raises(ValueError, match="acquisition trace"):
        corpus.check_acquisition_trace(trace)


def test_tracked_incident_replays_as_a_typed_acquisition_rejection() -> None:
    case = next(
        item
        for item in corpus.load_corpus(TRACKED_CORPUS)
        if item.case_id == "eviction-refused-phantom-proposal"
    )

    result = corpus.replay_case(case)

    assert result.passed is True
    assert result.feedback_type == "acquisition_refused"
    assert result.source == "acquisition_trace"
    assert result.detail == "model cache has no safe reclaim candidate"
    assert result.worker_succeeded is False
    assert result.parent_stage == "acquisition"
