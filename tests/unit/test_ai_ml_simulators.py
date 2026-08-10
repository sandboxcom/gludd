"""Unit tests for AIML Phase E: simulator adapter framework (AIML-015, spec §8.2).

Dedicated tests for ``src/general_ludd/ai_ml/simulators.py`` — complements
the SimulatorAdapter/run_simulation tests in ``test_ai_ml_world_models.py``
by covering every dataclass, every validation path, every
run_simulation refusal branch, and the private helpers.

Does NOT duplicate:
  - SimulatorAdapter field-declaration test (test_ai_ml_world_models.py:338-356)
  - SimulatorAdapter semver/digest/determinism rejection tests
    (test_ai_ml_world_models.py:358-409)
  - ResourceLimits negative-value test
    (test_ai_ml_world_models.py:411-416)
  - run_simulation: network-denied default, network-allowed refusal,
    unit normalization, output-validation failure, timeout_s=0 refusal,
    engine exception (test_ai_ml_world_models.py:424-552)
"""

from __future__ import annotations

import pytest

from general_ludd.ai_ml.simulators import (
    Determinism,
    ResourceLimits,
    SandboxProfile,
    SimulationResult,
    SimulatorAdapter,
    run_simulation,
)

_SHA = "a" * 64


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _default_resources(**overrides: object) -> ResourceLimits:
    kwargs: dict[str, object] = {"cpu": 2, "memory_mb": 1024, "gpu": 0, "timeout_s": 60}
    kwargs.update(overrides)
    return ResourceLimits(**kwargs)  # type: ignore[arg-type]


def _default_sandbox(**overrides: object) -> SandboxProfile:
    kwargs: dict[str, object] = {"network_denied": True}
    kwargs.update(overrides)
    return SandboxProfile(**kwargs)  # type: ignore[arg-type]


def _default_adapter(**overrides: object) -> SimulatorAdapter:
    kwargs: dict[str, object] = {
        "capability_id": "simulator.physics.rigidbody",
        "adapter_version": "1.0.0",
        "engine_name": "bullet3",
        "engine_version": "3.25",
        "engine_digest": _SHA,
        "input_schema": "artifact://schemas/in.json",
        "output_schema": "artifact://schemas/out.json",
        "units_system": "SI",
        "determinism": Determinism.SEEDED,
        "resources": _default_resources(),
        "license": "Zlib",
        "sandbox_profile": _default_sandbox(),
        "validation_suite": "artifact://suites/v1.json",
    }
    kwargs.update(overrides)
    return SimulatorAdapter(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_enum_has_three_values(self) -> None:
        assert Determinism.DETERMINISTIC == "deterministic"
        assert Determinism.SEEDED == "seeded"
        assert Determinism.STOCHASTIC == "stochastic"

    def test_coerce_from_string(self) -> None:
        assert Determinism("deterministic") == Determinism.DETERMINISTIC
        assert Determinism("seeded") == Determinism.SEEDED
        assert Determinism("stochastic") == Determinism.STOCHASTIC

    def test_coerce_rejects_invalid_string(self) -> None:
        with pytest.raises(ValueError):
            Determinism("bogus")


# ---------------------------------------------------------------------------
# ResourceLimits
# ---------------------------------------------------------------------------


class TestResourceLimits:
    def test_defaults(self) -> None:
        r = ResourceLimits(cpu=1, memory_mb=100)
        assert r.gpu == 0
        assert r.timeout_s == 60

    def test_bool_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="cpu"):
            ResourceLimits(cpu=True, memory_mb=100)  # type: ignore[arg-type]

    def test_float_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="cpu"):
            ResourceLimits(cpu=1.5, memory_mb=100)  # type: ignore[arg-type]

    def test_memory_bool_rejected(self) -> None:
        with pytest.raises(ValueError, match="memory_mb"):
            ResourceLimits(cpu=1, memory_mb=False)  # type: ignore[arg-type]

    def test_gpu_bool_rejected(self) -> None:
        with pytest.raises(ValueError, match="gpu"):
            ResourceLimits(cpu=1, memory_mb=100, gpu=False)  # type: ignore[arg-type]

    def test_timeout_bool_rejected(self) -> None:
        with pytest.raises(ValueError, match="timeout_s"):
            ResourceLimits(cpu=1, memory_mb=100, timeout_s=False)  # type: ignore[arg-type]

    def test_timeout_at_floor_constructs(self) -> None:
        r = ResourceLimits(cpu=1, memory_mb=100, timeout_s=0)
        assert r.timeout_s == 0

    def test_negative_gpu_rejected(self) -> None:
        with pytest.raises(ValueError, match="gpu"):
            ResourceLimits(cpu=1, memory_mb=100, gpu=-1)


# ---------------------------------------------------------------------------
# SandboxProfile
# ---------------------------------------------------------------------------


class TestSandboxProfile:
    def test_defaults(self) -> None:
        s = SandboxProfile()
        assert s.network_denied is True
        assert s.filesystem_writable_paths == ()
        assert s.env_allowlist == ()

    def test_network_denied_must_be_bool(self) -> None:
        with pytest.raises(ValueError, match="network_denied"):
            SandboxProfile(network_denied="yes")  # type: ignore[arg-type]

    def test_filesystem_paths_must_be_strings(self) -> None:
        with pytest.raises(ValueError, match="filesystem_writable_paths"):
            SandboxProfile(filesystem_writable_paths=(1,))  # type: ignore[arg-type]

    def test_filesystem_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="filesystem_writable_paths"):
            SandboxProfile(filesystem_writable_paths=("   ",))  # type: ignore[arg-type]

    def test_filesystem_not_a_tuple_rejected(self) -> None:
        with pytest.raises(ValueError, match="filesystem_writable_paths"):
            SandboxProfile(filesystem_writable_paths=["/tmp"])  # type: ignore[arg-type]

    def test_env_allowlist_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="env_allowlist"):
            SandboxProfile(env_allowlist=("",))

    def test_valid_paths_construct(self) -> None:
        s = SandboxProfile(
            filesystem_writable_paths=("/tmp/work", "/var/run"),
            env_allowlist=("HOME", "PATH"),
        )
        assert len(s.filesystem_writable_paths) == 2
        assert len(s.env_allowlist) == 2


# ---------------------------------------------------------------------------
# SimulatorAdapter — additional validation paths
# ---------------------------------------------------------------------------


class TestSimulatorAdapter:
    def test_adapter_rejects_non_resource_limits(self) -> None:
        with pytest.raises(ValueError, match="resources"):
            _default_adapter(resources="not-resources")  # type: ignore[arg-type]

    def test_adapter_rejects_non_sandbox_profile(self) -> None:
        with pytest.raises(ValueError, match="sandbox_profile"):
            _default_adapter(sandbox_profile="not-sandbox")  # type: ignore[arg-type]

    def test_capability_id_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="capability_id"):
            _default_adapter(capability_id="")

    def test_capability_id_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="capability_id"):
            _default_adapter(capability_id="   ")

    def test_engine_name_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="engine_name"):
            _default_adapter(engine_name="")

    def test_license_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="license"):
            _default_adapter(license="")

    def test_validation_suite_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="validation_suite"):
            _default_adapter(validation_suite="")

    def test_units_system_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="units_system"):
            _default_adapter(units_system="")

    def test_prerelease_semver_accepted(self) -> None:
        a = _default_adapter(adapter_version="2.0.0-alpha.1")
        assert a.adapter_version == "2.0.0-alpha.1"

    def test_build_metadata_semver_accepted(self) -> None:
        a = _default_adapter(adapter_version="2.0.0+build.42")
        assert a.adapter_version == "2.0.0+build.42"

    def test_determinism_from_string_coercion(self) -> None:
        a = _default_adapter(determinism="stochastic")
        assert a.determinism == Determinism.STOCHASTIC

    def test_determinism_from_enum_passthrough(self) -> None:
        a = _default_adapter(determinism=Determinism.DETERMINISTIC)
        assert a.determinism == Determinism.DETERMINISTIC


# ---------------------------------------------------------------------------
# SimulationResult
# ---------------------------------------------------------------------------


class TestSimulationResult:
    def test_default_noop_result(self) -> None:
        r = SimulationResult()
        assert r.outputs == {}
        assert r.units_normalized is False
        assert r.validation_passed is False
        assert r.terminal_event == "refused"
        assert r.refused_reason is None
        assert r.wall_clock_s == 0.0

    def test_completed_result(self) -> None:
        r = SimulationResult(
            outputs={"force_n": 9.81},
            units_normalized=True,
            validation_passed=True,
            terminal_event="completed",
            wall_clock_s=0.001,
        )
        assert r.outputs["force_n"] == pytest.approx(9.81)
        assert r.units_normalized is True
        assert r.validation_passed is True
        assert r.terminal_event == "completed"
        assert r.refused_reason is None
        assert r.wall_clock_s == 0.001

    def test_crashed_result(self) -> None:
        r = SimulationResult(
            terminal_event="crashed",
            refused_reason="engine segfaulted",
            wall_clock_s=0.05,
        )
        assert r.outputs == {}
        assert r.terminal_event == "crashed"
        assert r.refused_reason == "engine segfaulted"

    def test_frozen(self) -> None:
        r = SimulationResult(terminal_event="completed")
        with pytest.raises(AttributeError):
            r.terminal_event = "refused"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# run_simulation — refusal / error branches
# ---------------------------------------------------------------------------


class TestRunSimulationRefusals:
    def test_non_adapter_raises_immediate(self) -> None:
        with pytest.raises(ValueError, match="adapter"):
            run_simulation(
                adapter="not-adapter",  # type: ignore[arg-type]
                inputs={"x": 1.0},
                engine_fn=lambda i: i,
            )

    def test_non_mapping_inputs_raises(self) -> None:
        with pytest.raises(ValueError, match="inputs"):
            run_simulation(
                adapter=_default_adapter(),
                inputs=[("x", 1.0)],  # type: ignore[arg-type]
                engine_fn=lambda i: i,
            )

    def test_caller_network_not_denied_refuses(self) -> None:
        """sandbox_network_denied=False + adapter network_denied=True → effective=False → REFUSED."""
        adapter = _default_adapter()
        result = run_simulation(
            adapter,
            inputs={"x": 1.0},
            engine_fn=lambda i: {"y": i["x"]},
            sandbox_network_denied=False,
        )
        assert result.terminal_event == "refused"
        assert "network" in (result.refused_reason or "")

    def test_both_network_allowed_refuses(self) -> None:
        """Both adapter and caller allow network → effective=False → REFUSED."""
        adapter = _default_adapter(sandbox_profile=SandboxProfile(network_denied=False))
        result = run_simulation(
            adapter,
            inputs={"x": 1.0},
            engine_fn=lambda i: {"y": i["x"]},
            sandbox_network_denied=False,
        )
        assert result.terminal_event == "refused"
        assert "network" in (result.refused_reason or "")

    def test_timeout_at_floor_0_refuses(self) -> None:
        adapter = _default_adapter(resources=_default_resources(timeout_s=0))
        result = run_simulation(
            adapter,
            inputs={"x": 1.0},
            engine_fn=lambda i: {"y": i["x"]},
        )
        assert result.terminal_event == "timeout"
        assert result.outputs == {}

    def test_timeout_at_floor_1_proceeds(self) -> None:
        """timeout_s=1 is >= _MIN_TIMEOUT_S (1) and should not be refused upfront."""
        adapter = _default_adapter(resources=_default_resources(timeout_s=1))
        result = run_simulation(
            adapter,
            inputs={"x": 1.0},
            engine_fn=lambda i: {"y": i["x"]},
        )
        assert result.terminal_event == "completed"

    def test_unit_normalizer_raises_refuses(self) -> None:
        def bad_normalizer(inp, system):
            raise ValueError("cannot convert units")

        result = run_simulation(
            adapter=_default_adapter(),
            inputs={"x": 1.0},
            engine_fn=lambda i: {"y": i["x"]},
            unit_normalizer=bad_normalizer,
        )
        assert result.terminal_event == "refused"
        assert "unit normalization" in (result.refused_reason or "")
        assert result.outputs == {}
        assert result.units_normalized is False

    def test_engine_returns_non_mapping(self) -> None:
        result = run_simulation(
            adapter=_default_adapter(),
            inputs={"x": 1.0},
            engine_fn=lambda i: 42,  # type: ignore[arg-type]
        )
        assert result.terminal_event == "refused"
        assert "non-Mapping" in (result.refused_reason or "")
        assert result.outputs == {}

    def test_engine_returns_list_refused(self) -> None:
        result = run_simulation(
            adapter=_default_adapter(),
            inputs={"x": 1.0},
            engine_fn=lambda i: [1, 2, 3],  # type: ignore[arg-type]
        )
        assert result.terminal_event == "refused"
        assert result.outputs == {}

    def test_output_validator_raises_refuses(self) -> None:
        def raising_validator(outputs):
            raise RuntimeError("validator failed")

        result = run_simulation(
            adapter=_default_adapter(),
            inputs={"x": 1.0},
            engine_fn=lambda i: {"y": i["x"]},
            output_validator=raising_validator,
        )
        assert result.terminal_event == "refused"
        assert "output validator raised" in (result.refused_reason or "")
        assert result.outputs == {}

    def test_output_validator_passes(self) -> None:
        def ok_validator(outputs):
            return True

        result = run_simulation(
            adapter=_default_adapter(),
            inputs={"x": 1.0},
            engine_fn=lambda i: {"y": i["x"]},
            output_validator=ok_validator,
        )
        assert result.terminal_event == "completed"
        assert result.validation_passed is True

    def test_wall_clock_recorded_on_success(self) -> None:
        result = run_simulation(
            adapter=_default_adapter(),
            inputs={"x": 1.0},
            engine_fn=lambda i: {"y": i["x"]},
        )
        assert result.wall_clock_s > 0.0

    def test_wall_clock_on_exception_is_zero(self) -> None:
        """_refused helper returns wall_clock_s=0.0 even though elapsed is measured."""
        result = run_simulation(
            adapter=_default_adapter(),
            inputs={"x": 1.0},
            engine_fn=lambda i: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert result.wall_clock_s == 0.0
        assert result.terminal_event == "crashed"

    def test_full_success_path_with_normalizer_and_validator(self) -> None:
        def normalizer(inp, system):
            return {k.replace("_cm", "_m"): v / 100.0 for k, v in inp.items()}

        def validator(out):
            return all(v >= 0 for v in out.values())

        result = run_simulation(
            adapter=_default_adapter(),
            inputs={"height_cm": 250.0},
            engine_fn=lambda i: {"height_m": i["height_m"]},
            unit_normalizer=normalizer,
            output_validator=validator,
        )
        assert result.terminal_event == "completed"
        assert result.units_normalized is True
        assert result.validation_passed is True
        assert result.outputs["height_m"] == pytest.approx(2.5)
        assert result.wall_clock_s > 0.0
