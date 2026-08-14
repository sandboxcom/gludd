"""Unit tests for AIML Phase E: world-model contract (AIML-014) and simulator
adapter framework (AIML-015).

Covers docs/specs/FEATURE_AI_ML_EXPERT.md §8.1 (World-model contract) and
§8.2 (Simulator adapter contract):

  - ``WorldModelEnvironment`` defines observation/action/state schemas, units,
    time step, reset/terminal behavior, stochastic seed, legal actions,
    constraints, reward objective, simulator/source version, dataset manifest
    (spec §8.1).
  - ``evaluate_rollout`` measures multi-horizon prediction error, calibration,
    constraint violations, compounding error, and planning regret, and exposes
    epistemic + aleatoric uncertainty (spec §8.1, AIML-AT-013).
  - A low-confidence rollout cannot authorize real-world actuation (spec §8.1,
    AIML-AT-013: "unsafe actuation is impossible").
  - ``SimulatorAdapter`` declares the full spec §8.2 contract block
    (capability_id, engine_name/version/digest, schemas, units_system,
    determinism, resources, license, sandbox_profile, validation_suite).
  - ``run_simulation`` runs in a network-denied sandbox, enforces resource
    limits, normalizes units, validates outputs, and refuses unsupported
    fidelity/boundary conditions without returning a fabricated result
    (spec §8.2, AIML-AT-014, AIML-AT-015).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import pytest

from general_ludd.ai_ml.simulators import (
    Determinism,
    ResourceLimits,
    SandboxProfile,
    SimulatorAdapter,
    run_simulation,
)
from general_ludd.ai_ml.world_models import (
    ConstraintSpec,
    ConstraintViolation,
    HorizonMetrics,
    RolloutEvaluation,
    RolloutUncertainty,
    WorldModelEnvironment,
    evaluate_rollout,
)

_SHA_SIM = "a" * 64
_SHA_ENGINE = "b" * 64
_SCHEMA_URI = "artifact://schemas/obs-v1.json"


def _default_constraints() -> tuple[ConstraintSpec, ...]:
    return (
        ConstraintSpec(name="max_velocity", value=10.0, unit="m/s"),
        ConstraintSpec(name="min_distance", value=0.5, unit="m"),
    )


def _default_legal_actions() -> tuple[str, ...]:
    return ("noop", "move_forward", "turn_left", "turn_right")


def _default_env() -> WorldModelEnvironment:
    return WorldModelEnvironment(
        env_id="gridworld-v1",
        observation_schema="artifact://schemas/obs.json",
        action_schema="artifact://schemas/act.json",
        state_schema="artifact://schemas/state.json",
        units_system="SI",
        time_step_s=0.1,
        legal_actions=_default_legal_actions(),
        constraints=_default_constraints(),
        reward_objective="reach_goal",
        simulator_record_id="sim-gridworld-v1",
        dataset_manifest_uri="artifact://datasets/gridworld-v1.manifest.json",
        seed=42,
    )


# ---------------------------------------------------------------------------
# AIML-014 — WorldModelEnvironment contract (spec §8.1)
# ---------------------------------------------------------------------------


class TestWorldModelEnvironmentContract:
    def test_environment_defines_observation_action_state_schemas(self) -> None:
        """Spec §8.1: a world-model environment defines observation/action/
        state schemas, units, time step, reset/terminal, legal actions,
        constraints, reward, simulator/source version, dataset manifest."""
        env = _default_env()
        assert env.observation_schema.startswith("artifact://")
        assert env.action_schema.startswith("artifact://")
        assert env.state_schema.startswith("artifact://")
        assert env.units_system == "SI"
        assert env.time_step_s == 0.1
        assert set(env.legal_actions) == set(_default_legal_actions())
        assert len(env.constraints) == 2
        assert env.reward_objective == "reach_goal"
        assert env.simulator_record_id == "sim-gridworld-v1"
        assert env.dataset_manifest_uri.endswith(".manifest.json")
        assert env.seed == 42

    def test_environment_requires_nonempty_schemas_and_seed(self) -> None:
        """Spec §8.1 / AIML-AT-001: invalid records raise rather than
        producing silently malformed contracts."""
        with pytest.raises(ValueError, match="observation_schema"):
            WorldModelEnvironment(
                env_id="x",
                observation_schema="",
                action_schema="artifact://a",
                state_schema="artifact://s",
                units_system="SI",
                time_step_s=0.1,
                legal_actions=_default_legal_actions(),
                constraints=_default_constraints(),
                reward_objective="r",
                simulator_record_id="sim",
                dataset_manifest_uri="artifact://d",
                seed=1,
            )
        with pytest.raises(ValueError, match="time_step_s"):
            WorldModelEnvironment(
                env_id="x",
                observation_schema="artifact://o",
                action_schema="artifact://a",
                state_schema="artifact://s",
                units_system="SI",
                time_step_s=0.0,
                legal_actions=_default_legal_actions(),
                constraints=_default_constraints(),
                reward_objective="r",
                simulator_record_id="sim",
                dataset_manifest_uri="artifact://d",
                seed=1,
            )
        with pytest.raises(ValueError, match="legal_actions"):
            WorldModelEnvironment(
                env_id="x",
                observation_schema="artifact://o",
                action_schema="artifact://a",
                state_schema="artifact://s",
                units_system="SI",
                time_step_s=0.1,
                legal_actions=(),
                constraints=_default_constraints(),
                reward_objective="r",
                simulator_record_id="sim",
                dataset_manifest_uri="artifact://d",
                seed=1,
            )

    def test_environment_seed_must_be_nonnegative(self) -> None:
        with pytest.raises(ValueError, match="seed"):
            WorldModelEnvironment(
                env_id="x",
                observation_schema="artifact://o",
                action_schema="artifact://a",
                state_schema="artifact://s",
                units_system="SI",
                time_step_s=0.1,
                legal_actions=_default_legal_actions(),
                constraints=_default_constraints(),
                reward_objective="r",
                simulator_record_id="sim",
                dataset_manifest_uri="artifact://d",
                seed=-1,
            )

    def test_environment_rejects_invalid_collection_members(self) -> None:
        env = _default_env()
        invalid_constraints = cast(
            tuple[ConstraintSpec, ...], list(_default_constraints())
        )

        with pytest.raises(ValueError, match="legal_actions entries"):
            replace(env, legal_actions=("noop", ""))
        with pytest.raises(ValueError, match="tuple of ConstraintSpec"):
            replace(env, constraints=invalid_constraints)
        with pytest.raises(ValueError, match="at least one ConstraintSpec"):
            replace(env, constraints=())


class TestWorldModelMetricValidation:
    def test_constraint_value_rejects_boolean(self) -> None:
        with pytest.raises(ValueError, match="real number"):
            ConstraintSpec(name="limit", value=True, unit="m")

    def test_horizon_metric_rejects_invalid_values(self) -> None:
        with pytest.raises(ValueError, match="positive int"):
            HorizonMetrics(horizon_steps=0, mean_error=0.1, p95_error=0.2)
        with pytest.raises(ValueError, match="mean_error"):
            HorizonMetrics(horizon_steps=1, mean_error=True, p95_error=0.2)

    def test_constraint_severity_rejects_boolean(self) -> None:
        with pytest.raises(ValueError, match="real number"):
            ConstraintViolation(
                constraint_name="limit",
                severity=True,
                description="invalid severity",
            )


# ---------------------------------------------------------------------------
# AIML-014 — evaluate_rollout (spec §8.1, AIML-AT-013)
# ---------------------------------------------------------------------------


def _horizons(low: float = 0.05, high: float = 0.4) -> tuple[HorizonMetrics, ...]:
    return (
        HorizonMetrics(horizon_steps=1, mean_error=low, p95_error=low * 1.5),
        HorizonMetrics(horizon_steps=10, mean_error=high, p95_error=high * 1.8),
    )


class TestEvaluateRollout:
    def test_multi_horizon_prediction_error_reported(self) -> None:
        """Spec §8.1: training/evaluation measure multi-horizon prediction."""
        env = _default_env()
        result = evaluate_rollout(
            env,
            horizon_errors=_horizons(),
            calibration_ece=0.08,
            constraint_violations=(),
            compounding_error_rate=0.02,
            planning_regret=0.1,
            uncertainty=RolloutUncertainty(epistemic=0.1, aleatoric=0.05, method="deep_ensemble"),
        )
        assert isinstance(result, RolloutEvaluation)
        assert len(result.horizon_errors) == 2
        assert result.horizon_errors[0].horizon_steps == 1
        assert result.horizon_errors[1].horizon_steps == 10
        # Error grows with horizon (compounding).
        assert result.horizon_errors[1].mean_error > result.horizon_errors[0].mean_error

    def test_calibration_ece_reported(self) -> None:
        """Spec §8.1: evaluation measures calibration."""
        env = _default_env()
        result = evaluate_rollout(
            env,
            horizon_errors=_horizons(),
            calibration_ece=0.12,
            constraint_violations=(),
            compounding_error_rate=0.01,
            planning_regret=0.05,
            uncertainty=RolloutUncertainty(epistemic=0.1, aleatoric=0.05, method="deep_ensemble"),
        )
        assert result.calibration_ece == 0.12

    def test_constraint_violations_flagged(self) -> None:
        """Spec §8.1: evaluation measures constraint violations."""
        env = _default_env()
        violations = (
            ConstraintViolation(constraint_name="max_velocity", severity=0.9, description="exceeded 10 m/s"),
            ConstraintViolation(constraint_name="min_distance", severity=0.3, description="below 0.5 m"),
        )
        result = evaluate_rollout(
            env,
            horizon_errors=_horizons(),
            calibration_ece=0.05,
            constraint_violations=violations,
            compounding_error_rate=0.01,
            planning_regret=0.05,
            uncertainty=RolloutUncertainty(epistemic=0.1, aleatoric=0.05, method="deep_ensemble"),
        )
        assert len(result.constraint_violations) == 2
        assert result.constraint_violations[0].constraint_name == "max_velocity"
        # Any constraint violation blocks actuation regardless of uncertainty.
        assert result.can_authorize_actuation is False

    def test_rollout_uncertainty_exposed(self) -> None:
        """Spec §8.1: rollouts expose epistemic and aleatoric uncertainty."""
        env = _default_env()
        unc = RolloutUncertainty(epistemic=0.15, aleatoric=0.08, method="mc_dropout")
        result = evaluate_rollout(
            env,
            horizon_errors=_horizons(),
            calibration_ece=0.05,
            constraint_violations=(),
            compounding_error_rate=0.01,
            planning_regret=0.05,
            uncertainty=unc,
        )
        assert result.uncertainty.epistemic == 0.15
        assert result.uncertainty.aleatoric == 0.08
        assert result.uncertainty.method == "mc_dropout"

    def test_low_confidence_rollout_cannot_authorize_actuation(self) -> None:
        """Spec §8.1 / AIML-AT-013: a low-confidence rollout cannot authorize
        real-world actuation. 'unsafe actuation is impossible.'"""
        env = _default_env()
        result = evaluate_rollout(
            env,
            horizon_errors=_horizons(high=0.9),
            calibration_ece=0.25,
            constraint_violations=(),
            compounding_error_rate=0.1,
            planning_regret=0.5,
            uncertainty=RolloutUncertainty(epistemic=0.6, aleatoric=0.3, method="deep_ensemble"),
        )
        assert result.can_authorize_actuation is False

    def test_high_confidence_rollout_can_authorize_actuation(self) -> None:
        """Spec §8.1: only a well-calibrated, low-uncertainty, violation-free
        rollout may authorize actuation."""
        env = _default_env()
        result = evaluate_rollout(
            env,
            horizon_errors=_horizons(low=0.01, high=0.05),
            calibration_ece=0.02,
            constraint_violations=(),
            compounding_error_rate=0.001,
            planning_regret=0.01,
            uncertainty=RolloutUncertainty(epistemic=0.05, aleatoric=0.02, method="deep_ensemble"),
        )
        assert result.can_authorize_actuation is True

    def test_compounding_error_and_planning_regret_reported(self) -> None:
        """Spec §8.1: evaluation measures compounding error and planning regret."""
        env = _default_env()
        result = evaluate_rollout(
            env,
            horizon_errors=_horizons(),
            calibration_ece=0.05,
            constraint_violations=(),
            compounding_error_rate=0.03,
            planning_regret=0.15,
            uncertainty=RolloutUncertainty(epistemic=0.1, aleatoric=0.05, method="deep_ensemble"),
        )
        assert result.compounding_error_rate == 0.03
        assert result.planning_regret == 0.15

    def test_uncertainty_score_must_be_in_unit_interval(self) -> None:
        with pytest.raises(ValueError, match="epistemic"):
            RolloutUncertainty(epistemic=1.5, aleatoric=0.1, method="x")
        with pytest.raises(ValueError, match="aleatoric"):
            RolloutUncertainty(epistemic=0.1, aleatoric=-0.1, method="x")


# ---------------------------------------------------------------------------
# AIML-015 — SimulatorAdapter contract (spec §8.2)
# ---------------------------------------------------------------------------


def _default_resources() -> ResourceLimits:
    return ResourceLimits(cpu=2, memory_mb=1024, gpu=0, timeout_s=60)


def _default_sandbox() -> SandboxProfile:
    return SandboxProfile(network_denied=True)


def _default_adapter() -> SimulatorAdapter:
    return SimulatorAdapter(
        capability_id="simulator.physics.rigidbody",
        adapter_version="1.0.0",
        engine_name="bullet3",
        engine_version="3.25",
        engine_digest=_SHA_ENGINE,
        input_schema="artifact://schemas/rigidbody-in.json",
        output_schema="artifact://schemas/rigidbody-out.json",
        units_system="SI",
        determinism=Determinism.SEEDED,
        resources=_default_resources(),
        license="Zlib",
        sandbox_profile=_default_sandbox(),
        validation_suite="artifact://suites/rigidbody-v1.json",
    )


class TestSimulatorAdapterContract:
    def test_adapter_declares_all_spec_fields(self) -> None:
        """Spec §8.2: each adapter declares the full contract block."""
        a = _default_adapter()
        assert a.capability_id == "simulator.physics.rigidbody"
        assert a.adapter_version == "1.0.0"
        assert a.engine_name == "bullet3"
        assert a.engine_version == "3.25"
        assert a.engine_digest == _SHA_ENGINE
        assert a.input_schema.startswith("artifact://")
        assert a.output_schema.startswith("artifact://")
        assert a.units_system == "SI"
        assert a.determinism == Determinism.SEEDED
        assert a.resources.cpu == 2
        assert a.resources.memory_mb == 1024
        assert a.resources.gpu == 0
        assert a.resources.timeout_s == 60
        assert a.license == "Zlib"
        assert a.sandbox_profile.network_denied is True
        assert a.validation_suite.startswith("artifact://")

    def test_adapter_rejects_invalid_semver_and_digest(self) -> None:
        """Spec §8.2 / AIML-AT-001: invalid records raise."""
        with pytest.raises(ValueError, match="adapter_version"):
            SimulatorAdapter(
                capability_id="x",
                adapter_version="not-a-version",
                engine_name="e",
                engine_version="1",
                engine_digest=_SHA_ENGINE,
                input_schema="i",
                output_schema="o",
                units_system="SI",
                determinism=Determinism.DETERMINISTIC,
                resources=_default_resources(),
                license="MIT",
                sandbox_profile=_default_sandbox(),
                validation_suite="v",
            )
        with pytest.raises(ValueError, match="engine_digest"):
            SimulatorAdapter(
                capability_id="x",
                adapter_version="1.0.0",
                engine_name="e",
                engine_version="1",
                engine_digest="tooshort",
                input_schema="i",
                output_schema="o",
                units_system="SI",
                determinism=Determinism.DETERMINISTIC,
                resources=_default_resources(),
                license="MIT",
                sandbox_profile=_default_sandbox(),
                validation_suite="v",
            )

    def test_adapter_rejects_invalid_determinism(self) -> None:
        with pytest.raises(ValueError, match="determinism"):
            SimulatorAdapter(
                capability_id="x",
                adapter_version="1.0.0",
                engine_name="e",
                engine_version="1",
                engine_digest=_SHA_ENGINE,
                input_schema="i",
                output_schema="o",
                units_system="SI",
                determinism="bogus",  # type: ignore[arg-type]
                resources=_default_resources(),
                license="MIT",
                sandbox_profile=_default_sandbox(),
                validation_suite="v",
            )

    def test_resource_limits_must_be_nonnegative(self) -> None:
        with pytest.raises(ValueError, match="cpu"):
            ResourceLimits(cpu=-1, memory_mb=100, gpu=0, timeout_s=10)
        with pytest.raises(ValueError, match="timeout_s"):
            ResourceLimits(cpu=1, memory_mb=100, gpu=0, timeout_s=-1)
        assert ResourceLimits(cpu=1, memory_mb=100, gpu=0, timeout_s=0).timeout_s == 0


# ---------------------------------------------------------------------------
# AIML-015 — run_simulation sandbox + validation (spec §8.2, AIML-AT-014/015)
# ---------------------------------------------------------------------------


class TestRunSimulation:
    def test_network_denied_by_default(self) -> None:
        """Spec §8.2: adapters run in network-denied sandboxes by default."""
        adapter = _default_adapter()
        # Adapter's sandbox is network-denied -> run proceeds; a caller trying
        # to override to network-allowed on a default-denied adapter is refused.
        result = run_simulation(
            adapter,
            inputs={"mass_kg": 1.0},
            engine_fn=lambda inp: {"force_n": inp["mass_kg"] * 9.81},
        )
        assert result.terminal_event == "completed"
        assert result.refused_reason is None
        assert result.validation_passed is True
        assert result.outputs["force_n"] == pytest.approx(9.81)

    def test_network_allowed_on_adapter_blocks_run(self) -> None:
        """Spec §8.2: network egress is allowlisted per role; an adapter that
        declares network_allowed=True cannot be run because the default sandbox
        contract requires network denied."""
        adapter = SimulatorAdapter(
            capability_id="simulator.x",
            adapter_version="1.0.0",
            engine_name="e",
            engine_version="1",
            engine_digest=_SHA_ENGINE,
            input_schema="i",
            output_schema="o",
            units_system="SI",
            determinism=Determinism.DETERMINISTIC,
            resources=_default_resources(),
            license="MIT",
            sandbox_profile=SandboxProfile(network_denied=False),
            validation_suite="v",
        )
        result = run_simulation(
            adapter,
            inputs={"x": 1.0},
            engine_fn=lambda inp: {"y": inp["x"]},
        )
        assert result.refused_reason is not None
        assert "network" in result.refused_reason.lower()
        assert result.terminal_event == "refused"
        # No scientific value is returned on refusal (spec §8.2, AIML-AT-015).
        assert result.outputs == {}

    def test_unit_normalization_applied(self) -> None:
        """Spec §8.2: adapters normalize units."""
        adapter = _default_adapter()

        def normalizer(
            values: Mapping[str, float], _system: str
        ) -> Mapping[str, float]:
            # Convert grams to kilograms when system is SI.
            return {k.replace("_g", "_kg"): v / 1000.0 if k.endswith("_g") else v for k, v in values.items()}

        result = run_simulation(
            adapter,
            inputs={"mass_g": 1000.0},
            engine_fn=lambda inp: {"mass_kg": inp["mass_kg"]},
            unit_normalizer=normalizer,
        )
        assert result.units_normalized is True
        assert result.outputs["mass_kg"] == pytest.approx(1.0)

    def test_output_validation_failure_refuses_result(self) -> None:
        """Spec §8.2: outputs are validated against engine-specific invariants.
        Unsupported fidelity or boundary conditions produce a refusal, not an
        extrapolated result (AIML-AT-014)."""
        adapter = _default_adapter()

        def validator(outputs: Mapping[str, float]) -> bool:
            # Invariant: force must be non-negative.
            return all(v >= 0 for v in outputs.values())

        result = run_simulation(
            adapter,
            inputs={"mass_kg": 1.0},
            engine_fn=lambda inp: {"force_n": -5.0},
            output_validator=validator,
        )
        assert result.validation_passed is False
        assert result.refused_reason is not None
        assert "validation" in result.refused_reason.lower()
        assert result.terminal_event == "refused"
        assert result.outputs == {}

    def test_resource_limit_timeout_refuses_result(self) -> None:
        """Spec §8.2 / AIML-AT-015: a simulator timeout kills children, emits
        a terminal event, and returns no scientific value."""
        adapter = SimulatorAdapter(
            capability_id="simulator.x",
            adapter_version="1.0.0",
            engine_name="e",
            engine_version="1",
            engine_digest=_SHA_ENGINE,
            input_schema="i",
            output_schema="o",
            units_system="SI",
            determinism=Determinism.DETERMINISTIC,
            resources=ResourceLimits(cpu=1, memory_mb=10, gpu=0, timeout_s=0),
            license="MIT",
            sandbox_profile=_default_sandbox(),
            validation_suite="v",
        )
        # timeout_s=0 means zero time budget; any engine call exceeds it.
        result = run_simulation(
            adapter,
            inputs={"x": 1.0},
            engine_fn=lambda inp: {"y": inp["x"]},
        )
        assert result.terminal_event == "timeout"
        assert result.refused_reason is not None
        assert result.outputs == {}

    def test_engine_exception_refuses_without_fabricating(self) -> None:
        """Spec §11 (Simulator crash/timeout): terminate sandbox, preserve
        bounded diagnostics, return no fabricated result."""
        adapter = _default_adapter()

        def boom(_inp: Mapping[str, float]) -> Mapping[str, float]:
            raise RuntimeError("engine segfaulted")

        result = run_simulation(
            adapter,
            inputs={"x": 1.0},
            engine_fn=boom,
        )
        assert result.terminal_event == "crashed"
        assert result.refused_reason is not None
        assert result.outputs == {}
