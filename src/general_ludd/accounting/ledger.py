"""Per-project accounting ledger.

Aggregates, per project:
  - elapsed_seconds   total wall-clock time from usage records
  - tokens_used       total LLM tokens consumed
  - usd_spent         total USD spent
  - quota_usd         configured quota (injected)
  - pct_quota         usd_spent / quota_usd * 100  (0.0 when quota is 0)
  - loc_changed       lines of code changed (injected provider)
  - role_stats        {role_name: run_count} map
  - todo_summary      {status_bucket: count} e.g. {"pending": 2, "done": 5}
  - points_estimated  sum of point estimates across ALL todos
  - points_done       sum of point estimates for "done" todos only

All data sources are injected as simple callables so unit tests can supply
fakes without hitting a live daemon or database.

loc_changed is accumulated by :class:`LocLedger` from per-commit deltas
(counted via ``git show --numstat`` in ``git_automation.repo``) and exposed
to :class:`Accountant` as the ``loc_provider`` callable.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------


def _finite_float(value: object) -> float:
    """Return ``value`` as a finite float, clamping NaN/Inf/negative to 0.0."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.debug("_finite_float: non-numeric value %r, clamped to 0.0", value)
        return 0.0
    if not math.isfinite(f) or f < 0.0:
        logger.debug("_finite_float: non-finite/negative value %r, clamped to 0.0", f)
        return 0.0
    return f


def _finite_nonneg_int(value: object) -> int:
    """Return ``value`` as a non-negative int, clamping NaN/Inf/negative to 0."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.debug("_finite_nonneg_int: non-numeric value %r, clamped to 0", value)
        return 0
    if not math.isfinite(f) or f < 0.0:
        logger.debug("_finite_nonneg_int: non-finite/negative value %r, clamped to 0", f)
        return 0
    return int(f)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProjectAccounting:
    """Immutable accounting snapshot for a single project."""

    project_id: str
    elapsed_seconds: float
    tokens_used: int
    usd_spent: float
    quota_usd: float
    pct_quota: float          # 0-100+ (>100 means over-budget)
    loc_changed: int
    role_stats: dict[str, int]
    todo_summary: dict[str, int]   # {status_bucket: count}
    points_estimated: int
    points_done: int


# ---------------------------------------------------------------------------
# Accountant
# ---------------------------------------------------------------------------

# Callable type aliases for readability
UsageProvider = Callable[[str], list[Any]]   # (project_id) -> [HasUsage]
TodoProvider = Callable[[str], list[Any]]    # (project_id) -> [HasTodo]
RoleProvider = Callable[[str], list[Any]]    # (project_id) -> [HasRoleRun]
LocProvider = Callable[[str], int]           # (project_id) -> loc_changed
ProjectProvider = Callable[[], list[str]]    # () -> [project_id]


class Accountant:
    """Pure aggregation over injected data providers.

    Parameters
    ----------
    usage_provider:
        Callable that, given a project_id, returns a list of objects with
        ``tokens_used: int``, ``usd_spent: float``, and
        ``elapsed_seconds: float``.
    todo_provider:
        Callable that, given a project_id, returns a list of objects with
        ``status: str`` and ``points: int``.  The status value is the raw
        bucket string ("pending", "in_progress", "done", etc.).
    role_provider:
        Callable that, given a project_id, returns a list of objects with
        ``role: str``.
    loc_provider:
        Callable that, given a project_id, returns the total lines-of-code
        changed as an int.
    project_provider:
        Zero-arg callable that returns the list of project_id strings to
        enumerate for ``account_all()``.
    quota_usd:
        USD budget quota used to compute ``pct_quota``.  When 0, pct_quota
        is returned as 0.0 (safe division).
    """

    def __init__(
        self,
        usage_provider: UsageProvider,
        todo_provider: TodoProvider,
        role_provider: RoleProvider,
        loc_provider: LocProvider,
        project_provider: ProjectProvider,
        quota_usd: float = 0.0,
    ) -> None:
        self._usage = usage_provider
        self._todos = todo_provider
        self._roles = role_provider
        self._loc = loc_provider
        self._projects = project_provider
        self._quota_usd = quota_usd

    def account_for(self, project_id: str) -> ProjectAccounting:
        """Compute a full accounting snapshot for a single project."""

        # --- usage aggregation ---
        usage_records = self._usage(project_id)
        elapsed_seconds: float = sum(
            _finite_float(getattr(r, "elapsed_seconds", 0)) for r in usage_records
        )
        tokens_used: int = sum(
            _finite_nonneg_int(getattr(r, "tokens_used", 0)) for r in usage_records
        )
        usd_spent: float = sum(
            _finite_float(getattr(r, "usd_spent", 0.0)) for r in usage_records
        )

        # --- pct_quota (zero-safe) ---
        quota_usd = self._quota_usd
        pct_quota = usd_spent / quota_usd * 100.0 if quota_usd and quota_usd > 0.0 else 0.0

        # --- loc changed (deferred to integration) ---
        loc_changed: int = self._loc(project_id)

        # --- role stats ---
        role_records = self._roles(project_id)
        role_stats: dict[str, int] = {}
        for r in role_records:
            role = str(getattr(r, "role", "unknown"))
            role_stats[role] = role_stats.get(role, 0) + 1

        # --- todo summary + points ---
        todo_records = self._todos(project_id)
        todo_summary: dict[str, int] = {}
        points_estimated: int = 0
        points_done: int = 0
        for t in todo_records:
            status = str(getattr(t, "status", "unknown"))
            todo_summary[status] = todo_summary.get(status, 0) + 1
            pts = _finite_nonneg_int(getattr(t, "points", 0))
            points_estimated += pts
            if status == "done":
                points_done += pts

        return ProjectAccounting(
            project_id=project_id,
            elapsed_seconds=elapsed_seconds,
            tokens_used=tokens_used,
            usd_spent=usd_spent,
            quota_usd=quota_usd,
            pct_quota=pct_quota,
            loc_changed=loc_changed,
            role_stats=role_stats,
            todo_summary=todo_summary,
            points_estimated=points_estimated,
            points_done=points_done,
        )

    def account_all(self) -> list[ProjectAccounting]:
        """Return an accounting snapshot for every known project."""
        return [self.account_for(pid) for pid in self._projects()]


# ---------------------------------------------------------------------------
# LocLedger — cumulative per-project lines-of-code accumulator
# ---------------------------------------------------------------------------


class LocLedger:
    """Accumulates per-commit lines-of-code deltas, per project.

    The event loop calls :meth:`record_loc_changed` after every git commit
    (``delta`` counted via ``GitAutomation.lines_changed_in_commit``); the
    :class:`Accountant` reads the running cumulative total through the bound
    ``LocProvider`` returned by :meth:`as_provider`.

    Stored on ``app.state._loc_ledger`` so the daemon (event loop writes) and
    the accounting router (reads) share one accumulator.
    """

    def __init__(self) -> None:
        self._totals: dict[str, int] = {}

    def record_loc_changed(self, project_id: str, delta: int) -> int:
        """Add ``delta`` to ``project_id``'s cumulative total; return the new total."""
        added = int(delta)
        if added < 0:
            added = 0
        if project_id:
            self._totals[project_id] = self._totals.get(project_id, 0) + added
        return self._totals.get(project_id, 0)

    def total(self, project_id: str) -> int:
        """Return the cumulative lines-of-code changed for ``project_id``."""
        return self._totals.get(project_id, 0)

    def as_provider(self) -> LocProvider:
        """Return a :data:`LocProvider` callable bound to :meth:`total`."""
        return self.total

    def snapshot(self) -> dict[str, int]:
        """Return a copy of the per-project cumulative totals."""
        return dict(self._totals)
