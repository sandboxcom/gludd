"""Integration tests: ZDD release lifecycle (GRC-AT-007, spec §7, §8).

Drives the :class:`ReleaseStateMachine` end-to-end through the spec §7 forward
graph, then injects a canary regression and verifies the rollback path restores
the prior known-good digest. Combines the state machine with the deployment
orchestrator's :class:`HealthGate` evaluation to mirror a real release flow:

    DISCOVER -> PLAN -> BUILD_ONCE -> VERIFY_OFFLINE -> STAGE
            -> CANARY -> (regression) -> ROLLBACK
"""

from __future__ import annotations

import pytest

from general_ludd.git_release.deployment import (
    AbortDecision,
    DeploymentConfig,
    DeploymentOrchestrator,
    DeploymentStrategy,
    HealthGate,
    HealthSample,
    HoldDecision,
    RollbackDecision,
)
from general_ludd.git_release.release_state import (
    AdvanceResult,
    ReleaseState,
    ReleaseStateMachine,
    TransitionError,
)

_SOURCE_SHA = "a" * 40
_MOVED_SHA = "c" * 40
_ARTIFACT_DIGEST = "sha256:newbuild"
_PRIOR_DIGEST = "sha256:knowngood"
_GATE_EVIDENCE = [("unit", "passed", "log://unit"), ("lint", "passed", "log://lint")]


def _healthy_sample() -> HealthSample:
    return HealthSample(availability=0.9999, error_rate=0.001, latency_p99_ms=120)


def _regression_sample() -> HealthSample:
    return HealthSample(availability=0.90, error_rate=0.08, latency_p99_ms=900)


def _walk_to_canary(sm: ReleaseStateMachine) -> None:
    """Walk a fresh state machine forward through STAGE into CANARY."""
    sm.advance(target=ReleaseState.PLAN)
    sm.advance(target=ReleaseState.BUILD_ONCE)
    sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=_GATE_EVIDENCE)
    sm.advance(target=ReleaseState.STAGE, artifact_digest=_ARTIFACT_DIGEST)
    sm.advance(
        target=ReleaseState.CANARY,
        prior_digest=_PRIOR_DIGEST,
        health_gate_passed=True,
    )


# ---------------------------------------------------------------------------
# Happy path: DISCOVER -> ... -> RELEASED.
# ---------------------------------------------------------------------------


def test_zdd_full_happy_path_reaches_released() -> None:
    sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)
    sm.advance(target=ReleaseState.PLAN)
    sm.advance(target=ReleaseState.BUILD_ONCE)
    sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=_GATE_EVIDENCE)
    sm.advance(target=ReleaseState.STAGE, artifact_digest=_ARTIFACT_DIGEST)
    sm.advance(
        target=ReleaseState.CANARY,
        prior_digest=_PRIOR_DIGEST,
        health_gate_passed=True,
    )
    sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=True)
    sm.advance(target=ReleaseState.VERIFY_RELEASE_PAGE, release_page_proven=True)
    sm.advance(target=ReleaseState.RELEASED)

    assert sm.state == ReleaseState.RELEASED
    # RELEASED is terminal: the system serves the new artifact at 100%.
    assert sm.serving_digest == _ARTIFACT_DIGEST


# ---------------------------------------------------------------------------
# GRC-SEC-004 / GRC-ZDD-001: preconditions block and leave state unchanged.
# ---------------------------------------------------------------------------


def test_zdd_blocks_offline_verify_without_gate_evidence() -> None:
    sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)
    sm.advance(target=ReleaseState.PLAN)
    sm.advance(target=ReleaseState.BUILD_ONCE)
    result = sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=[])

    assert isinstance(result, AdvanceResult)
    assert result.blocked is True
    assert "GRC-SEC-004" in result.reasons
    assert sm.state == ReleaseState.BUILD_ONCE


def test_zdd_blocks_stage_on_digest_mismatch_and_then_moved_sha() -> None:
    sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)
    sm.advance(target=ReleaseState.PLAN)
    sm.advance(target=ReleaseState.BUILD_ONCE)
    sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=_GATE_EVIDENCE)

    # Digest mismatch blocks STAGE.
    bad = sm.advance(target=ReleaseState.STAGE, artifact_digest="sha256:WRONG")
    assert bad.blocked is True
    assert "GRC-ZDD-001" in bad.reasons
    assert sm.state == ReleaseState.VERIFY_OFFLINE

    # A moved source SHA blocks every subsequent stage (GRC-SEC-004).
    moved = sm.advance(
        target=ReleaseState.STAGE,
        artifact_digest=_ARTIFACT_DIGEST,
        observed_source_sha=_MOVED_SHA,
    )
    assert moved.blocked is True
    assert "GRC-SEC-004" in moved.reasons
    assert "source-sha-moved" in moved.reasons


# ---------------------------------------------------------------------------
# Canary regression: HealthGate fires -> ROLLBACK restores prior digest.
# ---------------------------------------------------------------------------


def test_zdd_canary_regression_triggers_rollback_and_restores_prior_digest() -> None:
    sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)
    _walk_to_canary(sm)
    assert sm.state == ReleaseState.CANARY
    assert sm.serving_digest == _ARTIFACT_DIGEST

    # Evaluate a regression sample through the deployment orchestrator.
    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.CANARY,
        health_gate=HealthGate(max_error_rate=0.01, min_availability=0.999, max_latency_p99_ms=500),
        abort_threshold=0.02,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    decision = orch.evaluate(stage="canary-5pct", sample=_regression_sample())

    # The orchestrator decides to roll back to the prior known-good digest.
    assert isinstance(decision, RollbackDecision)
    assert decision.target_digest == _PRIOR_DIGEST

    # The state machine performs the rollback; serving digest is restored.
    sm.rollback(reason="canary error-rate regression")
    assert sm.state == ReleaseState.ROLLBACK
    assert sm.serving_digest == _PRIOR_DIGEST


def test_zdd_healthy_canary_advances_to_promote_then_release_page() -> None:
    """A canary that stays healthy promotes and reaches the release-page gate."""
    sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)
    _walk_to_canary(sm)

    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.CANARY,
        health_gate=HealthGate(max_error_rate=0.01, min_availability=0.999, max_latency_p99_ms=500),
        abort_threshold=0.02,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    decision = orch.evaluate(stage="canary-25pct", sample=_healthy_sample())
    assert not isinstance(decision, AbortDecision)
    assert not isinstance(decision, RollbackDecision)

    # Health gate passed -> PROMOTE.
    sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=True)
    assert sm.state == ReleaseState.PROMOTE

    # Release page must be proven before RELEASED.
    blocked = sm.advance(target=ReleaseState.VERIFY_RELEASE_PAGE, release_page_proven=False)
    assert blocked.blocked is True
    assert "GRC-ZDD-005" in blocked.reasons
    assert sm.state == ReleaseState.PROMOTE

    sm.advance(target=ReleaseState.VERIFY_RELEASE_PAGE, release_page_proven=True)
    sm.advance(target=ReleaseState.RELEASED)
    assert sm.state == ReleaseState.RELEASED


# ---------------------------------------------------------------------------
# ROLLBACK from PROMOTE and missing-telemetry hold (GRC-ZDD-003).
# ---------------------------------------------------------------------------


def test_zdd_promote_can_roll_back_after_late_regression() -> None:
    """A regression detected at PROMOTE (100% traffic) can still roll back."""
    sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)
    _walk_to_canary(sm)
    sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=True)
    assert sm.state == ReleaseState.PROMOTE

    sm.rollback(reason="latency regression at 100pct traffic")
    assert sm.state == ReleaseState.ROLLBACK
    assert sm.serving_digest == _PRIOR_DIGEST


def test_zdd_missing_telemetry_blocks_promotion_until_recovered() -> None:
    """GRC-ZDD-003: missing or stale telemetry blocks promotion."""
    sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)
    _walk_to_canary(sm)

    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.CANARY,
        health_gate=HealthGate(max_error_rate=0.01, min_availability=0.999, max_latency_p99_ms=500),
        abort_threshold=0.02,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    decision = orch.evaluate(stage="canary-5pct", sample=None)
    assert isinstance(decision, HoldDecision)
    assert "telemetry" in decision.reason

    # State machine stays in CANARY; no promotion until telemetry recovers.
    blocked = sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=False)
    assert blocked.blocked is True
    assert "GRC-ZDD-003" in blocked.reasons
    assert sm.state == ReleaseState.CANARY


# ---------------------------------------------------------------------------
# ROLLBACK is terminal: no escape, no re-release from the same machine.
# ---------------------------------------------------------------------------


def test_zdd_rollback_is_terminal_and_released_cannot_rollback() -> None:
    sm = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)
    _walk_to_canary(sm)
    sm.rollback(reason="regression")
    assert sm.state == ReleaseState.ROLLBACK
    # ROLLBACK has no outgoing edges (spec §7 graph).
    with pytest.raises(TransitionError):
        sm.advance(target=ReleaseState.PROMOTE)

    # A shipped release also cannot roll back; recovery is a fresh plan.
    sm2 = ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)
    sm2.state = ReleaseState.RELEASED
    with pytest.raises(TransitionError):
        sm2.rollback(reason="too late")
