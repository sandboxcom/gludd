"""Adaptive compaction-aggressiveness controller — "compact as much as possible
until accuracy drops".

This controller answers a single question each time it is asked: *given the
aggression rung we are compacting at right now and a fresh measurement of task
accuracy, should we push to a MORE aggressive rung, back OFF a rung, or hold?*

It is the policy twin of
:class:`~general_ludd.controllers.saturation.SaturationController` and mirrors
its shape exactly:

  - **Reconcile, never drift.**  The current aggression level is an
    AUTHORITATIVE input passed in by the caller every cycle (e.g. read from the
    persisted controller state or the live compaction config).  This controller
    keeps no internal level counter that could drift out of sync.
  - **Evidence-gated.**  We never move on thin evidence.  Below ``min_samples``
    observations, or with zero observations at all, we HOLD the current rung.
  - **Climb while accuracy holds.**  When the measured pass-rate is at or above
    ``floor`` we step ONE rung MORE aggressive (toward smaller/faster context),
    bounded by ``max_level`` — "as aggressive as possible while accuracy holds".
  - **Back off gracefully.**  When the pass-rate falls below ``floor`` we step
    ONE rung LESS aggressive — a graceful single step, never a full reset — and
    floor at rung 0.
  - **Pure / deterministic.**  The decision is a side-effect-free
    ``staticmethod`` of its inputs: no sleeps, no clocks, no IO, no internal
    mutable state.  The same inputs always produce the same next level, and the
    result is always clamped to ``[0, max_level]``.

Wiring this controller to the live accuracy signal and persisting the chosen
rung across cycles are deliberately OUT of scope here — this module is the pure
decision core only, exactly as :class:`SaturationController` is the pure
backfill core.
"""

from __future__ import annotations

from dataclasses import dataclass

from general_ludd.compaction.aggressive import LEVELS

__all__ = ["AccuracySample", "CompactionAggressivenessController"]


@dataclass(frozen=True)
class AccuracySample:
    """An in-memory accuracy measurement over a batch of outcomes.

    Attributes:
        passed: Count of outcomes judged correct/acceptable.  Must be >= 0.
        total: Total outcomes observed in the batch.  Must be >= 0.
    """

    passed: int
    total: int

    @property
    def rate(self) -> float | None:
        """Pass-rate in ``[0.0, 1.0]``, or ``None`` for insufficient data.

        Returns ``None`` when ``total == 0`` — there is nothing to divide, so
        the caller has NO evidence and the controller must hold.  Otherwise the
        ratio is clamped to the closed interval ``[0.0, 1.0]`` so a malformed
        sample (``passed > total`` or ``passed < 0``) can never push the policy
        with an out-of-range rate.
        """
        if self.total <= 0:
            return None
        return max(0.0, min(1.0, self.passed / self.total))


class CompactionAggressivenessController:
    """Decide the next compaction-aggression rung from an accuracy sample.

    The controller holds no per-cycle state.  Every call is reconciled from the
    ``current_level`` the caller passes in, so it cannot drift away from the
    true configured rung.

    Args:
        floor: Accuracy pass-rate at or above which we keep climbing and below
            which we back off.  Defaults to ``0.9``.
        min_samples: Minimum observations required before ANY move; below this
            we hold (noise guard).  Defaults to ``20``.
        max_level: Most-aggressive rung index we will climb to.  Defaults to the
            top rung of :data:`~general_ludd.compaction.aggressive.LEVELS`.
    """

    def __init__(
        self,
        floor: float = 0.9,
        min_samples: int = 20,
        max_level: int | None = None,
    ) -> None:
        self.floor = floor
        self.min_samples = min_samples
        self.max_level = len(LEVELS) - 1 if max_level is None else max_level

    @staticmethod
    def next_level(
        current_level: int,
        sample: AccuracySample,
        *,
        floor: float,
        min_samples: int,
        max_level: int,
    ) -> int:
        """Pure decision: the aggression rung to use next.

        Policy (in order):

          1. ``sample.total < min_samples`` — not enough evidence => HOLD.
          2. ``sample.rate is None`` (zero observations) => HOLD.
          3. ``rate >= floor`` and ``current_level < max_level`` => climb ONE
             rung MORE aggressive (``current_level + 1``).
          4. ``rate < floor`` => back off ONE rung (``current_level - 1``),
             floored at 0.
          5. otherwise (at ``max_level`` with holding accuracy) => HOLD.

        The returned rung is always clamped to ``[0, max_level]``.  No clocks,
        no IO, no hidden state — identical inputs give an identical result.
        """
        # (1) Noise guard: too few observations to move on.
        if sample.total < min_samples:
            return CompactionAggressivenessController._clamp(current_level, max_level)

        rate = sample.rate
        # (2) No evidence at all — hold.
        if rate is None:
            return CompactionAggressivenessController._clamp(current_level, max_level)

        # (3) Accuracy holding: push more aggressive while there is headroom.
        if rate >= floor:
            if current_level < max_level:
                return CompactionAggressivenessController._clamp(
                    current_level + 1, max_level
                )
            # (5) Already at the ceiling — hold there, no overflow.
            return CompactionAggressivenessController._clamp(current_level, max_level)

        # (4) Accuracy dropped below the floor: graceful single step back.
        return CompactionAggressivenessController._clamp(current_level - 1, max_level)

    @staticmethod
    def _clamp(level: int, max_level: int) -> int:
        """Clamp a rung index into ``[0, max_level]`` (never negative)."""
        upper = max(0, max_level)
        return max(0, min(level, upper))

    def compute(self, current_level: int, sample: AccuracySample) -> int:
        """Instance wrapper around :meth:`next_level` using this controller's
        ``floor`` / ``min_samples`` / ``max_level``.
        """
        return self.next_level(
            current_level,
            sample,
            floor=self.floor,
            min_samples=self.min_samples,
            max_level=self.max_level,
        )

    def disable_signaled(self, current_level: int, sample: AccuracySample) -> bool:
        """True when compaction should be turned OFF entirely.

        Signalled only when we are already at the LEAST-aggressive rung
        (``current_level <= 0``) and accuracy is STILL below ``floor`` with
        enough samples to trust the reading.  At that point stepping back
        further is impossible, so the caller may disable compaction altogether.

        Pure: a side-effect-free function of its inputs.
        """
        if current_level > 0:
            return False
        if sample.total < self.min_samples:
            return False
        rate = sample.rate
        if rate is None:
            return False
        return rate < self.floor
