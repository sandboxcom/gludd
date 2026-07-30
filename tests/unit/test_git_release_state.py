"""Unit tests for general_ludd.git_release.release_state + deployment (GRC-P4/P5).

Covers the zero-downtime release state machine (spec GRC-001 §7) and the
deployment orchestrator (§7 GRC-ZDD-001..005, §5.4 ReleaseVerdict):

- ReleaseState lifecycle DISCOVER -> PLAN -> ... -> RELEASED
- invalid transitions rejected
- preconditions: gate evidence, artifact digest, stale SHA, health gate
- rollback restores prior known-good digest
- DeploymentOrchestrator: canary/rolling/blue-green, health evaluation,
  traffic shift bounds, abort threshold, automatic rollback target
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
    ReleaseState,
    ReleaseStateMachine,
    TransitionError,
)

# ---------------------------------------------------------------------------
# Fixtures: a fresh state machine pinned to a known source SHA + digest.
# ---------------------------------------------------------------------------

_SOURCE_SHA = "a" * 40
_OTHER_SHA = "b" * 40
_ARTIFACT_DIGEST = "sha256:aaa"
_PRIOR_DIGEST = "sha256:000"


@pytest.fixture()
def sm() -> ReleaseStateMachine:
    return ReleaseStateMachine(source_sha=_SOURCE_SHA, artifact_digest=_ARTIFACT_DIGEST)


@pytest.fixture()
def armed_sm(sm: ReleaseStateMachine) -> ReleaseStateMachine:
    """A state machine walked all the way to CANARY with prior digest saved."""
    sm.advance(target=ReleaseState.PLAN)
    sm.advance(target=ReleaseState.BUILD_ONCE)
    sm.advance(
        target=ReleaseState.VERIFY_OFFLINE,
        gate_evidence=[("gate-unit", "passed", "log://unit")],
    )
    sm.advance(
        target=ReleaseState.STAGE,
        artifact_digest=_ARTIFACT_DIGEST,
    )
    sm.advance(
        target=ReleaseState.CANARY,
        prior_digest=_PRIOR_DIGEST,
        health_gate_passed=True,
    )
    return sm


# ---------------------------------------------------------------------------
# State transitions (spec §7 forward path)
# ---------------------------------------------------------------------------


def test_state_machine_starts_at_discover(sm: ReleaseStateMachine) -> None:
    assert sm.state == ReleaseState.DISCOVER


def test_state_machine_walks_forward_path(sm: ReleaseStateMachine) -> None:
    sm.advance(target=ReleaseState.PLAN)
    sm.advance(target=ReleaseState.BUILD_ONCE)
    sm.advance(
        target=ReleaseState.VERIFY_OFFLINE,
        gate_evidence=[("gate-unit", "passed", "log://unit")],
    )
    sm.advance(target=ReleaseState.STAGE, artifact_digest=_ARTIFACT_DIGEST)
    sm.advance(target=ReleaseState.CANARY, prior_digest=_PRIOR_DIGEST, health_gate_passed=True)
    sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=True)
    sm.advance(
        target=ReleaseState.VERIFY_RELEASE_PAGE,
        release_page_proven=True,
    )
    sm.advance(target=ReleaseState.RELEASED)
    assert sm.state == ReleaseState.RELEASED


def test_state_machine_released_is_terminal(sm: ReleaseStateMachine) -> None:
    sm.state = ReleaseState.VERIFY_RELEASE_PAGE
    sm.advance(target=ReleaseState.RELEASED)
    with pytest.raises(TransitionError):
        sm.advance(target=ReleaseState.DISCOVER)


# ---------------------------------------------------------------------------
# Invalid transitions rejected (spec §7 — only spec-listed edges allowed)
# ---------------------------------------------------------------------------


def test_state_machine_rejects_skip_ahead(sm: ReleaseStateMachine) -> None:
    # DISCOVER -> CANARY is not a spec-listed edge.
    try:
        sm.advance(target=ReleaseState.CANARY)
    except TransitionError as exc:
        assert "canary" in str(exc).lower()
    else:
        raise AssertionError("expected TransitionError for DISCOVER -> CANARY")


def test_state_machine_rejects_backward_to_discover(sm: ReleaseStateMachine) -> None:
    sm.advance(target=ReleaseState.PLAN)
    with pytest.raises(TransitionError):
        sm.advance(target=ReleaseState.DISCOVER)


def test_state_machine_rejects_same_state(sm: ReleaseStateMachine) -> None:
    with pytest.raises(TransitionError):
        sm.advance(target=ReleaseState.DISCOVER)


# ---------------------------------------------------------------------------
# Preconditions: missing gate evidence / digest mismatch / stale SHA
# (spec GRC-SEC-004 fail-closed, GRC-ZDD-001 build once)
# ---------------------------------------------------------------------------


def test_state_machine_blocks_offline_verify_without_gate_evidence(sm: ReleaseStateMachine) -> None:
    sm.advance(target=ReleaseState.PLAN)
    sm.advance(target=ReleaseState.BUILD_ONCE)
    result = sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=[])
    assert result.blocked is True
    assert "GRC-SEC-004" in result.reasons
    assert sm.state == ReleaseState.BUILD_ONCE


def test_state_machine_blocks_stage_on_digest_mismatch(sm: ReleaseStateMachine) -> None:
    sm.advance(target=ReleaseState.PLAN)
    sm.advance(target=ReleaseState.BUILD_ONCE)
    sm.advance(
        target=ReleaseState.VERIFY_OFFLINE,
        gate_evidence=[("g", "passed", "u")],
    )
    result = sm.advance(target=ReleaseState.STAGE, artifact_digest="sha256:WRONG")
    assert result.blocked is True
    assert sm.state == ReleaseState.VERIFY_OFFLINE


def test_state_machine_blocks_canary_without_health_gate(sm: ReleaseStateMachine) -> None:
    sm.advance(target=ReleaseState.PLAN)
    sm.advance(target=ReleaseState.BUILD_ONCE)
    sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=[("g", "passed", "u")])
    sm.advance(target=ReleaseState.STAGE, artifact_digest=_ARTIFACT_DIGEST)
    result = sm.advance(target=ReleaseState.CANARY, prior_digest=_PRIOR_DIGEST, health_gate_passed=False)
    assert result.blocked is True
    assert "GRC-ZDD-003" in result.reasons


def test_state_machine_blocks_release_page_without_proven_page(sm: ReleaseStateMachine) -> None:
    sm.state = ReleaseState.PROMOTE
    result = sm.advance(target=ReleaseState.VERIFY_RELEASE_PAGE, release_page_proven=False)
    assert result.blocked is True
    assert "GRC-ZDD-005" in result.reasons


def test_state_machine_blocks_on_stale_source_sha(sm: ReleaseStateMachine) -> None:
    sm.advance(target=ReleaseState.PLAN)
    sm.advance(target=ReleaseState.BUILD_ONCE)
    # Source ref moved after BUILD_ONCE — every later stage MUST refuse.
    result = sm.advance(
        target=ReleaseState.VERIFY_OFFLINE,
        gate_evidence=[("g", "passed", "u")],
        observed_source_sha=_OTHER_SHA,
    )
    assert result.blocked is True
    assert "GRC-SEC-004" in result.reasons


# ---------------------------------------------------------------------------
# Canary regression -> ROLLBACK (spec §7, §8 "Canary regression" row)
# ---------------------------------------------------------------------------


def test_state_machine_canary_can_rollback(armed_sm: ReleaseStateMachine) -> None:
    armed_sm.rollback(reason="error_rate Spike")
    assert armed_sm.state == ReleaseState.ROLLBACK


def test_state_machine_promote_can_rollback(armed_sm: ReleaseStateMachine) -> None:
    armed_sm.advance(target=ReleaseState.PROMOTE, health_gate_passed=True)
    armed_sm.rollback(reason="latency regression at 100%")
    assert armed_sm.state == ReleaseState.ROLLBACK


def test_state_machine_rollback_restores_prior_digest(armed_sm: ReleaseStateMachine) -> None:
    armed_sm.rollback(reason="canary regression")
    # After rollback the system is serving the prior known-good digest.
    assert armed_sm.serving_digest == _PRIOR_DIGEST


def test_state_machine_rollback_without_prior_digest_is_blocked(sm: ReleaseStateMachine) -> None:
    # Place the machine in CANARY directly (bypassing advance(), which would
    # capture the prior digest) so the rollback precondition fires.
    sm.state = ReleaseState.CANARY
    sm._prior_digest = None  # noqa: SLF001 — intentionally testing the guard
    try:
        sm.rollback(reason="nothing to roll back to")
    except TransitionError as exc:
        assert "prior" in str(exc).lower()
    else:
        raise AssertionError("expected TransitionError for missing prior digest")


def test_state_machine_rollback_terminal_after_release(sm: ReleaseStateMachine) -> None:
    # A shipped release can no longer self-rollback; recovery is a new verdict.
    sm.state = ReleaseState.RELEASED
    with pytest.raises(TransitionError):
        sm.rollback(reason="too late")


# ---------------------------------------------------------------------------
# DeploymentOrchestrator: health gate, traffic bounds, abort, rollback target
# (spec §7 GRC-ZDD-003, GRC-ZDD-004)
# ---------------------------------------------------------------------------


def _health(error_rate: float, availability: float, latency_ms: float) -> HealthSample:
    return HealthSample(
        availability=availability,
        error_rate=error_rate,
        latency_p99_ms=latency_ms,
    )


def test_deployment_canary_aborts_on_error_rate_spige() -> None:
    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.CANARY,
        health_gate=HealthGate(max_error_rate=0.01, min_availability=0.999, max_latency_p99_ms=500),
        abort_threshold=0.02,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    decision = orch.evaluate(stage="canary-5pct", sample=_health(error_rate=0.05, availability=0.999, latency_ms=100))
    assert isinstance(decision, AbortDecision)
    assert decision.reason.startswith("error_rate")


def test_deployment_canary_aborts_on_latency_regression() -> None:
    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.CANARY,
        health_gate=HealthGate(max_error_rate=0.01, min_availability=0.999, max_latency_p99_ms=200),
        abort_threshold=0.02,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    decision = orch.evaluate(
        stage="canary-25pct",
        sample=_health(error_rate=0.0, availability=1.0, latency_ms=900),
    )
    assert isinstance(decision, AbortDecision)


def test_deployment_canary_aborts_on_availability_drop() -> None:
    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.CANARY,
        health_gate=HealthGate(max_error_rate=0.01, min_availability=0.999, max_latency_p99_ms=500),
        abort_threshold=0.02,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    decision = orch.evaluate(
        stage="canary-50pct",
        sample=_health(error_rate=0.0, availability=0.95, latency_ms=100),
    )
    assert isinstance(decision, AbortDecision)


def test_deployment_healthy_sample_promotes() -> None:
    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.CANARY,
        health_gate=HealthGate(max_error_rate=0.01, min_availability=0.999, max_latency_p99_ms=500),
        abort_threshold=0.02,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    decision = orch.evaluate(
        stage="canary-25pct",
        sample=_health(error_rate=0.001, availability=0.9995, latency_ms=120),
    )
    assert not isinstance(decision, AbortDecision)


def test_deployment_abort_returns_rollback_to_prior_digest() -> None:
    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.CANARY,
        health_gate=HealthGate(max_error_rate=0.0, min_availability=1.0, max_latency_p99_ms=100),
        abort_threshold=0.02,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    decision = orch.evaluate(
        stage="canary-5pct",
        sample=_health(error_rate=0.5, availability=0.7, latency_ms=100),
    )
    assert isinstance(decision, RollbackDecision)
    assert decision.target_digest == _PRIOR_DIGEST


def test_deployment_traffic_shift_respects_bounds() -> None:
    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.CANARY,
        health_gate=HealthGate(max_error_rate=0.01, min_availability=0.999, max_latency_p99_ms=500),
        abort_threshold=0.02,
        max_step_percent=25,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    shift = orch.next_shift(current_percent=0)
    assert shift.next_percent == 25
    shift = orch.next_shift(current_percent=80)
    assert shift.next_percent == 100
    assert shift.observation_window_s > 0


def test_deployment_rolling_strategy_uses_full_traffic_steps() -> None:
    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.ROLLING,
        health_gate=HealthGate(max_error_rate=0.01, min_availability=0.999, max_latency_p99_ms=500),
        abort_threshold=0.02,
        max_step_percent=25,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    shifts = [orch.next_shift(current_percent=p) for p in (0, 25, 50, 75)]
    assert [s.next_percent for s in shifts] == [25, 50, 75, 100]


def test_deployment_blue_green_single_cut() -> None:
    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.BLUE_GREEN,
        health_gate=HealthGate(max_error_rate=0.0, min_availability=1.0, max_latency_p99_ms=100),
        abort_threshold=0.02,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    shift = orch.next_shift(current_percent=0)
    # Blue-green cuts over in one step (spec §4.2 — "blue-green deployments").
    assert shift.next_percent == 100


def test_deployment_missing_telemetry_blocks() -> None:
    cfg = DeploymentConfig(
        strategy=DeploymentStrategy.CANARY,
        health_gate=HealthGate(max_error_rate=0.01, min_availability=0.999, max_latency_p99_ms=500),
        abort_threshold=0.02,
    )
    orch = DeploymentOrchestrator(config=cfg, prior_digest=_PRIOR_DIGEST, new_digest=_ARTIFACT_DIGEST)
    # Stale / missing telemetry MUST block promotion (GRC-ZDD-003). A hold is
    # a fail-closed block: traffic does not advance until telemetry recovers.
    decision = orch.evaluate(stage="canary-5pct", sample=None)
    assert isinstance(decision, HoldDecision)
    assert "telemetry" in decision.reason
