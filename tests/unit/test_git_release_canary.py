"""GRC-AT-007: ZDD & canary rollout — canary harness with health gates,
regression detection, and automatic rollback within recovery objective.

Per spec GRC-001 §8, the deployment orchestrator evaluates health samples
against a gate and plans bounded traffic shifts.  When a severe regression
is detected (error rate exceeds the abort threshold), it triggers automatic
rollback to the prior known-good digest.

``general_ludd.git_release.deployment`` provides
:class:`DeploymentOrchestrator`, :class:`HealthGate`, :class:`HealthSample`,
and the decision hierarchy (:class:`PromoteDecision`, :class:`AbortDecision`,
:class:`RollbackDecision`, :class:`HoldDecision`).  This module exercises
every decision path and the traffic-shift planning contract.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_DEPLOYMENT_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "git_release", "deployment.py")


def _load_mod(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


deployment = _load_mod(_DEPLOYMENT_PATH, "grc_deployment_at007")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _healthy_gate():
    return deployment.HealthGate(
        max_error_rate=0.02,
        min_availability=0.995,
        max_latency_p99_ms=100.0,
    )


def _healthy_sample():
    return deployment.HealthSample(
        availability=0.999,
        error_rate=0.005,
        latency_p99_ms=45.0,
    )


def _canary_config(**overrides):
    kw = {
        "strategy": deployment.DeploymentStrategy.CANARY,
        "health_gate": _healthy_gate(),
        "abort_threshold": 0.05,
    }
    kw.update(overrides)
    return deployment.DeploymentConfig(**kw)


def _orchestrator(**overrides):
    kw = {
        "config": _canary_config(),
        "prior_digest": "sha256:known-good",
        "new_digest": "sha256:candidate",
    }
    kw.update(overrides)
    return deployment.DeploymentOrchestrator(**kw)


# ---------------------------------------------------------------------------
# Tests: Health evaluation
# ---------------------------------------------------------------------------


class TestHealthEvaluation:
    """GRC-ZDD-003: health gate evaluation on telemetry samples."""

    def test_healthy_sample_promotes(self):
        orch = _orchestrator()
        decision = orch.evaluate(stage="canary", sample=_healthy_sample())
        assert isinstance(decision, deployment.PromoteDecision)
        assert decision.digest == "sha256:candidate"

    def test_high_error_rate_aborts(self):
        orch = _orchestrator()
        bad = deployment.HealthSample(availability=0.999, error_rate=0.10, latency_p99_ms=45.0)
        decision = orch.evaluate(stage="canary", sample=bad)
        assert isinstance(decision, (deployment.AbortDecision, deployment.RollbackDecision))

    def test_low_availability_aborts(self):
        orch = _orchestrator()
        bad = deployment.HealthSample(availability=0.80, error_rate=0.005, latency_p99_ms=45.0)
        decision = orch.evaluate(stage="canary", sample=bad)
        assert isinstance(decision, (deployment.AbortDecision, deployment.RollbackDecision))

    def test_high_latency_aborts(self):
        orch = _orchestrator()
        bad = deployment.HealthSample(availability=0.999, error_rate=0.005, latency_p99_ms=500.0)
        decision = orch.evaluate(stage="canary", sample=bad)
        assert isinstance(decision, (deployment.AbortDecision, deployment.RollbackDecision))

    def test_missing_telemetry_holds(self):
        orch = _orchestrator()
        decision = orch.evaluate(stage="canary", sample=None)
        assert isinstance(decision, deployment.HoldDecision)
        assert "missing" in decision.reason.lower()

    def test_severe_error_rate_rolls_back(self):
        """Error rate >= abort_threshold triggers RollbackDecision."""
        orch = _orchestrator(
            config=_canary_config(abort_threshold=0.05),
        )
        severe = deployment.HealthSample(availability=0.999, error_rate=0.07, latency_p99_ms=45.0)
        decision = orch.evaluate(stage="canary", sample=severe)
        assert isinstance(decision, deployment.RollbackDecision)
        assert decision.target_digest == "sha256:known-good"


# ---------------------------------------------------------------------------
# Tests: Traffic shift planning
# ---------------------------------------------------------------------------


class TestTrafficShift:
    """GRC-ZDD-004: bounded, observable traffic shifts."""

    def test_canary_shift_starts_at_max_step(self):
        orch = _orchestrator()
        shift = orch.next_shift(current_percent=0)
        assert shift.next_percent == 25  # max_step_percent default
        assert shift.observation_window_s == 120

    def test_canary_shift_capped_at_100(self):
        orch = _orchestrator()
        shift = orch.next_shift(current_percent=90)
        assert shift.next_percent == 100

    def test_blue_green_cuts_over_in_one_step(self):
        orch = _orchestrator(
            config=_canary_config(strategy=deployment.DeploymentStrategy.BLUE_GREEN),
        )
        shift = orch.next_shift(current_percent=0)
        assert shift.next_percent == 100

    def test_rolling_strategy_increments(self):
        orch = _orchestrator(
            config=_canary_config(strategy=deployment.DeploymentStrategy.ROLLING),
        )
        shift = orch.next_shift(current_percent=25)
        assert shift.next_percent == 50

    def test_canary_shift_at_100_saturates(self):
        orch = _orchestrator()
        shift = orch.next_shift(current_percent=100)
        assert shift.next_percent == 100


# ---------------------------------------------------------------------------
# Tests: Decision hierarchy
# ---------------------------------------------------------------------------


class TestDecisionHierarchy:
    """Every deployment decision carries a digest or reason."""

    def test_promote_carries_digest(self):
        orch = _orchestrator()
        decision = orch.evaluate(stage="canary", sample=_healthy_sample())
        assert isinstance(decision, deployment.PromoteDecision)
        assert len(decision.digest) > 0

    def test_abort_carries_metric_and_threshold(self):
        orch = _orchestrator()
        bad = deployment.HealthSample(availability=0.999, error_rate=0.10, latency_p99_ms=45.0)
        decision = orch.evaluate(stage="canary", sample=bad)
        assert isinstance(decision, (deployment.AbortDecision, deployment.RollbackDecision))
        assert len(decision.reason) > 0
        assert len(decision.metric) > 0

    def test_rollback_carries_target_digest(self):
        orch = _orchestrator(
            config=_canary_config(abort_threshold=0.05),
        )
        severe = deployment.HealthSample(availability=0.999, error_rate=0.06, latency_p99_ms=45.0)
        decision = orch.evaluate(stage="canary", sample=severe)
        assert isinstance(decision, deployment.RollbackDecision)
        assert decision.target_digest == "sha256:known-good"

    def test_hold_carries_reason(self):
        orch = _orchestrator()
        decision = orch.evaluate(stage="canary", sample=None)
        assert isinstance(decision, deployment.HoldDecision)
        assert len(decision.reason) > 0

    def test_module_exports_decision_types(self):
        assert hasattr(deployment, "PromoteDecision")
        assert hasattr(deployment, "AbortDecision")
        assert hasattr(deployment, "RollbackDecision")
        assert hasattr(deployment, "HoldDecision")
        assert hasattr(deployment, "BlueGreenCutComplete")


# ---------------------------------------------------------------------------
# Tests: End-to-end canary (integration, skipped)
# ---------------------------------------------------------------------------


class TestCanaryEndToEnd:
    """GRC-AT-007: full canary rollout with synthetic load.

    Skipped: requires a two-version service fixture with synthetic load
    generation.  The orchestrator primitives (evaluate, next_shift) are
    correct; the integration harness belongs under
    tests/integration/git_release/.
    """

    @pytest.mark.skip(
        "GRC-AT-007: canary load fixture not yet wired.  Requires two-version "
        "service fixture + synthetic load generator."
    )
    def test_synthetic_load_no_failed_requests(self):
        pass

    @pytest.mark.skip("GRC-AT-007: canary regression fixture not yet wired.")
    def test_canary_regression_triggers_rollback(self):
        pass
