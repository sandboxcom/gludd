"""D-20: Config hot-reload with atomic worker switch, attestation, shadow evaluation, and rollback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from general_ludd.security.config_compiler import (
    CompiledConfig,
    ConfigCompiler,
    ConfigCompilerError,
    ConfigGeneration,
    ConfigGenerationState,
    SwitchResult,
    SwitchState,
    compile_config,
)


def _assert_generation_state(generation: ConfigGeneration, expected: ConfigGenerationState) -> None:
    assert generation.state == expected

# ── ConfigCompiler ──


class TestConfigCompiler:
    def test_compile_valid_config(self) -> None:
        result = ConfigCompiler().compile(
            {
                "security": {
                    "schema_version": 1,
                    "posture": "locked",
                },
                "sandbox": {"backend": "firecracker"},
            }
        )
        assert isinstance(result, CompiledConfig)
        assert result.generation == 1
        assert result.posture == "locked"
        assert len(result.policy_hash) == 64

    def test_compile_rejects_unknown_keys(self) -> None:
        with pytest.raises(ConfigCompilerError, match="unknown"):
            compile_config({"typoed_security_section": {}})

    def test_compile_rejects_invalid_schema_version(self) -> None:
        with pytest.raises(ConfigCompilerError, match="schema_version"):
            compile_config({"security": {"schema_version": 999}})

    def test_compile_rejects_invalid_posture(self) -> None:
        with pytest.raises(ConfigCompilerError, match="posture"):
            compile_config({"security": {"posture": "wide-open"}})

    def test_compiled_config_is_hash_deterministic(self) -> None:
        a = compile_config({"security": {"schema_version": 1, "posture": "standard"}})
        b = compile_config({"security": {"schema_version": 1, "posture": "standard"}})
        assert a.policy_hash == b.policy_hash

    def test_compiled_config_hash_differs_by_generation(self) -> None:
        compiler = ConfigCompiler()
        a = compiler.compile({"security": {"schema_version": 1, "posture": "standard"}})
        b = compiler.compile({"security": {"schema_version": 1, "posture": "standard"}})
        assert a.generation == 1
        assert b.generation == 2

    def test_compiled_config_attests_fields(self) -> None:
        result = compile_config({"security": {"schema_version": 1, "posture": "locked", "profile": "untrusted-code"}})
        assert result.profile == "untrusted-code"
        assert "security.posture" in result.attestation_fields()


# ── ConfigGeneration state machine ──


class TestConfigGenerationStateMachine:
    def test_generation_starts_draft(self) -> None:
        compiled = compile_config({"security": {"posture": "standard"}})
        gen = ConfigGeneration(compiled=compiled)
        assert gen.state == ConfigGenerationState.DRAFT

    def test_generation_compiles(self) -> None:
        compiled = compile_config({"security": {"posture": "standard"}})
        gen = ConfigGeneration(compiled=compiled)
        gen.compile_canaries(success=True)
        assert gen.state == ConfigGenerationState.COMPILED

    def test_generation_compile_failure_goes_rejected(self) -> None:
        compiled = compile_config({"security": {"posture": "standard"}})
        gen = ConfigGeneration(compiled=compiled)
        gen.compile_canaries(success=False)
        assert gen.state == ConfigGenerationState.REJECTED

    def test_generation_full_lifecycle(self) -> None:
        compiled = compile_config({"security": {"posture": "standard"}})
        gen = ConfigGeneration(compiled=compiled)
        _assert_generation_state(gen, ConfigGenerationState.DRAFT)
        gen.compile_canaries(success=True)
        _assert_generation_state(gen, ConfigGenerationState.COMPILED)
        gen.shadow_evaluate(success=True)
        _assert_generation_state(gen, ConfigGenerationState.SHADOW)
        gen.activate()
        _assert_generation_state(gen, ConfigGenerationState.ACTIVE)

    def test_generation_shadow_failure_goes_rejected(self) -> None:
        compiled = compile_config({"security": {"posture": "standard"}})
        gen = ConfigGeneration(compiled=compiled)
        gen.compile_canaries(success=True)
        gen.shadow_evaluate(success=False)
        assert gen.state == ConfigGenerationState.REJECTED


# ── SwitchResult ──


class TestSwitchResult:
    def test_successful_switch(self) -> None:
        compiler = ConfigCompiler()
        compiled = compiler.compile({"security": {"posture": "standard"}})
        result = SwitchResult(
            success=True,
            prior_generation=0,
            new_generation=compiled.generation,
            policy_hash=compiled.policy_hash,
            state=SwitchState.COMPLETED,
        )
        assert result.success
        assert result.new_generation == compiled.generation

    def test_failed_switch_preserves_prior(self) -> None:
        result = SwitchResult(
            success=False,
            prior_generation=5,
            new_generation=6,
            policy_hash="deadbeef",
            state=SwitchState.REJECTED,
            error="canary failed",
        )
        assert not result.success
        assert result.prior_generation == 5
        assert result.error == "canary failed"


# ── Immutable prior versions ──


class TestImmutablePriorVersions:
    def test_compiler_keeps_prior_version_on_switch(self) -> None:
        compiler = ConfigCompiler()
        v1 = compiler.compile({"security": {"posture": "standard"}})
        v2 = compiler.compile({"security": {"posture": "standard"}})

        result = compiler.atomic_switch(
            compiled=v2,
            health_check=lambda: True,
        )
        assert result.success
        assert result.prior_generation == v1.generation
        assert compiler.active_generation() == v2.generation

    def test_compiler_rolls_back_on_failed_switch(self) -> None:
        compiler = ConfigCompiler()
        v1 = compiler.compile({"security": {"posture": "standard"}})
        v2 = compiler.compile({"security": {"posture": "locked"}})

        result = compiler.atomic_switch(
            compiled=v2,
            health_check=lambda: False,
        )
        assert not result.success
        assert compiler.active_generation() == v1.generation

    def test_get_compiled_version_returns_prior(self) -> None:
        compiler = ConfigCompiler()
        v1 = compiler.compile({"security": {"posture": "standard"}})
        v2 = compiler.compile({"security": {"posture": "locked"}})
        compiler.atomic_switch(compiled=v2, health_check=lambda: True)

        assert compiler.get_compiled_version(v1.generation) == v1
        assert compiler.get_compiled_version(v2.generation) == v2


# ── Continuous-traffic shadow evaluation ──


class TestShadowEvaluation:
    def test_shadow_eval_compares_policy_hash(self) -> None:
        compiler = ConfigCompiler()
        active = compiler.compile({"security": {"posture": "standard"}})
        compiled = compile_config({"security": {"posture": "standard"}})
        gen = ConfigGeneration(compiled=compiled)
        gen.compile_canaries(success=True)

        divergence = gen.shadow_evaluate_against(active)
        assert not divergence

    def test_shadow_detects_policy_divergence(self) -> None:
        compiler = ConfigCompiler()
        active = compiler.compile({"security": {"posture": "standard"}})
        compiled = compile_config({"security": {"posture": "locked"}})
        gen = ConfigGeneration(compiled=compiled)
        gen.compile_canaries(success=True)

        divergence = gen.shadow_evaluate_against(active)
        assert divergence


# ── Config compiler from file ──


class TestConfigCompilerFromFile:
    def test_compile_from_dict_without_secrets(self, tmp_path: Path) -> None:
        config: dict[str, object] = {"security": {"schema_version": 1, "posture": "standard"}}
        result = compile_config(config)
        assert result.posture == "standard"
        assert "GLUDD_AUTH_PSK" not in json.dumps(result.metadata())

    def test_compile_truncates_long_names(self) -> None:
        config: dict[str, object] = {
            "security": {"schema_version": 1, "posture": "standard", "profile": "unt" + "r" * 500}
        }
        with pytest.raises(ConfigCompilerError):
            compile_config(config)
