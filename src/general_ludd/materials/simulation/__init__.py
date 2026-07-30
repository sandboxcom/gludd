"""Simulation subpackage for spec MATE-001 §6 (tool and simulator adapters)
and §13 phase MATE-P4.

Holds adapter protocols, verification, and validation code:

  - :mod:`protocols`  — :class:`SolverAdapter` Protocol + the structured
    spec types (ResourceBounds, DeterminismSpec, CheckpointRestartSpec,
    ValidationCase) an adapter declares.
  - :mod:`verification` — :func:`verify_simulation` (patch / manufactured
    solution / mesh convergence / conservation) + :func:`check_convergence`
    + :func:`is_question_falsifiable`.
  - :mod:`validation` (future) — experimental-dataset comparison, outlier
    reporting, uncertainty propagation, deterministic sensitivity analysis.
"""

from __future__ import annotations

from general_ludd.materials.simulation.protocols import (
    CheckpointRestartSpec,
    DeterminismSpec,
    ResourceBounds,
    SolverAdapter,
    ValidationCase,
)
from general_ludd.materials.simulation.verification import (
    ConvergenceResult,
    SimulationVerdict,
    check_convergence,
    is_question_falsifiable,
    verify_simulation,
)

__all__ = [
    "CheckpointRestartSpec",
    "ConvergenceResult",
    "DeterminismSpec",
    "ResourceBounds",
    "SimulationVerdict",
    "SolverAdapter",
    "ValidationCase",
    "check_convergence",
    "is_question_falsifiable",
    "verify_simulation",
]
