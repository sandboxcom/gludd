"""Unit tests for src/general_ludd/security/config_compiler.py.

Covers: config compilation, template expansion, validation, error handling,
edge cases, security policy generation, state machine lifecycle, atomic switch.
"""

from __future__ import annotations

import re

import pytest

from general_ludd.security.config_compiler import (
    _ALLOWED_TOP_KEYS,
    _VALID_BACKENDS,
    _VALID_POSTURES,
    _VALID_SCHEMA_VERSIONS,
    CompiledConfig,
    ConfigCompiler,
    ConfigCompilerError,
    ConfigGeneration,
    ConfigGenerationState,
    SwitchResult,
    SwitchState,
    compile_config,
)


def _minimal_raw() -> dict[str, object]:
    return {"security": {"posture": "standard"}}


def _rich_raw() -> dict[str, object]:
    return {
        "security": {
            "schema_version": 1,
            "posture": "locked",
            "profile": "no-network",
        },
        "sandbox": {
            "backend": "nsjail",
        },
        "network": {"allow_outbound": False},
        "resources": {"max_memory_mb": 256},
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_valid_postures() -> None:
    assert frozenset({"locked", "standard", "development"}) == _VALID_POSTURES


def test_valid_backends() -> None:
    assert frozenset({"firecracker", "gvisor", "nsjail", "bubblewrap", "landlock", "seccomp"}) == _VALID_BACKENDS


def test_valid_schema_versions() -> None:
    assert frozenset({1}) == _VALID_SCHEMA_VERSIONS
    assert 1 in _VALID_SCHEMA_VERSIONS


def test_allowed_top_keys() -> None:
    assert "security" in _ALLOWED_TOP_KEYS
    assert "sandbox" in _ALLOWED_TOP_KEYS
    assert "network" in _ALLOWED_TOP_KEYS


# ---------------------------------------------------------------------------
# ConfigGenerationState / SwitchState enums
# ---------------------------------------------------------------------------


def test_config_generation_state_values() -> None:
    assert ConfigGenerationState.DRAFT.value == "draft"
    assert ConfigGenerationState.COMPILED.value == "compiled"
    assert ConfigGenerationState.SHADOW.value == "shadow"
    assert ConfigGenerationState.ACTIVE.value == "active"
    assert ConfigGenerationState.REJECTED.value == "rejected"


def test_switch_state_values() -> None:
    assert SwitchState.COMPLETED.value == "completed"
    assert SwitchState.REJECTED.value == "rejected"
    assert SwitchState.PREPARING.value == "preparing"


# ---------------------------------------------------------------------------
# ConfigCompilerError
# ---------------------------------------------------------------------------


def test_compiler_error_is_exception() -> None:
    err = ConfigCompilerError("test")
    assert isinstance(err, Exception)
    assert str(err) == "test"


# ---------------------------------------------------------------------------
# SwitchResult
# ---------------------------------------------------------------------------


def test_switch_result_success() -> None:
    result = SwitchResult(
        success=True,
        prior_generation=1,
        new_generation=2,
        policy_hash="abc123",
        state=SwitchState.COMPLETED,
    )
    assert result.success is True
    assert result.prior_generation == 1
    assert result.new_generation == 2
    assert result.policy_hash == "abc123"
    assert result.state == SwitchState.COMPLETED
    assert result.error is None


def test_switch_result_failure() -> None:
    result = SwitchResult(
        success=False,
        prior_generation=3,
        new_generation=4,
        policy_hash="deadbeef",
        state=SwitchState.REJECTED,
        error="health check failed",
    )
    assert result.success is False
    assert result.error == "health check failed"


# ---------------------------------------------------------------------------
# CompiledConfig
# ---------------------------------------------------------------------------


def test_compiled_config_defaults() -> None:
    cfg = CompiledConfig(
        generation=1,
        posture="standard",
        profile="default",
        backend="firecracker",
        policy_hash="abcdef",
    )
    assert cfg.generation == 1
    assert cfg.posture == "standard"
    assert cfg.profile == "default"
    assert cfg.backend == "firecracker"
    assert cfg.policy_hash == "abcdef"
    assert cfg.metadata_fields == {}
    assert cfg._raw == {}


def test_compiled_config_custom_fields() -> None:
    cfg = CompiledConfig(
        generation=5,
        posture="locked",
        profile="no-network",
        backend="nsjail",
        policy_hash="sha256...",
        metadata_fields={"source": "test"},
        _raw={"security": {"posture": "locked"}},
    )
    assert cfg.generation == 5
    assert cfg.metadata_fields == {"source": "test"}


def test_compiled_config_immutable() -> None:
    cfg = CompiledConfig(
        generation=1,
        posture="standard",
        profile="default",
        backend="firecracker",
        policy_hash="abc",
    )
    with pytest.raises(AttributeError):
        cfg.generation = 2  # type: ignore[misc]


def test_compiled_config_metadata_no_secrets() -> None:
    cfg = CompiledConfig(
        generation=7,
        posture="locked",
        profile="restricted",
        backend="gvisor",
        policy_hash="abcd1234",
        _raw={"secrets": {"api_key": "test-key-not-a-real-secret"}},
    )
    meta = cfg.metadata()
    assert meta == {
        "generation": 7,
        "posture": "locked",
        "profile": "restricted",
        "backend": "gvisor",
        "policy_hash": "abcd1234",
    }
    assert "secrets" not in meta
    assert "api_key" not in str(meta)


def test_compiled_config_attestation_fields_dict() -> None:
    cfg = CompiledConfig(
        generation=1,
        posture="standard",
        profile="default",
        backend="firecracker",
        policy_hash="abc",
        _raw={
            "security": {"posture": "locked"},
            "sandbox": {"backend": "nsjail"},
            "network": {"allow_outbound": False},
        },
    )
    fields = cfg.attestation_fields()
    assert "security.posture" in fields
    assert "sandbox.backend" in fields
    assert "network.allow_outbound" in fields


def test_compiled_config_attestation_fields_non_dict() -> None:
    cfg = CompiledConfig(
        generation=1,
        posture="standard",
        profile="default",
        backend="firecracker",
        policy_hash="abc",
        _raw={"security": "locked", "sandbox": "nsjail"},
    )
    fields = cfg.attestation_fields()
    assert "security" in fields
    assert "sandbox" in fields


def test_compiled_config_attestation_fields_empty_raw() -> None:
    cfg = CompiledConfig(
        generation=1,
        posture="standard",
        profile="default",
        backend="firecracker",
        policy_hash="abc",
    )
    fields = cfg.attestation_fields()
    assert fields == []


def test_compiled_config_attestation_fields_sorted() -> None:
    cfg = CompiledConfig(
        generation=1,
        posture="standard",
        profile="default",
        backend="firecracker",
        policy_hash="abc",
        _raw={
            "sandbox": {"b": 1},
            "security": {"a": 1},
        },
    )
    fields = cfg.attestation_fields()
    assert fields == sorted(fields)


# ---------------------------------------------------------------------------
# ConfigGeneration — state machine lifecycle
# ---------------------------------------------------------------------------


def _make_compiled(gen: int = 1, policy_hash: str = "test-hash") -> CompiledConfig:
    return CompiledConfig(
        generation=gen,
        posture="standard",
        profile="default",
        backend="firecracker",
        policy_hash=policy_hash,
    )


def test_generation_initial_state() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    assert gen.state == ConfigGenerationState.DRAFT


def test_generation_compile_canaries_success() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    assert gen.state == ConfigGenerationState.COMPILED


def test_generation_compile_canaries_failure() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=False)
    assert gen.state == ConfigGenerationState.REJECTED


def test_generation_compile_canaries_idempotent_after_compiled() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    gen.compile_canaries(success=False)
    assert gen.state == ConfigGenerationState.COMPILED


def test_generation_shadow_evaluate_success() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    gen.shadow_evaluate(success=True)
    assert gen.state == ConfigGenerationState.SHADOW


def test_generation_shadow_evaluate_failure() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    gen.shadow_evaluate(success=False)
    assert gen.state == ConfigGenerationState.REJECTED


def test_generation_shadow_evaluate_skips_if_not_compiled() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.shadow_evaluate(success=True)
    assert gen.state == ConfigGenerationState.DRAFT


def test_generation_activate_valid() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    gen.shadow_evaluate(success=True)
    gen.activate()
    assert gen.state == ConfigGenerationState.ACTIVE


def test_generation_activate_invalid_state() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    with pytest.raises(ConfigCompilerError) as exc:
        gen.activate()
    assert "cannot activate generation in state draft" in str(exc.value)


def test_generation_activate_from_compiled_raises() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    with pytest.raises(ConfigCompilerError) as exc:
        gen.activate()
    assert "compiled" in str(exc.value)


def test_generation_drain_from_active() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    gen.shadow_evaluate(success=True)
    gen.activate()
    gen.drain()
    assert gen.state == ConfigGenerationState.DRAINING


def test_generation_drain_idempotent() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    gen.shadow_evaluate(success=True)
    gen.activate()
    gen.drain()
    gen.drain()
    assert gen.state == ConfigGenerationState.DRAINING


def test_generation_drain_skips_retired() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    gen.shadow_evaluate(success=True)
    gen.activate()
    gen.drain()
    gen.retire()
    gen.drain()
    assert gen.state == ConfigGenerationState.RETIRED


def test_generation_retire_from_draining() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    gen.shadow_evaluate(success=True)
    gen.activate()
    gen.drain()
    gen.retire()
    assert gen.state == ConfigGenerationState.RETIRED


def test_generation_retire_skips_active() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.compile_canaries(success=True)
    gen.shadow_evaluate(success=True)
    gen.activate()
    gen.retire()
    assert gen.state == ConfigGenerationState.ACTIVE


def test_generation_shadow_evaluate_against_match() -> None:
    compiled1 = _make_compiled(gen=1, policy_hash="same-hash-12345")
    compiled2 = _make_compiled(gen=2, policy_hash="same-hash-12345")
    gen = ConfigGeneration(compiled=compiled1)
    diverged = gen.shadow_evaluate_against(compiled2)
    assert diverged is False
    assert gen.shadow_divergence == []


def test_generation_shadow_evaluate_against_diverged() -> None:
    compiled1 = _make_compiled(gen=1, policy_hash="hash-old-12345")
    compiled2 = _make_compiled(gen=2, policy_hash="hash-new-67890")
    gen = ConfigGeneration(compiled=compiled1)
    diverged = gen.shadow_evaluate_against(compiled2)
    assert diverged is True
    assert len(gen.shadow_divergence) == 1
    assert "policy_hash differs" in gen.shadow_divergence[0]


def test_generation_canary_results() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    gen.canary_results["check1"] = True
    gen.canary_results["check2"] = False
    assert gen.canary_results == {"check1": True, "check2": False}


def test_generation_shadow_divergence_init() -> None:
    gen = ConfigGeneration(compiled=_make_compiled())
    assert gen.shadow_divergence == []


# ---------------------------------------------------------------------------
# ConfigCompiler — compile
# ---------------------------------------------------------------------------


def test_compiler_compile_minimal() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile(_minimal_raw())
    assert isinstance(cfg, CompiledConfig)
    assert cfg.generation == 1
    assert cfg.posture == "standard"
    assert cfg.profile == "untrusted-code"
    assert cfg.backend == "firecracker"
    assert isinstance(cfg.policy_hash, str)
    assert len(cfg.policy_hash) == 64


def test_compiler_compile_sets_active_on_first() -> None:
    compiler = ConfigCompiler()
    compiler.compile(_minimal_raw())
    assert compiler.active_generation() == 1


def test_compiler_compile_custom_values() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile(_rich_raw())
    assert cfg.posture == "locked"
    assert cfg.profile == "no-network"
    assert cfg.backend == "nsjail"


def test_compiler_deterministic_policy_hash() -> None:
    raw = _rich_raw()
    compiler1 = ConfigCompiler()
    compiler2 = ConfigCompiler()
    cfg1 = compiler1.compile(raw)
    cfg2 = compiler2.compile(raw)
    assert cfg1.policy_hash == cfg2.policy_hash


def test_compiler_different_hash_for_different_config() -> None:
    compiler = ConfigCompiler()
    cfg1 = compiler.compile({"security": {"posture": "standard"}})
    cfg2 = compiler.compile({"security": {"posture": "locked"}})
    assert cfg1.policy_hash != cfg2.policy_hash


def test_compiler_generation_increments() -> None:
    compiler = ConfigCompiler()
    cfg1 = compiler.compile(_minimal_raw())
    cfg2 = compiler.compile(_minimal_raw())
    cfg3 = compiler.compile(_minimal_raw())
    assert cfg1.generation == 1
    assert cfg2.generation == 2
    assert cfg3.generation == 3


def test_compiler_generation_same_policy_hash_same_config() -> None:
    compiler = ConfigCompiler()
    cfg1 = compiler.compile(_minimal_raw())
    cfg2 = compiler.compile(_minimal_raw())
    assert cfg1.policy_hash == cfg2.policy_hash
    assert cfg1.generation != cfg2.generation


def test_compiler_active_generation_unchanged_after_second() -> None:
    compiler = ConfigCompiler()
    compiler.compile(_minimal_raw())
    assert compiler.active_generation() == 1
    compiler.compile(_minimal_raw())
    assert compiler.active_generation() == 1


def test_compiler_get_compiled_version() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile(_minimal_raw())
    retrieved = compiler.get_compiled_version(cfg.generation)
    assert retrieved is cfg


def test_compiler_get_compiled_version_missing() -> None:
    compiler = ConfigCompiler()
    assert compiler.get_compiled_version(999) is None


def test_compiler_store_compiled() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile(_minimal_raw())
    assert compiler.get_compiled_version(cfg.generation) is not None


# ---------------------------------------------------------------------------
# ConfigCompiler — atomic_switch
# ---------------------------------------------------------------------------


def test_atomic_switch_success() -> None:
    compiler = ConfigCompiler()
    _ = compiler.compile(_minimal_raw())
    cfg2 = compiler.compile(_minimal_raw())
    result = compiler.atomic_switch(cfg2, health_check=lambda: True)
    assert result.success is True
    assert result.state == SwitchState.COMPLETED
    assert result.prior_generation == 1
    assert result.new_generation == 2
    assert compiler.active_generation() == 2


def test_atomic_switch_health_check_fails() -> None:
    compiler = ConfigCompiler()
    _ = compiler.compile(_minimal_raw())
    cfg2 = compiler.compile(_minimal_raw())
    result = compiler.atomic_switch(cfg2, health_check=lambda: False)
    assert result.success is False
    assert result.state == SwitchState.REJECTED
    assert "health check failed after activation" in str(result.error)
    assert compiler.active_generation() == 1


def test_atomic_switch_health_check_raises() -> None:
    compiler = ConfigCompiler()
    _ = compiler.compile(_minimal_raw())
    cfg2 = compiler.compile(_minimal_raw())

    def failing_check() -> bool:
        raise RuntimeError("boom")

    result = compiler.atomic_switch(cfg2, health_check=failing_check)
    assert result.success is False
    assert "health check failed after activation" in str(result.error)
    assert compiler.active_generation() == 1


def test_atomic_switch_shadow_divergence() -> None:
    compiler = ConfigCompiler()
    _ = compiler.compile(_minimal_raw())
    cfg2 = compiler.compile({"security": {"posture": "development"}})
    result = compiler.atomic_switch(cfg2, health_check=lambda: True)
    assert result.success is False
    assert "shadow evaluation detected policy divergence" in str(result.error)
    assert compiler.active_generation() == 1


def test_atomic_switch_incrementing_generations() -> None:
    compiler = ConfigCompiler()
    _ = compiler.compile(_minimal_raw())
    for i in range(3):
        cfg = compiler.compile({"security": {"posture": "development", "profile": f"p{i}"}})
        result = compiler.atomic_switch(cfg, health_check=lambda: True)
        assert result.success is False
    assert compiler.active_generation() == 1


# ---------------------------------------------------------------------------
# Validation — unknown keys
# ---------------------------------------------------------------------------


def test_compile_unknown_top_key_raises() -> None:
    compiler = ConfigCompiler()
    with pytest.raises(ConfigCompilerError) as exc:
        compiler.compile({"security": {"posture": "standard"}, "unknown_section": {}})
    assert "unknown top-level config keys" in str(exc.value)
    assert "unknown_section" in str(exc.value)


def test_compile_multiple_unknown_keys() -> None:
    compiler = ConfigCompiler()
    with pytest.raises(ConfigCompilerError) as exc:
        compiler.compile({"bad1": {}, "bad2": {}, "security": {}})
    msg = str(exc.value)
    assert "bad1" in msg
    assert "bad2" in msg


# ---------------------------------------------------------------------------
# Validation — schema_version
# ---------------------------------------------------------------------------


def test_compile_bad_schema_version_raises() -> None:
    compiler = ConfigCompiler()
    with pytest.raises(ConfigCompilerError) as exc:
        compiler.compile({"security": {"schema_version": 99, "posture": "standard"}})
    assert "unsupported schema_version" in str(exc.value)
    assert "99" in str(exc.value)


def test_compile_schema_version_none_ok() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile({"security": {"posture": "standard"}})
    assert cfg is not None


def test_compile_valid_schema_version() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile({"security": {"schema_version": 1, "posture": "standard"}})
    assert cfg is not None
    assert cfg.generation == 1


# ---------------------------------------------------------------------------
# Validation — posture
# ---------------------------------------------------------------------------


def test_compile_invalid_posture_raises() -> None:
    compiler = ConfigCompiler()
    with pytest.raises(ConfigCompilerError) as exc:
        compiler.compile({"security": {"posture": "paranoid"}})
    assert "invalid posture" in str(exc.value)
    assert "paranoid" in str(exc.value)


def test_compile_all_valid_postures() -> None:
    compiler = ConfigCompiler()
    for posture in ("locked", "standard", "development"):
        cfg = compiler.compile({"security": {"posture": posture}})
        assert cfg.posture == posture


def test_compile_default_posture_when_missing() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile({"security": {}})
    assert cfg.posture == "standard"


# ---------------------------------------------------------------------------
# Validation — profile
# ---------------------------------------------------------------------------


def test_compile_profile_too_long_raises() -> None:
    compiler = ConfigCompiler()
    with pytest.raises(ConfigCompilerError) as exc:
        compiler.compile({"security": {"posture": "standard", "profile": "x" * 129}})
    assert "profile name exceeds 128 characters" in str(exc.value)


def test_compile_profile_exactly_128_ok() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile({"security": {"posture": "standard", "profile": "x" * 128}})
    assert cfg.profile == "x" * 128


def test_compile_profile_default_when_missing() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile({"security": {"posture": "standard"}})
    assert cfg.profile == "untrusted-code"


# ---------------------------------------------------------------------------
# Validation — backend
# ---------------------------------------------------------------------------


def test_compile_invalid_backend_raises() -> None:
    compiler = ConfigCompiler()
    with pytest.raises(ConfigCompilerError) as exc:
        compiler.compile({"security": {"posture": "standard"}, "sandbox": {"backend": "docker"}})
    assert "invalid backend" in str(exc.value)
    assert "docker" in str(exc.value)


def test_compile_all_valid_backends() -> None:
    compiler = ConfigCompiler()
    for backend in _VALID_BACKENDS:
        cfg = compiler.compile({"security": {"posture": "standard"}, "sandbox": {"backend": backend}})
        assert cfg.backend == backend


def test_compile_default_backend_when_missing() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile({"security": {"posture": "standard"}})
    assert cfg.backend == "firecracker"


def test_compile_default_backend_when_sandbox_present_but_no_backend() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile({"security": {"posture": "standard"}, "sandbox": {}})
    assert cfg.backend == "firecracker"


# ---------------------------------------------------------------------------
# compile_config convenience function
# ---------------------------------------------------------------------------


def test_compile_config_convenience() -> None:
    cfg = compile_config({"security": {"posture": "standard"}})
    assert isinstance(cfg, CompiledConfig)
    assert cfg.generation >= 1
    assert cfg.posture == "standard"


def test_compile_config_convenience_increments() -> None:
    cfg1 = compile_config({"security": {"posture": "standard"}})
    cfg2 = compile_config({"security": {"posture": "standard"}})
    assert cfg2.generation > cfg1.generation


def test_compile_config_convenience_same_hash_same_config() -> None:
    cfg1 = compile_config({"security": {"posture": "standard"}})
    cfg2 = compile_config({"security": {"posture": "standard"}})
    assert cfg1.policy_hash == cfg2.policy_hash
    assert cfg1.generation != cfg2.generation


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_compile_empty_dict_uses_defaults() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile({})
    assert cfg.posture == "standard"
    assert cfg.profile == "untrusted-code"
    assert cfg.backend == "firecracker"
    assert isinstance(cfg.policy_hash, str)


def test_compile_security_not_a_dict() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile({"security": "plain string"})
    assert cfg.posture == "standard"
    assert cfg.profile == "untrusted-code"


def test_compile_sandbox_not_a_dict() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile({"security": {"posture": "standard"}, "sandbox": "plain"})
    assert cfg.backend == "firecracker"


def test_policy_hash_is_64_char_hex() -> None:
    compiler = ConfigCompiler()
    cfg = compiler.compile(_rich_raw())
    assert re.fullmatch(r"[0-9a-f]{64}", cfg.policy_hash) is not None


def test_compile_all_allowed_top_keys() -> None:
    compiler = ConfigCompiler()
    raw: dict[str, object] = {"security": {"posture": "standard"}}
    for key in _ALLOWED_TOP_KEYS:
        if key != "security":
            raw[key] = {}
    cfg = compiler.compile(raw)
    assert cfg is not None


def test_compiler_thread_safety() -> None:
    import threading

    compiler = ConfigCompiler()
    results: list[CompiledConfig] = []
    errors: list[Exception] = []

    def compile_one() -> None:
        try:
            results.append(compiler.compile({"security": {"posture": "standard"}}))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=compile_one) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(results) == 20
    generations = [r.generation for r in results]
    assert len(generations) == 20
    assert len(set(generations)) == 20


def test_atomic_switch_with_nonexistent_compiled() -> None:
    compiler = ConfigCompiler()
    _ = compiler.compile(_minimal_raw())
    fake_cfg = CompiledConfig(
        generation=999,
        posture="standard",
        profile="default",
        backend="firecracker",
        policy_hash="fake",
    )
    result = compiler.atomic_switch(fake_cfg, health_check=lambda: True)
    assert result.success is False


def test_config_compiler_error_with_details() -> None:
    err = ConfigCompilerError("validation failed: foo, bar, baz")
    assert "foo" in str(err)
    assert "bar" in str(err)
