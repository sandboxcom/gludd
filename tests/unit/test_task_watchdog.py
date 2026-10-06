"""Tests for scripts/task_watchdog.py — the task-killer watchdog daemon.

The watchdog reads /tmp/gludd-task-deadlines.json (written by enforce-deadline.ts),
finds tasks whose elapsed wall-clock > GLUDD_TASK_TIMEOUT_MS, kills their
associated processes, and records kills in /tmp/gludd-task-killed.json.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from scripts.makefile_layout import compose_makefile
from scripts.process_cleanup import ProcessInfo
from scripts.task_watchdog import (
    _parse_etime,
    _read_gate_run_lock_pid,
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
    source_text = source.read_text()
    assert "from process_cleanup import descendant_processes, snapshot_processes" in source_text
    assert "from active_work_status import _repository_roots" in source_text

# ---------------------------------------------------------------------------
# load_deadlines
# ---------------------------------------------------------------------------

class TestLoadDeadlines:
    def test_loads_dict_format(self, tmp_path: Path) -> None:
        """Plugin writes {task_id: epoch_ms}. Must parse to {task_id: float}."""
        f = tmp_path / "deadlines.json"
        now_ms = time.time() * 1000
        f.write_text(json.dumps({"task-a": now_ms, "task-b": now_ms - 1000}))
        result = load_deadlines(str(f))
        assert "task-a" in result
        assert "task-b" in result
        assert isinstance(result["task-a"], float)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Missing state file = no tracked tasks (fail-open)."""
        assert load_deadlines(str(tmp_path / "nonexistent.json")) == {}

    def test_malformed_json_returns_empty(self, tmp_path: Path) -> None:
        """Corrupt JSON must not crash — return {} (fail-open)."""
        f = tmp_path / "bad.json"
        f.write_text("not json at all {{{")
        assert load_deadlines(str(f)) == {}

    def test_non_dict_returns_empty(self, tmp_path: Path) -> None:
        """If the file is a list or scalar, return {} gracefully."""
        f = tmp_path / "list.json"
        f.write_text(json.dumps([1, 2, 3]))
        assert load_deadlines(str(f)) == {}

    def test_invalid_values_are_ignored(self, tmp_path: Path) -> None:
        """One malformed deadline must not hide the valid task entries."""
        f = tmp_path / "mixed.json"
        f.write_text(json.dumps({"valid": "123.5", "invalid": None}))
        assert load_deadlines(str(f)) == {"valid": 123.5}


# ---------------------------------------------------------------------------
# find_stale_tasks
# ---------------------------------------------------------------------------

class TestFindStaleTasks:
    def test_finds_task_over_timeout(self) -> None:
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

    def test_empty_deadlines_returns_empty(self) -> None:
        assert find_stale_tasks({}, timeout_ms=300_000) == []

    def test_stale_entry_has_elapsed_field(self) -> None:
        """Each stale finding must carry elapsed_ms for the kill record."""
        now_ms = time.time() * 1000
        deadlines = {"old": now_ms - 500_000}
        stale = find_stale_tasks(deadlines, timeout_ms=300_000, now_ms=now_ms)
        assert len(stale) == 1
        assert "elapsed_ms" in stale[0]
        assert stale[0]["elapsed_ms"] > 300_000

    def test_stale_entry_has_task_id_and_start(self) -> None:
        now_ms = time.time() * 1000
        start = now_ms - 500_000
        deadlines = {"d-abc123": start}
        stale = find_stale_tasks(deadlines, timeout_ms=300_000, now_ms=now_ms)
        assert stale[0]["task_id"] == "d-abc123"
        assert stale[0]["start_ms"] == start

    def test_nonpositive_start_is_not_treated_as_stale(self) -> None:
        """Unset and invalid epoch values are ignored rather than killed."""
        stale = find_stale_tasks(
            {"unset": 0.0, "invalid": -1.0},
            timeout_ms=1.0,
            now_ms=1_000.0,
        )
        assert stale == []


# ---------------------------------------------------------------------------
# load_stale_ids
# ---------------------------------------------------------------------------

class TestLoadStaleIds:
    def test_reads_stale_file(self, tmp_path: Path) -> None:
        """Plugin writes breached task IDs to stale file."""
        f = tmp_path / "stale.json"
        f.write_text(json.dumps([
            {"task_id": "d-abc", "stale_at": time.time()},
        ]))
        ids = load_stale_ids(str(f))
        assert "d-abc" in ids

    def test_missing_stale_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_stale_ids(str(tmp_path / "nope.json")) == set()

    def test_supports_legacy_dict_and_rejects_scalar(self, tmp_path: Path) -> None:
        """Legacy dictionaries remain readable while unknown shapes fail open."""
        f = tmp_path / "stale.json"
        f.write_text(json.dumps({"d-legacy": 1}))
        assert load_stale_ids(str(f)) == {"d-legacy"}
        f.write_text(json.dumps("unexpected"))
        assert load_stale_ids(str(f)) == set()


# ---------------------------------------------------------------------------
# elapsed time and gate ownership parsing
# ---------------------------------------------------------------------------

class TestProcessParsing:
    def test_parse_etime_supports_ps_formats_and_bad_values(self) -> None:
        """The process scanner accepts every elapsed-time form emitted by ps."""
        assert _parse_etime("2-01:02:03") == 176_523
        assert _parse_etime("01:02:03") == 3_723
        assert _parse_etime("02:03") == 123
        assert _parse_etime("7") == 7
        assert _parse_etime("not-a-time") == 0

    def test_gate_run_lock_reader_fails_closed_on_untrusted_shapes(
        self, tmp_path: Path
    ) -> None:
        """Only a JSON object containing an integer-like PID grants exemption."""
        lock = tmp_path / "gate-run.lock"
        lock.write_text(json.dumps([{"pid": 123}]))
        assert _read_gate_run_lock_pid(str(lock)) is None
        lock.write_text(json.dumps({"pid": "not-a-pid"}))
        assert _read_gate_run_lock_pid(str(lock)) is None
        lock.write_text("{not-json")
        assert _read_gate_run_lock_pid(str(lock)) is None


# ---------------------------------------------------------------------------
# record_kill
# ---------------------------------------------------------------------------

class TestRecordKill:
    def test_appends_to_killed_file(self, tmp_path: Path) -> None:
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

    def test_kill_record_has_timestamp(self, tmp_path: Path) -> None:
        f = tmp_path / "killed.json"
        record_kill("d-aaa", pid=12345, elapsed_ms=400_000,
                    reason="timeout", killed_file=str(f))
        data = json.loads(f.read_text())
        assert "killed_at" in data[0]
        assert isinstance(data[0]["killed_at"], (int, float))

    def test_malformed_existing_log_is_replaced_atomically(self, tmp_path: Path) -> None:
        """A corrupt audit log cannot prevent a new kill record from persisting."""
        f = tmp_path / "killed.json"
        f.write_text("{not-json")
        record_kill(
            "d-recovered",
            pid=12347,
            elapsed_ms=600_000,
            reason="timeout",
            killed_file=str(f),
        )
        assert json.loads(f.read_text())[0]["task_id"] == "d-recovered"


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

    def test_sigterm_then_sigkill_called(self) -> None:
        """kill_process must try SIGTERM first, wait, then SIGKILL."""
        with patch("scripts.task_watchdog.os.kill") as mock_kill, \
             patch("scripts.task_watchdog.time.sleep"):
            kill_process(99999)
            # At least 2 calls: SIGTERM + SIGKILL
            assert mock_kill.call_count >= 2
            signals_sent = [call.args[1] for call in mock_kill.call_args_list]
            assert signal.SIGTERM in signals_sent

    def test_returns_true_when_process_exists(self) -> None:
        """Should return True (killed) when the process existed."""
        with patch("scripts.task_watchdog.os.kill") as mock_kill, \
             patch("scripts.task_watchdog.time.sleep"):
            mock_kill.side_effect = None  # process exists
            result = kill_process(99999)
            assert result is True

    def test_returns_false_when_process_gone(self) -> None:
        """Should return False when process already exited (ProcessLookupError)."""
        with patch("scripts.task_watchdog.os.kill", side_effect=ProcessLookupError), \
             patch("scripts.task_watchdog.time.sleep"):
            result = kill_process(99999)
            assert result is False

    def test_handles_permission_error(self) -> None:
        """PermissionError must not crash (fail-open)."""
        with patch("scripts.task_watchdog.os.kill", side_effect=PermissionError), \
             patch("scripts.task_watchdog.time.sleep"):
            result = kill_process(99999)
            assert result is False

    def test_identity_change_prevents_pid_reuse_kill(self) -> None:
        """A reused PID with a different command must never be signalled."""
        process = ProcessInfo(99999, 1, 600.0, "unrelated service")
        with (
            patch("scripts.task_watchdog.snapshot_processes", return_value={99999: process}),
            patch("scripts.task_watchdog.os.kill") as mock_kill,
        ):
            assert kill_process(99999, expected_command="make gate") is False
        mock_kill.assert_not_called()


# ---------------------------------------------------------------------------
# find_hung_processes
# ---------------------------------------------------------------------------

class TestFindHungProcesses:
    def test_returns_processes_over_timeout(self) -> None:
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

    def test_excludes_watchdog_itself(self) -> None:
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

    def test_excludes_gate_background(self, tmp_path: Path) -> None:
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

    def test_excludes_gate_descendants(self, tmp_path: Path) -> None:
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

    def test_excludes_foreground_gate_run_lock_tree(self, tmp_path: Path) -> None:
        """A foreground gate and its descendants own their lifecycle."""
        gate_pid = 55555
        gate_pid_file = tmp_path / ".gate-background.pid"
        gate_run_lock_file = tmp_path / "gate-run.lock"
        gate_run_lock_file.write_text(json.dumps({"pid": gate_pid}))
        ps_output = (
            "  PID  PPID ELAPSED COMMAND\n"
            f"{gate_pid}     1 10:00 make gate\n"
            "66666 55555 10:00 uv run python -m pytest tests/integration\n"
            "77777 66666 10:00 python3 -m pytest tests/integration\n"
            "88888     1 10:00 make test-unit\n"
        )
        with patch("scripts.task_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = mock_run.return_value.__class__(
                stdout=ps_output, returncode=0)
            procs = find_hung_processes(
                timeout_secs=300,
                gate_pid_file=str(gate_pid_file),
                gate_run_lock_file=str(gate_run_lock_file),
            )

        assert [proc["pid"] for proc in procs] == [88888]

    def test_excludes_gate_tree_owned_by_registered_linked_worktree(
        self, tmp_path: Path
    ) -> None:
        """One worktree's watchdog cannot terminate another worktree's gate."""
        main_root = tmp_path / "main"
        linked_root = tmp_path / "linked"
        main_root.mkdir()
        (linked_root / ".gate-logs").mkdir(parents=True)
        linked_gate_pid = 55555
        (linked_root / ".gate-logs" / "gate-run.lock").write_text(
            json.dumps({"pid": linked_gate_pid})
        )
        ps_output = (
            "  PID  PPID ELAPSED COMMAND\n"
            f"{linked_gate_pid}     1 10:00 make gate\n"
            "66666 55555 10:00 uv run python -m pytest tests/integration\n"
            "77777 66666 10:00 python3 -m pytest tests/integration\n"
            "88888     1 10:00 make test-unit\n"
        )
        with patch("scripts.task_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = mock_run.return_value.__class__(
                stdout=ps_output, returncode=0
            )
            procs = find_hung_processes(
                timeout_secs=300,
                gate_pid_file=str(main_root / ".gate-background.pid"),
                gate_run_lock_file=str(main_root / ".gate-logs" / "gate-run.lock"),
                repository_roots=(main_root, linked_root),
            )

        assert [proc["pid"] for proc in procs] == [88888]

    def test_gate_entrypoints_carry_legacy_watchdog_exclusion_marker(self) -> None:
        """Every long-lived gate layer must survive an older linked-worktree watchdog."""
        root = Path(__file__).resolve().parents[2]
        makefile = compose_makefile(root / "Makefile")
        gate_runner = (root / "scripts" / "run_gate.sh").read_text(encoding="utf-8")

        assert "_integration-health-watchdog-owned-gate" in makefile
        assert "check_integration_health.py --watchdog-owned-gate" in makefile
        assert "nohup $(MAKE) gate gludd_watchdog_owned_gate=1" in makefile
        assert "run_ci_shards_serial.py --watchdog-owned-gate" in gate_runner

    def test_malformed_gate_lock_does_not_exempt_processes(self, tmp_path: Path) -> None:
        """Corrupt ownership evidence cannot create a broad kill exemption."""
        gate_run_lock_file = tmp_path / "gate-run.lock"
        gate_run_lock_file.write_text("{not-json")
        ps_output = (
            "  PID  PPID ELAPSED COMMAND\n"
            "55555     1 10:00 make test-unit\n"
        )
        with patch("scripts.task_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = mock_run.return_value.__class__(
                stdout=ps_output, returncode=0)
            procs = find_hung_processes(
                timeout_secs=300,
                gate_pid_file=str(tmp_path / "missing-background.pid"),
                gate_run_lock_file=str(gate_run_lock_file),
            )
        assert [proc["pid"] for proc in procs] == [55555]

    def test_ps_failure_returns_no_kill_candidates(self) -> None:
        """Unavailable process metadata fails open without guessing ownership."""
        with patch(
            "scripts.task_watchdog.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ps", 10),
        ):
            assert find_hung_processes(timeout_secs=300) == []

    def test_malformed_ps_rows_are_ignored(self, tmp_path: Path) -> None:
        """Partial and nonnumeric ps rows do not abort scanning valid rows."""
        ps_output = (
            "  PID  PPID ELAPSED COMMAND\n"
            "partial-row\n"
            "not-a-pid 1 10:00 make test-unit\n"
            "44444 1 10:00 make test-unit\n"
        )
        with patch("scripts.task_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = mock_run.return_value.__class__(
                stdout=ps_output, returncode=0)
            procs = find_hung_processes(
                timeout_secs=300,
                gate_pid_file=str(tmp_path / "missing-background.pid"),
                gate_run_lock_file=str(tmp_path / "missing-run.lock"),
            )
        assert [proc["pid"] for proc in procs] == [44444]


# ---------------------------------------------------------------------------
# run_once (integration of the above)
# ---------------------------------------------------------------------------

class TestRunOnce:
    def test_no_deadlines_file_is_noop(self, tmp_path: Path) -> None:
        """When no deadlines file exists, run_once returns zeros (fail-open)."""
        result = run_once(
            deadlines_file=str(tmp_path / "nope.json"),
            stale_file=str(tmp_path / "nope2.json"),
            killed_file=str(tmp_path / "killed.json"),
        )
        assert result["stale"] == 0
        assert result["killed"] == 0

    def test_stale_task_triggers_kill_and_record(self, tmp_path: Path) -> None:
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

    def test_stale_scan_receives_complete_repository_worktree_inventory(
        self, tmp_path: Path
    ) -> None:
        """The production poll protects gate owners in every Git worktree."""
        now_ms = time.time() * 1000
        deadlines_file = tmp_path / "deadlines.json"
        deadlines_file.write_text(json.dumps({"stale-task": now_ms - 400_000}))
        roots = (tmp_path / "main", tmp_path / "linked")

        with (
            patch("scripts.task_watchdog._repository_roots", return_value=roots),
            patch("scripts.task_watchdog.find_hung_processes", return_value=[]) as mock_find,
        ):
            result = run_once(
                deadlines_file=str(deadlines_file),
                stale_file=str(tmp_path / "stale.json"),
                killed_file=str(tmp_path / "killed.json"),
            )

        assert result == {"stale": 1, "killed": 0}
        mock_find.assert_called_once_with(timeout_secs=300.0, repository_roots=roots)

    def test_worktree_inventory_failure_prevents_ambiguous_kill(
        self, tmp_path: Path
    ) -> None:
        """Missing ownership evidence fails safe before destructive scanning."""
        now_ms = time.time() * 1000
        deadlines_file = tmp_path / "deadlines.json"
        deadlines_file.write_text(json.dumps({"stale-task": now_ms - 400_000}))

        with (
            patch("scripts.task_watchdog._repository_roots", side_effect=OSError("git unavailable")),
            patch("scripts.task_watchdog.find_hung_processes") as mock_find,
        ):
            result = run_once(
                deadlines_file=str(deadlines_file),
                stale_file=str(tmp_path / "stale.json"),
                killed_file=str(tmp_path / "killed.json"),
            )

        assert result == {"stale": 0, "killed": 0}
        mock_find.assert_not_called()

    def test_no_hung_processes_means_no_kills(self, tmp_path: Path) -> None:
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

    def test_fresh_task_does_not_scan_processes(self, tmp_path: Path) -> None:
        """A tracked task inside its deadline cannot trigger process discovery."""
        deadlines_file = tmp_path / "deadlines.json"
        deadlines_file.write_text(json.dumps({"fresh-task": time.time() * 1000}))
        with patch("scripts.task_watchdog.find_hung_processes") as mock_find:
            result = run_once(
                deadlines_file=str(deadlines_file),
                stale_file=str(tmp_path / "stale.json"),
                killed_file=str(tmp_path / "killed.json"),
            )
        assert result == {"stale": 0, "killed": 0}
        mock_find.assert_not_called()

    def test_plugin_stale_id_and_failed_kill_are_nonfatal(self, tmp_path: Path) -> None:
        """Plugin corroboration is logged, but failed identity checks stay safe."""
        now_ms = time.time() * 1000
        deadlines_file = tmp_path / "deadlines.json"
        deadlines_file.write_text(json.dumps({"stale-task": now_ms - 400_000}))
        stale_file = tmp_path / "stale.json"
        stale_file.write_text(json.dumps([{"task_id": "stale-task"}]))
        with (
            patch(
                "scripts.task_watchdog.find_hung_processes",
                return_value=[
                    {"pid": 88888, "etime_secs": 400, "command": "make test-unit"}
                ],
            ),
            patch("scripts.task_watchdog.kill_process", return_value=False),
        ):
            result = run_once(
                deadlines_file=str(deadlines_file),
                stale_file=str(stale_file),
                killed_file=str(tmp_path / "killed.json"),
            )
        assert result == {"stale": 1, "killed": 0}

    def test_fail_open_on_exception(self, tmp_path: Path) -> None:
        """Any internal error must not crash — return zeros."""
        with patch(
            "scripts.task_watchdog.load_deadlines",
            side_effect=RuntimeError("unexpected read failure"),
        ):
            result = run_once(
                deadlines_file=str(tmp_path / "deadlines.json"),
                stale_file=str(tmp_path / "stale.json"),
                killed_file=str(tmp_path / "killed.json"),
            )
        assert result["stale"] == 0
        assert result["killed"] == 0
