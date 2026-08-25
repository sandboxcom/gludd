"""Tests for scripts/task_watchdog.py — the task-killer watchdog daemon.

The watchdog reads /tmp/gludd-task-deadlines.json (written by enforce-deadline.ts),
finds tasks whose elapsed wall-clock > GLUDD_TASK_TIMEOUT_MS, kills their
associated processes, and records kills in /tmp/gludd-task-killed.json.
"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from unittest.mock import patch

from scripts.process_cleanup import ProcessInfo
from scripts.task_watchdog import (
    find_hung_processes,
    find_stale_tasks,
    kill_process,
    load_deadlines,
    load_stale_ids,
    record_kill,
    run_once,
)


def test_direct_script_execution_has_process_cleanup_import_fallback() -> None:
    """The Makefile launches the file path, so imports must work outside package mode."""
    source = Path(__file__).resolve().parents[2] / "scripts" / "task_watchdog.py"
    assert "from process_cleanup import descendant_processes, snapshot_processes" in source.read_text()

# ---------------------------------------------------------------------------
# load_deadlines
# ---------------------------------------------------------------------------

class TestLoadDeadlines:
    def test_loads_dict_format(self, tmp_path: Path):
        """Plugin writes {task_id: epoch_ms}. Must parse to {task_id: float}."""
        f = tmp_path / "deadlines.json"
        now_ms = time.time() * 1000
        f.write_text(json.dumps({"task-a": now_ms, "task-b": now_ms - 1000}))
        result = load_deadlines(str(f))
        assert "task-a" in result
        assert "task-b" in result
        assert isinstance(result["task-a"], float)

    def test_missing_file_returns_empty(self, tmp_path: Path):
        """Missing state file = no tracked tasks (fail-open)."""
        assert load_deadlines(str(tmp_path / "nonexistent.json")) == {}

    def test_malformed_json_returns_empty(self, tmp_path: Path):
        """Corrupt JSON must not crash — return {} (fail-open)."""
        f = tmp_path / "bad.json"
        f.write_text("not json at all {{{")
        assert load_deadlines(str(f)) == {}

    def test_non_dict_returns_empty(self, tmp_path: Path):
        """If the file is a list or scalar, return {} gracefully."""
        f = tmp_path / "list.json"
        f.write_text(json.dumps([1, 2, 3]))
        assert load_deadlines(str(f)) == {}


# ---------------------------------------------------------------------------
# find_stale_tasks
# ---------------------------------------------------------------------------

class TestFindStaleTasks:
    def test_finds_task_over_timeout(self):
        """A task whose elapsed > timeout_ms is stale."""
        now_ms = time.time() * 1000
        deadlines = {
            "fresh-task": now_ms - 10_000,          # 10s ago — fresh
            "stale-task": now_ms - 400_000,          # 400s ago — stale (>300s)
        }
        stale = find_stale_tasks(deadlines, timeout_ms=300_000, now_ms=now_ms)
        stale_ids = [s["task_id"] for s in stale]
        assert "stale-task" in stale_ids
        assert "fresh-task" not in stale_ids

    def test_empty_deadlines_returns_empty(self):
        assert find_stale_tasks({}, timeout_ms=300_000) == []

    def test_stale_entry_has_elapsed_field(self):
        """Each stale finding must carry elapsed_ms for the kill record."""
        now_ms = time.time() * 1000
        deadlines = {"old": now_ms - 500_000}
        stale = find_stale_tasks(deadlines, timeout_ms=300_000, now_ms=now_ms)
        assert len(stale) == 1
        assert "elapsed_ms" in stale[0]
        assert stale[0]["elapsed_ms"] > 300_000

    def test_stale_entry_has_task_id_and_start(self):
        now_ms = time.time() * 1000
        start = now_ms - 500_000
        deadlines = {"d-abc123": start}
        stale = find_stale_tasks(deadlines, timeout_ms=300_000, now_ms=now_ms)
        assert stale[0]["task_id"] == "d-abc123"
        assert stale[0]["start_ms"] == start


# ---------------------------------------------------------------------------
# load_stale_ids
# ---------------------------------------------------------------------------

class TestLoadStaleIds:
    def test_reads_stale_file(self, tmp_path: Path):
        """Plugin writes breached task IDs to stale file."""
        f = tmp_path / "stale.json"
        f.write_text(json.dumps([
            {"task_id": "d-abc", "stale_at": time.time()},
        ]))
        ids = load_stale_ids(str(f))
        assert "d-abc" in ids

    def test_missing_stale_file_returns_empty(self, tmp_path: Path):
        assert load_stale_ids(str(tmp_path / "nope.json")) == set()


# ---------------------------------------------------------------------------
# record_kill
# ---------------------------------------------------------------------------

class TestRecordKill:
    def test_appends_to_killed_file(self, tmp_path: Path):
        """Kill records must be appended (audit trail), not overwrite."""
        f = tmp_path / "killed.json"
        record_kill("d-aaa", pid=12345, elapsed_ms=400_000,
                    reason="timeout", killed_file=str(f))
        record_kill("d-bbb", pid=12346, elapsed_ms=500_000,
                    reason="timeout", killed_file=str(f))
        data = json.loads(f.read_text())
        assert len(data) == 2
        assert data[0]["task_id"] == "d-aaa"
        assert data[1]["task_id"] == "d-bbb"

    def test_kill_record_has_timestamp(self, tmp_path: Path):
        f = tmp_path / "killed.json"
        record_kill("d-aaa", pid=12345, elapsed_ms=400_000,
                    reason="timeout", killed_file=str(f))
        data = json.loads(f.read_text())
        assert "killed_at" in data[0]
        assert isinstance(data[0]["killed_at"], (int, float))


# ---------------------------------------------------------------------------
# kill_process
# ---------------------------------------------------------------------------

class TestKillProcess:
    def test_verified_process_tree_terminates_descendants_before_parent(self) -> None:
        """An owned task tree is drained child-first for TERM and KILL."""
        parent = ProcessInfo(9100, 1, 600.0, "pytest worker")
        child = ProcessInfo(9101, 9100, 590.0, "python child")
        table = {parent.pid: parent, child.pid: child}

        with (
            patch("scripts.task_watchdog.snapshot_processes", return_value=table),
            patch("scripts.task_watchdog.os.kill") as mock_kill,
            patch("scripts.task_watchdog.time.sleep"),
        ):
            assert kill_process(parent.pid, expected_command=parent.command) is True

        calls = [(call.args[0], call.args[1]) for call in mock_kill.call_args_list]
        assert calls == [
            (child.pid, signal.SIGTERM),
            (parent.pid, signal.SIGTERM),
            (child.pid, signal.SIGKILL),
            (parent.pid, signal.SIGKILL),
        ]

    def test_sigterm_then_sigkill_called(self):
        """kill_process must try SIGTERM first, wait, then SIGKILL."""
        with patch("scripts.task_watchdog.os.kill") as mock_kill, \
             patch("scripts.task_watchdog.time.sleep"):
            kill_process(99999)
            # At least 2 calls: SIGTERM + SIGKILL
            assert mock_kill.call_count >= 2
            signals_sent = [call.args[1] for call in mock_kill.call_args_list]
            assert signal.SIGTERM in signals_sent

    def test_returns_true_when_process_exists(self):
        """Should return True (killed) when the process existed."""
        with patch("scripts.task_watchdog.os.kill") as mock_kill, \
             patch("scripts.task_watchdog.time.sleep"):
            mock_kill.side_effect = None  # process exists
            result = kill_process(99999)
            assert result is True

    def test_returns_false_when_process_gone(self):
        """Should return False when process already exited (ProcessLookupError)."""
        with patch("scripts.task_watchdog.os.kill", side_effect=ProcessLookupError), \
             patch("scripts.task_watchdog.time.sleep"):
            result = kill_process(99999)
            assert result is False

    def test_handles_permission_error(self):
        """PermissionError must not crash (fail-open)."""
        with patch("scripts.task_watchdog.os.kill", side_effect=PermissionError), \
             patch("scripts.task_watchdog.time.sleep"):
            result = kill_process(99999)
            assert result is False


# ---------------------------------------------------------------------------
# find_hung_processes
# ---------------------------------------------------------------------------

class TestFindHungProcesses:
    def test_returns_processes_over_timeout(self):
        """Processes whose elapsed > timeout_secs are candidates."""
        ps_output = (
            "  PID  PPID ELAPSED COMMAND\n"
            "11111     1  00:30 /bin/short_process\n"
            "22222     1 10:00:00 /usr/bin/hung_pytest\n"
            "33333     1 06:00 make test-unit\n"
        )
        with patch("scripts.task_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = mock_run.return_value.__class__(
                stdout=ps_output, returncode=0)
            procs = find_hung_processes(timeout_secs=300)
        pids = [p["pid"] for p in procs]
        assert 22222 in pids   # 10 hours — hung
        assert 33333 in pids   # 6 min — over 5 min timeout
        assert 11111 not in pids  # 30 sec — fine

    def test_excludes_watchdog_itself(self):
        """Must not kill the task_watchdog.py process."""
        ps_output = (
            "  PID  PPID ELAPSED COMMAND\n"
            f"{os.getpid()}     1 10:00:00 python3 task_watchdog.py\n"
        )
        with patch("scripts.task_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = mock_run.return_value.__class__(
                stdout=ps_output, returncode=0)
            procs = find_hung_processes(timeout_secs=300)
        pids = [p["pid"] for p in procs]
        assert os.getpid() not in pids

    def test_excludes_gate_background(self, tmp_path: Path):
        """Gate background process has its own killer — don't double-kill."""
        gate_pid = 55555
        gate_pid_file = tmp_path / ".gate-background.pid"
        gate_pid_file.write_text(str(gate_pid))
        ps_output = (
            "  PID  PPID ELAPSED COMMAND\n"
            f"{gate_pid}     1 30:00 make gate\n"
        )
        with patch("scripts.task_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = mock_run.return_value.__class__(
                stdout=ps_output, returncode=0)
            procs = find_hung_processes(timeout_secs=300,
                                        gate_pid_file=str(gate_pid_file))
        pids = [p["pid"] for p in procs]
        assert gate_pid not in pids

    def test_excludes_gate_descendants(self, tmp_path: Path):
        """A gate timeout must not orphan-kill its pytest descendants."""
        gate_pid = 55555
        gate_pid_file = tmp_path / ".gate-background.pid"
        gate_pid_file.write_text(str(gate_pid))
        ps_output = (
            "  PID  PPID ELAPSED COMMAND\n"
            f"{gate_pid}     1 30:00 make gate\n"
            "66666 55555 30:00 uv run python -m pytest tests/unit\n"
            "77777 66666 30:00 python3 -m pytest tests/unit\n"
        )
        with patch("scripts.task_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = mock_run.return_value.__class__(
                stdout=ps_output, returncode=0)
            procs = find_hung_processes(
                timeout_secs=300, gate_pid_file=str(gate_pid_file))
        assert [p["pid"] for p in procs] == []


# ---------------------------------------------------------------------------
# run_once (integration of the above)
# ---------------------------------------------------------------------------

class TestRunOnce:
    def test_no_deadlines_file_is_noop(self, tmp_path: Path):
        """When no deadlines file exists, run_once returns zeros (fail-open)."""
        result = run_once(
            deadlines_file=str(tmp_path / "nope.json"),
            stale_file=str(tmp_path / "nope2.json"),
            killed_file=str(tmp_path / "killed.json"),
        )
        assert result["stale"] == 0
        assert result["killed"] == 0

    def test_stale_task_triggers_kill_and_record(self, tmp_path: Path):
        """End-to-end: stale task in deadlines → process killed → recorded."""
        now_ms = time.time() * 1000
        deadlines_file = tmp_path / "deadlines.json"
        deadlines_file.write_text(json.dumps({
            "stale-task": now_ms - 400_000,
        }))
        killed_file = tmp_path / "killed.json"

        with patch("scripts.task_watchdog.find_hung_processes") as mock_find, \
             patch("scripts.task_watchdog.kill_process") as mock_kill:
            mock_find.return_value = [
                {"pid": 88888, "etime_secs": 400, "command": "make test-unit"}
            ]
            mock_kill.return_value = True
            result = run_once(
                deadlines_file=str(deadlines_file),
                stale_file=str(tmp_path / "stale.json"),
                killed_file=str(killed_file),
            )
        assert result["stale"] == 1
        assert result["killed"] >= 1
        kills = json.loads(killed_file.read_text())
        assert len(kills) >= 1

    def test_no_hung_processes_means_no_kills(self, tmp_path: Path):
        """Stale task but no matching process = no kill (already exited)."""
        now_ms = time.time() * 1000
        deadlines_file = tmp_path / "deadlines.json"
        deadlines_file.write_text(json.dumps({
            "stale-task": now_ms - 400_000,
        }))
        with patch("scripts.task_watchdog.find_hung_processes") as mock_find:
            mock_find.return_value = []
            result = run_once(
                deadlines_file=str(deadlines_file),
                stale_file=str(tmp_path / "stale.json"),
                killed_file=str(tmp_path / "killed.json"),
            )
        assert result["stale"] == 1
        assert result["killed"] == 0

    def test_fail_open_on_exception(self, tmp_path: Path):
        """Any internal error must not crash — return zeros."""
        bad_file = str(tmp_path / "deadlines.json")
        Path(bad_file).write_text("{{{bad json")
        result = run_once(
            deadlines_file=bad_file,
            stale_file=str(tmp_path / "stale.json"),
            killed_file=str(tmp_path / "killed.json"),
        )
        assert result["stale"] == 0
        assert result["killed"] == 0
