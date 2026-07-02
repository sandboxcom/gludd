"""Per-task token-cost tracking — a "which tasks are token-heavy" sense.

Mirrors :class:`general_ludd.observability.timing.DurationTracker`, but for
token consumption instead of wall-clock. Record how many tokens each *kind* of
task (keyed by ``work_type``, optionally ``work_type::model_profile``) burns,
learn a per-key baseline (median total tokens), and rank kinds by weight so
gludd can tell a token-heavy pipeline from a light one — the substrate for
preferring a cheaper equivalent pipeline when quality allows.

Thread-safe, pure-stdlib, no I/O. The gateway records a sample per billed call;
callers query :meth:`heaviest` / :meth:`classify` to reason about relative cost.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from statistics import median

# Defaults mirror DurationTracker's rolling-window learning.
_DEFAULT_WINDOW = 50  # rolling samples kept per key
_DEFAULT_MIN_SAMPLES = 3  # need this many before a baseline is trusted
# A key whose median total exceeds the cross-key median by this factor is
# "heavy"; below the reciprocal is "light"; otherwise "moderate".
_DEFAULT_HEAVY_FACTOR = 1.5


@dataclass(frozen=True)
class TokenSample:
    """One billed call's token usage."""

    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class TokenWeight:
    """A key's learned token profile."""

    key: str
    samples: int
    median_input: float
    median_output: float
    median_total: float


class TokenCostTracker:
    """Learns per-task-kind token baselines and ranks kinds by token weight."""

    def __init__(
        self,
        *,
        window: int = _DEFAULT_WINDOW,
        min_samples: int = _DEFAULT_MIN_SAMPLES,
        heavy_factor: float = _DEFAULT_HEAVY_FACTOR,
    ) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        if min_samples <= 0:
            raise ValueError("min_samples must be positive")
        if heavy_factor <= 1.0:
            raise ValueError("heavy_factor must be > 1.0")
        self._window = int(window)
        self._min_samples = int(min_samples)
        self._heavy_factor = float(heavy_factor)
        self._history: dict[str, list[TokenSample]] = {}
        self._lock = threading.Lock()

    def record(self, key: str, input_tokens: int, output_tokens: int) -> None:
        """Record one billed call's tokens for *key* (ignores negatives)."""
        if input_tokens < 0 or output_tokens < 0:
            return
        sample = TokenSample(int(input_tokens), int(output_tokens))
        with self._lock:
            hist = self._history.setdefault(key, [])
            hist.append(sample)
            if len(hist) > self._window:
                del hist[: len(hist) - self._window]

    def weight(self, key: str) -> TokenWeight | None:
        """Learned :class:`TokenWeight` for *key*, or None until min_samples seen."""
        with self._lock:
            hist = self._history.get(key)
            if hist is None or len(hist) < self._min_samples:
                return None
            return TokenWeight(
                key=key,
                samples=len(hist),
                median_input=median(s.input_tokens for s in hist),
                median_output=median(s.output_tokens for s in hist),
                median_total=median(s.total for s in hist),
            )

    def baseline_total(self, key: str) -> float | None:
        """Median total tokens for *key*, or None until min_samples seen."""
        w = self.weight(key)
        return w.median_total if w is not None else None

    def heaviest(self, n: int | None = None) -> list[TokenWeight]:
        """Keys with a trusted baseline, ranked by median total tokens (desc).

        Answers "which task kinds are token-heavy". *n* caps the result length.
        """
        with self._lock:
            keys = list(self._history.keys())
        weights = [w for k in keys if (w := self.weight(k)) is not None]
        weights.sort(key=lambda w: w.median_total, reverse=True)
        return weights[:n] if n is not None else weights

    def classify(self, key: str) -> str:
        """Classify *key* as ``heavy`` / ``moderate`` / ``light`` / ``unknown``.

        Relative to the cross-key median of per-key medians, so the judgement
        adapts to whatever workload this gludd instance actually runs.
        """
        target = self.baseline_total(key)
        if target is None:
            return "unknown"
        others = [w.median_total for w in self.heaviest()]
        if len(others) < 2:
            # Not enough distinct kinds to compare against — no relative call.
            return "moderate"
        reference = median(others)
        if reference <= 0:
            return "moderate"
        if target >= reference * self._heavy_factor:
            return "heavy"
        if target <= reference / self._heavy_factor:
            return "light"
        return "moderate"


_shared_lock = threading.Lock()
_shared_tracker: TokenCostTracker | None = None


def default_token_tracker() -> TokenCostTracker:
    """Process-wide shared :class:`TokenCostTracker`.

    Mirrors ``timing.default_tracker`` so token histories accumulate across all
    call sites (the gateway billing path, benchmark recording) rather than being
    scattered per-instance.
    """
    global _shared_tracker
    with _shared_lock:
        if _shared_tracker is None:
            _shared_tracker = TokenCostTracker()
        return _shared_tracker
