"""Pydantic contracts for tool/simulator adapters (spec MATE-001 §6).

Every adapter SHALL declare solver/version, license, supported physics,
unit conventions, determinism controls, resource bounds, checkpoint/restart,
input and output schemas, validation cases, and known limitations.

These structured types are the typed surface an adapter implements; the
:class:`SolverAdapter` Protocol below is the structural contract a class
satisfies (dataclass, Pydantic model, or plain object) to be considered a
valid adapter at runtime.

Invariants:

  - MATE-SAFE-004: resource bounds are non-negative so a sandboxed solver
    cannot be coaxed into requesting unbounded CPU/memory/time.
  - MATE-AT-007: every adapter ships at least one validation case so that
    patch/manufactured-solution/benchmark verification is possible.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class _SpecBase(BaseModel):
    """Common config: forbid unknown keys, validate on assignment."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


# ─── ResourceBounds (§6: resource bounds + MATE-SAFE-004 sandbox) ────────────


class ResourceBounds(_SpecBase):
    """Hard ceiling on the resources a solver may consume in one run.

    Used by the dispatcher to refuse work that would exceed the adapter's
    declared envelope before the solver is ever invoked. Per MATE-SAFE-004,
    sandboxed solvers MUST be resource-bounded; an unbounded adapter cannot
    be dispatched.
    """

    cpu_cores: int = Field(ge=0, description="Max CPU cores the solver may use.")
    memory_mb: int = Field(ge=0, description="Max resident memory in megabytes.")
    wall_time_s: int = Field(ge=0, description="Max wall-clock time in seconds.")
    disk_gb: float = Field(ge=0.0, description="Max scratch disk in gigabytes.")

    def allows(
        self,
        cpu_cores: int,
        memory_mb: int,
        wall_time_s: int,
        disk_gb: float,
    ) -> bool:
        """Return True iff a request fits entirely within these bounds."""
        return (
            cpu_cores <= self.cpu_cores
            and memory_mb <= self.memory_mb
            and wall_time_s <= self.wall_time_s
            and disk_gb <= self.disk_gb
        )


# ─── DeterminismSpec (§6: determinism controls) ──────────────────────────────


class DeterminismSpec(_SpecBase):
    """Declares whether repeated runs with identical inputs produce identical output.

    MATE-DEC-004 (calculation traceability) requires that every derived value
    retain software version AND numerical precision. A non-reproducible adapter
    MUST widen the uncertainty band of any result it produces.
    """

    reproducible: bool
    seed_controlled: bool = Field(
        default=False,
        description="True if stochastic seeds are explicit inputs (not implicit globals).",
    )
    version_pinned: bool = Field(
        default=False,
        description="True if the solver version is digest-addressed and cannot drift.",
    )


# ─── CheckpointRestartSpec (§6: checkpoint/restart) ───────────────────────────


class CheckpointRestartSpec(_SpecBase):
    """Declares whether a long solver run can be checkpointed and resumed.

    Long simulations (MATE §10) SHALL stream phase progress and emit a
    heartbeat every 30s; an adapter that supports checkpoint/restart allows
    recovery from interruption without re-running the whole job.
    """

    supported: bool
    format: str | None = Field(
        default=None,
        description="On-disk format of the checkpoint (e.g. 'hdf5', 'restart').",
    )
    max_checkpoints: int = Field(default=0, ge=0)


# ─── ValidationCase (§6: validation cases + MATE-AT-007) ──────────────────────


class ValidationCase(_SpecBase):
    """A named benchmark the adapter has been verified against.

    The ``tolerance`` dict carries the acceptance band (e.g.
    ``{"rel": 0.02}``); ``status`` records the most recent verification
    outcome (``passing`` / ``failing`` / ``unverified``).
    """

    name: str = Field(min_length=1)
    benchmark_uri: str | None = None
    tolerance: dict[str, float] = Field(default_factory=dict)
    status: str = Field(default="unverified")


# ─── SolverAdapter Protocol (§6: the structural contract) ────────────────────


@runtime_checkable
class SolverAdapter(Protocol):
    """Structural contract every tool/simulator adapter satisfies (spec §6).

    An adapter is any object (dataclass, Pydantic model, plain class) that
    carries the 13 attributes below. The ``@runtime_checkable`` decorator
    allows ``isinstance(adapter, SolverAdapter)`` to verify the contract at
    dispatch time — an incomplete adapter is rejected before any solver is
    invoked.

    Required attributes:

      - ``capability_id``         stable identifier referenced by SimulationPlan.solver_adapter
      - ``solver_name``           human-readable solver name
      - ``version``               solver version (digest-addressed when ``determinism.version_pinned``)
      - ``license``               license under which the solver is being used
      - ``supported_physics``     non-empty list of physics the adapter can model
      - ``unit_conventions``      mapping of dimension → canonical unit for this adapter
      - ``determinism``           :class:`DeterminismSpec` controls
      - ``resource_bounds``       :class:`ResourceBounds` ceiling (MATE-SAFE-004)
      - ``checkpoint_restart``    :class:`CheckpointRestartSpec` config
      - ``input_schema``          JSON-schema-ish dict describing valid inputs
      - ``output_schema``         JSON-schema-ish dict describing produced outputs
      - ``validation_cases``      non-empty list of :class:`ValidationCase` (MATE-AT-007)
      - ``known_limitations``     list of human-readable limitation strings
    """

    capability_id: str
    solver_name: str
    version: str
    license: str
    supported_physics: list[str]
    unit_conventions: dict[str, str]
    determinism: DeterminismSpec
    resource_bounds: ResourceBounds
    checkpoint_restart: CheckpointRestartSpec
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    validation_cases: list[ValidationCase]
    known_limitations: list[str]


__all__ = [
    "CheckpointRestartSpec",
    "DeterminismSpec",
    "ResourceBounds",
    "SolverAdapter",
    "ValidationCase",
]
