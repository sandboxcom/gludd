"""Deep finite-state machine library.

State definition, transition table, guard conditions, entry/exit actions,
timer transitions, and history (deep) state.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# type aliases
# ---------------------------------------------------------------------------

Action = Callable[[Any], None]
Guard = Callable[[Any], bool]


# ===========================================================================
# Core types
# ===========================================================================


class Event:
    __slots__ = ("name", "payload")

    def __init__(self, name: str, payload: Any = None) -> None:
        self.name: str = name
        self.payload: Any = payload

    def __repr__(self) -> str:
        return f"Event({self.name!r})"


class Timer:
    __slots__ = ("_duration", "_reset_at")

    def __init__(self, duration_seconds: float) -> None:
        self._duration: float = duration_seconds
        self._reset_at: float = time.monotonic()

    @property
    def duration(self) -> float:
        return self._duration

    def reset(self) -> None:
        self._reset_at = time.monotonic()

    def is_timed_out(self) -> bool:
        return (time.monotonic() - self._reset_at) >= self._duration

    def __repr__(self) -> str:
        return f"Timer({self._duration!r}s, elapsed={time.monotonic() - self._reset_at:.3f}s)"


# ===========================================================================
# State
# ===========================================================================


class State:
    __slots__ = ("_entry", "_exit", "_final", "_initial", "data", "name")

    def __init__(
        self,
        name: str,
        *,
        initial: bool = False,
        final: bool = False,
        entry: Action | None = None,
        exit: Action | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self._initial = initial
        self._final = final
        self._entry = entry
        self._exit = exit
        self.data: dict[str, Any] = data or {}

    @property
    def is_initial(self) -> bool:
        return self._initial

    @property
    def is_final(self) -> bool:
        return self._final

    def enter(self, context: Any) -> None:
        if self._entry is not None:
            self._entry(context)

    def exit(self, context: Any) -> None:
        if self._exit is not None:
            self._exit(context)

    def __repr__(self) -> str:
        return f"State({self.name!r})"


# ===========================================================================
# History state
# ===========================================================================


class HistoryState:
    __slots__ = ("_cache", "_default", "_lock", "data", "name")

    def __init__(
        self,
        name: str,
        *,
        default_target: State | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self._cache: State | None = None
        self._default: State | None = default_target
        self._lock = threading.Lock()
        self.data: dict[str, Any] = data or {}

    @property
    def is_initial(self) -> bool:
        return False

    @property
    def is_final(self) -> bool:
        return False

    def record(self, state: State) -> None:
        with self._lock:
            self._cache = state

    def restore(self) -> State | None:
        with self._lock:
            return self._cache or self._default

    def clear(self) -> None:
        with self._lock:
            self._cache = None

    def enter(self, context: Any) -> None:
        pass

    def exit(self, context: Any) -> None:
        pass

    def __repr__(self) -> str:
        return f"HistoryState({self.name!r})"


# ===========================================================================
# Transition
# ===========================================================================


class Transition:
    __slots__ = ("action", "event_name", "guard", "priority", "source", "target", "timer")

    def __init__(
        self,
        source: State,
        target: State | HistoryState,
        event_name: str,
        *,
        guard: Guard | None = None,
        action: Action | None = None,
        timer: Timer | None = None,
        priority: int = 0,
    ) -> None:
        self.source = source
        self.target = target
        self.event_name = event_name
        self.guard = guard
        self.action = action
        self.timer = timer
        self.priority = priority

    @property
    def event(self) -> str:
        return self.event_name

    def matches_event(self, name: str) -> bool:
        return self.event_name == name

    def is_timed_out(self) -> bool:
        if self.timer is None:
            return True
        return self.timer.is_timed_out()

    def fire(self, context: Any) -> None:
        if self.action is not None:
            self.action(context)

    def __repr__(self) -> str:
        return f"Transition({self.source.name!r} --{self.event_name!r}--> {self.target.name!r})"


# ===========================================================================
# FSM engine
# ===========================================================================


class FSM:
    __slots__ = ("_context", "_current", "_history_states", "_started", "_states", "_transitions")

    def __init__(self) -> None:
        self._states: dict[str, State] = {}
        self._transitions: list[Transition] = []
        self._current: State | HistoryState | None = None
        self._context: dict[str, Any] = {}
        self._history_states: dict[str, HistoryState] = {}
        self._started: bool = False

    # ------------------------------------------------------------------ public

    @property
    def context(self) -> dict[str, Any]:
        return self._context

    @property
    def current_state(self) -> State | HistoryState | None:
        return self._current

    @property
    def is_finished(self) -> bool:
        return self._current is not None and self._current.is_final

    def add_state(self, state: State | HistoryState) -> None:
        if isinstance(state, HistoryState):
            self._history_states[state.name] = state
        self._states[state.name] = state  # type: ignore[assignment]

    def add_transition(self, transition: Transition) -> None:
        if transition.source.name not in self._states:
            raise ValueError(f"Source state {transition.source.name!r} not registered")
        target_name = transition.target.name
        if target_name not in self._states:
            raise ValueError(f"Target state {target_name!r} not registered")
        self._transitions.append(transition)

    def start(self) -> None:
        initials = [s for s in self._states.values() if s.is_initial]
        if len(initials) == 0:
            raise ValueError("No initial state defined")
        if len(initials) > 1:
            raise ValueError(f"Multiple initial states: {[s.name for s in initials]}")
        for hs in self._history_states.values():
            hs.clear()
        self._current = initials[0]
        self._started = True
        self._current.enter(self._context)

    def send(self, event: Event) -> Event:
        if self._current is None:
            return event
        source = self._current
        if source.is_final:
            return event

        source_name: str = source.name

        elapsed_before: dict[str, float] = {}
        for t in self._transitions:
            if t.timer is not None:
                elapsed_before[t.event_name] = t.timer.is_timed_out()

        transitions = [t for t in self._transitions if t.source.name == source_name and t.matches_event(event.name)]

        transitions.sort(key=lambda t: -t.priority)

        for t in transitions:
            if not t.is_timed_out():
                continue
            if t.guard is not None and not t.guard(self._context):
                continue

            source.exit(self._context)
            t.fire(self._context)

            if isinstance(t.target, HistoryState):
                if isinstance(source, State):
                    t.target.record(source)
                self._current = t.target
                self._current.enter(self._context)
            else:
                self._current = t.target
                self._current.enter(self._context)

            _reset_timers_on_entry(self._transitions, self._current)

            if isinstance(source, HistoryState):
                hist_target = source.restore()
                if hist_target is not None:
                    self._current = hist_target
                    self._current.enter(self._context)
                    _reset_timers_on_entry(self._transitions, self._current)

            return event

        return event

    def __repr__(self) -> str:
        cur = self._current.name if self._current else "None"
        return f"FSM(current={cur!r}, n_states={len(self._states)})"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _reset_timers_on_entry(transitions: list[Transition], state: State | HistoryState) -> None:
    for t in transitions:
        if t.source.name == state.name and t.timer is not None:
            t.timer.reset()


# ===========================================================================
# StateMachine — declarative builder
# ===========================================================================


class StateMachine:
    __slots__ = ("_names", "_states", "_transitions")

    def __init__(self) -> None:
        self._states: dict[str, State] = {}
        self._transitions: list[Transition] = []
        self._names: set[str] = set()

    def state(
        self,
        name: str,
        *,
        initial: bool = False,
        final: bool = False,
        entry: Action | None = None,
        exit: Action | None = None,
    ) -> StateMachine:
        st = State(name, initial=initial, final=final, entry=entry, exit=exit)
        self._states[name] = st
        return self

    def transition(
        self,
        source: str,
        target: str,
        event: str,
        *,
        guard: Guard | None = None,
        action: Action | None = None,
        timer_seconds: float | None = None,
    ) -> StateMachine:
        src = self._states.get(source)
        tgt = self._states.get(target)
        if src is None:
            raise KeyError(f"Unknown source state: {source!r}")
        if tgt is None:
            raise KeyError(f"Unknown target state: {target!r}")
        timer = Timer(timer_seconds) if timer_seconds is not None else None
        self._transitions.append(Transition(src, tgt, event, guard=guard, action=action, timer=timer))
        return self

    def build(self) -> FSM:
        fsm = FSM()
        for s in self._states.values():
            fsm.add_state(s)
        for t in self._transitions:
            fsm.add_transition(t)
        return fsm
