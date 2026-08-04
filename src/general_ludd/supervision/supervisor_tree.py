"""Erlang-style supervisor tree with one_for_one, one_for_all, rest_for_one strategies.

Implements restart-intensity tracking (max restarts per time window) and
per-child restart delays. Thread-safe.
"""

from __future__ import annotations

import enum
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


class RestartPolicy(enum.Enum):
    ONE_FOR_ONE = "one_for_one"
    ONE_FOR_ALL = "one_for_all"
    REST_FOR_ONE = "rest_for_one"


@dataclass(slots=True)
class ChildSpec:
    name: str
    start: Callable[[], object]
    restart_delay: float = 0.0


class SupervisorError(RuntimeError):
    """Raised when restart intensity is exceeded."""


@dataclass(slots=True)
class _ChildState:
    spec: ChildSpec
    instance: object | None = None
    running: bool = False
    stop: Callable[[], None] | None = None


_RestartRecord = dict[str, object]


class SupervisorTree:
    """Manages child workers with configurable restart policy.

    Policies:
      - ONE_FOR_ONE: only the failed child is restarted.
      - ONE_FOR_ALL: every child is stopped and all are restarted.
      - REST_FOR_ONE: the failed child and every child added after it are
        stopped and restarted; children before it continue running.
    """

    def __init__(
        self,
        policy: RestartPolicy | str = RestartPolicy.ONE_FOR_ONE,
        max_restarts: int = 3,
        max_seconds: float = 60.0,
    ) -> None:
        if isinstance(policy, str):
            try:
                policy = RestartPolicy(policy)
            except ValueError:
                raise ValueError(
                    f"Unknown restart policy: {policy!r}. Use one of {[p.value for p in RestartPolicy]}"
                ) from None
        self._policy: RestartPolicy = policy
        self._max_restarts: int = max_restarts
        self._max_seconds: float = max_seconds

        self._children: list[_ChildState] = []
        self._by_name: dict[str, _ChildState] = {}
        self._history: list[_RestartRecord] = []
        self._restart_timestamps: deque[float] = deque()
        self._lock: threading.Lock = threading.Lock()

    # ── public read-only ───────────────────────────────────────────────

    @property
    def policy(self) -> RestartPolicy:
        return self._policy

    @property
    def child_count(self) -> int:
        return len(self._children)

    @property
    def running_count(self) -> int:
        return sum(1 for c in self._children if c.running)

    @property
    def restart_history(self) -> list[_RestartRecord]:
        with self._lock:
            return list(self._history)

    # ── child management ───────────────────────────────────────────────

    def add_child(self, spec: ChildSpec) -> None:
        with self._lock:
            state = _ChildState(spec=spec)
            self._children.append(state)
            self._by_name[spec.name] = state

    def start_all(self) -> None:
        with self._lock:
            for state in self._children:
                try:
                    self._start_child_locked(state)
                except Exception:
                    state.running = False

    def stop_all(self) -> None:
        with self._lock:
            for state in self._children:
                self._stop_child_locked(state)

    def handle_failure(self, name: str, error: Exception) -> list[str]:
        """Notify the supervisor that *name* failed.  Returns the list of
        child names that were restarted as a result."""
        with self._lock:
            return self._handle_failure_locked(name, error)

    # ── internal ───────────────────────────────────────────────────────

    def _start_child_locked(self, state: _ChildState) -> None:
        try:
            state.instance = state.spec.start()
            state.running = True
        except Exception:
            state.running = False
            raise

    def _stop_child_locked(self, state: _ChildState) -> None:
        if not state.running:
            return
        if state.stop is not None:
            state.stop()
        elif state.instance is not None:
            _stop = getattr(state.instance, "stop", None)
            if callable(_stop):
                _stop()
        state.instance = None
        state.running = False

    def _index_of(self, name: str) -> int:
        for i, state in enumerate(self._children):
            if state.spec.name == name:
                return i
        return -1

    def _restart_child(self, state: _ChildState, error_msg: str) -> None:
        delay = state.spec.restart_delay
        if delay > 0:
            time.sleep(delay)
        self._stop_child_locked(state)
        try:
            self._start_child_locked(state)
        except Exception as e:
            self._record_restart(state.spec.name, repr(e))
            raise
        else:
            self._record_restart(state.spec.name, error_msg)

    def _record_restart(self, name: str, error: str) -> None:
        now = time.monotonic()
        self._history.append({"child": name, "error": error, "timestamp": now})
        self._restart_timestamps.append(now)

    def _check_intensity_locked(self) -> None:
        now = time.monotonic()
        cutoff = now - self._max_seconds
        while self._restart_timestamps and self._restart_timestamps[0] < cutoff:
            self._restart_timestamps.popleft()
        if len(self._restart_timestamps) >= self._max_restarts:
            raise SupervisorError(
                f"Max restart intensity ({self._max_restarts} restarts in {self._max_seconds}s) exceeded"
            )

    def _handle_failure_locked(self, name: str, error: Exception) -> list[str]:
        error_msg = f"{type(error).__name__}: {error}"

        if self._policy == RestartPolicy.ONE_FOR_ONE:
            state = self._by_name.get(name)
            if state is None:
                return []
            self._check_intensity_locked()
            self._restart_child(state, error_msg)
            return [name]

        elif self._policy == RestartPolicy.ONE_FOR_ALL:
            self._check_intensity_locked()
            ordered = list(self._children)
            for s in ordered:
                self._stop_child_locked(s)
            restarted: list[str] = []
            for s in ordered:
                self._restart_child(s, error_msg)
                restarted.append(s.spec.name)
            return restarted

        else:  # REST_FOR_ONE
            idx = self._index_of(name)
            if idx < 0:
                return []
            self._check_intensity_locked()
            affected = self._children[idx:]
            for s in affected:
                self._stop_child_locked(s)
            restarted: list[str] = []
            for s in affected:
                self._restart_child(s, error_msg)
                restarted.append(s.spec.name)
            return restarted
