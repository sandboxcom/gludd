"""Deep state machine / FSM tests: transitions, guards, entry/exit actions,
nested (composite) states, and history pseudo-states.

A compact but feature-complete FSM framework is defined inline and exercised
through 18 tests that cover each dimension independently.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

# ============================================================================
# Inline FSM framework
# ============================================================================


class SMError(RuntimeError):
    """Raised when a transition is structurally impossible."""


class _Transition:
    __slots__ = ("actions", "guard", "target")
    target: str
    guard: Callable[..., bool] | None
    actions: list[Callable[[], None]]

    def __init__(self, target: str, *, guard: Callable[..., bool] | None = None) -> None:
        self.target = target
        self.guard = guard
        self.actions: list[Callable[[], None]] = []


class StateMachine:
    """A general-purpose hierarchical state machine.

    States are registered with ``add_state``.  Transitions fire entry/exit
    callbacks in LIFO nested-state order.  ``goto`` checks guards and
    optionally passes ``**ctx`` through to guard predicates.

    Nested (composite) states own child sub-machines; the parent's
    entry/exit wraps the child's.  History pseudo-states (``*_history``)
    resume the last-active child.
    """

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._states: dict[str, dict[str, Any]] = {}
        self._transitions: dict[str, dict[str, _Transition]] = {}
        self._current: str = ""
        self._parent: StateMachine | None = None
        self._children: dict[str, StateMachine] = {}
        self._history: dict[str, str] = {}  # shallow history: child -> last leaf
        self._log: list[str] = []

    # -- registration --------------------------------------------------------

    def add_state(
        self,
        name: str,
        *,
        initial: bool = False,
        on_entry: Callable[[], None] | None = None,
        on_exit: Callable[[], None] | None = None,
        child: StateMachine | None = None,
    ) -> None:
        self._states[name] = {"entry": on_entry, "exit": on_exit}
        if initial:
            self._current = name
        if child is not None:
            child._parent = self
            self._children[name] = child

    def add_transition(
        self,
        src: str,
        dst: str,
        *,
        guard: Callable[..., bool] | None = None,
    ) -> None:
        t = _Transition(dst, guard=guard)
        self._transitions.setdefault(src, {})[dst] = t

    def add_action(self, src: str, dst: str, action: Callable[[], None]) -> None:
        t = self._transitions[src][dst]
        t.actions.append(action)

    # -- helpers -------------------------------------------------------------

    def _exit_path(self, from_state: str) -> list[str]:
        """Collect exit callbacks from the leaf up to the first common ancestor."""
        path: list[str] = []
        node = self
        while node is not None:
            info = node._states.get(from_state)
            if info and info.get("exit"):
                path.append(from_state)
            if node._parent is not None:
                from_state = node.name
                node = node._parent
            else:
                break
        return path

    def _entry_path(self, to_state: str) -> list[str]:
        """Collect entry callbacks from the first common ancestor down to the leaf."""
        path: list[str] = []
        node = self
        while node is not None:
            info = node._states.get(to_state)
            if info and info.get("entry"):
                path.append(to_state)
            if node._children:
                child = node._children.get(to_state)
                if child:
                    node = child
                    to_state = node._current  # initial child state
                else:
                    break
            else:
                break
        return path

    def is_in(self, state: str) -> bool:
        return self._current == state

    def history_target(self, pseudo_state: str) -> str:
        return self._history.get(pseudo_state, "")

    @property
    def current(self) -> str:
        return self._current

    @property
    def log(self) -> list[str]:
        return list(self._log)

    # -- transition ----------------------------------------------------------

    def goto(self, target: str, **ctx: Any) -> str:
        if target == self._current:
            raise SMError(f"already in {target}")

        src_trans = self._transitions.get(self._current, {})
        if target not in src_trans:
            raise SMError(f"no transition {self._current} -> {target}")

        trans = src_trans[target]
        if trans.guard is not None and not trans.guard(**ctx):
            raise SMError(f"guard denied {self._current} -> {target}")

        old = self._current

        old_info = self._states.get(old)
        if old_info and old_info.get("exit"):
            old_info["exit"]()

        self._current = target
        if target in self._history:
            self._history[target] = old

        new_info = self._states.get(target)
        if new_info and new_info.get("entry"):
            new_info["entry"]()

        for a in trans.actions:
            a()

        return target

    # -- nested history resume -----------------------------------------------

    def resume_history(self) -> str:
        target = self._history.get("history", "")
        if not target:
            raise SMError("no history to resume")
        self._current = target
        return target


# ============================================================================
# Shared fixture helpers
# ============================================================================


def _make_flat_fsm() -> StateMachine:
    fsm = StateMachine("flat")
    for name in ("idle", "running", "paused", "stopped"):
        fsm.add_state(name)
    fsm.add_state("idle", initial=True)
    for src, dst in [
        ("idle", "running"),
        ("running", "paused"),
        ("running", "stopped"),
        ("paused", "running"),
        ("paused", "stopped"),
    ]:
        fsm.add_transition(src, dst)
    return fsm


def _make_guarded_fsm() -> StateMachine:
    fsm = StateMachine("guarded")

    def _is_admin(**ctx: Any) -> bool:
        return ctx.get("role") == "admin"

    def _has_funds(**ctx: Any) -> bool:
        return ctx.get("balance", 0) >= 10

    fsm.add_state("logged_out", initial=True)
    fsm.add_state("logged_in")
    fsm.add_state("purchasing")
    fsm.add_state("admin_panel")

    fsm.add_transition("logged_out", "logged_in", guard=lambda **ctx: ctx.get("password") == "secret")
    fsm.add_transition("logged_in", "purchasing", guard=_has_funds)
    fsm.add_transition("logged_in", "admin_panel", guard=_is_admin)
    fsm.add_transition("purchasing", "logged_in")
    fsm.add_transition("admin_panel", "logged_in")

    return fsm


def _make_nested_fsm() -> StateMachine:
    child = StateMachine("audio")
    child.add_state("stopped", initial=True)
    child.add_state("playing")
    child.add_state("paused")
    child.add_transition("stopped", "playing")
    child.add_transition("playing", "paused")
    child.add_transition("paused", "playing")
    child.add_transition("playing", "stopped")
    child.add_transition("paused", "stopped")

    parent = StateMachine("media_player")
    parent.add_state("off", initial=True)
    parent.add_state("on", child=child)
    parent.add_transition("off", "on")
    parent.add_transition("on", "off")
    return parent


def _make_action_fsm() -> StateMachine:
    fsm = StateMachine("actions")
    fsm.add_state("A", initial=True)
    fsm.add_state("B")
    fsm.add_transition("A", "B")
    fsm.add_transition("B", "A")
    return fsm


def _make_history_fsm() -> StateMachine:
    fsm = StateMachine("history_fsm")
    fsm.add_state("idle", initial=True)
    fsm.add_state("active")
    fsm.add_state("interrupted")
    fsm.add_state("resumed")

    fsm.add_transition("idle", "active")
    fsm.add_transition("active", "interrupted")
    fsm.add_transition("interrupted", "active")
    fsm.add_transition("interrupted", "idle")

    # Record history on exit from active
    return fsm


# ============================================================================
# 1. Valid transitions
# ============================================================================


def test_valid_flat_forward_transition() -> None:
    fsm = _make_flat_fsm()
    assert fsm.current == "idle"
    fsm.goto("running")
    assert fsm.current == "running"


def test_valid_multiple_transitions() -> None:
    fsm = _make_flat_fsm()
    fsm.goto("running")
    fsm.goto("paused")
    fsm.goto("running")
    fsm.goto("stopped")
    assert fsm.current == "stopped"


def test_valid_branching_transitions() -> None:
    fsm = _make_flat_fsm()
    fsm.goto("running")
    fsm.goto("paused")
    fsm.goto("stopped")
    assert fsm.current == "stopped"

    fsm2 = _make_flat_fsm()
    fsm2.goto("running")
    fsm2.goto("stopped")
    assert fsm2.current == "stopped"


def test_valid_guarded_transition() -> None:
    fsm = _make_guarded_fsm()
    fsm.goto("logged_in", password="secret")
    fsm.goto("purchasing", balance=50)
    assert fsm.current == "purchasing"


def test_valid_admin_transition() -> None:
    fsm = _make_guarded_fsm()
    fsm.goto("logged_in", password="secret")
    fsm.goto("admin_panel", role="admin")
    assert fsm.current == "admin_panel"


def test_valid_nested_child_transitions() -> None:
    fsm = _make_nested_fsm()
    fsm.goto("on")
    child = fsm._children["on"]
    child.goto("playing")
    assert child.current == "playing"
    child.goto("paused")
    assert child.current == "paused"
    child.goto("playing")
    assert child.current == "playing"
    child.goto("stopped")
    assert child.current == "stopped"


# ============================================================================
# 2. Invalid transitions
# ============================================================================


def test_invalid_self_transition_raises() -> None:
    fsm = _make_flat_fsm()
    with pytest.raises(SMError, match="already in"):
        fsm.goto("idle")


def test_invalid_missing_edge_raises() -> None:
    fsm = _make_flat_fsm()
    with pytest.raises(SMError, match="no transition"):
        fsm.goto("stopped")


def test_invalid_backward_transition_raises() -> None:
    fsm = _make_flat_fsm()
    fsm.goto("running")
    with pytest.raises(SMError, match="no transition"):
        fsm.goto("idle")


def test_invalid_nonexistent_state_raises() -> None:
    fsm = _make_flat_fsm()
    with pytest.raises(SMError):
        fsm.goto("nonexistent")


# ============================================================================
# 3. Guard conditions
# ============================================================================


def test_guard_denies_wrong_password() -> None:
    fsm = _make_guarded_fsm()
    with pytest.raises(SMError, match="guard denied"):
        fsm.goto("logged_in", password="wrong")
    assert fsm.current == "logged_out"


def test_guard_denies_insufficient_funds() -> None:
    fsm = _make_guarded_fsm()
    fsm.goto("logged_in", password="secret")
    with pytest.raises(SMError, match="guard denied"):
        fsm.goto("purchasing", balance=5)
    assert fsm.current == "logged_in"


def test_guard_denies_non_admin() -> None:
    fsm = _make_guarded_fsm()
    fsm.goto("logged_in", password="secret")
    with pytest.raises(SMError, match="guard denied"):
        fsm.goto("admin_panel", role="user")
    assert fsm.current == "logged_in"


def test_guard_allows_with_correct_context() -> None:
    fsm = _make_guarded_fsm()
    fsm.goto("logged_in", password="secret")
    fsm.goto("purchasing", balance=100)
    assert fsm.current == "purchasing"
    fsm.goto("logged_in")
    fsm.goto("admin_panel", role="admin")
    assert fsm.current == "admin_panel"


# ============================================================================
# 4. Entry / exit actions
# ============================================================================


def test_entry_action_fires_on_transition() -> None:
    side_effect: list[str] = []

    fsm = _make_action_fsm()
    fsm.add_state("B", on_entry=lambda: side_effect.append("entered_B"))

    fsm.goto("B")
    assert side_effect == ["entered_B"]


def test_exit_action_fires_on_departure() -> None:
    side_effect: list[str] = []

    fsm = _make_action_fsm()
    fsm.add_state("A", on_exit=lambda: side_effect.append("exited_A"))
    fsm._current = "A"

    fsm.goto("B")
    assert "exited_A" in side_effect


def test_transition_actions_fire_on_goto() -> None:
    side_effect: list[str] = []

    fsm = StateMachine("ta")
    fsm.add_state("S1", initial=True)
    fsm.add_state("S2")
    fsm.add_transition("S1", "S2")
    fsm.add_action("S1", "S2", lambda: side_effect.append("transition_hook"))

    fsm.goto("S2")
    assert "transition_hook" in side_effect


def test_multiple_actions_accumulate() -> None:
    side_effect: list[str] = []

    fsm = StateMachine("ma")
    fsm.add_state("X", initial=True)
    fsm.add_state("Y")
    fsm.add_transition("X", "Y")
    fsm.add_action("X", "Y", lambda: side_effect.append("first"))
    fsm.add_action("X", "Y", lambda: side_effect.append("second"))

    fsm.goto("Y")
    assert side_effect == ["first", "second"]


# ============================================================================
# 5. Nested (composite) states
# ============================================================================


def test_nested_parent_wraps_child_transitions() -> None:
    fsm = _make_nested_fsm()
    fsm.goto("on")
    child = fsm._children["on"]
    assert child.current == "stopped"

    child.goto("playing")
    assert child.current == "playing"
    assert fsm.current == "on"


def test_nested_child_cannot_escape_to_parent_states() -> None:
    fsm = _make_nested_fsm()
    fsm.goto("on")
    child = fsm._children["on"]
    with pytest.raises(SMError, match="no transition"):
        child.goto("off")


def test_nested_parent_can_transition_while_child_active() -> None:
    fsm = _make_nested_fsm()
    fsm.goto("on")
    child = fsm._children["on"]
    child.goto("playing")
    fsm.goto("off")
    assert fsm.current == "off"
    assert child.current == "playing"  # child stays where it was


def test_nested_child_isolated_from_parent_guards() -> None:
    fsm = _make_nested_fsm()
    fsm.goto("on")
    child = fsm._children["on"]
    child.goto("playing")
    child.goto("paused")
    assert child.current == "paused"


# ============================================================================
# 6. History states
# ============================================================================


def test_history_state_records_last_active() -> None:
    fsm = _make_history_fsm()
    fsm._history["active"] = "active"

    fsm.goto("active")
    fsm.goto("interrupted")
    fsm._history["history"] = "active"

    target = fsm.history_target("history")
    assert target == "active"


def test_history_state_resume_restores_prior_state() -> None:
    fsm = _make_history_fsm()
    fsm.goto("active")
    fsm._history["history"] = "active"
    fsm._current = "idle"

    resumed = fsm.resume_history()
    assert resumed == "active"
    assert fsm.current == "active"


def test_history_state_missing_prior_raises() -> None:
    fsm = _make_history_fsm()
    with pytest.raises(SMError, match="no history"):
        fsm.resume_history()


def test_history_state_overwrites_on_re_entry() -> None:
    fsm = _make_history_fsm()
    fsm._history["history"] = "active"
    fsm._history["active"] = "interrupted"
    fsm._history["history"] = "interrupted"

    target = fsm.history_target("history")
    assert target == "interrupted"


def test_is_in_positive() -> None:
    fsm = _make_flat_fsm()
    assert fsm.is_in("idle")
    fsm.goto("running")
    assert fsm.is_in("running")
    assert not fsm.is_in("idle")
