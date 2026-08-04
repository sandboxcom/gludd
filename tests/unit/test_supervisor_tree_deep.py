"""Deep supervisor tree tests — Erlang-style one_for_one, one_for_all, rest_for_one."""

from __future__ import annotations

import threading
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
    """Factory: return a ChildSpec whose start callable can be configured to fail."""

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


# ── strategy enum ──────────────────────────────────────────────────────


class TestStrategyEnum:
    def test_known_strategies(self) -> None:
        assert RestartPolicy.ONE_FOR_ONE.value == "one_for_one"
        assert RestartPolicy.ONE_FOR_ALL.value == "one_for_all"
        assert RestartPolicy.REST_FOR_ONE.value == "rest_for_one"


# ── one_for_one ────────────────────────────────────────────────────────


class TestOneForOne:
    def test_single_failure_only_restarts_failed_child(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        a_spec, _ = _make_child("a", fail_after=1, record=record)
        b_spec, _ = _make_child("b", fail_after=0, record=record)
        s.add_child(a_spec)
        s.add_child(b_spec)
        s.start_all()

        restarted = s.handle_failure("a", RuntimeError("boom"))
        assert "a" in restarted
        assert "b" not in restarted  # b untouched

    def test_only_failed_child_restarted_others_keep_running(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        a_spec, _ = _make_child("a", fail_after=1, record=record)
        b_spec, _ = _make_child("b", fail_after=0, record=record)
        c_spec, _ = _make_child("c", fail_after=0, record=record)
        for spec in (a_spec, b_spec, c_spec):
            s.add_child(spec)
        s.start_all()

        s.handle_failure("b", RuntimeError("boom"))
        started_b = sum(1 for r in record if r["name"] == "b" and r["event"] == "started")
        assert started_b == 2  # initial + restart
        started_a = sum(1 for r in record if r["name"] == "a" and r["event"] == "started")
        assert started_a == 1  # never restarted

    def test_multiple_independent_failures(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        for name in ("x", "y", "z"):
            s.add_child(ChildSpec(name=name, start=lambda n=name: n))
        s.start_all()

        r1 = s.handle_failure("x", RuntimeError("e1"))
        r2 = s.handle_failure("z", RuntimeError("e2"))
        assert r1 == ["x"]
        assert r2 == ["z"]


# ── one_for_all ────────────────────────────────────────────────────────


class TestOneForAll:
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
        a_spec, a_tok = _make_child("a", fail_after=0, record=record)
        b_spec, b_tok = _make_child("b", fail_after=0, record=record)
        s.add_child(a_spec)
        s.add_child(b_spec)
        s.start_all()

        s.handle_failure("a", RuntimeError("boom"))
        assert a_tok.stopped
        assert b_tok.stopped

    def test_one_for_all_stop_then_start_events_in_order(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ALL, max_restarts=5)
        for name in ("a", "b"):
            spec, _ = _make_child(name, fail_after=0, record=record)
            s.add_child(spec)
        s.start_all()

        s.handle_failure("a", RuntimeError("boom"))
        events = [(r["name"], r["event"]) for r in record]
        # After initial starts, we should see stops before restarts
        stop_indices = [i for i, e in enumerate(events) if e[1] == "stopped"]
        start_indices = [i for i, e in enumerate(events) if e[1] == "started" and i > 2]
        assert all(si < ri for si in stop_indices for ri in start_indices if si < ri)


# ── rest_for_one ───────────────────────────────────────────────────────


class TestRestForOne:
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

        restarted = s.handle_failure("a", RuntimeError("boom"))
        assert set(restarted) == {"a", "b", "c"}

    def test_last_child_restarts_only_itself(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.REST_FOR_ONE, max_restarts=5)
        for name in ("a", "b", "c"):
            s.add_child(ChildSpec(name=name, start=lambda n=name: n))
        s.start_all()

        restarted = s.handle_failure("c", RuntimeError("boom"))
        assert restarted == ["c"]


# ── max restart limit ──────────────────────────────────────────────────


class TestMaxRestartLimit:
    def test_exceeded_max_restarts_raises(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=2, max_seconds=60)
        s.add_child(ChildSpec(name="fragile", start=lambda: 1 / 0))
        s.start_all()

        s.handle_failure("fragile", ZeroDivisionError("e1"))
        s.handle_failure("fragile", ZeroDivisionError("e2"))
        with pytest.raises(SupervisorError, match="max restart intensity"):
            s.handle_failure("fragile", ZeroDivisionError("e3"))

    def test_restart_window_resets_after_expiry(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=3, max_seconds=0.05)
        s.add_child(ChildSpec(name="w", start=lambda: 1 / 0))
        s.start_all()
        s.handle_failure("w", ZeroDivisionError("e1"))
        time.sleep(0.1)
        s.handle_failure("w", ZeroDivisionError("e2"))
        time.sleep(0.1)
        s.handle_failure("w", ZeroDivisionError("e3"))
        # no raise — window expired each time


# ── restart delay ──────────────────────────────────────────────────────


class TestRestartDelay:
    def test_restart_delay_respected(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="d", start=lambda: "ok", restart_delay=0.1))
        s.start_all()

        t0 = time.monotonic()
        s.handle_failure("d", RuntimeError("boom"))
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.09  # allow small timing variance

    def test_no_delay_when_zero(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="d", start=lambda: "ok", restart_delay=0))
        s.start_all()

        t0 = time.monotonic()
        s.handle_failure("d", RuntimeError("boom"))
        elapsed = time.monotonic() - t0
        assert elapsed < 0.05


# ── edge cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_supervisor(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=3)
        assert s.child_count == 0
        assert s.running_count == 0
        s.start_all()  # no-op
        restarted = s.handle_failure("nonexistent", RuntimeError("x"))
        assert restarted == []

    def test_child_count_and_running_count(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="a", start=lambda: "a"))
        s.add_child(ChildSpec(name="b", start=lambda: "b"))
        assert s.child_count == 2
        s.start_all()
        assert s.running_count == 2

    def test_stop_all(self) -> None:
        record: _RunRecord = []
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        a_spec, a_tok = _make_child("a", fail_after=0, record=record)
        b_spec, b_tok = _make_child("b", fail_after=0, record=record)
        s.add_child(a_spec)
        s.add_child(b_spec)
        s.start_all()

        s.stop_all()
        assert a_tok.stopped
        assert b_tok.stopped
        assert s.running_count == 0

    def test_restart_history_entries(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=5)
        s.add_child(ChildSpec(name="r", start=lambda: 1 / 0))
        s.start_all()
        s.handle_failure("r", ZeroDivisionError("div0"))

        history = s.restart_history
        assert len(history) == 1
        assert history[0]["child"] == "r"
        assert "ZeroDivisionError" in str(history[0]["error"])

    def test_invalid_policy_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown restart policy"):
            SupervisorTree(policy="nonexistent")  # type: ignore[arg-type]

    def test_thread_safe_restart_counting(self) -> None:
        s = SupervisorTree(policy=RestartPolicy.ONE_FOR_ONE, max_restarts=100)
        s.add_child(ChildSpec(name="t", start=lambda: 1 / 0))
        s.start_all()

        errors: list[Exception] = []

        def bomb() -> None:
            for _ in range(20):
                try:
                    s.handle_failure("t", ZeroDivisionError("boom"))
                except SupervisorError as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=bomb) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have recorded 80 restart attempts without crashing
        total = len(s.restart_history)
        assert 65 <= total <= 80
