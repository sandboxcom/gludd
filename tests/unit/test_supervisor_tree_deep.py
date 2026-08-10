"""Deep supervisor tree tests — Erlang-style one_for_one, one_for_all, rest_for_one.

Covers restart policies, child lifecycle, intensity tracking, delay, threading,
and edge-case error paths.
"""

from __future__ import annotations

import contextlib
import time

import pytest

from general_ludd.supervision.supervisor_tree import (
    ChildSpec,
    RestartPolicy,
    SupervisorError,
    SupervisorTree,
)

_RunRecord = list[dict[str, object]]


def _make_child(
    name: str,
    fail_after: int = 0,
    delay: float = 0.0,
    record: _RunRecord | None = None,
):
    """Return (ChildSpec, _Token) where start creates/destroys a token.

    *fail_after*: fail on the Nth start (1-indexed).  0 = never fail.
    """

    class _Token:
        def __init__(self) -> None:
            self.started = 0
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    token = _Token()

    def start() -> object:
        token.started += 1
        if record is not None:
            record.append({"name": name, "event": "started", "start_count": token.started})
        if 0 < fail_after <= token.started:
            if record is not None:
                record.append({"name": name, "event": "failed", "start_count": token.started})
            raise RuntimeError(f"{name} simulated failure on start #{token.started}")
        if delay > 0:
            time.sleep(delay)
        token.stopped = False
        return token

    return ChildSpec(name=name, start=start), token


# ── policy enum ─────────────────────────────────────────────────────────


class TestPolicyEnum:
    def test_values(self) -> None:
        assert RestartPolicy.ONE_FOR_ONE.value == "one_for_one"
        assert RestartPolicy.ONE_FOR_ALL.value == "one_for_all"
        assert RestartPolicy.REST_FOR_ONE.value == "rest_for_one"

    def test_from_string(self) -> None:
        assert RestartPolicy("one_for_one") == RestartPolicy.ONE_FOR_ONE
        assert RestartPolicy("one_for_all") == RestartPolicy.ONE_FOR_ALL
        assert RestartPolicy("rest_for_one") == RestartPolicy.REST_FOR_ONE

    def test_from_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown restart policy"):
            SupervisorTree(policy="bogus")  # type: ignore[arg-type]


# ── SupervisorTree construction ─────────────────────────────────────────


class TestConstruction:
    def test_defaults(self) -> None:
        s = SupervisorTree()
        assert s.policy == RestartPolicy.ONE_FOR_ONE
        assert s.child_count == 0
        assert s.running_count == 0

    def test_policy_as_enum(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE)
        assert s.policy == RestartPolicy.REST_FOR_ONE

    def test_policy_as_string(self) -> None:
        s = SupervisorTree(policy="one_for_all")
        assert s.policy == RestartPolicy.ONE_FOR_ALL

    def test_custom_intensity(self) -> None:
        s = SupervisorTree(max_restarts=7, max_seconds=120.0)
        assert s.child_count == 0


# ── child lifecycle ─────────────────────────────────────────────────────


class TestChildLifecycle:
    def test_add_child_increases_count(self) -> None:
        s = SupervisorTree()
        s.add_child(ChildSpec(name="a", start=lambda: "a"))
        assert s.child_count == 1
        s.add_child(ChildSpec(name="b", start=lambda: "b"))
        assert s.child_count == 2

    def test_child_count_and_running_count_after_start_all(self) -> None:
        s = SupervisorTree()
        s.add_child(ChildSpec(name="a", start=lambda: "a"))
        s.add_child(ChildSpec(name="b", start=lambda: "b"))
        s.start_all()
        assert s.running_count == 2

    def test_start_all_preserves_order(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree()
        for name in ("c", "a", "b"):
            spec, _ = _make_child(name, fail_after=0, record=record)
            s.add_child(spec)
        s.start_all()
        started = [r["name"] for r in record if r["event"] == "started"]
        assert started == ["c", "a", "b"]

    def test_start_all_with_one_failing_child_others_still_start(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(max_restarts=5)
        a_spec, _ = _make_child("a", fail_after=0, record=record)
        b_spec, _ = _make_child("b", fail_after=1, record=record)  # fails on first start
        c_spec, _ = _make_child("c", fail_after=0, record=record)
        s.add_child(a_spec)
        s.add_child(b_spec)
        s.add_child(c_spec)
        s.start_all()

        started_names = [r["name"] for r in record if r["event"] == "started"]
        assert "a" in started_names
        assert "c" in started_names
        assert s.running_count == 2  # b is not running

    def test_start_all_failing_child_not_marked_running(self) -> None:
        s = SupervisorTree(max_restarts=5)
        s.add_child(ChildSpec(name="crash", start=lambda: 1 / 0))
        s.add_child(ChildSpec(name="ok", start=lambda: "ok"))
        s.start_all()
        assert s.running_count == 1

    def test_stop_all_stops_everything(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree()
        a_spec, a_tok = _make_child("a", fail_after=0, record=record)
        b_spec, b_tok = _make_child("b", fail_after=0, record=record)
        s.add_child(a_spec)
        s.add_child(b_spec)
        s.start_all()
        s.stop_all()
        assert a_tok.stopped
        assert b_tok.stopped
        assert s.running_count == 0

    def test_stop_all_twice_no_op(self) -> None:
        s = SupervisorTree()
        s.add_child(ChildSpec(name="a", start=lambda: "a"))
        s.start_all()
        s.stop_all()
        s.stop_all()  # second call should not raise
        assert s.running_count == 0

    def test_empty_start_stop_all_no_op(self) -> None:
        s = SupervisorTree()
        s.start_all()
        s.stop_all()
        assert s.child_count == 0
        assert s.running_count == 0

    def test_child_with_stop_callable_on_instance(self) -> None:
        class Stoppable:
            def __init__(self) -> None:
                self.stopped_flag = False

            def stop(self) -> None:
                self.stopped_flag = True

        inst = Stoppable()
        s = SupervisorTree()
        s.add_child(ChildSpec(name="s", start=lambda: inst))
        s.start_all()
        s.stop_all()
        assert inst.stopped_flag

    def test_child_with_explicit_stop_in_state(self) -> None:
        s = SupervisorTree()
        s.add_child(ChildSpec(name="s", start=lambda: object()))
        s.start_all()
        s.stop_all()  # instance has no stop method, should not crash
        assert s.running_count == 0

    def test_child_with_neither_stop_nor_stop_method(self) -> None:
        s = SupervisorTree()
        s.add_child(ChildSpec(name="naked", start=lambda: 42))
        s.start_all()
        s.stop_all()
        assert s.running_count == 0


# ── one_for_one deep ────────────────────────────────────────────────────


class TestOneForOneDeep:
    def test_single_failure_only_restarts_failed_child(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        a_spec, _ = _make_child("a", fail_after=0, record=record)
        b_spec, _ = _make_child("b", fail_after=0, record=record)
        s.add_child(a_spec)
        s.add_child(b_spec)
        s.start_all()

        restarted = s.handle_failure("a", RuntimeError("boom"))
        assert restarted == ["a"]
        assert "b" not in restarted
        started_a = sum(1 for r in record if r["name"] == "a" and r["event"] == "started")
        assert started_a == 2  # initial + restart

    def test_only_failed_child_restarted_others_keep_running(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        a_spec, _ = _make_child("a", fail_after=0, record=record)
        b_spec, _ = _make_child("b", fail_after=0, record=record)
        c_spec, _ = _make_child("c", fail_after=0, record=record)
        for spec in (a_spec, b_spec, c_spec):
            s.add_child(spec)
        s.start_all()
        s.handle_failure("b", RuntimeError("boom"))
        started_b = sum(1 for r in record if r["name"] == "b" and r["event"] == "started")
        assert started_b == 2
        started_a = sum(1 for r in record if r["name"] == "a" and r["event"] == "started")
        assert started_a == 1

    def test_multiple_independent_failures(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        for name in ("x", "y", "z"):
            s.add_child(ChildSpec(name=name, start=lambda n=name: n))
        s.start_all()
        assert s.handle_failure("x", RuntimeError("e1")) == ["x"]
        assert s.handle_failure("z", RuntimeError("e2")) == ["z"]

    def test_unknown_child_returns_empty(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="a", start=lambda: "a"))
        s.start_all()
        restarted = s.handle_failure("nonexistent", RuntimeError("x"))
        assert restarted == []

    def test_failure_intensity_not_checked_for_unknown(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=0)
        restarted = s.handle_failure("ghost", RuntimeError("x"))
        assert restarted == []

    def test_restart_that_fails_propagates(self) -> None:
        """When a child's restart raises, the exception propagates through
        handle_failure.  The restart is still recorded in history."""
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="doomed", start=lambda: 1 / 0, restart_delay=0))
        s.start_all()
        with pytest.raises(ZeroDivisionError):
            s.handle_failure("doomed", RuntimeError("original"))
        history = s.restart_history
        assert len(history) == 1
        assert history[0]["child"] == "doomed"
        assert "ZeroDivisionError" in str(history[0]["error"])


# ── one_for_all deep ────────────────────────────────────────────────────


class TestOneForAllDeep:
    def test_one_failure_restarts_all_children(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ALL, max_restarts=5)
        for name in ("a", "b", "c"):
            spec, _ = _make_child(name, fail_after=0, record=record)
            s.add_child(spec)
        s.start_all()
        restarted = s.handle_failure("b", RuntimeError("boom"))
        assert set(restarted) == {"a", "b", "c"}

    def test_all_stopped_before_restart(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ALL, max_restarts=5)
        a_spec, _a_tok = _make_child("a", fail_after=0, record=record)
        b_spec, _b_tok = _make_child("b", fail_after=0, record=record)
        s.add_child(a_spec)
        s.add_child(b_spec)
        s.start_all()

        s.handle_failure("a", RuntimeError("boom"))
        # Both tokens were stopped during the restart cycle, then restarted.
        # After restart, start() resets the stopped flag, so the token is
        # no longer stopped.  We verify by checking that both children got
        # a second start (restart happened for both).
        started_a = sum(1 for r in record if r["name"] == "a" and r["event"] == "started")
        started_b = sum(1 for r in record if r["name"] == "b" and r["event"] == "started")
        assert started_a == 2  # initial + restart
        assert started_b == 2  # initial + restart

    def test_unknown_child_name_still_restarts_all(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ALL, max_restarts=5)
        a_spec, _ = _make_child("a", fail_after=0, record=record)
        b_spec, _ = _make_child("b", fail_after=0, record=record)
        s.add_child(a_spec)
        s.add_child(b_spec)
        s.start_all()
        restarted = s.handle_failure("unknown", RuntimeError("boom"))
        assert set(restarted) == {"a", "b"}

    def test_empty_supervisor_one_for_all(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ALL, max_restarts=5)
        restarted = s.handle_failure("any", RuntimeError("boom"))
        assert restarted == []

    def test_first_child_fails_on_restart_stops_chain(self) -> None:
        """ONE_FOR_ALL restarts in order; if the first child's restart raises,
        later children are never restarted."""
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ALL, max_restarts=5)
        s.add_child(ChildSpec(name="a", start=lambda: 1 / 0, restart_delay=0))
        s.add_child(ChildSpec(name="b", start=lambda: "b"))
        s.start_all()
        with pytest.raises(ZeroDivisionError):
            s.handle_failure("a", RuntimeError("boom"))
        # b was stopped but never restarted because a's restart raised first
        assert s.running_count == 0  # a failed, b was stopped and restart aborted


# ── rest_for_one deep ───────────────────────────────────────────────────


class TestRestForOneDeep:
    def test_failed_and_after_restarted(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE, max_restarts=5)
        a_spec, _ = _make_child("a", fail_after=0, record=record)
        b_spec, _ = _make_child("b", fail_after=0, record=record)
        c_spec, _ = _make_child("c", fail_after=0, record=record)
        for spec in (a_spec, b_spec, c_spec):
            s.add_child(spec)
        s.start_all()
        restarted = s.handle_failure("b", RuntimeError("boom"))
        assert "a" not in restarted
        assert "b" in restarted
        assert "c" in restarted

    def test_first_child_restarts_all(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE, max_restarts=5)
        for name in ("a", "b", "c"):
            s.add_child(ChildSpec(name=name, start=lambda n=name: n))
        s.start_all()
        assert set(s.handle_failure("a", RuntimeError("boom"))) == {"a", "b", "c"}

    def test_last_child_restarts_only_itself(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE, max_restarts=5)
        for name in ("a", "b", "c"):
            s.add_child(ChildSpec(name=name, start=lambda n=name: n))
        s.start_all()
        assert s.handle_failure("c", RuntimeError("boom")) == ["c"]

    def test_unknown_child_returns_empty(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="a", start=lambda: "a"))
        s.start_all()
        restarted = s.handle_failure("nonexistent", RuntimeError("x"))
        assert restarted == []

    def test_intensity_not_checked_for_unknown_in_rest_for_one(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE, max_restarts=0)
        restarted = s.handle_failure("ghost", RuntimeError("x"))
        assert restarted == []

    def test_single_child_rest_for_one(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="only", start=lambda: "only"))
        s.start_all()
        restarted = s.handle_failure("only", RuntimeError("boom"))
        assert restarted == ["only"]

    def test_mid_chain_failure_stops_restart_chain(self) -> None:
        """REST_FOR_ONE restarts [idx:] in order; if the first child's restart
        raises, later children are never restarted."""
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="a", start=lambda: "a"))
        s.add_child(ChildSpec(name="b", start=lambda: 1 / 0, restart_delay=0))
        s.add_child(ChildSpec(name="c", start=lambda: "c"))
        s.start_all()

        with pytest.raises(ZeroDivisionError):
            s.handle_failure("b", RuntimeError("boom"))
        # c was stopped but never restarted because b's restart raised
        assert s.running_count == 1  # only "a" is running


# ── restart intensity ───────────────────────────────────────────────────


class TestRestartIntensity:
    def test_exceeded_raises(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=2, max_seconds=60)
        s.add_child(ChildSpec(name="f", start=lambda: "ok"))
        s.start_all()

        s.handle_failure("f", ZeroDivisionError("e1"))
        s.handle_failure("f", ZeroDivisionError("e2"))
        with pytest.raises(SupervisorError, match="Max restart intensity"):
            s.handle_failure("f", ZeroDivisionError("e3"))

    def test_window_expires(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=3, max_seconds=0.05)
        s.add_child(ChildSpec(name="w", start=lambda: "ok"))
        s.start_all()

        s.handle_failure("w", ZeroDivisionError("e1"))
        time.sleep(0.1)
        s.handle_failure("w", ZeroDivisionError("e2"))
        time.sleep(0.1)
        s.handle_failure("w", ZeroDivisionError("e3"))
        # no raise — each window expired before the next restart

    def test_max_restarts_one(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=1, max_seconds=60)
        s.add_child(ChildSpec(name="f", start=lambda: "ok"))
        s.start_all()

        s.handle_failure("f", ZeroDivisionError("e1"))
        with pytest.raises(SupervisorError, match="Max restart intensity"):
            s.handle_failure("f", ZeroDivisionError("e2"))

    def test_max_seconds_zero_instantly_prunes(self) -> None:
        """max_seconds=0 means every timestamp is immediately outside the
        window, so restarts never accumulate and intensity is never exceeded."""
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5, max_seconds=0)
        s.add_child(ChildSpec(name="f", start=lambda: "ok"))
        s.start_all()
        for i in range(20):
            s.handle_failure("f", ZeroDivisionError(f"e{i}"))
        assert len(s.restart_history) == 20

    def test_old_entries_pruned(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=3, max_seconds=0.05)
        s.add_child(ChildSpec(name="w", start=lambda: "ok"))
        s.start_all()

        s.handle_failure("w", ZeroDivisionError("e1"))
        time.sleep(0.1)
        s.handle_failure("w", ZeroDivisionError("e2"))
        history = s.restart_history
        assert len(history) == 2

    def test_one_for_all_records_per_child_restart(self) -> None:
        """ONE_FOR_ALL checks intensity once at the start of the operation,
        then records one timestamp per child restart.  Intensity check is NOT
        re-run mid-operation, so 3 children with max_restarts=2 does NOT
        fail on the first call, but WILL fail on the second."""
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ALL, max_restarts=2, max_seconds=60)
        for name in ("a", "b", "c"):
            s.add_child(ChildSpec(name=name, start=lambda n=name: n))
        s.start_all()

        s.handle_failure("a", RuntimeError("e1"))  # records 3 timestamps
        assert s.child_count == 3
        # Next call: intensity checked with 3 existing timestamps >= 2 → fails
        with pytest.raises(SupervisorError):
            s.handle_failure("b", RuntimeError("e2"))

    def test_rest_for_one_records_per_child_restart(self) -> None:
        """REST_FOR_ONE records one timestamp per child restart.  Failing "b"
        restarts b,c (2 timestamps); failing "a" restarts a,b,c (3 timestamps)."""
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE, max_restarts=3, max_seconds=60)
        for name in ("a", "b", "c"):
            s.add_child(ChildSpec(name=name, start=lambda n=name: n))
        s.start_all()

        s.handle_failure("b", RuntimeError("e1"))  # restarts b,c → 2 timestamps
        assert len(s.restart_history) == 2
        # Next failure at "a" restarts a,b,c → 3 more, total 5, exceeds max_restarts=3
        # But intensity is checked BEFORE the restart loop, so only the first 2
        # timestamps are in the window.  So the second call succeeds with 3 children
        # restarted, total 5.
        s.handle_failure("a", RuntimeError("e2"))
        assert len(s.restart_history) == 5


# ── restart delay ───────────────────────────────────────────────────────


class TestRestartDelay:
    def test_delay_respected(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="d", start=lambda: "ok", restart_delay=0.1))
        s.start_all()
        t0 = time.monotonic()
        s.handle_failure("d", RuntimeError("boom"))
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.09

    def test_no_delay_when_zero(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="d", start=lambda: "ok", restart_delay=0))
        s.start_all()
        t0 = time.monotonic()
        s.handle_failure("d", RuntimeError("boom"))
        assert (time.monotonic() - t0) < 0.1

    def test_delay_happens_before_timestamp_recorded(self) -> None:
        """Restart delay is applied, then the restart timestamp is recorded.
        So two restarts with 0.06s delay each push the first timestamp back
        by the cumulative delay, allowing it to fall out of a short window."""
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=2, max_seconds=0.05)
        s.add_child(ChildSpec(name="d", start=lambda: "ok", restart_delay=0.06))
        s.start_all()

        s.handle_failure("d", RuntimeError("e1"))  # delay 0.06, then record
        s.handle_failure("d", RuntimeError("e2"))  # delay 0.06, then record
        # First timestamp ~0.12s old, max_seconds=0.05 → fell out of window.
        # Total recorded: 2 (no intensity exceeded)
        assert len(s.restart_history) == 2

    def test_delay_applied_per_child_in_one_for_all(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ALL, max_restarts=5)
        s.add_child(ChildSpec(name="a", start=lambda: "a", restart_delay=0.03))
        s.add_child(ChildSpec(name="b", start=lambda: "b", restart_delay=0.03))
        s.start_all()
        t0 = time.monotonic()
        s.handle_failure("a", RuntimeError("boom"))
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.05  # at least sum of two delays


# ── restart history ─────────────────────────────────────────────────────


class TestRestartHistory:
    def test_entries_recorded(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="r", start=lambda: "ok"))
        s.start_all()
        s.handle_failure("r", ZeroDivisionError("div0"))
        history = s.restart_history
        assert len(history) == 1
        assert history[0]["child"] == "r"
        assert "ZeroDivisionError" in str(history[0]["error"])

    def test_returned_list_is_copy(self) -> None:
        s = SupervisorTree()
        s.add_child(ChildSpec(name="r", start=lambda: "ok"))
        s.start_all()
        s.handle_failure("r", ZeroDivisionError("e"))
        h1 = s.restart_history
        h1.append({"child": "fake", "error": "injected", "timestamp": 0.0})
        h2 = s.restart_history
        assert len(h2) == 1  # internal list unchanged

    def test_failed_restart_recorded_with_exception_repr(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="doomed", start=lambda: 1 / 0, restart_delay=0))
        s.start_all()
        with pytest.raises(ZeroDivisionError):
            s.handle_failure("doomed", RuntimeError("original"))
        history = s.restart_history
        assert len(history) >= 1
        error_str = str(history[0]["error"])
        assert "ZeroDivisionError" in error_str


# ── empty / missing ─────────────────────────────────────────────────────


class TestEmptyAndMissing:
    def test_empty_supervisor(self) -> None:
        s = SupervisorTree()
        assert s.child_count == 0
        assert s.running_count == 0
        s.start_all()
        assert s.handle_failure("x", RuntimeError("x")) == []

    def test_handle_failure_on_empty_one_for_all(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ALL)
        assert s.handle_failure("any", RuntimeError("x")) == []

    def test_handle_failure_on_empty_rest_for_one(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE)
        assert s.handle_failure("any", RuntimeError("x")) == []


# ── thread safety ───────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_restarts_no_crash(self) -> None:
        import threading

        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=200)
        s.add_child(ChildSpec(name="t", start=lambda: "ok", restart_delay=0))
        s.start_all()

        errors: list[Exception] = []

        def bomb() -> None:
            for _ in range(40):
                try:
                    s.handle_failure("t", ZeroDivisionError("boom"))
                except SupervisorError as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=bomb) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = len(s.restart_history)
        # 4 threads x 40 calls = 160 restart attempts; all should record
        assert total == 160

    def test_concurrent_restart_history_not_corrupted(self) -> None:
        import threading

        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=200)
        s.add_child(ChildSpec(name="t", start=lambda: "ok", restart_delay=0))
        s.start_all()

        def bomb() -> None:
            for _ in range(10):
                with contextlib.suppress(SupervisorError):
                    s.handle_failure("t", ZeroDivisionError("boom"))

        threads = [threading.Thread(target=bomb) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for entry in s.restart_history:
            assert entry["child"] == "t"
            assert isinstance(entry["timestamp"], float)
            assert isinstance(entry["error"], str)

    def test_concurrent_add_and_start(self) -> None:
        import threading

        s = SupervisorTree(max_restarts=100)

        def adder() -> None:
            for i in range(20):
                n = str(i)
                s.add_child(ChildSpec(name=n, start=lambda v=n: v))

        def starter() -> None:
            for _ in range(20):
                s.start_all()

        t1 = threading.Thread(target=adder)
        t2 = threading.Thread(target=starter)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        s.start_all()
        assert s.child_count == 20
        assert s.running_count == 20


# ── _index_of corner cases ──────────────────────────────────────────────


class TestIndexOf:
    def test_name_not_found(self) -> None:
        s = SupervisorTree()
        assert s._index_of("missing") == -1

    def test_case_sensitive(self) -> None:
        s = SupervisorTree()
        s.add_child(ChildSpec(name="Child", start=lambda: "ok"))
        assert s._index_of("child") == -1
        assert s._index_of("Child") == 0


# ── restart after stop_all ──────────────────────────────────────────────


class TestRestartAfterStopAll:
    def test_handle_failure_restarts_stopped_child(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="a", start=lambda: "a"))
        s.start_all()
        s.stop_all()
        assert s.running_count == 0
        restarted = s.handle_failure("a", RuntimeError("stopped"))
        assert restarted == ["a"]
        assert s.running_count == 1

    def test_one_for_all_restarts_all_even_if_stopped(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ALL, max_restarts=5)
        for name in ("a", "b", "c"):
            s.add_child(ChildSpec(name=name, start=lambda n=name: n))
        s.start_all()
        s.stop_all()
        restarted = s.handle_failure("b", RuntimeError("boom"))
        assert set(restarted) == {"a", "b", "c"}
        assert s.running_count == 3

    def test_rest_for_one_restarts_from_index_even_if_stopped(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE, max_restarts=5)
        for name in ("a", "b", "c"):
            s.add_child(ChildSpec(name=name, start=lambda n=name: n))
        s.start_all()
        s.stop_all()
        restarted = s.handle_failure("b", RuntimeError("boom"))
        assert set(restarted) == {"b", "c"}
        assert "a" not in restarted
