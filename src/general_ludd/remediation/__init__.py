"""Remediation system: detect blocked agents/tasks and apply remediation.

Public surface:
  - :class:`RemediationConfig`  — threshold configuration
  - :class:`BlockedTask`        — one blocked-task finding
  - :class:`ChronicBlocker`     — a (task_type, blocker_kind) pair that recurs
  - :class:`BlockerDetector`    — scan + chronic analysis
  - :class:`RemediationAction`  — outcome of one remediation call
  - :class:`RemediationDispatcher` — apply a remediation strategy
  - :func:`chronic_blocker_report` — structured report
"""

from __future__ import annotations

from general_ludd.remediation.blocker_detector import (
    BlockedTask,
    BlockerDetector,
    ChronicBlocker,
    RemediationConfig,
)
from general_ludd.remediation.dispatcher import (
    RemediationAction,
    RemediationActionKind,
    RemediationDispatcher,
)
from general_ludd.remediation.reporter import chronic_blocker_report

__all__ = [
    "BlockedTask",
    "BlockerDetector",
    "ChronicBlocker",
    "RemediationAction",
    "RemediationActionKind",
    "RemediationActionModel",
    "RemediationConfig",
    "RemediationDispatcher",
    "chronic_blocker_report",
]


def __getattr__(name: str) -> object:
    # Lazy attribute access for RemediationActionModel — the model lives in
    # db/models.py and importing it eagerly here would create a circular import
    # for callers that import the package for the data classes only.
    if name == "RemediationActionModel":
        from general_ludd.db.models import RemediationActionModel

        return RemediationActionModel
    raise AttributeError(name)
