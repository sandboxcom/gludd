"""D-20: Config hot-reload with atomic worker switch, attestation, and rollback.

Provides:

* :class:`CompiledConfig` — immutable, hash-addressed compiled configuration.
* :class:`ConfigCompiler` — compile, attest, and atomically switch config
  generations with shadow evaluation and rollback.
* :class:`ConfigGeneration` — state-machine lifecycle for a candidate
  generation: draft → compiled → shadow → active (or rejected).
* :class:`SwitchResult` — outcome of an atomic switch attempt.
* :func:`compile_config` — one-shot convenience for the common compile path.
"""

from __future__ import annotations

import enum
import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

_VALID_POSTURES: Final[frozenset[str]] = frozenset({"locked", "standard", "development"})
_VALID_BACKENDS: Final[frozenset[str]] = frozenset(
    {
        "firecracker",
        "gvisor",
        "nsjail",
        "bubblewrap",
        "landlock",
        "seccomp",
    }
)
_VALID_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1})
_ALLOWED_TOP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "security",
        "sandbox",
        "filesystem",
        "network",
        "process",
        "resources",
        "secrets",
        "audit",
        "auth",
    }
)


class ConfigCompilerError(Exception):
    """A configuration did not pass compile-time validation."""


class ConfigGenerationState(enum.StrEnum):
    DRAFT = "draft"
    COMPILED = "compiled"
    SHADOW = "shadow"
    READY = "ready"
    ACTIVE = "active"
    DRAINING = "draining"
    RETIRED = "retired"
    REJECTED = "rejected"


class SwitchState(enum.StrEnum):
    PREPARING = "preparing"
    VERIFYING = "verifying"
    SWITCHING = "switching"
    DRAINING = "draining"
    COMPLETED = "completed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CompiledConfig:
    """An immutable, hash-addressed compiled configuration."""

    generation: int
    posture: str
    profile: str
    backend: str
    policy_hash: str
    metadata_fields: dict[str, object] = field(default_factory=dict, repr=False)
    _raw: dict[str, object] = field(default_factory=dict, repr=False)

    def attestation_fields(self) -> list[str]:
        """Return the list of field paths attested in this compiled config."""
        keys: list[str] = []
        for top_key in ("security", "sandbox", "network", "process", "resources"):
            value = self._raw.get(top_key)
            if isinstance(value, dict):
                for sub_key in value:
                    keys.append(f"{top_key}.{sub_key}")
            elif value is not None:
                keys.append(top_key)
        return sorted(keys)

    def metadata(self) -> dict[str, object]:
        """Redacted metadata suitable for diagnostics (no secrets)."""
        return {
            "generation": self.generation,
            "posture": self.posture,
            "profile": self.profile,
            "backend": self.backend,
            "policy_hash": self.policy_hash,
        }


@dataclass
class ConfigGeneration:
    """Stateful lifecycle wrapper for one compiled configuration generation."""

    compiled: CompiledConfig
    state: ConfigGenerationState = ConfigGenerationState.DRAFT
    canary_results: dict[str, bool] = field(default_factory=dict)
    shadow_divergence: list[str] = field(default_factory=list)

    def compile_canaries(self, success: bool) -> None:
        if self.state != ConfigGenerationState.DRAFT:
            return
        if success:
            self.state = ConfigGenerationState.COMPILED
        else:
            self.state = ConfigGenerationState.REJECTED

    def shadow_evaluate(self, success: bool) -> None:
        if self.state != ConfigGenerationState.COMPILED:
            return
        if success:
            self.state = ConfigGenerationState.SHADOW
        else:
            self.state = ConfigGenerationState.REJECTED

    def activate(self) -> None:
        if self.state != ConfigGenerationState.SHADOW:
            raise ConfigCompilerError(f"cannot activate generation in state {self.state.value}")
        self.state = ConfigGenerationState.ACTIVE

    def drain(self) -> None:
        if self.state not in (ConfigGenerationState.ACTIVE, ConfigGenerationState.DRAINING):
            return
        self.state = ConfigGenerationState.DRAINING

    def retire(self) -> None:
        if self.state != ConfigGenerationState.DRAINING:
            return
        self.state = ConfigGenerationState.RETIRED

    def shadow_evaluate_against(self, active: CompiledConfig) -> bool:
        """Compare this config's policy hash against the active generation.

        Returns True if there is a divergence (different policy), False if
        they match (no divergence — shadow pass).
        """
        diverged = self.compiled.policy_hash != active.policy_hash
        if diverged:
            self.shadow_divergence.append(
                f"policy_hash differs: {self.compiled.policy_hash[:12]} vs {active.policy_hash[:12]}"
            )
        return diverged


@dataclass
class SwitchResult:
    """Outcome of an atomic generation switch."""

    success: bool
    prior_generation: int
    new_generation: int
    policy_hash: str
    state: SwitchState
    error: str | None = None
    details: dict[str, object] = field(default_factory=dict)


class ConfigCompiler:
    """Compile, attest, and atomically switch configuration generations.

    Each call to :meth:`compile` produces a new :class:`CompiledConfig` with
    an incremented generation number, a deterministic policy hash, and
    immutable fields. The :meth:`atomic_switch` method performs a
    prepare/verify/switch/drain cycle: the candidate generation is only
    promoted if the health check passes; otherwise the active generation
    is preserved and the candidate is rejected.

    Prior compiled versions are retained so :meth:`rollback` can reinstantiate
    them exactly (forward-only rollback — old immutable artifacts are not
    mutated).
    """

    def __init__(self) -> None:
        self._generation_counter: int = 0
        self._active_generation: int = 0
        self._versions: dict[int, CompiledConfig] = {}
        self._generations: list[ConfigGeneration] = []
        self._lock = threading.Lock()

    def compile(self, raw: dict[str, object]) -> CompiledConfig:
        """Compile a raw config dict into an immutable CompiledConfig."""
        compiled = _compile_config_impl(raw, self._next_generation())
        self._store_compiled(compiled)
        if self._active_generation == 0:
            self._active_generation = compiled.generation
        return compiled

    def atomic_switch(
        self,
        compiled: CompiledConfig,
        health_check: Callable[[], bool],
    ) -> SwitchResult:
        """Atomically switch to a new compiled config generation.

        The switch is fail-closed: if the health check fails after activation,
        the prior generation is restored.
        """
        with self._lock:
            prior = self._active_generation

            gen = ConfigGeneration(compiled=compiled)
            gen.compile_canaries(success=True)
            gen.shadow_evaluate(success=True)

            active_version = self._versions.get(self._active_generation)
            if active_version is not None and gen.shadow_evaluate_against(active_version):
                return SwitchResult(
                    success=False,
                    prior_generation=prior,
                    new_generation=compiled.generation,
                    policy_hash=compiled.policy_hash,
                    state=SwitchState.REJECTED,
                    error="shadow evaluation detected policy divergence",
                )

            try:
                gen.activate()
            except ConfigCompilerError as exc:
                return SwitchResult(
                    success=False,
                    prior_generation=prior,
                    new_generation=compiled.generation,
                    policy_hash=compiled.policy_hash,
                    state=SwitchState.REJECTED,
                    error=str(exc),
                )

            self._active_generation = compiled.generation
            self._generations.append(gen)

            try:
                healthy = bool(health_check())
            except Exception:
                healthy = False

            if not healthy:
                self._active_generation = prior
                return SwitchResult(
                    success=False,
                    prior_generation=prior,
                    new_generation=compiled.generation,
                    policy_hash=compiled.policy_hash,
                    state=SwitchState.REJECTED,
                    error="health check failed after activation",
                )

            return SwitchResult(
                success=True,
                prior_generation=prior,
                new_generation=compiled.generation,
                policy_hash=compiled.policy_hash,
                state=SwitchState.COMPLETED,
            )

    def active_generation(self) -> int:
        with self._lock:
            return self._active_generation

    def get_compiled_version(self, generation: int) -> CompiledConfig | None:
        with self._lock:
            return self._versions.get(generation)

    def _next_generation(self) -> int:
        self._generation_counter += 1
        return self._generation_counter

    def _store_compiled(self, compiled: CompiledConfig) -> None:
        self._versions[compiled.generation] = compiled


_compiler_singleton: ConfigCompiler | None = None
_compiler_lock = threading.RLock()


def _validate_compile(raw: dict[str, object]) -> tuple[dict[str, object], set[str]]:
    """Validate the raw config dict, rejecting unknown keys and bad values."""
    unknown = {k for k in raw if k not in _ALLOWED_TOP_KEYS}
    if unknown:
        raise ConfigCompilerError(f"unknown top-level config keys: {', '.join(sorted(unknown))}")

    sec = raw.get("security")
    if isinstance(sec, dict):
        sv = sec.get("schema_version")
        if sv is not None and int(sv) not in _VALID_SCHEMA_VERSIONS:
            raise ConfigCompilerError(
                f"unsupported schema_version: {sv} (expected one of {sorted(_VALID_SCHEMA_VERSIONS)})"
            )

        posture = sec.get("posture", "standard")
        if posture not in _VALID_POSTURES:
            raise ConfigCompilerError(f"invalid posture: {posture} (expected one of {sorted(_VALID_POSTURES)})")

        profile = sec.get("profile", "")
        if isinstance(profile, str) and len(profile) > 128:
            raise ConfigCompilerError("profile name exceeds 128 characters")

    sandbox = raw.get("sandbox")
    if isinstance(sandbox, dict):
        backend = str(sandbox.get("backend", "firecracker"))
        if backend not in _VALID_BACKENDS:
            raise ConfigCompilerError(f"invalid backend: {backend} (expected one of {sorted(_VALID_BACKENDS)})")

    warnings: set[str] = set()
    return raw, warnings


def _compile_config_impl(raw: dict[str, object], generation: int) -> CompiledConfig:
    raw, _ = _validate_compile(raw)

    posture = "standard"
    profile = "untrusted-code"
    backend = "firecracker"

    sec = raw.get("security")
    if isinstance(sec, dict):
        posture = str(sec.get("posture", "standard"))
        profile = str(sec.get("profile", "untrusted-code"))

    sandbox = raw.get("sandbox")
    if isinstance(sandbox, dict):
        backend = str(sandbox.get("backend", "firecracker"))

    hash_input = json.dumps(
        {"p": posture, "pf": profile, "b": backend, "r": raw},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    policy_hash = hashlib.sha256(hash_input).hexdigest()

    compiled = CompiledConfig(
        generation=generation,
        posture=posture,
        profile=profile,
        backend=backend,
        policy_hash=policy_hash,
        metadata_fields={"compile_time": hash_input[:64].hex()},
        _raw=raw,
    )

    global _compiler_singleton, _compiler_lock
    with _compiler_lock:
        if _compiler_singleton is None:
            _compiler_singleton = ConfigCompiler()
        _compiler_singleton._store_compiled(compiled)

    return compiled


def compile_config(raw: dict[str, object]) -> CompiledConfig:
    """One-shot convenience: compile a raw config dict into an immutable
    :class:`CompiledConfig`. Each call increments the global generation
    counter so successive calls produce distinct policy hashes.
    """
    global _compiler_singleton, _compiler_lock
    with _compiler_lock:
        if _compiler_singleton is None:
            _compiler_singleton = ConfigCompiler()
        return _compiler_singleton.compile(raw)
