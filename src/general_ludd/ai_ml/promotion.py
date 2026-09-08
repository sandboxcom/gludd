"""Zero-downtime AI/ML delivery promotion gate (AIML-020)."""

from __future__ import annotations

from dataclasses import dataclass, field

from general_ludd.ai_ml.promotion_contracts import (
    ROLLBACK_SLO_SECONDS,
    AliasSwap,
    CanaryBudgets,
    CanaryMetrics,
    CanaryVerdict,
    PromotionPhase,
    RollbackResult,
)
from general_ludd.ai_ml.schemas import _require_nonempty_str


@dataclass
class PromotionGate:
    """Orchestrate the spec §12 zero-downtime promotion pipeline (AIML-020).

    The gate owns the current production alias target and the list of
    prior known-good versions retained for rollback (spec §12 step 8:
    'Retain at least the prior two known-good versions').

    Parameters:
      budgets: the canary budget envelopes (spec §12 step 6).
      current_version: the version the production alias currently points at.
      prior_versions: the retained prior versions, newest-first. Index 0
        is the immediate predecessor and the rollback target.
      enforce_retention: when ``True``, the constructor refuses to build
        a gate with fewer than two prior versions (spec §12 step 8).
      rollback_window_s: the rollback initiation SLO; defaults to
        :data:`ROLLBACK_SLO_SECONDS`.
    """

    budgets: CanaryBudgets
    current_version: str
    prior_versions: tuple[str, ...] = ()
    enforce_retention: bool = False
    rollback_window_s: int = ROLLBACK_SLO_SECONDS
    _aliases: dict[str, str] = field(default_factory=lambda: {"production": ""}, repr=False)

    def __post_init__(self) -> None:
        """Validate gate configuration and seed the production alias."""
        if not isinstance(self.budgets, CanaryBudgets):
            raise ValueError("budgets must be a CanaryBudgets instance")
        _require_nonempty_str(self.current_version, "current_version")
        if not isinstance(self.prior_versions, tuple):
            raise ValueError("prior_versions must be a tuple of strings")
        for v in self.prior_versions:
            _require_nonempty_str(v, "prior_versions[i]")
        if self.rollback_window_s <= 0:
            raise ValueError(f"rollback_window_s must be > 0, got {self.rollback_window_s}")
        if self.enforce_retention and len(self.prior_versions) < 2:
            raise ValueError(
                f"retention policy requires at least 2 prior known-good versions, "
                f"got {len(self.prior_versions)} (spec §12 step 8: 'Retain at least the "
                "prior two known-good versions')"
            )
        # Seed the production alias at the current version so resolve_alias
        # works out of the box.
        self._aliases["production"] = self.current_version

    # ------------------------------------------------------------------
    # Canary check (spec §12 step 6)
    # ------------------------------------------------------------------

    def canary_check(self, metrics: CanaryMetrics) -> CanaryVerdict:
        """Compare observed metrics against the canary budgets (spec §12.6).

        Returns a :class:`CanaryVerdict` with ``healthy=True`` only when
        every budget is satisfied. A breach of ANY budget — quality,
        safety, latency, error_rate, or cost — makes the verdict
        unhealthy. The caller triggers :meth:`rollback` on an unhealthy
        verdict.
        """
        if not isinstance(metrics, CanaryMetrics):
            raise ValueError("metrics must be a CanaryMetrics instance")
        breached: list[str] = []
        if metrics.quality < self.budgets.quality_floor:
            breached.append("quality")
        if metrics.safety < self.budgets.safety_floor:
            breached.append("safety")
        if metrics.latency_p99_ms > self.budgets.latency_p99_ceiling_ms:
            breached.append("latency")
        if metrics.error_rate > self.budgets.error_rate_ceiling:
            breached.append("error_rate")
        if metrics.cost_usd_per_kreq > self.budgets.cost_ceiling_usd_per_kreq:
            breached.append("cost")
        healthy = not breached
        return CanaryVerdict(healthy=healthy, breached_budgets=tuple(breached))

    # ------------------------------------------------------------------
    # Atomic alias swap (spec §12 step 7)
    # ------------------------------------------------------------------

    def alias_swap(
        self,
        *,
        alias: str,
        to_version: str,
        in_flight_requests: int = 0,
    ) -> AliasSwap:
        """Atomically repoint ``alias`` to ``to_version`` (spec §12 step 7).

        Spec §12 step 7: 'Atomically swap the alias; in-flight requests
        finish on their original version.' After this call:

          - :meth:`resolve_alias` returns ``to_version`` for new requests;
          - the returned :class:`AliasSwap` records the prior version
            (``from_version``) and the in-flight count so the caller can
            :meth:`drain_in_flight` before declaring the swap complete.

        The alias map mutation is a single dict assignment — the
        linearization point of the swap. No request ever observes a
        half-swapped alias.
        """
        _require_nonempty_str(alias, "alias")
        _require_nonempty_str(to_version, "to_version")
        if not isinstance(in_flight_requests, int) or in_flight_requests < 0:
            raise ValueError(f"in_flight_requests must be a non-negative int, got {in_flight_requests!r}")
        from_version = self._aliases.get(alias, "")
        if not from_version:
            from_version = self.current_version
        # Atomic linearization point: a single dict assignment.
        self._aliases[alias] = to_version
        # Advance current_version tracking so a subsequent rollback knows
        # what to roll back from.
        object.__setattr__(self, "current_version", to_version)
        object.__setattr__(self, "prior_versions", (from_version, *self.prior_versions))
        return AliasSwap(
            alias=alias,
            from_version=from_version,
            to_version=to_version,
            in_flight_requests=in_flight_requests,
            drained=False,
        )

    def drain_in_flight(self, swap: AliasSwap) -> AliasSwap:
        """Return a new :class:`AliasSwap` with all in-flight requests drained.

        Spec §12: 'zero dropped accepted requests.' In-flight requests
        are allowed to finish against their original version; once they
        complete, the swap is fully drained and the new version is the
        only one serving traffic.
        """
        if not isinstance(swap, AliasSwap):
            raise ValueError("swap must be an AliasSwap instance")
        return AliasSwap(
            alias=swap.alias,
            from_version=swap.from_version,
            to_version=swap.to_version,
            in_flight_requests=0,
            drained=True,
        )

    def resolve_alias(self, alias: str) -> str | None:
        """Return the version ``alias`` currently points at, or ``None``."""
        return self._aliases.get(alias)

    # ------------------------------------------------------------------
    # Rollback (AIML-AT-005: within 60s of breach)
    # ------------------------------------------------------------------

    def rollback(self, *, breach_time_s: float) -> RollbackResult:
        """Roll the production alias back to the immediate prior version.

        AIML-AT-005: rollback serves 100% successful requests while
        atomically returning to the prior snapshot within 60 seconds.

        Parameters:
          breach_time_s: wall-clock seconds between the threshold breach
            and the rollback initiation. Used to flag the SLO outcome.

        Refuses when no prior version exists (spec §12 step 8: prior
        versions must be retained so rollback is always possible).
        """
        if not isinstance(breach_time_s, int | float) or isinstance(breach_time_s, bool):
            raise ValueError(f"breach_time_s must be a number, got {breach_time_s!r}")
        if breach_time_s < 0:
            raise ValueError(f"breach_time_s must be >= 0, got {breach_time_s}")
        if not self.prior_versions:
            raise ValueError(
                "cannot roll back: no prior known-good version is retained "
                "(spec §12 step 8: 'Retain at least the prior two known-good versions')"
            )
        target = self.prior_versions[0]
        # Atomic alias repoint back to the immediate prior version.
        self._aliases["production"] = target
        object.__setattr__(self, "current_version", target)
        object.__setattr__(self, "prior_versions", self.prior_versions[1:])
        return RollbackResult(
            swapped_back_to=target,
            initiated_within_60s=breach_time_s <= self.rollback_window_s,
            seconds_to_initiate=float(breach_time_s),
        )


__all__ = [
    "ROLLBACK_SLO_SECONDS",
    "AliasSwap",
    "CanaryBudgets",
    "CanaryMetrics",
    "CanaryVerdict",
    "PromotionGate",
    "PromotionPhase",
    "RollbackResult",
]
