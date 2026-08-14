"""Operation duration tracking + anomaly (slow / stall) detection.

Gives gludd a "clock time" sense: record how long each *kind* of operation
takes, learn a per-operation baseline, and flag when an operation is
anomalously slow (a call that usually takes ~1s suddenly takes 10s) or has
STALLED (exceeded its expected time with no completion). Two pieces:

- :class:`DurationTracker` — records COMPLETED-operation durations keyed by an
  operation name, keeps a bounded rolling window per key, and answers
  :meth:`is_anomalous` against the learned baseline (the median, times a
  multiplicative factor, with an absolute floor so sub-ms jitter never flags).
  :meth:`track` is a context manager that times a block, records it, and returns
  the verdict.

- :class:`StallWatchdog` — for IN-FLIGHT operations: :meth:`start` stamps a
  monotonic start + a deadline (the tracker's learned expectation x a factor, or
  an explicit/absolute deadline); :meth:`poll` (or the background sweeper) flags
  any op still running past its deadline as STALLED and hands it to an
  investigation callback.

Thread-safe, pure-stdlib, no I/O. Callers wire the anomaly/stall callbacks to
logging, the event bus, or metrics — this module only decides *when* something
is too slow or hung, and (optionally) captures a thread-stack snapshot so the
"investigate why it hung" question has an answer.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from statistics import median

logger = logging.getLogger(__name__)

# Defaults chosen so normal jitter never flags but a genuine 3x slowdown does.
_DEFAULT_WINDOW = 50  # rolling samples kept per operation key
_DEFAULT_MIN_SAMPLES = 5  # need this many before a baseline is trusted
_DEFAULT_SLOW_FACTOR = 3.0  # "10s when it's usually 1s" ⇒ factor 10 > 3 → flag
_DEFAULT_ABS_FLOOR_S = 0.05  # never flag ops whose slowdown is under this delta
_DEFAULT_STALL_FACTOR = 5.0  # in-flight op past baselinexthis ⇒ presumed stalled
_DEFAULT_ABS_DEADLINE_S = 600.0  # fallback deadline when there is no baseline yet


@dataclass(frozen=True)
class DurationVerdict:
    """The outcome of checking one duration against its learned baseline."""

    key: str
    seconds: float
    baseline: float | None
    anomalous: bool
    reason: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        """Render a concise human-readable verdict."""
        base = f"{self.baseline:.3f}s" if self.baseline is not None else "n/a"
        flag = "SLOW" if self.anomalous else "ok"
        return f"[{flag}] {self.key}: {self.seconds:.3f}s (baseline {base}) {self.reason}".strip()


class DurationTracker:
    """Learns per-operation baselines and flags anomalously-slow durations."""

    def __init__(
        self,
        *,
        window: int = _DEFAULT_WINDOW,
        min_samples: int = _DEFAULT_MIN_SAMPLES,
        slow_factor: float = _DEFAULT_SLOW_FACTOR,
        abs_floor_s: float = _DEFAULT_ABS_FLOOR_S,
    ) -> None:
        """Initialize bounded baseline tracking and anomaly thresholds."""
        if window <= 0:
            raise ValueError("window must be positive")
        if min_samples <= 0:
            raise ValueError("min_samples must be positive")
        if slow_factor <= 1.0:
            raise ValueError("slow_factor must be > 1.0")
        self._window = int(window)
        self._min_samples = int(min_samples)
        self._slow_factor = float(slow_factor)
        self._abs_floor_s = float(abs_floor_s)
        # key -> rolling list of the most recent durations (bounded to _window).
        self._history: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def record(self, key: str, seconds: float) -> None:
        """Record a completed operation's duration (ignores non-finite/negative)."""
        if seconds < 0 or seconds != seconds or seconds == float("inf"):
            return
        with self._lock:
            hist = self._history.setdefault(key, [])
            hist.append(float(seconds))
            if len(hist) > self._window:
                del hist[: len(hist) - self._window]

    def baseline(self, key: str) -> float | None:
        """Median duration for ``key``, or ``None`` until ``min_samples`` seen."""
        with self._lock:
            hist = self._history.get(key)
            if hist is None or len(hist) < self._min_samples:
                return None
            return median(hist)

    def is_anomalous(self, key: str, seconds: float) -> DurationVerdict:
        """Judge ``seconds`` against the learned baseline for ``key``.

        Not anomalous while still learning (< ``min_samples``). Otherwise flagged
        when it exceeds ``baseline x slow_factor`` AND the absolute slowdown is at
        least ``abs_floor_s`` (so a 1ms→4ms blip never trips).
        """
        base = self.baseline(key)
        if base is None:
            return DurationVerdict(key, seconds, None, False, "learning (insufficient samples)")
        if base <= 0:
            # Degenerate baseline (a window of ~0s durations): a ratio is
            # meaningless and would divide by zero below — treat as not anomalous.
            return DurationVerdict(key, seconds, base, False, "baseline not positive")
        threshold = base * self._slow_factor
        if seconds > threshold and (seconds - base) >= self._abs_floor_s:
            return DurationVerdict(
                key,
                seconds,
                base,
                True,
                f"{seconds / base:.1f}x baseline (> {self._slow_factor:g}x threshold)",
            )
        return DurationVerdict(key, seconds, base, False, "within baseline")

    def check_then_record(self, key: str, seconds: float) -> DurationVerdict:
        """Judge ``seconds`` against the current baseline.

        Record the sample only after producing the verdict.
        """
        verdict = self.is_anomalous(key, seconds)
        self.record(key, seconds)
        return verdict

    @contextmanager
    def track(self, key: str, *, on_anomaly: Callable[[DurationVerdict], None] | None = None) -> Iterator[None]:
        """Time the wrapped block, record it, and act on an anomalous duration.

        On exit records the elapsed time; if it is anomalously slow, logs a
        warning and calls ``on_anomaly`` (if given). Never suppresses the block's
        own exception — the duration is still recorded on the error path.
        """
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - start
            verdict = self.is_anomalous(key, elapsed)
            self.record(key, elapsed)
            if verdict.anomalous:
                logger.warning("slow operation: %s", verdict)
                if on_anomaly is not None:
                    try:
                        on_anomaly(verdict)
                    except Exception:  # a callback must never break the caller
                        logger.warning("on_anomaly callback failed for %s", key)


@dataclass
class StallReport:
    """A snapshot of an in-flight operation that has run past its deadline."""

    op_id: str
    key: str
    elapsed_s: float
    deadline_s: float
    started_monotonic: float
    thread_stacks: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        """Render a concise human-readable stall report."""
        return f"STALL {self.key} (op {self.op_id}): {self.elapsed_s:.1f}s elapsed, deadline {self.deadline_s:.1f}s"


def capture_thread_stacks() -> dict[str, str]:
    """Snapshot every thread's stack — the 'investigate why it hung' evidence."""
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    out: dict[str, str] = {}
    for ident, frame in frames.items():
        label = f"{names.get(ident, 'unknown')}-{ident}"
        out[label] = "".join(traceback.format_stack(frame))
    return out


class StallWatchdog:
    """Detects IN-FLIGHT operations that have hung past their expected time.

    ``start(op_id, key, ...)`` stamps a monotonic start and a deadline. The
    deadline is, in order of preference: an explicit ``deadline_s``; else the
    :class:`DurationTracker` baseline for ``key`` x ``stall_factor``; else
    ``abs_deadline_s``. ``poll()`` returns a :class:`StallReport` for every op
    past its deadline (capturing thread stacks so the hang can be investigated).
    A background sweeper thread can drive ``poll`` automatically.
    """

    def __init__(
        self,
        tracker: DurationTracker | None = None,
        *,
        stall_factor: float = _DEFAULT_STALL_FACTOR,
        abs_deadline_s: float = _DEFAULT_ABS_DEADLINE_S,
        on_stall: Callable[[StallReport], None] | None = None,
        capture_stacks: bool = True,
    ) -> None:
        """Initialize an in-flight operation watchdog."""
        self._tracker = tracker
        self._stall_factor = float(stall_factor)
        self._abs_deadline_s = float(abs_deadline_s)
        self._on_stall = on_stall
        self._capture_stacks = capture_stacks
        # op_id -> (key, start_monotonic, deadline_s)
        self._inflight: dict[str, tuple[str, float, float]] = {}
        self._reported: set[str] = set()  # fire on_stall once per op
        self._lock = threading.Lock()
        self._sweeper: threading.Thread | None = None
        self._stop = threading.Event()

    def _deadline_for(self, key: str, deadline_s: float | None) -> float:
        if deadline_s is not None:
            return float(deadline_s)
        if self._tracker is not None:
            base = self._tracker.baseline(key)
            if base is not None and base > 0:
                return base * self._stall_factor
        return self._abs_deadline_s

    def start(self, op_id: str, key: str, *, deadline_s: float | None = None) -> None:
        """Register an in-flight operation (idempotent per ``op_id``)."""
        deadline = self._deadline_for(key, deadline_s)
        with self._lock:
            if op_id not in self._inflight:
                self._inflight[op_id] = (key, time.monotonic(), deadline)
                self._reported.discard(op_id)

    def finish(self, op_id: str) -> None:
        """Mark an operation complete so it is no longer watched."""
        with self._lock:
            self._inflight.pop(op_id, None)
            self._reported.discard(op_id)

    def poll(self) -> list[StallReport]:
        """Return a report for every in-flight op past its deadline.

        Each op is reported at most once (until it finishes and restarts), so a
        repeated poll of a still-hung op won't spam the callback.
        """
        now = time.monotonic()
        newly: list[StallReport] = []
        with self._lock:
            for op_id, (key, start, deadline) in self._inflight.items():
                elapsed = now - start
                if elapsed >= deadline and op_id not in self._reported:
                    self._reported.add(op_id)
                    newly.append(
                        StallReport(
                            op_id=op_id,
                            key=key,
                            elapsed_s=elapsed,
                            deadline_s=deadline,
                            started_monotonic=start,
                        )
                    )
        # Capture stacks + fire callbacks OUTSIDE the lock (they may be slow).
        for report in newly:
            if self._capture_stacks:
                report.thread_stacks = capture_thread_stacks()
            logger.warning("%s", report)
            if self._on_stall is not None:
                try:
                    self._on_stall(report)
                except Exception:  # a callback must never break the sweep
                    logger.warning("on_stall callback failed for %s", report.op_id)
        return newly

    def start_sweeper(self, interval_s: float = 5.0) -> None:
        """Start a daemon thread that polls every ``interval_s`` (idempotent)."""
        if self._sweeper is not None and self._sweeper.is_alive():
            return
        self._stop.clear()

        def _run() -> None:
            while not self._stop.wait(interval_s):
                try:
                    self.poll()
                except Exception:  # pragma: no cover - the sweep must never die
                    logger.warning("stall sweeper poll failed")

        self._sweeper = threading.Thread(target=_run, name="stall-watchdog", daemon=True)
        self._sweeper.start()

    def stop_sweeper(self) -> None:
        """Stop the background sweeper (best-effort)."""
        self._stop.set()
        sweeper = self._sweeper
        if sweeper is not None:
            sweeper.join(timeout=2.0)
        self._sweeper = None

    @contextmanager
    def watch(self, op_id: str, key: str, *, deadline_s: float | None = None) -> Iterator[None]:
        """Context manager: watch an in-flight op, auto-finish on exit."""
        self.start(op_id, key, deadline_s=deadline_s)
        try:
            yield
        finally:
            self.finish(op_id)


# --- process-wide shared tracker -------------------------------------------- #
_default_tracker: DurationTracker | None = None
_default_tracker_lock = threading.Lock()


def default_tracker() -> DurationTracker:
    """Return the process-wide shared :class:`DurationTracker` (lazily created).

    Lets independent call sites (project-runner checks, model calls, task
    dispatch) accumulate baselines into ONE tracker so an operation's history is
    shared across the process.
    """
    global _default_tracker
    if _default_tracker is None:
        with _default_tracker_lock:
            if _default_tracker is None:
                _default_tracker = DurationTracker()
    return _default_tracker
