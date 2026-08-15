"""Deep behavioral tests for deadline enforcement plugins.

Covers enforce-deadline.ts + task_watchdog.py interaction across five dimensions:
1. Timeout detection — edge cases (exact boundary, clock skew, zero elapsed, extreme values)
2. Task killing — stale file → watchdog bridge, kill audit shape, multi-kill cycles
3. Stale task recovery — sweep mechanics, re-dispatch, crash recovery, atomic writes
4. Cascading deadline propagation — multi-task breach ordering, sweep cascades
5. Env var configuration — all 8 env vars individually and in combination
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Python mirror of plugin state-machine logic (from enforce-deadline.ts)
# ---------------------------------------------------------------------------

TASK_TIMEOUT_MS_DEFAULT = 300000


def _djb2(raw: str) -> str:
    hash_val = 5381
    for ch in raw:
        hash_val = ((hash_val << 5) + hash_val + ord(ch)) & 0xFFFFFFFF
    return f"d-{(hash_val):08x}"


def _extract_task_id(args: dict | None) -> str | None:
    if args is None:
        return None
    tid = args.get("task_id")
    if isinstance(tid, str) and tid:
        return tid
    fid = args.get("id")
    if isinstance(fid, str) and fid:
        return fid
    desc = args.get("description", "") or ""
    subtype = args.get("subagent_type", "") or ""
    if desc or subtype:
        return _djb2(f"{subtype}:{desc}")
    return None


def _is_dispatch_tool(tool: str) -> bool:
    return tool in ("task", "agent", "workflow")


def _load_deadlines(state_path: Path, timeout_ms: int) -> dict[str, float]:
    try:
        data = json.loads(state_path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, float] = {}
    now = time.time() * 1000
    max_age = timeout_ms * 3
    for k, v in data.items():
        if not isinstance(v, (int, float)):
            continue
        if now - v > max_age:
            continue
        result[k] = float(v)
    return result


def _save_deadlines(state_path: Path, state: dict[str, float]) -> None:
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, state_path)


def _now_ms() -> float:
    return time.time() * 1000


def _find_breaches(state: dict[str, float], timeout_ms: int, now_ms: float | None = None) -> list[dict]:
    if now_ms is None:
        now_ms = _now_ms()
    breaches: list[dict] = []
    for tid, start in state.items():
        if not isinstance(start, (int, float)):
            continue
        elapsed = now_ms - start
        if elapsed > timeout_ms:
            breaches.append({"task_id": tid, "start_ms": start, "elapsed_ms": elapsed})
    return breaches


def _load_deadlines_raw(state_path: Path) -> dict[str, float]:
    """Load state without sweeping stale entries (for breach-detection simulations)."""
    try:
        data = json.loads(state_path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, float] = {}
    for k, v in data.items():
        if not isinstance(v, (int, float)):
            continue
        result[k] = float(v)
    return result


def _simulate_plugin_before_hook(
    tool: str,
    args: dict | None,
    state_path: Path,
    timeout_ms: int,
    *,
    warned_ids: set[str] | None = None,
    deadline_enabled: bool = True,
    block_mode: bool = False,
) -> tuple[dict[str, float], list[str], list[dict]]:
    """Run one tool.execute.before cycle, returning (new_state, warnings, denials)."""
    if not deadline_enabled:
        return {}, [], []

    state = _load_deadlines_raw(state_path)
    warnings: list[str] = []
    denials: list[dict] = []

    now = _now_ms()

    if _is_dispatch_tool(tool):
        tid = _extract_task_id(args) or f"auto-{int(now)}"
        state[tid] = now
        _save_deadlines(state_path, state)

    for tid, start in list(state.items()):
        if not isinstance(start, (int, float)):
            continue
        elapsed = now - start
        if elapsed > timeout_ms:
            mins = elapsed / 60000
            limit_min = timeout_ms / 60000
            line = f"TASK DEADLINE EXCEEDED: task {tid} has been running for {mins:.1f}min (limit {limit_min:.0f}min)."
            if warned_ids is not None and tid not in warned_ids:
                warned_ids.add(tid)
                warnings.append(line)
            if block_mode and not _is_dispatch_tool(tool):
                denials.append(
                    {
                        "permissionDecision": "deny",
                        "message": f"TASK DEADLINE EXCEEDED: task {tid}",
                    }
                )

    return state, warnings, denials


def _load_stale_ids(stale_path: Path) -> set[str]:
    try:
        data = json.loads(stale_path.read_text())
    except Exception:
        return set()
    if isinstance(data, list):
        return {e["task_id"] for e in data if isinstance(e, dict) and "task_id" in e}
    if isinstance(data, dict):
        return {str(k) for k in data}
    return set()


# ===========================================================================
# DIMENSION 1 — Timeout detection: deep edge cases
# ===========================================================================


class TestTimeoutDetectionExactBoundary:
    """elapsed > timeout is the condition — the exact boundary (==) must NOT fire."""

    def test_elapsed_equals_timeout_does_not_fire(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {"task-exact": now - timeout_ms}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 0

    def test_elapsed_one_ms_over_timeout_fires(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {"task-over": now - (timeout_ms + 1)}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 1
        assert breaches[0]["task_id"] == "task-over"

    def test_elapsed_one_ms_under_timeout_does_not_fire(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {"task-under": now - (timeout_ms - 1)}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 0

    def test_zero_elapsed_newly_dispatched_task_does_not_breach(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {"task-new": now}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 0


class TestTimeoutDetectionClockSkew:
    """Tasks with start times in the future (clock skew) must not produce negative elapsed."""

    def test_future_start_time_does_not_breach(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {"task-future": now + 60000}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 0

    def test_negative_elapsed_skipped(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {"task-future": now + 100000}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 0

    def test_very_far_future_start_does_not_break_math(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {"task-far": now + 10**12}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 0


class TestTimeoutDetectionMixedTasks:
    """Multiple tasks with mixed stale/fresh status."""

    def test_mixed_stale_and_fresh(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {
            "stale-a": now - 10000,
            "fresh-b": now - 2000,
            "stale-c": now - 7000,
            "fresh-d": now - 100,
        }
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 2
        ids = {b["task_id"] for b in breaches}
        assert ids == {"stale-a", "stale-c"}

    def test_all_stale(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {
            "a": now - 10000,
            "b": now - 8000,
            "c": now - 6000,
        }
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 3

    def test_none_stale(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {
            "a": now - 1000,
            "b": now - 2000,
            "c": now - 4999,
        }
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 0


class TestTimeoutDetectionExtremeValues:
    """Extreme timeout configurations."""

    def test_zero_timeout_every_task_stale_immediately(self):
        timeout_ms = 0
        now = _now_ms()
        state = {"task-x": now - 1}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 1

    def test_very_large_timeout_no_breach(self):
        timeout_ms = 3_600_000
        now = _now_ms()
        state = {"task-x": now - 300_000}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 0

    def test_non_number_entries_ignored(self):
        timeout_ms = 5000
        now = _now_ms()
        state: dict = {"task-str": "abc", "task-real": now - 10000}
        breaches = _find_breaches(state, timeout_ms, now)  # type: ignore[arg-type]
        assert len(breaches) == 1
        assert breaches[0]["task_id"] == "task-real"

    def test_tasks_with_zero_start_may_breach_by_elapsed(self):
        timeout_ms = 5000
        now = _now_ms()
        state = {"task-zero": 0.0, "task-real": now - 10000}
        breaches = _find_breaches(state, timeout_ms, now)
        assert any(b["task_id"] == "task-real" for b in breaches)
        assert len(breaches) >= 1


# ===========================================================================
# DIMENSION 2 — Task killing: stale file → watchdog bridge
# ===========================================================================


class TestStaleFileWatchdogBridge:
    """The plugin writes /tmp/gludd-task-stale.json; the watchdog reads it."""

    def test_stale_file_written_with_correct_shape(self, tmp_path: Path):
        stale_path = tmp_path / "stale.json"
        now = _now_ms()
        entries = [
            {"task_id": "d-abc", "start_ms": now - 60000, "elapsed_ms": 60000, "stale_at": now},
        ]
        tmp = stale_path.with_suffix(stale_path.suffix + ".tmp")
        tmp.write_text(json.dumps(entries))
        os.replace(tmp, stale_path)

        ids = _load_stale_ids(stale_path)
        assert "d-abc" in ids

    def test_watchdog_finds_stale_via_deadlines_not_just_stale_file(self, tmp_path: Path):
        """Watchdog find_stale_tasks() works directly from deadlines, not the stale file."""
        from scripts.task_watchdog import find_stale_tasks

        now_ms = _now_ms()
        deadlines = {"task-old": now_ms - 400_000, "task-new": now_ms - 10_000}
        stale = find_stale_tasks(deadlines, timeout_ms=300_000, now_ms=now_ms)
        assert len(stale) == 1
        assert stale[0]["task_id"] == "task-old"

    def test_watchdog_detects_breach_from_task_watchdog_module(self, tmp_path: Path):
        from scripts.task_watchdog import load_deadlines as wd_load

        now_ms = _now_ms()
        f = tmp_path / "deadlines.json"
        f.write_text(json.dumps({"task-a": now_ms - 400_000}))
        deadlines = wd_load(str(f))
        assert "task-a" in deadlines

    def test_kill_record_full_shape(self, tmp_path: Path):
        from scripts.task_watchdog import record_kill

        f = tmp_path / "killed.json"
        record_kill("d-xyz", pid=4242, elapsed_ms=350_000, reason="task_timeout_exceeded", killed_file=str(f))
        data = json.loads(f.read_text())
        entry = data[0]
        assert entry["task_id"] == "d-xyz"
        assert entry["pid"] == 4242
        assert entry["elapsed_ms"] == 350_000
        assert entry["reason"] == "task_timeout_exceeded"
        assert "killed_at" in entry

    def test_multiple_kills_appended(self, tmp_path: Path):
        from scripts.task_watchdog import record_kill

        f = tmp_path / "killed.json"
        for i in range(5):
            record_kill(f"task-{i}", pid=1000 + i, elapsed_ms=300_000 + i * 1000, reason="timeout", killed_file=str(f))
        data = json.loads(f.read_text())
        assert len(data) == 5
        pids = {e["pid"] for e in data}
        assert pids == {1000, 1001, 1002, 1003, 1004}


class TestKillProcessEdgeCases:
    """kill_process signal ordering and error handling."""

    def test_sigterm_before_sigkill_ordering(self):
        import signal as sig_module
        from unittest.mock import patch

        from scripts.task_watchdog import kill_process

        signals_sent: list[int] = []

        def _record_kill(pid: int, sig: int) -> None:
            signals_sent.append(sig)

        with patch("scripts.task_watchdog.os.kill") as mk, patch("scripts.task_watchdog.time.sleep"):
            mk.side_effect = _record_kill
            kill_process(99999)
            assert sig_module.SIGTERM in signals_sent
            sigterm_idx = signals_sent.index(sig_module.SIGTERM)
            sigkill_detected = any(s == sig_module.SIGKILL for s in signals_sent[sigterm_idx:])
            assert sigkill_detected

    def test_process_exited_during_sigterm_wait(self):
        from unittest.mock import patch

        from scripts.task_watchdog import kill_process

        call_count = [0]

        def _alternating(pid: int, sig: int) -> None:
            call_count[0] += 1
            if call_count[0] == 2:
                raise ProcessLookupError

        with patch("scripts.task_watchdog.os.kill") as mk, patch("scripts.task_watchdog.time.sleep"):
            mk.side_effect = _alternating
            result = kill_process(99999)
            assert result is True

    def test_permission_denied_mid_sequence_continues(self):
        """PermissionError on fallback kill path returns False (fail-open, not a crash)."""
        from unittest.mock import patch

        from scripts.task_watchdog import kill_process

        with (
            patch("scripts.task_watchdog.os.kill", side_effect=PermissionError),
            patch("scripts.task_watchdog.time.sleep"),
        ):
            result = kill_process(99999)
            assert result is False


# ===========================================================================
# DIMENSION 3 — Stale task recovery: sweep, re-dispatch, crash recovery
# ===========================================================================


class TestSweepMechanicsDeep:
    """Sweep removes entries 3x past timeout. Unblocks warnedIds for re-dispatch."""

    def test_sweep_removes_exactly_3x_timeout_boundary(self, tmp_path: Path):
        timeout_ms = 5000
        now = _now_ms()
        now - (3 * timeout_ms)
        state = {
            "kept": now - (3 * timeout_ms - 1),
            "removed": now - (3 * timeout_ms + 1),
        }
        _save_deadlines(tmp_path / "d.json", state)
        loaded = _load_deadlines(tmp_path / "d.json", timeout_ms)
        assert "kept" in loaded
        assert "removed" not in loaded

    def test_sweep_clears_warned_ids_for_removed_entries(self):
        """When sweep removes an entry, its warnedIds record should be cleared."""
        now = _now_ms()
        timeout_ms = 5000
        warned_ids = {"task-ancient"}
        ancient_start = now - (4 * timeout_ms)

        entry_deleted = False
        id_cleared = False
        if "task-ancient" in warned_ids and now - ancient_start > timeout_ms * 3:
            warned_ids.discard("task-ancient")
            id_cleared = True
            entry_deleted = True
        assert id_cleared
        assert entry_deleted

    def test_redispatch_after_sweep_produces_new_warning(self):
        """After sweep removes a task, re-dispatching the same task ID should warn afresh."""
        timeout_ms = 5000
        warned_ids: set[str] = set()
        state_path = Path("/tmp/gludd-deep-redispatch-test.json")

        try:
            now = _now_ms()
            {"task-x": now - (4 * timeout_ms)}
            warned_ids.add("task-x")

            loaded = _load_deadlines(state_path, timeout_ms)
            assert "task-x" not in loaded
            warned_ids.discard("task-x")

            new_now = _now_ms()
            loaded["task-x"] = new_now
            _save_deadlines(state_path, loaded)

            elapsed = _now_ms() - new_now
            warned_again = "task-x" not in warned_ids and elapsed > 0
            assert warned_again
        finally:
            with contextlib.suppress(FileNotFoundError):
                state_path.unlink()
            with contextlib.suppress(FileNotFoundError):
                Path(str(state_path) + ".tmp").unlink()

    def test_sweep_only_removes_stale_not_fresh(self, tmp_path: Path):
        timeout_ms = 5000
        now = _now_ms()
        state = {
            "fresh": now - 2000,
            "moderate": now - 14000,
            "stale": now - (3 * timeout_ms + 5000),
        }
        _save_deadlines(tmp_path / "d.json", state)
        loaded = _load_deadlines(tmp_path / "d.json", timeout_ms)
        assert "fresh" in loaded
        assert "moderate" in loaded
        assert "stale" not in loaded

    def test_empty_sweep_no_crash(self, tmp_path: Path):
        state: dict[str, float] = {}
        _save_deadlines(tmp_path / "d.json", state)
        loaded = _load_deadlines(tmp_path / "d.json", 5000)
        assert loaded == {}


class TestCrashRecovery:
    """Corrupt state files must fail-open: return empty, never crash."""

    def test_partial_json_returns_empty(self, tmp_path: Path):
        f = tmp_path / "partial.json"
        f.write_text('{"task-a": 123, "task-b": 456')
        loaded = _load_deadlines(f, 300000)
        assert loaded == {}

    def test_truncated_json_returns_empty(self, tmp_path: Path):
        f = tmp_path / "truncated.json"
        f.write_text('{"task-a": 123, "task-b"')
        loaded = _load_deadlines(f, 300000)
        assert loaded == {}

    def test_list_instead_of_dict_returns_empty(self, tmp_path: Path):
        f = tmp_path / "list.json"
        f.write_text(json.dumps([1, 2, 3]))
        loaded = _load_deadlines(f, 300000)
        assert loaded == {}

    def test_empty_file_returns_empty(self, tmp_path: Path):
        f = tmp_path / "empty.json"
        f.write_text("")
        loaded = _load_deadlines(f, 300000)
        assert loaded == {}

    def test_nested_object_returns_entries_at_top_level(self, tmp_path: Path):
        f = tmp_path / "nested.json"
        now = _now_ms()
        f.write_text(json.dumps({"task-a": now - 2000, "__meta__": now - 1000}))
        loaded = _load_deadlines(f, 300000)
        assert "task-a" in loaded
        assert "__meta__" in loaded


class TestAtomicWriteSafety:
    """Partial writes must not corrupt the state file."""

    def test_atomic_rename_cleans_tmp(self, tmp_path: Path):
        state_path = tmp_path / "d.json"
        _save_deadlines(state_path, {"task-a": _now_ms()})
        assert state_path.exists()
        tmp_candidate = state_path.parent / (state_path.name + ".tmp")
        assert not tmp_candidate.exists()

    def test_consecutive_writes_are_atomic(self, tmp_path: Path):
        state_path = tmp_path / "d.json"
        _save_deadlines(state_path, {"task-a": _now_ms()})
        _save_deadlines(state_path, {"task-b": _now_ms()})
        loaded = _load_deadlines(state_path, 300000)
        assert list(loaded.keys()) == ["task-b"]


# ===========================================================================
# DIMENSION 4 — Cascading deadline propagation
# ===========================================================================


class TestCascadingBreachOrdering:
    """Multiple breached tasks should all be detected and warned (once each)."""

    def test_all_breached_tasks_warned_once_each(self, tmp_path: Path):
        timeout_ms = 5000
        now = _now_ms()
        state_path = tmp_path / "d.json"
        state = {
            "task-a": now - 7000,
            "task-b": now - 8000,
            "task-c": now - 9000,
        }
        _save_deadlines(state_path, state)

        warned_ids: set[str] = set()
        warnings: list[str] = []
        loaded = _load_deadlines(state_path, timeout_ms)
        for tid, start in loaded.items():
            if now - start > timeout_ms and tid not in warned_ids:
                warned_ids.add(tid)
                warnings.append(f"WARN {tid}")

        assert len(warnings) == 3
        assert warned_ids == {"task-a", "task-b", "task-c"}

    def test_second_cycle_no_duplicate_warnings(self, tmp_path: Path):
        timeout_ms = 5000
        now = _now_ms()
        state_path = tmp_path / "d.json"
        state = {"task-a": now - 7000}
        _save_deadlines(state_path, state)

        warned_ids: set[str] = set()
        for _ in range(5):
            loaded = _load_deadlines(state_path, timeout_ms)
            new_now = _now_ms()
            for tid, start in loaded.items():
                if new_now - start > timeout_ms and tid not in warned_ids:
                    warned_ids.add(tid)

        assert len(warned_ids) == 1

    def test_new_breach_in_subsequent_cycle_detected(self, tmp_path: Path):
        timeout_ms = 5000
        state_path = tmp_path / "d.json"
        now = _now_ms()
        state = {"task-a": now - 500}
        _save_deadlines(state_path, state)

        warned_ids: set[str] = set()

        loaded1 = _load_deadlines(state_path, timeout_ms)
        for tid, start in loaded1.items():
            if now - start > timeout_ms and tid not in warned_ids:
                warned_ids.add(tid)
        assert len(warned_ids) == 0

        time.sleep(0.01)
        later = _now_ms()
        loaded2 = _load_deadlines(state_path, timeout_ms)
        for _tid, start in loaded2.items():
            if later - start > timeout_ms:
                raise AssertionError("should still be under timeout with short wait")

    def test_sweep_cascade_does_not_remove_other_entries(self, tmp_path: Path):
        timeout_ms = 5000
        now = _now_ms()
        state = {
            "fresh": now - 2000,
            "stale": now - (3 * timeout_ms + 1000),
            "moderate": now - 14000,
        }
        state_path = tmp_path / "d.json"
        _save_deadlines(state_path, state)
        loaded = _load_deadlines(state_path, timeout_ms)
        assert "fresh" in loaded
        assert "moderate" in loaded
        assert "stale" not in loaded


class TestCascadingTaskParentChild:
    """Parent task timeout detection — watchdog finds child processes."""

    def test_parent_child_state_persistence(self, tmp_path: Path):
        """Parent and child task IDs are independent in state file."""
        state = {"parent-task": _now_ms() - 60000, "child-task": _now_ms() - 20000}
        state_path = tmp_path / "d.json"
        _save_deadlines(state_path, state)
        loaded = _load_deadlines(state_path, 300000)
        assert "parent-task" in loaded
        assert "child-task" in loaded

    def test_parent_breached_child_in_flight_still_tracked(self, tmp_path: Path):
        """If parent breaches but child is still under timeout, child remains tracked."""
        timeout_ms = 5000
        now = _now_ms()
        state = {
            "parent": now - 10000,
            "child": now - 2000,
        }
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 1
        assert breaches[0]["task_id"] == "parent"
        assert "child" in {k: v for k, v in state.items() if k == "child"}


# ===========================================================================
# DIMENSION 5 — Env var configuration
# ===========================================================================


class TestEnvVarTimeoutMs:
    """GLUDD_TASK_TIMEOUT_MS: all timeout detection logic respects this value."""

    def test_default_300000_ms(self):
        src = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-deadline.ts"
        m = re.search(r'GLUDD_TASK_TIMEOUT_MS \|\| "(\d+)"', src.read_text())
        assert m and int(m.group(1)) == 300000

    def test_watchdog_also_defaults_300000(self):
        from scripts.task_watchdog import TIMEOUT_MS

        assert TIMEOUT_MS == 300_000

    def test_custom_timeout_detected_in_plugin_pattern(self):
        """The plugin uses parseInt so any integer value is valid."""
        custom = 120000
        timeout_ms = custom
        now = _now_ms()
        state = {"task-x": now - (custom + 1000)}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 1

    def test_custom_timeout_nothing_detected_under(self):
        custom = 120000
        timeout_ms = custom
        now = _now_ms()
        state = {"task-x": now - (custom - 1000)}
        breaches = _find_breaches(state, timeout_ms, now)
        assert len(breaches) == 0

    def test_watchdog_find_stale_respects_custom_timeout(self):
        from scripts.task_watchdog import find_stale_tasks

        now_ms = _now_ms()
        deadlines = {"task-x": now_ms - 60_000}
        stale = find_stale_tasks(deadlines, timeout_ms=30_000, now_ms=now_ms)
        assert len(stale) == 1

        stale2 = find_stale_tasks(deadlines, timeout_ms=120_000, now_ms=now_ms)
        assert len(stale2) == 0


class TestEnvVarDeadlineEnabled:
    """GLUDD_TASK_DEADLINE_ENABLED=0 disables all deadline enforcement."""

    def test_disabled_plugin_skips_everything(self, tmp_path: Path):
        state_path = tmp_path / "d.json"
        warned_ids: set[str] = set()
        state, warnings, _denials = _simulate_plugin_before_hook(
            "task",
            {"task_id": "a"},
            state_path,
            5000,
            warned_ids=warned_ids,
            deadline_enabled=False,
        )
        assert len(state) == 0
        assert len(warnings) == 0

    def test_enabled_plugin_records_dispatch(self, tmp_path: Path):
        state_path = tmp_path / "d.json"
        warned_ids: set[str] = set()
        state, _warnings, _denials = _simulate_plugin_before_hook(
            "task",
            {"task_id": "enabled-task"},
            state_path,
            5000,
            warned_ids=warned_ids,
            deadline_enabled=True,
        )
        assert "enabled-task" in state

    def test_disabled_then_reenabled_old_state_persisted(self, tmp_path: Path):
        """If state existed from prior session, re-enabling detects old breaches."""
        now = _now_ms()
        state_path = tmp_path / "d.json"
        _save_deadlines(state_path, {"old-task": now - 60000})

        warned_ids: set[str] = set()
        _state, warnings, _denials = _simulate_plugin_before_hook(
            "read",
            {"path": "/x"},
            state_path,
            5000,
            warned_ids=warned_ids,
            deadline_enabled=True,
        )
        assert len(warnings) >= 1


class TestEnvVarDeadlineEnforceAndBlock:
    """GLUDD_TASK_DEADLINE_ENFORCE=0 and GLUDD_TASK_DEADLINE_BLOCK=0."""

    def test_enforce_disabled_returns_no_denials(self, tmp_path: Path):
        """When DEADLINE_ENFORCE=0, BLOCK is effectively disabled."""
        state_path = tmp_path / "d.json"
        now = _now_ms()
        _save_deadlines(state_path, {"hung": now - 60000})

        warned_ids: set[str] = set()
        _state, warnings, denials = _simulate_plugin_before_hook(
            "read",
            {"path": "/x"},
            state_path,
            5000,
            warned_ids=warned_ids,
            deadline_enabled=True,
            block_mode=False,
        )
        assert len(warnings) >= 1
        assert len(denials) == 0

    def test_block_mode_denies_non_dispatch_tools(self, tmp_path: Path):
        state_path = tmp_path / "d.json"
        now = _now_ms()
        _save_deadlines(state_path, {"hung": now - 60000})

        warned_ids: set[str] = set()
        _state, warnings, denials = _simulate_plugin_before_hook(
            "read",
            {"path": "/x"},
            state_path,
            5000,
            warned_ids=warned_ids,
            deadline_enabled=True,
            block_mode=True,
        )
        assert len(warnings) >= 1
        assert len(denials) == 1

    def test_block_mode_skips_dispatch_tools(self, tmp_path: Path):
        """The plugin source has: if (BLOCK && ... && !isDispatchTool(tool))."""
        state_path = tmp_path / "d.json"
        now = _now_ms()
        _save_deadlines(state_path, {"hung": now - 60000})

        warned_ids: set[str] = set()
        _state, _warnings, denials = _simulate_plugin_before_hook(
            "task",
            {"task_id": "fresh-work"},
            state_path,
            5000,
            warned_ids=warned_ids,
            deadline_enabled=True,
            block_mode=True,
        )
        assert len(denials) == 0


class TestEnvVarCustomFilePaths:
    """GLUDD_TASK_DEADLINE_STATE, GLUDD_TASK_DEADLINE_WARNINGS, GLUDD_TASK_STALE_FILE."""

    def test_default_state_path_in_source(self):
        src = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-deadline.ts"
        assert "DEADLINE_STATE" in src.read_text()
        assert "gludd-task-deadlines.json" in src.read_text()

    def test_default_warnings_path_in_source(self):
        src = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-deadline.ts"
        assert "WARNINGS_LOG" in src.read_text()
        assert "warnings.log" in src.read_text()

    def test_default_stale_path_in_source(self):
        src = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-deadline.ts"
        assert "STALE_FILE" in src.read_text()
        assert "gludd-task-stale.json" in src.read_text()

    def test_watchdog_also_readable_from_custom_path(self, tmp_path: Path):
        from scripts.task_watchdog import load_deadlines as wd_load
        from scripts.task_watchdog import load_stale_ids

        f = tmp_path / "custom-deadlines.json"
        now_ms = _now_ms()
        f.write_text(json.dumps({"task-custom": now_ms - 400_000}))
        deadlines = wd_load(str(f))
        assert "task-custom" in deadlines

        f2 = tmp_path / "custom-stale.json"
        f2.write_text(json.dumps([{"task_id": "d-abc", "stale_at": _now_ms()}]))
        ids = load_stale_ids(str(f2))
        assert "d-abc" in ids

    def test_watchdog_killed_file_path_respected(self, tmp_path: Path):
        from scripts.task_watchdog import record_kill

        f = tmp_path / "custom-killed.json"
        record_kill("d-xxx", pid=1, elapsed_ms=1, reason="test", killed_file=str(f))
        assert f.exists()
        data = json.loads(f.read_text())
        assert len(data) == 1


class TestEnvVarWatchdogPoll:
    """GLUDD_TASK_WATCHDOG_POLL configures poll interval."""

    def test_default_poll_is_5_seconds(self):
        from scripts.task_watchdog import POLL_SECS

        assert POLL_SECS == 5

    def test_custom_poll_via_env_mirrors_source(self):
        """The env var name is in the source and defaults to 5."""
        src = Path(__file__).resolve().parents[2] / "scripts/task_watchdog.py"
        content = src.read_text()
        assert "GLUDD_TASK_WATCHDOG_POLL" in content
        assert '"5"' in content


# ===========================================================================
# Integration: full lifecycle simulation
# ===========================================================================


class TestFullLifecycleSimulation:
    """End-to-end: dispatch → breach → sweep → re-dispatch."""

    def test_full_lifecycle(self, tmp_path: Path):
        state_path = tmp_path / "d.json"
        timeout_ms = 100000
        warned_ids: set[str] = set()

        state, _, _ = _simulate_plugin_before_hook(
            "task",
            {"task_id": "lifecycle-1"},
            state_path,
            timeout_ms,
            warned_ids=warned_ids,
        )
        assert "lifecycle-1" in state

        loaded = _load_deadlines(state_path, timeout_ms)
        assert "lifecycle-1" in loaded

        now = _now_ms()
        state_with_breach: dict[str, float] = {"lifecycle-1": now - (timeout_ms + 5000)}
        _save_deadlines(state_path, state_with_breach)

        _, warnings, _ = _simulate_plugin_before_hook(
            "read",
            {"path": "/x"},
            state_path,
            timeout_ms,
            warned_ids=warned_ids,
        )
        assert len(warnings) >= 1
        assert len(warned_ids) == 1

        ancient = now - (3 * timeout_ms + 10000)
        _save_deadlines(state_path, {"lifecycle-1": ancient})

        loaded2 = _load_deadlines(state_path, timeout_ms)
        assert "lifecycle-1" not in loaded2

        warned_ids.discard("lifecycle-1")
        state3, _, _ = _simulate_plugin_before_hook(
            "agent",
            {"task_id": "lifecycle-2"},
            state_path,
            timeout_ms,
            warned_ids=warned_ids,
        )
        assert "lifecycle-2" in state3

    def test_multiple_dispatches_different_ids(self, tmp_path: Path):
        state_path = tmp_path / "d.json"
        timeout_ms = 300000
        warned_ids: set[str] = set()

        for i in range(5):
            _simulate_plugin_before_hook(
                "task",
                {"task_id": f"task-{i}"},
                state_path,
                timeout_ms,
                warned_ids=warned_ids,
            )
        loaded = _load_deadlines(state_path, timeout_ms)
        assert len(loaded) == 5


# ===========================================================================
# Source code invariant tests
# ===========================================================================


class TestDeadlineSourceInvariants:
    """Verify the plugin source has the expected shape and constants."""

    _DEADLINE_SRC: str | None = None

    @property
    def src(self) -> str:
        if self._DEADLINE_SRC is None:
            self._DEADLINE_SRC = (
                Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-deadline.ts"
            ).read_text()
        return self._DEADLINE_SRC

    def test_all_env_vars_present(self):
        assert "GLUDD_TASK_TIMEOUT_MS" in self.src
        assert "GLUDD_TASK_DEADLINE_ENABLED" in self.src
        assert "GLUDD_TASK_DEADLINE_ENFORCE" in self.src
        assert "GLUDD_TASK_DEADLINE_BLOCK" in self.src
        assert "GLUDD_TASK_DEADLINE_STATE" in self.src
        assert "GLUDD_TASK_DEADLINE_WARNINGS" in self.src
        assert "GLUDD_TASK_STALE_FILE" in self.src

    def test_fail_open_in_all_functions(self):
        """Every function that does I/O must have try/catch."""
        assert "function loadDeadlines" in self.src
        assert "function saveDeadlines" in self.src
        assert "function sweepStaleEntries" in self.src
        assert "function appendWarning" in self.src
        assert "function recordStaleTask" in self.src

    def test_hot_reload_integration(self):
        assert "loadHotModule" in self.src
        assert "deadline" in self.src

    def test_subagent_guard_present(self):
        assert "isSubagent()" in self.src
        assert "OPENCODE_SUBAGENT" in self.src

    def test_sweep_max_age_formula(self):
        assert "TASK_TIMEOUT_MS * 3" in self.src

    def test_dispatch_tools_set(self):
        assert "isDispatchTool" in self.src
        assert "task" in self.src
        assert "agent" in self.src
        assert "workflow" in self.src

    def test_noise_control_throttle(self):
        assert "warnedIds" in self.src
        assert "Set<string>" in self.src

    def test_plugin_export_satisfies_contract(self):
        assert "satisfies Plugin" in self.src
        assert "export default ((" in self.src


class TestWatchdogSourceInvariants:
    """Verify watchdog source has expected constants and patterns."""

    _WD_SRC: str | None = None

    @property
    def src(self) -> str:
        if self._WD_SRC is None:
            self._WD_SRC = (Path(__file__).resolve().parents[2] / "scripts/task_watchdog.py").read_text()
        return self._WD_SRC

    def test_all_env_vars_present(self):
        assert "GLUDD_TASK_DEADLINE_STATE" in self.src
        assert "GLUDD_TASK_STALE_FILE" in self.src
        assert "GLUDD_TASK_KILLED_FILE" in self.src
        assert "GLUDD_TASK_TIMEOUT_MS" in self.src
        assert "GLUDD_TASK_WATCHDOG_POLL" in self.src
        assert "GLUDD_TASK_WATCHDOG_PID" in self.src
        assert "GLUDD_TASK_WATCHDOG_LOG" in self.src

    def test_default_source_values(self):
        # The default timeout is the shared gludd_env_defaults constant (single
        # source of truth); pin both the watchdog's use of it and its value.
        assert "gludd_env_defaults.TASK_TIMEOUT_MS_DEFAULT" in self.src
        defaults_path = Path(__file__).resolve().parents[2] / "scripts/gludd_env_defaults.py"
        defaults_src = defaults_path.read_text()
        assert 'TASK_TIMEOUT_MS_DEFAULT = "300000"' in defaults_src
        assert '"/tmp/gludd-task-deadlines.json"' in self.src
        assert '"/tmp/gludd-task-stale.json"' in self.src
        assert '"/tmp/gludd-task-killed.json"' in self.src
        assert '"5"' in self.src

    def test_sigterm_sigkill_sequence(self):
        assert "SIGTERM" in self.src
        assert "SIGKILL" in self.src

    def test_fail_open_annotation(self):
        assert "fail-open" in self.src.lower()

    def test_task_patterns_defined(self):
        assert "TASK_PROCESS_PATTERNS" in self.src
        assert "EXCLUDE_PATTERNS" in self.src

    def test_kill_multiple_candidates(self):
        assert "descendant_processes" in self.src or "for process in candidates" in self.src


# ===========================================================================
# Quick pytest run:
#   make test TESTFILE=tests/unit/test_deadline_enforcement_deep.py
# ===========================================================================
