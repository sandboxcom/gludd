"""CombinedCostTracker: unified model API + cloud infrastructure cost tracking.

Facades two existing mature trackers into a single spend surface:

* :class:`~general_ludd.controllers.spend_limiter.SpendLimiter` — rolling-window
  soft cap on **model API** costs (token spend). Costs expire out of the window.
* :class:`~general_ludd.infra.cost_tracker.InfraCostTracker` — cumulative
  **cloud infrastructure** costs across AWS / GCP / Azure / RunPod / Vast.ai /
  Terraform. Costs accumulate indefinitely with per-provider / per-resource-type
  / per-project breakdowns.

The combined tracker adds NO new state of its own — it delegates every record
and query to the two underlying trackers, which are already thread-safe. This
keeps a single source of truth: a record made via ``record_model_cost()`` is
visible to the existing spend-cap enforcement path (``SpendLimiter.try_charge``)
and to ``GET /api/spend``; a record made via ``record_infra_cost()`` is visible
to ``GET /api/costs`` and the ``cost_audit`` role.

Design notes
------------
* Either underlying tracker may be ``None`` (tracker not configured). The
  combined tracker treats the missing side as zero cost and an empty breakdown,
  rather than raising — this lets the daemon wire a single combined tracker
  regardless of which subsystems are live.
* ``get_total_spend()`` is a best-effort point-in-time snapshot; because the two
  underlying locks are independent, the model and infra halves are not read
  atomically. This is acceptable for observability/accounting (the same race
  exists in the existing inline ``GET /api/costs`` logic).
* ``record_model_cost()`` / ``record_infra_cost()`` raise ``RuntimeError`` when
  their underlying tracker is absent — silently dropping a recorded cost would
  be a bug (it would look recorded but not be queryable). Callers that don't
  know whether a side is configured should check ``has_model`` / ``has_infra``
  first.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:

    from general_ludd.controllers.spend_limiter import SpendLimiter
    from general_ludd.infra.cost_tracker import InfraCostRecord, InfraCostTracker

logger = logging.getLogger(__name__)


class CombinedCostTracker:
    """Unified facade over SpendLimiter (model API) + InfraCostTracker (infra).

    Args:
        spend_limiter: Rolling-window model-API spend limiter. May be ``None``
                       when model-API cost tracking is not configured; the
                       combined tracker then reports zero for the model side.
        infra_tracker: Cumulative cloud-infra cost tracker. May be ``None``
                       when infra cost tracking is not configured; the combined
                       tracker then reports zero for the infra side.
    """

    def __init__(
        self,
        spend_limiter: SpendLimiter | None = None,
        infra_tracker: InfraCostTracker | None = None,
    ) -> None:
        self._spend_limiter = spend_limiter
        self._infra_tracker = infra_tracker

    # ------------------------------------------------------------------
    # Configuration probes
    # ------------------------------------------------------------------

    @property
    def has_model(self) -> bool:
        """True when a SpendLimiter is wired (model-API side is live)."""
        return self._spend_limiter is not None

    @property
    def has_infra(self) -> bool:
        """True when an InfraCostTracker is wired (infra side is live)."""
        return self._infra_tracker is not None

    @property
    def spend_limiter(self) -> SpendLimiter | None:
        """Expose the underlying SpendLimiter (or ``None``)."""
        return self._spend_limiter

    @property
    def infra_tracker(self) -> InfraCostTracker | None:
        """Expose the underlying InfraCostTracker (or ``None``)."""
        return self._infra_tracker

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_model_cost(
        self,
        cost_usd: float,
        *,
        model: str | None = None,
        project_id: str | None = None,
        kind: str = "token",
        at: float | None = None,
    ) -> None:
        """Record a model-API cost event against the rolling spend window.

        Delegates to :meth:`SpendLimiter.record`, so the cap-enforcement path
        (``try_charge`` / ``would_exceed``) and ``GET /api/spend`` see the same
        record. Negative or non-finite costs raise ``ValueError`` (fail-closed)
        — see :meth:`SpendLimiter.record`.

        Args:
            cost_usd:   Amount spent in USD (finite, non-negative).
            model:      Optional model identifier (informational).
            project_id: Optional project scope.
            kind:       Resource kind. Defaults to ``"token"``; use ``"infra"``
                        only when recording an infra cost THROUGH the spend cap
                        (rare — prefer :meth:`record_infra_cost`).
            at:         Override the record timestamp (tests).

        Raises:
            RuntimeError: If no SpendLimiter is wired.
            ValueError:   If ``cost_usd`` is negative or non-finite.
        """
        if self._spend_limiter is None:
            raise RuntimeError(
                "CombinedCostTracker.record_model_cost(): no SpendLimiter wired "
                "— cannot record model-API cost. Wire one at construction or "
                "check `has_model` first."
            )
        self._spend_limiter.record(
            cost_usd,
            kind=kind,
            at=at,
            model=model,
            project_id=project_id,
        )

    def record_infra_cost(
        self,
        provider: str,
        resource_type: str,
        resource_id: str,
        cost_usd: float,
        **kwargs: Any,
    ) -> InfraCostRecord:
        """Record a cloud-infrastructure cost event.

        Delegates to :meth:`InfraCostTracker.record`. The created
        :class:`~general_ludd.infra.cost_tracker.InfraCostRecord` is returned
        for callers that want the structured record (e.g. for logging).

        Args:
            provider:      Provider slug (``"aws"``, ``"gcp"``, ``"runpod"``...).
            resource_type: One of :class:`~general_ludd.infra.cost_tracker.ResourceType`.
            resource_id:   Unique resource identifier.
            cost_usd:      Cost in USD (finite, non-negative).
            **kwargs:      Forwarded to ``InfraCostTracker.record``: ``sku``,
                           ``gpu_type``, ``gpu_count``, ``region``,
                           ``project_id``, ``spot``, ``notes``.

        Raises:
            RuntimeError: If no InfraCostTracker is wired.
            ValueError:   If ``cost_usd`` is negative or non-finite.
        """
        if self._infra_tracker is None:
            raise RuntimeError(
                "CombinedCostTracker.record_infra_cost(): no InfraCostTracker "
                "wired — cannot record infra cost. Wire one at construction or "
                "check `has_infra` first."
            )
        return self._infra_tracker.record(
            provider,
            resource_type,
            resource_id,
            cost_usd,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def model_spend(self, *, now: float | None = None) -> float:
        """Model-API spend within the current rolling window (USD)."""
        if self._spend_limiter is None:
            return 0.0
        return self._spend_limiter.window_spend(now=now)

    def infra_spend(self) -> float:
        """Cumulative cloud-infrastructure spend (USD)."""
        if self._infra_tracker is None:
            return 0.0
        return self._infra_tracker.total_cost()

    def get_total_spend(self, *, now: float | None = None) -> float:
        """Combined model-API + infrastructure spend (USD).

        Note: model-API spend is rolling-window (costs expire); infra spend is
        cumulative (costs persist). The sum is a point-in-time snapshot.

        Args:
            now: Override the spend-limiter clock (tests).

        Returns:
            ``model_spend(now) + infra_spend()``.
        """
        return self.model_spend(now=now) + self.infra_spend()

    def get_cost_breakdown(self, *, now: float | None = None) -> dict[str, Any]:
        """Per-category breakdown of combined spend.

        Keys:
            ``model_api``:               Rolling-window model-API spend.
            ``infrastructure``:          Cumulative infra spend.
            ``total``:                   Sum of the two.
            ``breakdown_by_provider``:   Infra spend keyed by provider slug.
            ``breakdown_by_resource_type``: Infra spend keyed by resource type.
            ``breakdown_by_project``:    MERGED model + infra spend keyed by
                                         project_id (records with no project
                                         are grouped under ``""``).
            ``record_count``:            Number of infra records accumulated.

        Args:
            now: Override the spend-limiter clock (tests).

        Returns:
            JSON-serializable dict matching the ``GET /api/costs`` response
            shape, so the router can delegate directly.
        """
        model_cost = self.model_spend(now=now)
        infra_cost = self.infra_spend()

        by_provider: dict[str, float] = {}
        by_resource_type: dict[str, float] = {}
        by_project: dict[str, float] = {}
        record_count = 0

        if self._infra_tracker is not None:
            by_provider = self._infra_tracker.cost_by_provider()
            by_resource_type = self._infra_tracker.cost_by_resource_type()
            by_project.update(self._infra_tracker.cost_by_project())
            record_count = len(self._infra_tracker.records())

        if self._spend_limiter is not None:
            for pid, cost in self._spend_limiter.project_breakdown(now=now).items():
                key = pid or ""
                by_project[key] = by_project.get(key, 0.0) + cost

        return {
            "model_api": model_cost,
            "infrastructure": infra_cost,
            "total": model_cost + infra_cost,
            "breakdown_by_provider": by_provider,
            "breakdown_by_resource_type": by_resource_type,
            "breakdown_by_project": by_project,
            "record_count": record_count,
        }

    # ------------------------------------------------------------------
    # Cap enforcement passthrough
    # ------------------------------------------------------------------

    def remaining_model_budget(self, *, now: float | None = None) -> float:
        """Remaining model-API rolling-window budget (USD).

        Returns ``float("inf")`` when no cap is configured (no SpendLimiter or
        a non-positive limit), so callers can compare uniformly.
        """
        if self._spend_limiter is None:
            return float("inf")
        if not self._spend_limiter.cap_configured:
            return float("inf")
        return self._spend_limiter.remaining(now=now)

    def would_exceed_combined(
        self,
        projected_model_usd: float,
        *,
        now: float | None = None,
    ) -> bool:
        """True if a projected model-API cost would breach the rolling cap.

        Infra spend is cumulative (no rolling cap), so only the model side is
        cap-checked. Delegates to :meth:`SpendLimiter.would_exceed`. When no
        SpendLimiter is wired, returns ``False`` (nothing to exceed).
        """
        if self._spend_limiter is None:
            return False
        return self._spend_limiter.would_exceed(projected_model_usd, now=now)

    # ------------------------------------------------------------------
    # Snapshot / restore (model side only — infra has its own snapshot)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Serializable snapshot of both sides for persistence across restarts.

        The model side is a list of rolling-window records (consumed by
        :meth:`SpendLimiter.restore`); the infra side is the InfraCostTracker
        summary dict (consumed by re-accumulation / display).
        """
        return {
            "model_records": (
                self._spend_limiter.snapshot() if self._spend_limiter is not None else []
            ),
            "infra": (
                self._infra_tracker.snapshot() if self._infra_tracker is not None else {}
            ),
        }

    def __repr__(self) -> str:
        return (
            f"CombinedCostTracker("
            f"has_model={self.has_model}, has_infra={self.has_infra}, "
            f"model_spend={self.model_spend():.4f}, "
            f"infra_spend={self.infra_spend():.4f})"
        )
