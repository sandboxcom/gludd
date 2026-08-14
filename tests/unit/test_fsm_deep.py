"""Deep FSM library tests — state definition, transitions, guards, actions, timers, history."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest

from general_ludd.fsm import (
    FSM,
    Event,
    HistoryState,
    State,
    StateMachine,
    Timer,
    Transition,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class EventLog:
    def __init__(self) -> None:
        self.entries: list[str] = []

    def record(self, msg: str) -> None:
        self.entries.append(msg)

    def clear(self) -> None:
        self.entries.clear()


def _make_log_action(log: EventLog, msg: str) -> Callable[[Any], None]:
    def _action(ctx: Any) -> None:
        log.record(msg)

    return _action


# ===========================================================================
# State
# ===========================================================================


class TestStateDefinition:
    def test_state_has_name(self):
        s = State("idle")
        assert s.name == "idle"

    def test_state_is_not_initial_by_default(self):
        s = State("idle")
        assert not s.is_initial

    def test_state_is_not_final_by_default(self):
        s = State("idle")
        assert not s.is_final

    def test_state_initial_flag(self):
        s = State("idle", initial=True)
        assert s.is_initial

    def test_state_final_flag(self):
        s = State("done", final=True)
        assert s.is_final

    def test_state_repr_includes_name(self):
        s = State("running")
        assert "running" in repr(s)

    def test_state_entry_action(self):
        log = EventLog()
        s = State("active", entry=_make_log_action(log, "enter:active"))
        s.enter(None)
        assert "enter:active" in log.entries

    def test_state_exit_action(self):
        log = EventLog()
        s = State("active", exit=_make_log_action(log, "exit:active"))
        s.exit(None)
        assert "exit:active" in log.entries

    def test_state_entry_action_called_on_transition(self):
        log = EventLog()
        s1 = State("idle", initial=True, exit=_make_log_action(log, "exit:idle"))
        s2 = State("active", entry=_make_log_action(log, "enter:active"))
        fsm = FSM()
        for s in (s1, s2):
            fsm.add_state(s)
        fsm.add_transition(Transition(s1, s2, "start"))
        fsm.start()
        log.clear()
        fsm.send(Event("start"))
        assert "exit:idle" in log.entries
        assert "enter:active" in log.entries

    def test_state_data_attached(self):
        s = State("ready", data={"meta": 42})
        assert s.data == {"meta": 42}


# ===========================================================================
# Transition
# ===========================================================================


class TestTransition:
    def test_transition_source_target_event(self):
        a = State("A")
        b = State("B")
        t = Transition(a, b, "go")
        assert t.source is a
        assert t.target is b
        assert t.event == "go"

    def test_transition_no_event_in_repr(self):
        a = State("A")
        b = State("B")
        t = Transition(a, b, "go")
        assert "go" in repr(t)

    def test_transition_with_guard(self):
        a = State("A")
        b = State("B")
        def guard(ctx):
            return bool(ctx)
        t = Transition(a, b, "advance", guard=guard)
        assert t.guard is guard

    def test_transition_with_action(self):
        log = EventLog()
        a = State("A")
        b = State("B")
        t = Transition(a, b, "advance", action=_make_log_action(log, "t:advance"))
        t.fire(None)
        assert "t:advance" in log.entries

    def test_transition_with_timer(self):
        a = State("A")
        b = State("B")
        timer = Timer(0.01)
        t = Transition(a, b, "tick", timer=timer)
        assert t.timer is timer
        assert not t.is_timed_out()
        time.sleep(0.02)
        assert t.is_timed_out()

    def test_event_matches_string(self):
        a = State("A")
        b = State("B")
        t = Transition(a, b, "go")
        assert t.matches_event("go")
        assert not t.matches_event("stop")


# ===========================================================================
# Event / Timer
# ===========================================================================


class TestEvent:
    def test_event_name_and_payload(self):
        e = Event("go", payload={"speed": 10})
        assert e.name == "go"
        assert e.payload == {"speed": 10}

    def test_event_default_payload(self):
        e = Event("go")
        assert e.payload is None


class TestTimer:
    def test_timer_default_not_timed_out(self):
        t = Timer(9999.0)
        assert not t.is_timed_out()

    def test_timer_timed_out_after_duration(self):
        t = Timer(0.001)
        time.sleep(0.005)
        assert t.is_timed_out()

    def test_timer_reset(self):
        t = Timer(9999.0)
        t.reset()
        time.sleep(0.005)
        assert not t.is_timed_out()
        t = Timer(0.001)
        time.sleep(0.005)
        assert t.is_timed_out()


# ===========================================================================
# Guard conditions
# ===========================================================================


class TestGuards:
    def test_guard_allows_transition(self):
        log = EventLog()
        a = State("A", initial=True, exit=_make_log_action(log, "exit:A"))
        b = State("B", entry=_make_log_action(log, "enter:B"))
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        fsm.add_transition(
            Transition(a, b, "go", guard=lambda ctx: ctx.get("allowed") is True)
        )
        fsm.start()
        fsm.context["allowed"] = True
        fsm.send(Event("go", payload={"allowed": True}))
        assert fsm.current_state is b

    def test_guard_blocks_transition(self):
        log = EventLog()
        a = State("A", initial=True, exit=_make_log_action(log, "exit:A"))
        b = State("B", entry=_make_log_action(log, "enter:B"))
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "go", guard=lambda ctx: False))
        fsm.start()
        log.clear()
        fsm.send(Event("go"))
        assert fsm.current_state is a
        assert "exit:A" not in log.entries

    def test_multiple_guards_best_match_wins(self):
        a = State("A", initial=True)
        b = State("B")
        c = State("C")
        fsm = FSM()
        for s in (a, b, c):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "next", guard=lambda ctx: ctx.get("val", 0) > 5))
        fsm.add_transition(Transition(a, c, "next", guard=lambda ctx: True))
        fsm.context["val"] = 3
        fsm.start()
        fsm.send(Event("next"))
        assert fsm.current_state is c

    def test_guard_receives_context(self):
        captured: list[dict[str, Any]] = []
        a = State("A", initial=True)
        b = State("B")
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "go", guard=lambda ctx: captured.append(dict(ctx)) or True))
        fsm.context["tag"] = "test"
        fsm.start()
        fsm.send(Event("go"))
        assert len(captured) == 1
        assert captured[0].get("tag") == "test"


# ===========================================================================
# FSM core
# ===========================================================================


class TestFSMCore:
    def test_fsm_start_sets_initial_state(self):
        a = State("A", initial=True)
        b = State("B")
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        fsm.start()
        assert fsm.current_state is a

    def test_fsm_start_raises_if_no_initial(self):
        a = State("A")
        fsm = FSM()
        fsm.add_state(a)
        with pytest.raises(ValueError):
            fsm.start()

    def test_fsm_start_raises_if_two_initials(self):
        a = State("A", initial=True)
        b = State("B", initial=True)
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        with pytest.raises(ValueError):
            fsm.start()

    def test_fsm_send_unknown_event_silent(self):
        a = State("A", initial=True)
        b = State("B")
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "go"))
        fsm.start()
        fsm.send(Event("unknown"))
        assert fsm.current_state is a

    def test_fsm_send_returns_event(self):
        a = State("A", initial=True)
        b = State("B")
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "go"))
        fsm.start()
        result = fsm.send(Event("go"))
        assert result.name == "go"
        assert fsm.current_state is b

    def test_fsm_final_state_stops_further_transitions(self):
        a = State("A", initial=True)
        b = State("B", final=True)
        c = State("C")
        fsm = FSM()
        for s in (a, b, c):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "done"))
        fsm.add_transition(Transition(b, c, "more"))
        fsm.start()
        fsm.send(Event("done"))
        assert fsm.current_state is b
        assert fsm.is_finished
        fsm.send(Event("more"))
        assert fsm.current_state is b

    def test_fsm_self_transition(self):
        log = EventLog()
        a = State("A", initial=True, entry=_make_log_action(log, "enter:A"))
        fsm = FSM()
        fsm.add_state(a)
        fsm.add_transition(Transition(a, a, "refresh"))
        fsm.start()
        log.clear()
        fsm.send(Event("refresh"))
        assert fsm.current_state is a
        assert "enter:A" in log.entries


# ===========================================================================
# Timer transitions
# ===========================================================================


class TestTimerTransitions:
    def test_timer_transition_fires_when_timed_out(self):
        a = State("A", initial=True)
        b = State("B")
        timer = Timer(0.001)
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "timeout", timer=timer))
        fsm.start()
        time.sleep(0.005)
        fsm.send(Event("timeout"))
        assert fsm.current_state is b

    def test_timer_transition_ignored_if_not_timed_out(self):
        a = State("A", initial=True)
        b = State("B")
        timer = Timer(9999.0)
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "timeout", timer=timer))
        fsm.start()
        fsm.send(Event("timeout"))
        assert fsm.current_state is a

    def test_timer_resets_on_state_entry(self):
        a = State("A", initial=True)
        b = State("B")
        timer = Timer(0.001)
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "go"))
        fsm.add_transition(Transition(b, a, "timeout", timer=timer))
        fsm.start()
        fsm.send(Event("go"))
        assert fsm.current_state is b
        time.sleep(0.002)
        fsm.send(Event("timeout"))
        assert fsm.current_state is a
        time.sleep(0.002)
        fsm.send(Event("go"))
        assert fsm.current_state is b


# ===========================================================================
# History state (deep)
# ===========================================================================


class TestHistoryState:
    def test_history_restores_snapshot_source_state(self):
        a = State("A", initial=True)
        b = State("B")
        c = State("C")
        h = HistoryState("H")
        fsm = FSM()
        for s in (a, b, c, h):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "go_b"))
        fsm.add_transition(Transition(b, a, "back"))
        fsm.add_transition(Transition(a, h, "snap"))
        fsm.add_transition(Transition(h, a, "restore"))  # h restores last when entered via a

        fsm.start()
        fsm.send(Event("go_b"))  # a -> b
        assert fsm.current_state is b
        fsm.send(Event("back"))  # b -> a
        assert fsm.current_state is a
        fsm.send(Event("snap"))  # a -> h (records last)
        assert fsm.current_state is h
        fsm.send(Event("restore"))  # h -> restores snap source = a
        assert fsm.current_state is a

    def test_history_defaults_to_initial_when_no_history(self):
        a = State("A", initial=True)
        b = State("B")
        h = HistoryState("H", default_target=a)
        fsm = FSM()
        for s in (a, b, h):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, h, "snap"))
        fsm.add_transition(Transition(h, a, "restore"))
        fsm.start()
        fsm.send(Event("snap"))
        assert fsm.current_state is h
        fsm.send(Event("restore"))
        assert fsm.current_state is a

    def test_history_state_repr(self):
        h = HistoryState("H")
        assert "HistoryState" in repr(h)

    def test_history_clears_on_fsm_start(self):
        a = State("A", initial=True)
        b = State("B")
        h = HistoryState("H", default_target=a)
        fsm = FSM()
        for s in (a, b, h):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "go"))
        fsm.add_transition(Transition(b, h, "snap"))
        fsm.add_transition(Transition(h, a, "restore"))
        fsm.start()
        fsm.send(Event("go"))
        fsm.send(Event("snap"))
        fsm.send(Event("restore"))
        assert fsm.current_state is b
        # restart
        fsm.start()
        fsm.send(Event("go"))
        fsm.send(Event("snap"))
        fsm.send(Event("restore"))
        assert fsm.current_state is b  # old history cleared; new B snapshot retained


# ===========================================================================
# StateMachine convenience
# ===========================================================================


class TestStateMachineConvenience:
    def test_state_machine_builder_initials(self):
        sm = StateMachine()
        sm.state("idle", initial=True)
        sm.state("active")
        sm.transition("idle", "active", "start")
        machine = sm.build()
        assert machine.current_state is None
        machine.start()
        assert machine.current_state.name == "idle"
        machine.send(Event("start"))
        assert machine.current_state.name == "active"

    def test_state_machine_multiple_transitions_same_event(self):
        sm = StateMachine()
        sm.state("idle", initial=True)
        sm.state("running")
        sm.state("paused")
        sm.transition("idle", "running", "go")
        sm.transition("running", "paused", "pause")
        sm.transition("paused", "running", "resume")
        machine = sm.build()
        machine.start()
        machine.send(Event("go"))
        assert machine.current_state.name == "running"
        machine.send(Event("pause"))
        assert machine.current_state.name == "paused"
        machine.send(Event("resume"))
        assert machine.current_state.name == "running"

    def test_state_machine_with_guard(self):
        sm = StateMachine()
        sm.state("low", initial=True)
        sm.state("high")
        sm.transition("low", "high", "upgrade", guard=lambda ctx: ctx.get("level", 0) >= 10)
        machine = sm.build()
        machine.start()
        machine.send(Event("upgrade"))
        assert machine.current_state.name == "low"
        machine.context["level"] = 10
        machine.send(Event("upgrade"))
        assert machine.current_state.name == "high"

    def test_state_machine_with_actions(self):
        log = EventLog()
        sm = StateMachine()
        sm.state("off", initial=True, exit=lambda ctx: log.record("exit:off"))
        sm.state("on", entry=lambda ctx: log.record("enter:on"))
        sm.transition("off", "on", "power_on")
        machine = sm.build()
        machine.start()
        log.clear()
        machine.send(Event("power_on"))
        assert "exit:off" in log.entries
        assert "enter:on" in log.entries

    def test_state_machine_final_state(self):
        sm = StateMachine()
        sm.state("start", initial=True)
        sm.state("end", final=True)
        sm.transition("start", "end", "finish")
        machine = sm.build()
        machine.start()
        machine.send(Event("finish"))
        assert machine.is_finished


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_transition_noop_when_current_not_source(self):
        a = State("A", initial=True)
        b = State("B")
        c = State("C")
        fsm = FSM()
        for s in (a, b, c):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "go"))
        fsm.add_transition(Transition(b, c, "next"))  # valid only from B
        fsm.start()
        fsm.send(Event("next"))  # current is A, not B
        assert fsm.current_state is a

    def test_context_persistence_across_transitions(self):
        a = State("A", initial=True)
        b = State("B")
        fsm = FSM()
        for s in (a, b):
            fsm.add_state(s)
        fsm.add_transition(Transition(a, b, "go"))
        fsm.start()
        fsm.context["x"] = 1
        fsm.send(Event("go"))
        assert fsm.context["x"] == 1

    def test_fsm_repr_includes_current_state(self):
        a = State("A", initial=True)
        fsm = FSM()
        fsm.add_state(a)
        fsm.start()
        assert "A" in repr(fsm)

    def test_fsm_initial_state_entry_action_called_on_start(self):
        log = EventLog()
        a = State("A", initial=True, entry=_make_log_action(log, "enter:A"))
        fsm = FSM()
        fsm.add_state(a)
        fsm.start()
        assert "enter:A" in log.entries
