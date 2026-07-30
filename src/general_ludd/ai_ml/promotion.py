"""AIML Phase E — zero-downtime delivery: promotion gate (spec §12, AIML-020).

Implements docs/specs/FEATURE_AI_ML_EXPERT.md §12:

  Knowledge snapshots, indexes, models, adapters, policies, and simulator
  adapters are independently versioned. Promotion follows:

    1. Build immutable candidate artifacts off the serving path.
    2. Validate schema and dependency compatibility.
    3. Restore and query the candidate in an isolated environment.
    4. Shadow representative traffic with outputs withheld.
    5. Canary by stable request hashing while the prior version remains warm.
    6. Compare online quality, safety, latency, error, and cost budgets.
    7. Atomically swap the alias; in-flight requests finish on their
       original version.
    8. Retain at least the prior two known-good versions and rehearse
       rollback.

  Required objectives: zero dropped accepted requests, zero mixed-version
  result manifests, rollback initiation within 60 seconds of a hard
  threshold breach, and recovery within 5 minutes for index/knowledge
  changes or the declared model load objective for large weights.

Acceptance tests pinned here:

  - AIML-AT-005: rollback serves 100% successful requests while atomically
    returning to the prior snapshot within 60 seconds.

This module is the typed contract the ``promote_release`` ansible role
plugs into; it never shells out and never provisions. Alias state lives
in an in-process map mirroring :class:`general_ludd.ai_ml.registries.Registry`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from general_ludd.ai_ml.schemas import _require_nonempty_str

# ---------------------------------------------------------------------------
# PromotionPhase — the spec §12 step order
# ---------------------------------------------------------------------------


class PromotionPhase(enum.StrEnum):
    """The six spec §12 promotion phases, in order.

    The enum value names match the spec's vocabulary exactly: ``build``,
    ``validate``, ``shadow``, ``canary``, ``compare``, ``swap``. Step 8
    (rollback rehearsal / prior-version retention) is modeled by
    :meth:`PromotionGate.rollback` and the constructor's retention check
    rather than a separate phase.
    """

    BUILD = "build"
    VALIDATE = "validate"
    SHADOW = "shadow"
    CANARY = "canary"
    COMPARE = "compare"
    SWAP = "swap"


# Spec §12 step 7 objective: "rollback initiation within 60 seconds of a
# hard threshold breach".
ROLLBACK_SLO_SECONDS: int = 60


# ---------------------------------------------------------------------------
# Canary budgets and metrics (spec §12 step 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanaryBudgets:
    """The online budget envelopes the canary must stay within (spec §12.6).

    Each field is a hard threshold: ``quality_floor`` and ``safety_floor``
    are higher-is-better floors; ``latency_p99_ceiling_ms``,
    ``error_rate_ceiling``, and ``cost_ceiling_usd_per_kreq`` are
    lower-is-better ceilings. A breach of ANY budget triggers rollback
    (spec §11: 'canary regression -> Automatic rollback').
    """

    quality_floor: float
    safety_floor: float
    latency_p99_ceiling_ms: float
    error_rate_ceiling: float
    cost_ceiling_usd_per_kreq: float

    def __post_init__(self) -> None:
        for fname in (
            "quality_floor",
            "safety_floor",
            "latency_p99_ceiling_ms",
            "error_rate_ceiling",
            "cost_ceiling_usd_per_kreq",
        ):
            value = getattr(self, fname)
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ValueError(f"{fname} must be a number, got {value!r}")
            if value < 0:
                raise ValueError(f"{fname} must be >= 0, got {value}")
        if not (0.0 <= self.error_rate_ceiling <= 1.0):
            raise ValueError(f"error_rate_ceiling must be a fraction in [0.0, 1.0], got {self.error_rate_ceiling}")


@dataclass(frozen=True)
class CanaryMetrics:
    """Observed canary metrics to compare against :class:`CanaryBudgets`.

    Each field maps 1:1 to a budget field. The verdict produced by
    :meth:`PromotionGate.canary_check` names every breached budget.
    """

    quality: float
    safety: float
    latency_p99_ms: float
    error_rate: float
    cost_usd_per_kreq: float

    def __post_init__(self) -> None:
        for fname in ("quality", "safety", "latency_p99_ms", "cost_usd_per_kreq"):
            value = getattr(self, fname)
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ValueError(f"{fname} must be a number, got {value!r}")
            if value < 0:
                raise ValueError(f"{fname} must be >= 0, got {value}")
        if not isinstance(self.error_rate, int | float) or isinstance(self.error_rate, bool):
            raise ValueError(f"error_rate must be a number, got {self.error_rate!r}")
        if not (0.0 <= self.error_rate <= 1.0):
            raise ValueError(f"error_rate must be in [0.0, 1.0], got {self.error_rate}")


@dataclass(frozen=True)
class CanaryVerdict:
    """Output of :meth:`PromotionGate.canary_check`.

    ``healthy`` is ``True`` only when every budget is satisfied. When
    ``False``, ``breached_budgets`` carries the names of the budgets that
    failed (``"quality"``, ``"safety"``, ``"latency"``, ``"error_rate"``,
    ``"cost"``) so the caller can decide whether the failure is a
    rollback-class regression.
    """

    healthy: bool
    breached_budgets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.healthy, bool):
            raise ValueError("healthy must be a bool")
        if self.healthy and self.breached_budgets:
            raise ValueError("a healthy verdict must not carry breached_budgets")
        for name in self.breached_budgets:
            _require_nonempty_str(name, "breached_budgets[i]")


# ---------------------------------------------------------------------------
# AliasSwap — atomic alias swap with in-flight drain (spec §12 step 7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AliasSwap:
    """An atomic alias swap (spec §12 step 7).

    Spec §12 step 7: 'Atomically swap the alias; in-flight requests
    finish on their original version.' The swap is atomic at the alias
    layer (new requests immediately resolve to ``to_version``), but
    requests that were already in flight when the swap happened continue
    against ``from_version`` until :attr:`drained` becomes ``True``.

    ``in_flight_requests`` is the count of requests still being served by
    the prior version at swap time. :meth:`PromotionGate.drain_in_flight`
    returns a new :class:`AliasSwap` with the count at zero and
    ``drained=True``.
    """

    alias: str
    from_version: str
    to_version: str
    in_flight_requests: int = 0
    drained: bool = False

    def __post_init__(self) -> None:
        for fname in ("alias", "from_version", "to_version"):
            _require_nonempty_str(getattr(self, fname), fname)
        if not isinstance(self.in_flight_requests, int) or self.in_flight_requests < 0:
            raise ValueError(f"in_flight_requests must be a non-negative int, got {self.in_flight_requests!r}")
        if not isinstance(self.drained, bool):
            raise ValueError("drained must be a bool")
        if self.drained and self.in_flight_requests != 0:
            raise ValueError("a drained swap must have zero in_flight_requests")


# ---------------------------------------------------------------------------
# RollbackResult — rollback within the 60s SLO (AIML-AT-005)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RollbackResult:
    """The outcome of :meth:`PromotionGate.rollback` (AIML-AT-005).

    ``initiated_within_60s`` is ``True`` only when the rollback began
    within :data:`ROLLBACK_SLO_SECONDS` of the breach. A rollback past
    the SLO still executes (the prior version is restored) but the SLO
    is flagged missed so the operator can investigate the delay.
    """

    swapped_back_to: str
    initiated_within_60s: bool
    seconds_to_initiate: float

    def __post_init__(self) -> None:
        _require_nonempty_str(self.swapped_back_to, "swapped_back_to")
        if not isinstance(self.initiated_within_60s, bool):
            raise ValueError("initiated_within_60s must be a bool")
        if not isinstance(self.seconds_to_initiate, int | float) or isinstance(self.seconds_to_initiate, bool):
            raise ValueError("seconds_to_initiate must be a number")
        if self.seconds_to_initiate < 0:
            raise ValueError(f"seconds_to_initiate must be >= 0, got {self.seconds_to_initiate}")


# ---------------------------------------------------------------------------
# PromotionGate
# ---------------------------------------------------------------------------


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
