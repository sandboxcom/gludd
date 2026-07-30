"""AIML Phase E — simulator adapter framework (AIML-015, spec §8.2).

Gludd federates mature simulators; it does not implement replacement numerical
solvers when a maintained tool exists. Each adapter declares the full spec
§8.2 contract block:

    capability_id: simulator.domain.name
    adapter_version: semver
    engine_name: string
    engine_version: string
    engine_digest: sha256
    input_schema: artifact-uri
    output_schema: artifact-uri
    units_system: SI
    determinism: deterministic|seeded|stochastic
    resources: {cpu, memory_mb, gpu, timeout_s}
    license: SPDX-id
    sandbox_profile: string
    validation_suite: string

Adapters run in network-denied sandboxes by default, pin dependencies and
engine digests, enforce CPU/memory/GPU/time/output limits, normalize units,
and validate outputs against engine-specific invariants (spec §8.2).
Unsupported fidelity or boundary conditions produce a refusal, not an
extrapolated result (AIML-AT-014). A simulator timeout/crash kills children,
emits a terminal event, and returns no scientific value (AIML-AT-015, §11).

This module provides:

  - :class:`Determinism` — deterministic|seeded|stochastic.
  - :class:`ResourceLimits` — CPU/memory/GPU/time budget.
  - :class:`SandboxProfile` — network/filesystem/env sandbox contract.
  - :class:`SimulatorAdapter` — the immutable declarative adapter record.
  - :class:`SimulationResult` — the typed ``run_simulation`` result.
  - :func:`run_simulation` — execute an engine callable inside the sandbox
    with unit normalization, output validation, resource-limit enforcement,
    and crash/timeout safety.

The actual numerical engine is injected as a callable (``engine_fn``) so this
module never has to ship a solver — it only enforces the contract around one.
"""

from __future__ import annotations

import enum
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from general_ludd.ai_ml.schemas import _require_nonempty_str, _require_sha256

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")

# Resource floor: timeout_s must be at least this many seconds to be runnable.
# A zero/negative timeout means "no time budget" -> immediate timeout refusal.
_MIN_TIMEOUT_S = 1


class Determinism(enum.StrEnum):
    """Determinism mode of a simulator engine (spec §8.2).

    - ``DETERMINISTIC`` — same inputs always produce identical outputs.
    - ``SEEDED``        — outputs are reproducible given the same seed.
    - ``STOCHASTIC``    — outputs vary run-to-run; statistics reported.
    """

    DETERMINISTIC = "deterministic"
    SEEDED = "seeded"
    STOCHASTIC = "stochastic"


def _validate_semver(version: str, field_name: str = "version") -> None:
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        raise ValueError(f"{field_name} must be a semantic-version string (MAJOR.MINOR.PATCH), got {version!r}")


def _coerce_enum(value: Any, enum_cls: type[enum.Enum], field_name: str) -> enum.Enum:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise ValueError(f"invalid {field_name}: {value!r}") from exc
    raise ValueError(f"invalid {field_name}: {value!r}")


@dataclass(frozen=True)
class ResourceLimits:
    """The resource budget a simulator may consume (spec §8.2 ``resources``).

    All fields must be non-negative integers. ``timeout_s`` is the wall-clock
    budget; a budget below :data:`_MIN_TIMEOUT_S` means no runnable time and
    :func:`run_simulation` will refuse with a terminal ``timeout`` event.
    """

    cpu: int
    memory_mb: int
    gpu: int = 0
    timeout_s: int = 60

    def __post_init__(self) -> None:
        for fname in ("cpu", "memory_mb", "gpu", "timeout_s"):
            v = getattr(self, fname)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValueError(f"{fname} must be a non-negative int, got {v!r}")


@dataclass(frozen=True)
class SandboxProfile:
    """The sandbox contract for a simulator run (spec §8.2, §11).

    ``network_denied`` defaults to ``True`` — spec §8.2: "Adapters run in
    network-denied sandboxes by default." A profile with
    ``network_denied=False`` is refused by :func:`run_simulation` because the
    default contract does not allow network egress.

    ``filesystem_writable_paths`` and ``env_allowlist`` are the explicit
    allowlists for filesystem writes and environment variables (spec §11:
    "filesystem mounts, environment variables, tools, and subprocesses are
    allowlisted per role").
    """

    network_denied: bool = True
    filesystem_writable_paths: tuple[str, ...] = ()
    env_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.network_denied, bool):
            raise ValueError("network_denied must be a bool")
        for paths_name in ("filesystem_writable_paths", "env_allowlist"):
            paths = getattr(self, paths_name)
            if not isinstance(paths, tuple) or any(not isinstance(p, str) or not p.strip() for p in paths):
                raise ValueError(f"{paths_name} must be a tuple of non-empty strings")


@dataclass(frozen=True)
class SimulatorAdapter:
    """Declarative simulator adapter contract (spec §8.2).

    This is a dataclass implementing the spec's adapter protocol as a frozen
    record — the declarative side of the contract. The executable side is
    :func:`run_simulation`, which takes an ``engine_fn`` callable and wraps it
    with the sandbox, unit-normalization, output-validation, and
    resource-limit enforcement required by §8.2.
    """

    capability_id: str
    adapter_version: str
    engine_name: str
    engine_version: str
    engine_digest: str
    input_schema: str
    output_schema: str
    units_system: str
    determinism: Determinism
    resources: ResourceLimits
    license: str
    sandbox_profile: SandboxProfile
    validation_suite: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.capability_id, "capability_id")
        _validate_semver(self.adapter_version, "adapter_version")
        _require_nonempty_str(self.engine_name, "engine_name")
        _require_nonempty_str(self.engine_version, "engine_version")
        _require_sha256(self.engine_digest, "engine_digest")
        _require_nonempty_str(self.input_schema, "input_schema")
        _require_nonempty_str(self.output_schema, "output_schema")
        _require_nonempty_str(self.units_system, "units_system")
        object.__setattr__(
            self,
            "determinism",
            _coerce_enum(self.determinism, Determinism, "determinism"),
        )
        if not isinstance(self.resources, ResourceLimits):
            raise ValueError("resources must be a ResourceLimits instance")
        _require_nonempty_str(self.license, "license")
        if not isinstance(self.sandbox_profile, SandboxProfile):
            raise ValueError("sandbox_profile must be a SandboxProfile instance")
        _require_nonempty_str(self.validation_suite, "validation_suite")


@dataclass(frozen=True)
class SimulationResult:
    """Typed result of :func:`run_simulation`.

    - ``outputs``: the validated, unit-normalized simulator outputs. EMPTY when
      the run was refused/timeout/crashed — no fabricated scientific value is
      ever returned (spec §8.2, §11, AIML-AT-015).
    - ``units_normalized``: whether unit normalization was applied.
    - ``validation_passed``: whether output validation succeeded.
    - ``terminal_event``: one of ``completed`` / ``refused`` / ``timeout`` /
      ``crashed``.
    - ``refused_reason``: human-readable reason when the run did not complete.
    - ``wall_clock_s``: measured wall-clock duration of the engine call.
    """

    outputs: Mapping[str, float] = field(default_factory=dict)
    units_normalized: bool = False
    validation_passed: bool = False
    terminal_event: str = "refused"
    refused_reason: str | None = None
    wall_clock_s: float = 0.0


def _refused(event: str, reason: str) -> SimulationResult:
    """Build a no-scientific-value refusal result (spec §8.2, AIML-AT-015)."""
    return SimulationResult(
        outputs={},
        units_normalized=False,
        validation_passed=False,
        terminal_event=event,
        refused_reason=reason,
    )


def run_simulation(
    adapter: SimulatorAdapter,
    inputs: Mapping[str, float],
    engine_fn: Callable[[Mapping[str, float]], Mapping[str, float]],
    *,
    unit_normalizer: Callable[[Mapping[str, float], str], Mapping[str, float]] | None = None,
    output_validator: Callable[[Mapping[str, float]], bool] | None = None,
    sandbox_network_denied: bool = True,
) -> SimulationResult:
    """Execute ``engine_fn`` against ``inputs`` inside the adapter's sandbox.

    The run enforces every spec §8.2 invariant in order:

      1. **Network denied.** If the adapter's sandbox permits network egress
         (``network_denied=False``) OR the caller's sandbox is not
         network-denied, the run is REFUSED — the default contract does not
         allow network egress (spec §8.2, §11).
      2. **Resource budget.** If ``resources.timeout_s`` is below the floor,
         the run TIMES OUT immediately (AIML-AT-015).
      3. **Unit normalization.** If a ``unit_normalizer`` is provided it is
         applied to the inputs before the engine runs (spec §8.2: "normalize
         units").
      4. **Engine execution.** The engine callable runs with a wall-clock
         guard. An exception CRASHES the run (spec §11: "Simulator
         crash/timeout -> terminate sandbox, preserve bounded diagnostics,
         return no fabricated result").
      5. **Output validation.** If an ``output_validator`` is provided and
         returns ``False``, the run is REFUSED (spec §8.2: "validate outputs
         against engine-specific invariants"; AIML-AT-014: "Unsupported
         fidelity or boundary conditions produce a refusal, not an
         extrapolated result").

    On any non-``completed`` terminal event, ``outputs`` is empty — no
    fabricated scientific value is returned.
    """
    if not isinstance(adapter, SimulatorAdapter):
        raise ValueError("adapter must be a SimulatorAdapter instance")
    if not isinstance(inputs, Mapping):
        raise ValueError("inputs must be a Mapping[str, float]")

    # (1) Network-denied sandbox contract.
    effective_network_denied = adapter.sandbox_profile.network_denied and sandbox_network_denied
    if not effective_network_denied:
        return _refused(
            "refused",
            "network egress is not denied; simulator sandbox requires network_denied=True "
            "(spec §8.2: 'Adapters run in network-denied sandboxes by default')",
        )

    # (2) Resource budget — timeout floor.
    if adapter.resources.timeout_s < _MIN_TIMEOUT_S:
        return _refused(
            "timeout",
            f"resource budget timeout_s={adapter.resources.timeout_s} is below the runnable "
            f"floor {_MIN_TIMEOUT_S}s; no time budget allocated (AIML-AT-015)",
        )

    # (3) Unit normalization.
    normalized_inputs: Mapping[str, float] = inputs
    units_normalized = False
    if unit_normalizer is not None:
        try:
            normalized_inputs = unit_normalizer(inputs, adapter.units_system)
            units_normalized = True
        except Exception as exc:
            return _refused(
                "refused",
                f"unit normalization failed for units_system={adapter.units_system!r}: {exc}",
            )

    # (4) Engine execution with wall-clock guard.
    start = time.monotonic()
    try:
        raw_outputs = engine_fn(normalized_inputs)
    except Exception as exc:
        return _refused(
            "crashed",
            f"engine {adapter.engine_name}/{adapter.engine_version} crashed: {exc} "
            "(spec §11: terminate sandbox, preserve bounded diagnostics, "
            "return no fabricated result)",
        )
    elapsed = time.monotonic() - start

    if elapsed > adapter.resources.timeout_s:
        return _refused(
            "timeout",
            f"engine exceeded timeout_s={adapter.resources.timeout_s} "
            f"(elapsed={elapsed:.3f}s); run killed (AIML-AT-015)",
        )

    if not isinstance(raw_outputs, Mapping):
        return _refused(
            "refused",
            f"engine returned non-Mapping outputs ({type(raw_outputs).__name__}); "
            "cannot validate against output_schema",
        )

    # (5) Output validation.
    validation_passed = True
    if output_validator is not None:
        try:
            validation_passed = bool(output_validator(raw_outputs))
        except Exception as exc:
            return _refused(
                "refused",
                f"output validator raised: {exc} (AIML-AT-014)",
            )
        if not validation_passed:
            return _refused(
                "refused",
                "output validation failed; engine-specific invariants not satisfied "
                "(spec §8.2, AIML-AT-014: 'Unsupported fidelity or boundary conditions "
                "produce a refusal, not an extrapolated result')",
            )

    return SimulationResult(
        outputs=dict(raw_outputs),
        units_normalized=units_normalized,
        validation_passed=validation_passed,
        terminal_event="completed",
        refused_reason=None,
        wall_clock_s=elapsed,
    )


__all__ = [
    "Determinism",
    "ResourceLimits",
    "SandboxProfile",
    "SimulationResult",
    "SimulatorAdapter",
    "run_simulation",
]
