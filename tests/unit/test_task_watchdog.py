"""Tests for scripts/task_watchdog.py — the task-killer watchdog daemon.

The watchdog reads /tmp/gludd-task-deadlines.json (written by enforce-deadline.ts),
finds tasks whose elapsed wall-clock > GLUDD_TASK_TIMEOUT_MS, kills their
associated processes, and records kills in /tmp/gludd-task-killed.json.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from scripts.makefile_layout import compose_makefile

if TYPE_CHECKING:
    import active_work_status as inventory_owner
    import kill_owned_gate as cleanup_owner
    import task_watchdog as watchdog
    from task_watchdog import (
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
else:
    from scripts import active_work_status as inventory_owner
    from scripts import kill_owned_gate as cleanup_owner
    from scripts import task_watchdog as watchdog
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


@pytest.fixture(autouse=True)
def _isolate_unit_watchdog_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep in-process diagnostics local without changing child environments."""
    monkeypatch.setattr(watchdog, "WATCHDOG_LOG", str(tmp_path / "watchdog.log"))


def test_direct_script_execution_has_process_cleanup_import_fallback() -> None:
    """The Makefile launches the file path, so imports must work outside package mode."""
    source = Path(__file__).resolve().parents[2] / "scripts" / "task_watchdog.py"
    source_text = source.read_text()
    assert "import kill_owned_gate as cleanup_owner" in source_text
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
    def test_verified_process_tree_terminates_descendants_before_parent(self, _owned_poll: OwnedPoll) -> None:
        """An owned task tree is drained child-first for TERM and KILL."""
        root, records, cwds, _deadlines, _sent = _owned_poll
        parent = cleanup_owner.ProcessRecord(9100, 1, 600, "pytest worker", started_at="parent-start")
        child = cleanup_owner.ProcessRecord(9101, 9100, 590, "python child", started_at="child-start")
        records[:] = [parent, child]
        cwds.update({parent.pid: root, child.pid: root})

        def survive_term(pid: int, signum: signal.Signals) -> None:
            if signum == signal.SIGKILL:
                records[:] = [record for record in records if record.pid != pid]

        with patch("scripts.task_watchdog.os.kill", side_effect=survive_term) as mock_kill:
            assert kill_process(
                parent.pid, expected_command=parent.command, expected_record=parent, repository_roots=(root,),
            ) is True
        calls = [(call.args[0], call.args[1]) for call in mock_kill.call_args_list]
        assert calls == [
            (child.pid, signal.SIGTERM),
            (parent.pid, signal.SIGTERM),
            (child.pid, signal.SIGKILL),
            (parent.pid, signal.SIGKILL),
        ]

    def test_sigterm_then_sigkill_called(self, _owned_poll: OwnedPoll) -> None:
        """kill_process must try SIGTERM first, wait, then SIGKILL."""
        root, records, _cwds, _deadlines, _sent = _owned_poll
        record = records[0]

        def survive_term(_pid: int, signum: signal.Signals) -> None:
            if signum == signal.SIGKILL:
                records.clear()

        with patch("scripts.task_watchdog.os.kill", side_effect=survive_term) as mock_kill:
            kill_process(record.pid, expected_record=record, repository_roots=(root,))
            assert mock_kill.call_count >= 2
            signals_sent = [call.args[1] for call in mock_kill.call_args_list]
            assert signal.SIGTERM in signals_sent

    def test_returns_true_when_process_exists(self, _owned_poll: OwnedPoll) -> None:
        """Should return True (killed) when the verified process actually exits."""
        root, records, _cwds, _deadlines, _sent = _owned_poll
        record = records[0]
        owned_sender = os.kill
        with patch("scripts.task_watchdog.os.kill", side_effect=owned_sender):
            result = kill_process(record.pid, expected_record=record, repository_roots=(root,))
            assert result is True

    def test_returns_false_when_process_gone(self, _owned_poll: OwnedPoll) -> None:
        """Should return False when process already exited (ProcessLookupError)."""
        root, records, _cwds, _deadlines, _sent = _owned_poll
        record = records[0]
        with patch("scripts.task_watchdog.os.kill", side_effect=ProcessLookupError):
            result = kill_process(record.pid, expected_record=record, repository_roots=(root,))
            assert result is False

    def test_handles_permission_error(self, _owned_poll: OwnedPoll) -> None:
        """PermissionError must not crash or claim a successful cleanup."""
        root, records, _cwds, _deadlines, _sent = _owned_poll
        record = records[0]
        with patch("scripts.task_watchdog.os.kill", side_effect=PermissionError):
            result = kill_process(record.pid, expected_record=record, repository_roots=(root,))
            assert result is False

    def test_identity_change_prevents_pid_reuse_kill(self, _owned_poll: OwnedPoll) -> None:
        """A reused PID with a different command must never be signalled."""
        root, records, _cwds, _deadlines, _sent = _owned_poll
        process = cleanup_owner.ProcessRecord(88888, 1, 600, "unrelated service", started_at="new-start")
        records[:] = [process]
        with patch("scripts.task_watchdog.os.kill") as mock_kill:
            assert kill_process(process.pid, expected_command="make gate", repository_roots=(root,)) is False
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
        launcher = (root / "scripts" / "start_gate_background.py").read_text(
            encoding="utf-8"
        )

        assert "_integration-health-watchdog-owned-gate" in makefile
        assert "check_integration_health.py --watchdog-owned-gate" in makefile
        assert "start_gate_background.py" in makefile
        assert '"gludd_watchdog_owned_gate=1"' in launcher
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

OwnedPoll = tuple[
    Path, list[cleanup_owner.ProcessRecord], dict[int, Path | None], Path, list[tuple[int, signal.Signals]],
]


@pytest.fixture
def _owned_poll(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OwnedPoll:
    root = tmp_path / "owned"
    root.mkdir()
    (root / "scripts").mkdir()
    (root / "scripts/stream_command.py").write_text("# native observer fixture\n", encoding="utf-8")
    old_start = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(time.time() - 400))
    records = [cleanup_owner.ProcessRecord(88888, 1, 400, "make test-unit", started_at=old_start)]
    cwds: dict[int, Path | None] = {88888: root}
    sent: list[tuple[int, signal.Signals]] = []
    deadlines = tmp_path / "owned-deadlines.json"
    deadlines.write_text(json.dumps({"stale-task": time.time() * 1000 - 400_000}), encoding="utf-8")
    monkeypatch.setattr(watchdog, "_repository_roots", lambda: (root,))
    monkeypatch.setattr(cleanup_owner, "_records", lambda _root: list(records))
    monkeypatch.setattr(cleanup_owner, "_process_cwd", lambda pid: cwds.get(pid))
    monkeypatch.setattr(watchdog, "WATCHDOG_LOG", str(tmp_path / "watchdog.log"))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    def candidates(**_kwargs: object) -> list[watchdog.HungProcess]:
        return [{"pid": record.pid, "etime_secs": 400, "command": record.command} for record in records]

    def send(pid: int, signum: signal.Signals) -> None:
        current = next((record for record in records if record.pid == pid), None)
        if current is None:
            raise ProcessLookupError(pid)
        sent.append((pid, signum))
        records.remove(current)

    monkeypatch.setattr(watchdog, "find_hung_processes", candidates)
    monkeypatch.setattr(os, "kill", send)
    return root, records, cwds, deadlines, sent


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

    def test_stale_task_triggers_kill_and_record(self, tmp_path: Path, _owned_poll: OwnedPoll) -> None:
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

    def test_plugin_stale_id_and_failed_kill_are_nonfatal(self, tmp_path: Path, _owned_poll: OwnedPoll) -> None:
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



def test_watchdog_destructive_boundary_requires_registered_ownership(
    tmp_path: Path, _owned_poll: OwnedPoll,
) -> None:
    """A host-wide elapsed/name match never authorizes a foreign or unknown PID."""
    root, records, cwds, deadlines, sent = _owned_poll
    records[:] = [
        cleanup_owner.ProcessRecord(pid, 1, 400, "python -m pytest tests/unit", started_at=records[0].started_at)
        for pid in (51000, 51001, 51002, 51003)
    ]
    cwds.update({
        51000: root, 51001: tmp_path / "foreign", 51002: tmp_path / "owned-lookalike", 51003: None,
    })
    result = run_once(
        deadlines_file=str(deadlines), stale_file=str(tmp_path / "missing-stale"),
        killed_file=str(tmp_path / "kills.json"),
    )
    assert result == {"stale": 1, "killed": 1}
    assert sent == [(51000, signal.SIGTERM)]
    assert {record.pid for record in records} == {51001, 51002, 51003}
    audit = json.loads((tmp_path / "kills.json").read_text(encoding="utf-8"))
    assert [entry["pid"] for entry in audit] == [51000]


@pytest.mark.parametrize("state", ["starting", "running"])
def test_live_native_observer_protects_children_and_upstream_make_uv(
    tmp_path: Path, _owned_poll: OwnedPoll, state: str,
) -> None:
    root, records, cwds, deadlines, sent = _owned_poll
    observer_command = (
        "python scripts/stream_command.py --label native-owned --max-secs 3600 "
        "--quiet-secs 600 -- make test-files"
    )
    records[:] = [
        cleanup_owner.ProcessRecord(52000, 1, 400, "make test-files", started_at="make-start"),
        cleanup_owner.ProcessRecord(52001, 52000, 400, "uv run " + observer_command, started_at="uv-start"),
        cleanup_owner.ProcessRecord(52002, 52001, 400, observer_command, started_at="observer-start"),
        cleanup_owner.ProcessRecord(52003, 52002, 400, "make test-files", started_at="child-start"),
        cleanup_owner.ProcessRecord(52004, 52003, 400, "python -m pytest tests/unit", started_at="worker-start"),
        cleanup_owner.ProcessRecord(52005, 1, 400, "make test-unit", started_at=records[0].started_at),
    ]
    cwds.update({record.pid: root for record in records})
    pointer = root / ".gate-logs/observed/native-owned/current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({
        "schema_version": 1, "kind": "observed_command", "label": "native-owned",
        "state": state, "owner_pid": 52002, "child_pid": 52003 if state == "running" else None,
    }), encoding="utf-8")
    result = run_once(
        deadlines_file=str(deadlines), stale_file=str(tmp_path / "missing-stale"),
        killed_file=str(tmp_path / "kills.json"),
    )
    assert result == {"stale": 1, "killed": 1}
    assert sent == [(52005, signal.SIGTERM)]
    assert {record.pid for record in records} == {52000, 52001, 52002, 52003, 52004}


@pytest.mark.parametrize("uncertainty", ["status-cap", "process-cap", "missing-status", "bad-status"])
def test_observer_inventory_omission_never_authorizes_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _owned_poll: OwnedPoll, uncertainty: str,
) -> None:
    root, records, cwds, deadlines, sent = _owned_poll
    records.append(cleanup_owner.ProcessRecord(
        53000, 1, 400, "python scripts/stream_command.py --label live --max-secs 3600 --quiet-secs 600",
        started_at="observer-start",
    ))
    cwds[53000] = root
    pointer = root / ".gate-logs/observed/live/current.json"
    pointer.parent.mkdir(parents=True)
    if uncertainty != "missing-status":
        pointer.write_text(json.dumps({
            "schema_version": 1, "kind": "observed_command", "label": "live",
            "state": "starting", "owner_pid": 53000, "child_pid": None,
        }) if uncertainty != "bad-status" else "{broken", encoding="utf-8")
    if uncertainty == "status-cap":
        monkeypatch.setattr(inventory_owner, "_OBSERVER_STATUS_LIMIT", 1)
    if uncertainty == "process-cap":
        monkeypatch.setattr(inventory_owner, "_OBSERVER_PROCESS_LIMIT", 1)
    result = run_once(
        deadlines_file=str(deadlines), stale_file=str(tmp_path / "missing-stale"),
        killed_file=str(tmp_path / "kills.json"),
    )
    assert result["killed"] == 0
    assert sent == [] and len(records) == 2
    assert not (tmp_path / "kills.json").exists()


def test_owned_tree_refuses_unknown_child_and_reused_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _owned_poll: OwnedPoll,
) -> None:
    root, records, cwds, deadlines, sent = _owned_poll
    records.append(cleanup_owner.ProcessRecord(88889, 88888, 400, "python child", started_at="child-start"))
    cwds[88889] = None
    result = run_once(
        deadlines_file=str(deadlines), stale_file=str(tmp_path / "missing-stale"),
        killed_file=str(tmp_path / "kills.json"),
    )
    assert result["killed"] == 0 and sent == []
    cwds[88889] = root
    original = list(records)
    reads = 0

    def replaced(_root: Path) -> list[cleanup_owner.ProcessRecord]:
        nonlocal reads
        reads += 1
        return original if reads == 1 else [
            cleanup_owner.ProcessRecord(record.pid, record.ppid, 400, record.command, started_at="replacement")
            for record in original
        ]

    monkeypatch.setattr(cleanup_owner, "_records", replaced)
    result = run_once(
        deadlines_file=str(deadlines), stale_file=str(tmp_path / "missing-stale"),
        killed_file=str(tmp_path / "kills.json"),
    )
    assert result["killed"] == 0 and sent == []


@pytest.mark.parametrize("apply", [False, True])
def test_verified_stop_drains_relative_argv_daemon_and_uv_before_handoff(
    tmp_path: Path, _owned_poll: OwnedPoll, apply: bool,
) -> None:
    root, records, cwds, _deadlines, sent = _owned_poll
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/task_watchdog.py").write_text("# owned daemon fixture\n", encoding="utf-8")
    records[:] = [
        cleanup_owner.ProcessRecord(54000, 1, 400, "uv run python3 scripts/task_watchdog.py", started_at="uv-start"),
        cleanup_owner.ProcessRecord(54001, 54000, 400, "python3 scripts/task_watchdog.py", started_at="daemon-start"),
    ]
    cwds.update({54000: root, 54001: root})
    marker = root / ".gate-logs/task-watchdog.pid"
    marker.parent.mkdir()
    marker.write_text("54001", encoding="utf-8")
    before = marker.read_bytes()
    assert watchdog.stop_owned_watchdog(root, apply=apply) is True
    if apply:
        assert sent == [(54001, signal.SIGTERM), (54000, signal.SIGTERM)]
        assert records == [] and not marker.exists()
    else:
        assert sent == [] and len(records) == 2 and marker.read_bytes() == before


@pytest.mark.parametrize("defect", ["foreign", "unknown", "lookalike", "missing-start", "unknown-child", "reused"])
def test_verified_stop_refuses_unowned_or_replaced_pidfile_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _owned_poll: OwnedPoll, defect: str,
) -> None:
    root, records, cwds, _deadlines, sent = _owned_poll
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/task_watchdog.py").write_text("# owned daemon fixture\n", encoding="utf-8")
    records[:] = [cleanup_owner.ProcessRecord(
        55000, 1, 400, "python3 scripts/task_watchdog.py" + (".other" if defect == "lookalike" else ""),
        started_at="" if defect == "missing-start" else "daemon-start",
    )]
    cwds[55000] = tmp_path / "foreign" if defect == "foreign" else (None if defect == "unknown" else root)
    marker = root / ".gate-logs/task-watchdog.pid"
    marker.parent.mkdir()
    marker.write_text("55000", encoding="utf-8")
    if defect == "unknown-child":
        records.append(cleanup_owner.ProcessRecord(55001, 55000, 400, "python child", started_at="child-start"))
    if defect == "reused":
        original = records[0]
        reads = 0

        def replaced(_root: Path) -> list[cleanup_owner.ProcessRecord]:
            nonlocal reads
            reads += 1
            return [original] if reads == 1 else [
                cleanup_owner.ProcessRecord(55000, 1, 400, original.command, started_at="replacement")
            ]

        monkeypatch.setattr(cleanup_owner, "_records", replaced)
    assert watchdog.stop_owned_watchdog(root, apply=True) is False
    assert sent == [] and marker.read_text(encoding="utf-8") == "55000"



def test_owned_term_ignoring_child_reparented_after_parent_term_still_drains(
    monkeypatch: pytest.MonkeyPatch, _owned_poll: OwnedPoll,
) -> None:
    root, records, cwds, _deadlines, sent = _owned_poll
    parent = records[0]
    child = cleanup_owner.ProcessRecord(88889, parent.pid, 400, "python child", started_at="child-start")
    records.append(child)
    cwds[child.pid] = root

    def parent_exits(pid: int, signum: signal.Signals) -> None:
        sent.append((pid, signum))
        if (pid, signum) == (parent.pid, signal.SIGTERM):
            records[:] = [cleanup_owner.ProcessRecord(
                child.pid, 1, 400, child.command, started_at=child.started_at,
            )]
        elif (pid, signum) == (child.pid, signal.SIGKILL):
            records.clear()

    monkeypatch.setattr(os, "kill", parent_exits)
    assert kill_process(parent.pid, expected_record=parent, repository_roots=(root,)) is True
    assert sent == [(child.pid, signal.SIGTERM), (parent.pid, signal.SIGTERM), (child.pid, signal.SIGKILL)]
    assert records == []


@pytest.mark.parametrize("mutation", ["cwd", "start", "command", "new-child", "gate"])
def test_owned_cleanup_revalidates_before_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _owned_poll: OwnedPoll, mutation: str,
) -> None:
    root, records, cwds, _deadlines, sent = _owned_poll
    expected = records[0]

    def mutate_after_term(pid: int, signum: signal.Signals) -> None:
        sent.append((pid, signum))
        assert signum == signal.SIGTERM
        if mutation == "cwd":
            cwds[pid] = tmp_path / "foreign"
        elif mutation in {"start", "command"}:
            records[:] = [cleanup_owner.ProcessRecord(
                pid, 1, 400, "foreign command" if mutation == "command" else expected.command,
                started_at="reused-start" if mutation == "start" else expected.started_at,
            )]
        elif mutation == "new-child":
            records.append(cleanup_owner.ProcessRecord(88889, pid, 400, "python child", started_at="new-child"))
        else:
            lock = root / ".gate-logs/gate-run.lock"
            lock.parent.mkdir()
            lock.write_text(json.dumps({"pid": pid}), encoding="utf-8")

    monkeypatch.setattr(os, "kill", mutate_after_term)
    assert kill_process(expected.pid, expected_record=expected, repository_roots=(root,)) is False
    assert sent == [(expected.pid, signal.SIGTERM)]


def test_terminal_observer_without_live_owner_does_not_disable_owned_cleanup(
    tmp_path: Path, _owned_poll: OwnedPoll,
) -> None:
    root, _records, _cwds, deadlines, sent = _owned_poll
    pointer = root / ".gate-logs/observed/old/current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({
        "schema_version": 1, "kind": "observed_command", "label": "old",
        "state": "passed", "owner_pid": 99999, "child_pid": 99998,
    }), encoding="utf-8")
    assert run_once(
        deadlines_file=str(deadlines), stale_file=str(tmp_path / "missing-stale"),
        killed_file=str(tmp_path / "kills.json"),
    ) == {"stale": 1, "killed": 1}
    assert sent == [(88888, signal.SIGTERM)]


def test_daemon_startup_names_policy_and_preserves_replaced_pid_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    marker = tmp_path / "daemon.pid"
    monkeypatch.setattr(watchdog, "WATCHDOG_PID_FILE", str(marker))
    monkeypatch.setattr(watchdog, "WATCHDOG_LOG", str(tmp_path / "daemon.log"))

    def interrupted() -> watchdog.PollResult:
        marker.write_text("99998", encoding="utf-8")
        raise KeyboardInterrupt

    monkeypatch.setattr(watchdog, "run_once", interrupted)
    assert watchdog.main([]) == 0
    assert "ownership_policy=registered-cwd-start-identity-observer-protection-v1" in capsys.readouterr().out
    assert marker.read_text(encoding="utf-8") == "99998"



def test_stop_make_policy_preserves_literal_values_without_writes(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    tripwire = tmp_path / "make-policy-tripwire"
    literal = "$(shell touch " + str(tripwire) + ")"
    for policy, expected in (("0", "0"), ("1", "1"), (literal, "invalid"), ("0 1", "invalid"), ("", "invalid")):
        result = subprocess.run(
            ["make", "--no-print-directory", "-n", "task-watchdog-stop",
             "TASK_WATCHDOG_VALIDATE_ONLY=" + policy], cwd=root, check=True,
            capture_output=True, text=True, timeout=10,
        )
        assert result.stdout.splitlines()[-1].endswith("--stop --validate-only " + expected)
    assert not tripwire.exists()



@pytest.mark.parametrize("identity", ["fresh", "future", "missing", "malformed"])
def test_legacy_elapsed_match_cannot_kill_a_fresh_or_unknown_native_start(
    tmp_path: Path, _owned_poll: OwnedPoll, identity: str,
) -> None:
    _root, records, _cwds, deadlines, sent = _owned_poll
    original = records[0]
    stamp = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(time.time() + (3600 if identity == "future" else 0)))
    if identity in {"missing", "malformed"}:
        stamp = "" if identity == "missing" else "unavailable-start"
    records[:] = [cleanup_owner.ProcessRecord(original.pid, 1, 400, original.command, started_at=stamp)]
    assert run_once(
        deadlines_file=str(deadlines), stale_file=str(tmp_path / "missing-stale"),
        killed_file=str(tmp_path / "kills.json"),
    ) == {"stale": 1, "killed": 0}
    assert sent == [] and not (tmp_path / "kills.json").exists()


@pytest.mark.parametrize("operation", ["kill", "stop"])
@pytest.mark.parametrize("exception", [
    subprocess.CalledProcessError(1, ["ps"]), subprocess.TimeoutExpired(["ps"], 1),
])
def test_unavailable_native_inventory_refuses_both_cleanup_apis(
    monkeypatch: pytest.MonkeyPatch, _owned_poll: OwnedPoll, operation: str,
    exception: subprocess.SubprocessError,
) -> None:
    root, records, _cwds, _deadlines, sent = _owned_poll
    expected = records[0]
    (root / "scripts/task_watchdog.py").write_text("# owned daemon fixture\n", encoding="utf-8")
    marker = root / ".gate-logs/task-watchdog.pid"
    marker.parent.mkdir()
    marker.write_text(str(expected.pid), encoding="utf-8")

    def unavailable(_root: Path) -> list[cleanup_owner.ProcessRecord]:
        raise exception

    monkeypatch.setattr(cleanup_owner, "_records", unavailable)
    if operation == "kill":
        result = kill_process(expected.pid, expected_record=expected, repository_roots=(root,))
    else:
        result = watchdog.stop_owned_watchdog(root, apply=True)
    assert result is False and sent == [] and marker.read_text(encoding="utf-8") == str(expected.pid)


@pytest.fixture
def _owned_daemon(_owned_poll: OwnedPoll) -> tuple[OwnedPoll, Path]:
    root, records, cwds, _deadlines, _sent = _owned_poll
    (root / "scripts/task_watchdog.py").write_text("# isolated daemon fixture\n", encoding="utf-8")
    records[:] = [
        cleanup_owner.ProcessRecord(54000, 1, 400, "uv run python3 scripts/task_watchdog.py", started_at="uv-start"),
        cleanup_owner.ProcessRecord(54001, 54000, 400, "python3 scripts/task_watchdog.py", started_at="daemon-start"),
    ]
    cwds.update({54000: root, 54001: root})
    marker = root / ".gate-logs/task-watchdog.pid"
    marker.parent.mkdir()
    marker.write_text("54001", encoding="utf-8")
    return _owned_poll, marker


@pytest.mark.parametrize("metadata", ["cwd", "command", "start"])
def test_watchdog_handoff_confirms_exit_after_metadata_changes(
    monkeypatch: pytest.MonkeyPatch, _owned_daemon: tuple[OwnedPoll, Path], metadata: str,
) -> None:
    """Post-TERM metadata is not death; fresh complete absence confirms drain."""
    (root, records, cwds, _deadlines, sent), marker = _owned_daemon
    empty_snapshots = 0

    def snapshot(_root: Path) -> list[cleanup_owner.ProcessRecord]:
        nonlocal empty_snapshots
        empty_snapshots += not records
        return list(records)

    def natural_exit_after_term(pid: int, signum: signal.Signals) -> None:
        assert (pid, signum) == (54001, signal.SIGTERM)
        sent.append((pid, signum))
        if metadata == "cwd":
            cwds[pid] = None
        else:
            daemon = records[1]
            records[1] = cleanup_owner.ProcessRecord(
                pid, daemon.ppid, 400, "(python3)" if metadata == "command" else daemon.command,
                started_at="unavailable-start" if metadata == "start" else daemon.started_at,
            )

    monkeypatch.setattr(cleanup_owner, "_records", snapshot)
    monkeypatch.setattr(os, "kill", natural_exit_after_term)
    monkeypatch.setattr(time, "sleep", lambda _seconds: records.clear())
    assert watchdog.stop_owned_watchdog(root, apply=True) is True
    assert sent == [(54001, signal.SIGTERM)]
    assert records == [] and empty_snapshots >= 2 and not marker.exists()


@pytest.mark.parametrize("metadata", ["cwd", "command", "start", "protected", "new-child"])
def test_live_uncertain_handoff_never_receives_another_signal(
    monkeypatch: pytest.MonkeyPatch, _owned_daemon: tuple[OwnedPoll, Path], metadata: str,
) -> None:
    """An occupied uncertain identity is never inferred dead or escalated."""
    (root, records, cwds, _deadlines, sent), marker = _owned_daemon

    def uncertain_after_term(pid: int, signum: signal.Signals) -> None:
        assert (pid, signum) == (54001, signal.SIGTERM)
        sent.append((pid, signum))
        daemon = records[1]
        if metadata == "cwd":
            cwds[pid] = None
        elif metadata in {"command", "start"}:
            records[1] = cleanup_owner.ProcessRecord(
                pid, daemon.ppid, 400, "foreign command" if metadata == "command" else daemon.command,
                started_at="reused-start" if metadata == "start" else daemon.started_at,
            )
        elif metadata == "protected":
            (marker.parent / "gate-run.lock").write_text(json.dumps({"pid": pid}), encoding="utf-8")
        else:
            records.append(cleanup_owner.ProcessRecord(54002, pid, 400, "python child", started_at="unknown-child"))

    monkeypatch.setattr(os, "kill", uncertain_after_term)
    assert watchdog.stop_owned_watchdog(root, apply=True) is False
    assert sent == [(54001, signal.SIGTERM)]
    assert marker.read_text(encoding="utf-8") == "54001"


@pytest.mark.parametrize("apply", [False, True])
def test_absent_watchdog_pointer_recovery_is_signal_free(
    _owned_daemon: tuple[OwnedPoll, Path], apply: bool,
) -> None:
    """Only an unchanged owned pointer with a complete absent census is recoverable."""
    (root, records, _cwds, _deadlines, sent), marker = _owned_daemon
    records.clear()
    before = marker.read_bytes()
    assert watchdog.stop_owned_watchdog(root, apply=apply) is True
    assert sent == []
    assert not marker.exists() if apply else marker.read_bytes() == before


@pytest.mark.parametrize("defect", [
    "reused", "foreign", "unknown", "malformed", "invalid-pid", "live-daemon", "live-uv",
    "unknown-daemon-cwd", "reappeared", "replaced-pointer", "symlink",
])
def test_absent_pointer_recovery_refuses_uncertain_or_live_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    _owned_daemon: tuple[OwnedPoll, Path], defect: str,
) -> None:
    (root, records, cwds, _deadlines, sent), marker = _owned_daemon
    daemon, launcher = records[1], records[0]
    records.clear()
    if defect in {"reused", "foreign", "unknown"}:
        records.append(cleanup_owner.ProcessRecord(
            daemon.pid, 1, 400, "foreign command", started_at="reused-start",
        ))
        cwds[daemon.pid] = root if defect == "reused" else (tmp_path if defect == "foreign" else None)
    elif defect in {"live-daemon", "unknown-daemon-cwd"}:
        records.append(cleanup_owner.ProcessRecord(
            54002, 1, 400, daemon.command, started_at="other-daemon",
        ))
        cwds[54002] = None if defect == "unknown-daemon-cwd" else root
    elif defect == "live-uv":
        records.append(launcher)
    elif defect in {"malformed", "invalid-pid"}:
        marker.write_text("broken" if defect == "malformed" else "0", encoding="utf-8")
    elif defect == "symlink":
        foreign = tmp_path / "foreign.pid"
        foreign.write_text("54001", encoding="utf-8")
        marker.unlink()
        marker.symlink_to(foreign)
    if defect in {"reappeared", "replaced-pointer"}:
        reads = 0

        def changing(_root: Path) -> list[cleanup_owner.ProcessRecord]:
            nonlocal reads
            reads += 1
            if reads >= 2:
                if defect == "reappeared":
                    return [daemon]
                marker.unlink()
                marker.write_text("54001", encoding="utf-8")
            return []

        monkeypatch.setattr(cleanup_owner, "_records", changing)
    before = marker.read_bytes()
    assert watchdog.stop_owned_watchdog(root, apply=True) is False
    assert sent == [] and marker.read_bytes() == before


def test_absent_pointer_recovery_requires_a_fresh_available_snapshot(
    monkeypatch: pytest.MonkeyPatch, _owned_daemon: tuple[OwnedPoll, Path],
) -> None:
    (root, records, _cwds, _deadlines, sent), marker = _owned_daemon
    records.clear()
    reads = 0

    def unavailable(_root: Path) -> list[cleanup_owner.ProcessRecord]:
        nonlocal reads
        reads += 1
        if reads > 1:
            raise subprocess.CalledProcessError(1, ["ps"])
        return []

    monkeypatch.setattr(cleanup_owner, "_records", unavailable)
    assert watchdog.stop_owned_watchdog(root, apply=True) is False
    assert sent == [] and marker.read_text(encoding="utf-8") == "54001"


def test_real_native_watchdog_handoff_drains_natural_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real UV/Python metadata and natural exit stay confined to an isolated fixture."""
    root = tmp_path / "fixture-checkout"
    (root / "scripts").mkdir(parents=True)
    (root / ".gate-logs").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "gludd-watchdog-handoff-fixture"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    marker = root / ".gate-logs/task-watchdog.pid"
    received = root / "received-term"
    foreign_cwd = tmp_path / "exiting-cwd"
    foreign_cwd.mkdir()
    (root / "scripts/task_watchdog.py").write_text(
        "import os, signal, sys, time\nfrom pathlib import Path\n"
        "def stop(_signal, _frame):\n"
        f"    os.chdir({str(foreign_cwd)!r})\n"
        f"    Path({str(received)!r}).write_text('TERM')\n"
        "    time.sleep(0.4)\n    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "deadline = time.monotonic() + 6\n"
        "while time.monotonic() < deadline:\n    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    uv = shutil.which("uv")
    assert uv is not None
    monkeypatch.setattr(watchdog, "_repository_roots", lambda: (root,))
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=sys.prefix, UV_NO_SYNC="1", UV_PYTHON_DOWNLOADS="never")
    with subprocess.Popen(
        [uv, "run", "python3", "scripts/task_watchdog.py"], cwd=root, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    ) as launcher, ThreadPoolExecutor(max_workers=1) as reaper:
        try:
            deadline = time.monotonic() + 4
            while not marker.exists() and launcher.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists()
            daemon_pid = int(marker.read_text(encoding="utf-8"))
            # The fixture owns Popen and must reap UV while native stop waits for
            # actual PID absence; a zombie in ps must never count as drained.
            exited = reaper.submit(launcher.wait, timeout=8)
            assert watchdog.stop_owned_watchdog(root, apply=True) is True
            assert exited.result(timeout=8) == 0
            assert launcher.returncode == 0 and received.read_text(encoding="utf-8") == "TERM"
            assert not marker.exists()
            fresh = cleanup_owner._records(root)
            assert not {daemon_pid, launcher.pid} & {record.pid for record in fresh}
        finally:
            launcher.wait(timeout=8)
    assert not (root / ".venv").exists() and not (root / "uv.lock").exists()


@pytest.mark.parametrize("apply", [False, True])
def test_absent_recovery_excludes_only_current_verified_stop_invocation(
    _owned_daemon: tuple[OwnedPoll, Path], apply: bool,
) -> None:
    (root, records, cwds, _deadlines, sent), marker = _owned_daemon
    command = f"python3 scripts/task_watchdog.py --stop --validate-only {int(not apply)}"
    records[:] = [
        cleanup_owner.ProcessRecord(os.getppid(), 1, 400, "uv run " + command, started_at="stop-uv-start"),
        cleanup_owner.ProcessRecord(watchdog._SELF_PID, os.getppid(), 400, command, started_at="stop-start"),
    ]
    cwds.update({record.pid: root for record in records})
    assert watchdog.stop_owned_watchdog(root, apply=apply) is True
    assert sent == []
    assert not marker.exists() if apply else marker.read_text(encoding="utf-8") == "54001"


@pytest.mark.parametrize("defect", [
    "other-stop-pid", "extra-arg", "wrong-policy", "unknown-cwd", "missing-start", "unrelated-uv",
])
def test_absent_recovery_never_excludes_arbitrary_stop_text(
    _owned_daemon: tuple[OwnedPoll, Path], defect: str,
) -> None:
    (root, records, cwds, _deadlines, sent), marker = _owned_daemon
    pid = 54003 if defect == "other-stop-pid" else watchdog._SELF_PID
    command = "python3 scripts/task_watchdog.py --stop --validate-only " + ("1" if defect == "wrong-policy" else "0")
    if defect == "extra-arg":
        command += " --other"
    records[:] = [cleanup_owner.ProcessRecord(
        pid, os.getppid(), 400, command, started_at="" if defect == "missing-start" else "stop-start",
    )]
    cwds[pid] = None if defect == "unknown-cwd" else root
    if defect == "unrelated-uv":
        records.append(cleanup_owner.ProcessRecord(
            54004, 1, 400, "uv run " + command, started_at="unrelated-uv",
        ))
        cwds[54004] = root
    assert watchdog.stop_owned_watchdog(root, apply=True) is False
    assert sent == [] and marker.read_text(encoding="utf-8") == "54001"


@pytest.mark.parametrize("apply", [False, True])
def test_real_native_stop_cli_recovers_only_absent_fixture_pointer(
    tmp_path: Path, apply: bool,
) -> None:
    root = tmp_path / "cli-fixture"
    (root / "scripts").mkdir(parents=True)
    (root / ".gate-logs").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "gludd-stop-cli-fixture"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    # Copy the actual owner into the fixture so __file__ also stays confined.
    # Only repository membership differs; argv/ps/cwd/start/pointer IO stay native.
    owner_root = Path(__file__).resolve().parents[2]
    source = (owner_root / "scripts/task_watchdog.py").read_text(encoding="utf-8")
    source = source.replace(
        'if __name__ == "__main__":\n',
        'if __name__ == "__main__":\n    _repository_roots = lambda: (Path(__file__).resolve().parents[1],)\n',
    )
    (root / "scripts/task_watchdog.py").write_text(source, encoding="utf-8")
    with subprocess.Popen([sys.executable, "-c", "pass"]) as departed:
        departed.wait(timeout=3)
    marker = root / ".gate-logs/task-watchdog.pid"
    marker.write_text(str(departed.pid), encoding="utf-8")
    uv = shutil.which("uv")
    assert uv is not None
    result = subprocess.run(
        [uv, "run", "python3", "scripts/task_watchdog.py", "--stop", "--validate-only", str(int(not apply))],
        cwd=root, env=dict(os.environ, UV_PROJECT_ENVIRONMENT=sys.prefix, UV_NO_SYNC="1",
                           UV_PYTHON_DOWNLOADS="never", PYTHONPATH=str(owner_root / "scripts")),
        capture_output=True, text=True, timeout=8, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "absent-pointer verified" in result.stdout and "signals=false" in result.stdout
    assert not marker.exists() if apply else marker.read_text(encoding="utf-8") == str(departed.pid)
    assert not (root / ".venv").exists() and not (root / "uv.lock").exists()


@pytest.mark.parametrize("changed", ["self-start", "launcher-start", "self-cwd"])
def test_stop_invocation_exemption_is_rechecked_in_the_fresh_snapshot(
    monkeypatch: pytest.MonkeyPatch, _owned_daemon: tuple[OwnedPoll, Path], changed: str,
) -> None:
    (root, records, cwds, _deadlines, sent), marker = _owned_daemon
    command = "python3 scripts/task_watchdog.py --stop --validate-only 0"
    records[:] = [
        cleanup_owner.ProcessRecord(os.getppid(), 1, 400, "uv run " + command, started_at="uv-start"),
        cleanup_owner.ProcessRecord(watchdog._SELF_PID, os.getppid(), 400, command, started_at="stop-start"),
    ]
    cwds.update({record.pid: root for record in records})
    reads = 0

    def changed_snapshot(_root: Path) -> list[cleanup_owner.ProcessRecord]:
        nonlocal reads
        reads += 1
        if reads > 1:
            if changed == "self-cwd":
                cwds[watchdog._SELF_PID] = None
            else:
                index = 0 if changed == "launcher-start" else 1
                original = records[index]
                records[index] = cleanup_owner.ProcessRecord(
                    original.pid, original.ppid, 400, original.command, started_at="reused-start",
                )
        return list(records)

    monkeypatch.setattr(cleanup_owner, "_records", changed_snapshot)
    assert watchdog.stop_owned_watchdog(root, apply=True) is False
    assert sent == [] and marker.read_text(encoding="utf-8") == "54001"


def test_native_watchdog_start_requires_child_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _owned_poll: OwnedPoll,
) -> None:
    """A published PID and preexisting log text cannot substitute for fresh acknowledgement."""
    root, records, cwds, _deadlines, sent = _owned_poll
    records.clear()
    script = root / "scripts/task_watchdog.py"
    script.write_text("# isolated native startup fixture\n", encoding="utf-8")
    log = root / ".gate-logs/task-watchdog.log"
    log.parent.mkdir()
    log.write_text("prior log\n[watchdog] task_watchdog started: pid=56000 timeout=300\n", encoding="utf-8")
    child = MagicMock(spec=subprocess.Popen)
    child.pid = 56000
    child.poll.return_value = None
    child.wait.return_value = 0

    def spawn(*args: object, **kwargs: object) -> MagicMock:
        records.append(cleanup_owner.ProcessRecord(
            child.pid, 1, 0, f"{sys.executable} {script}", pgid=child.pid, started_at="child-start",
        ))
        cwds[child.pid] = root
        (log.parent / "task-watchdog.pid").write_text(str(child.pid), encoding="utf-8")
        return child

    with monkeypatch.context() as launch_patch:
        launch_patch.setattr(subprocess, "Popen", spawn)
        launch_patch.setattr(os, "getpgid", lambda pid: pid)
        launch_patch.setattr(os, "getsid", lambda pid: pid)
        result = watchdog.start_owned_watchdog(root, apply=True)
        assert result.success is False
        assert sent == [(child.pid, signal.SIGTERM)]
        assert records == [] and log.read_text(encoding="utf-8").startswith("prior log\n")

    for target, variable, recursive in (
        ("task-watchdog-start", "TASK_WATCHDOG_START_VALIDATE_ONLY", False),
        ("task-watchdog-stop", "TASK_WATCHDOG_VALIDATE_ONLY", False),
        ("task-watchdog-start", "TASK_WATCHDOG_START_VALIDATE_ONLY", True),
    ):
        _assert_native_lifecycle_literal_policy(tmp_path, target, variable, recursive=recursive)


@pytest.mark.parametrize("value", ["0", "1", "0 1", "", "function"])
def test_native_watchdog_start_target_uses_literal_safe_policy(tmp_path: Path, value: str) -> None:
    tripwire = tmp_path / "start-policy-tripwire"
    if value == "function":
        value = "$(shell touch " + str(tripwire) + ")"
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        ["make", "--no-print-directory", "-n", "task-watchdog-start", "TASK_WATCHDOG_START_VALIDATE_ONLY=" + value],
        cwd=root, check=True, capture_output=True, text=True, timeout=10,
    )
    expected = value if value in {"0", "1"} else "invalid"
    assert completed.stdout.splitlines()[-1].endswith("--start --validate-only " + expected)
    assert not tripwire.exists()


def _native_start_fixture_checkout(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Confine the actual native source, bounded daemon and every state path."""
    root = tmp_path / "native-start-fixture"
    (root / "scripts").mkdir(parents=True)
    (root / ".gate-logs").mkdir()
    owner_root = Path(__file__).resolve().parents[2]
    source = (owner_root / "scripts/task_watchdog.py").read_text(encoding="utf-8")
    injection = (
        'if __name__ == "__main__":\n'
        '    _repository_roots = lambda: (Path(__file__).resolve().parents[1],)\n'
        '    _fixture_deadline = time.monotonic() + 12\n'
        '    _fixture_poll = run_once\n'
        '    def _bounded_poll():\n'
        '        if time.monotonic() >= _fixture_deadline:\n'
        '            raise KeyboardInterrupt\n'
        '        return _fixture_poll()\n'
        '    run_once = _bounded_poll\n'
        '    POLL_SECS = 0.02\n'
    )
    (root / "scripts/task_watchdog.py").write_text(
        source.replace('if __name__ == "__main__":\n', injection), encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname="gludd-native-start-fixture"\nversion="0.0.0"\nrequires-python=">=3.11"\n',
        encoding="utf-8",
    )
    uv = shutil.which("uv")
    assert uv is not None
    (root / "Makefile").write_text(
        f"UV := {uv}\nGLUDD_TASK_TIMEOUT := 300\n"
        + (owner_root / "make/80-agent-enforcement.mk").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for name, content in (("deadlines", "{}"), ("stale", "[]"), ("killed", "[]")):
        (root / name).write_text(content, encoding="utf-8")
    environment = dict(
        os.environ, UV_PROJECT_ENVIRONMENT=sys.prefix, UV_NO_SYNC="1", UV_PYTHON_DOWNLOADS="never",
        PYTHONPATH=str(owner_root / "scripts"), GLUDD_TASK_DEADLINE_STATE=str(root / "deadlines"),
        GLUDD_TASK_STALE_FILE=str(root / "stale"), GLUDD_TASK_KILLED_FILE=str(root / "killed"),
        GLUDD_TASK_WATCHDOG_PID=str(root / "inherited-wrong.pid"),
        GLUDD_TASK_WATCHDOG_LOG=str(root / "inherited-wrong.log"),
    )
    return root, environment


def test_real_native_watchdog_start_survives_caller_group_termination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapt the existing gate session-survival control with confined real Make/Python."""
    root, environment = _native_start_fixture_checkout(tmp_path)
    receipt = root / "caller.json"
    caller_source = (
        "import json, subprocess, sys, time\nfrom pathlib import Path\n"
        "result = subprocess.run(['make','--no-print-directory','task-watchdog-start',"
        "'TASK_WATCHDOG_START_VALIDATE_ONLY=0'], capture_output=True, text=True, timeout=8)\n"
        f"Path({str(receipt)!r}).write_text(json.dumps(dict(rc=result.returncode, "
        "out=result.stdout, err=result.stderr)))\n"
        "time.sleep(15)\n"
    )
    monkeypatch.setattr(watchdog, "_repository_roots", lambda: (root,))
    caller = subprocess.Popen(
        [sys.executable, "-c", caller_source], cwd=root, env=environment, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not receipt.exists() and caller.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert receipt.exists()
        report = json.loads(receipt.read_text(encoding="utf-8"))
        assert report["rc"] == 0, report
        match = re.search(r"TASK-WATCHDOG-START acknowledged pid=(\d+)", report["out"])
        assert match is not None, report
        pid = int(match[1])
        before = next(record for record in cleanup_owner._records(root) if record.pid == pid)
        assert before.started_at and cleanup_owner._process_cwd(pid) == root
        assert os.getpgid(pid) == os.getsid(pid) == pid and pid != caller.pid
        assert (root / ".gate-logs/task-watchdog.pid").read_text(encoding="utf-8") == str(pid)
        assert not (root / "inherited-wrong.pid").exists() and not (root / "inherited-wrong.log").exists()
        os.killpg(caller.pid, signal.SIGTERM)
        caller.wait(timeout=3)
        after = next(record for record in cleanup_owner._records(root) if record.pid == pid)
        assert cleanup_owner._same_identity(before, after)
        assert cleanup_owner._process_cwd(pid) == root and os.getpgid(pid) == os.getsid(pid) == pid
    finally:
        if caller.poll() is None:
            os.killpg(caller.pid, signal.SIGTERM)
        caller.wait(timeout=3)
        assert watchdog.stop_owned_watchdog(root, apply=True)
    assert (root / "killed").read_text(encoding="utf-8") == "[]"
    assert not (root / ".venv").exists() and not (root / "uv.lock").exists()


StartFixture = tuple[OwnedPoll, MagicMock, dict[str, object], Callable[..., MagicMock]]


@pytest.fixture
def _start_fixture(monkeypatch: pytest.MonkeyPatch, _owned_poll: OwnedPoll) -> StartFixture:
    root, records, cwds, _deadlines, _sent = _owned_poll
    records.clear()
    (root / "scripts/task_watchdog.py").write_text("# native start fixture\n", encoding="utf-8")
    directory = root / ".gate-logs"
    directory.mkdir()
    (directory / "task-watchdog.log").write_text("retained prior log\n", encoding="utf-8")
    child = MagicMock(spec=subprocess.Popen)
    child.pid = 56000
    child.poll.return_value = None
    child.wait.return_value = 0
    calls: dict[str, object] = {}

    def spawn(command: list[str], **kwargs: object) -> MagicMock:
        calls["command"] = command
        calls.update(kwargs)
        records.append(cleanup_owner.ProcessRecord(
            child.pid, 1, 0, f"{sys.executable} {root / 'scripts/task_watchdog.py'}",
            pgid=child.pid, started_at="new-child-start",
        ))
        cwds[child.pid] = root
        with (directory / "task-watchdog.log").open("a", encoding="utf-8") as log:
            log.write(f"task_watchdog ready: pid={child.pid} ownership_policy=fixture\n")
            log.flush()
            (directory / "task-watchdog.pid").write_text(str(child.pid), encoding="utf-8")
            log.write(f"task_watchdog started: pid={child.pid} timeout=300\n")
        return child

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "getsid", lambda pid: pid)
    return _owned_poll, child, calls, spawn


def test_native_start_uses_selected_python_and_verified_singleton(_start_fixture: StartFixture) -> None:
    (root, records, _cwds, _deadlines, sent), child, calls, _spawn = _start_fixture
    first = watchdog.start_owned_watchdog(root, apply=True)
    assert first.success and first.process is child and first.identity == records[0]
    assert calls["command"] == [sys.executable, str(root / "scripts/task_watchdog.py")]
    assert calls["cwd"] == root and calls["start_new_session"] is True and calls["close_fds"] is True
    assert calls["stdin"] == subprocess.DEVNULL and calls["stderr"] == subprocess.STDOUT
    environment = calls["env"]
    assert isinstance(environment, dict)
    assert environment["GLUDD_TASK_WATCHDOG_PID"] == str(root / ".gate-logs/task-watchdog.pid")
    assert environment["GLUDD_TASK_WATCHDOG_LOG"] == str(root / ".gate-logs/task-watchdog.log")
    assert environment["PYTHONUNBUFFERED"] == "1"
    second = watchdog.start_owned_watchdog(root, apply=True)
    assert second.success and second.process is None and second.identity == first.identity
    assert len(records) == 1 and sent == []
    assert (root / ".gate-logs/task-watchdog.log").read_text(encoding="utf-8").startswith("retained prior log\n")


def test_native_start_plan_has_no_mutations_or_spawn(_start_fixture: StartFixture) -> None:
    (root, records, _cwds, _deadlines, sent), _child, calls, _spawn = _start_fixture
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert watchdog.start_owned_watchdog(root, apply=False).success
    assert calls == {} and sent == [] and records == []
    assert before == {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("defect", ["stale", "malformed", "foreign", "symlink", "log-symlink", "live-unpointed"])
def test_native_start_refuses_unowned_admission_without_spawning(
    tmp_path: Path, _start_fixture: StartFixture, defect: str,
) -> None:
    (root, records, cwds, _deadlines, sent), _child, calls, _spawn = _start_fixture
    marker = root / ".gate-logs/task-watchdog.pid"
    if defect == "symlink":
        foreign = tmp_path / "foreign.pid"
        foreign.write_text("56000", encoding="utf-8")
        marker.symlink_to(foreign)
    elif defect == "log-symlink":
        log = root / ".gate-logs/task-watchdog.log"
        log.unlink()
        log.symlink_to(tmp_path / "foreign.log")
    else:
        if defect != "live-unpointed":
            marker.write_text("broken" if defect == "malformed" else "56000", encoding="utf-8")
        if defect in {"foreign", "live-unpointed"}:
            records.append(cleanup_owner.ProcessRecord(
                56000, 1, 400, f"python3 {root / 'scripts/task_watchdog.py'}", pgid=56000, started_at="live-start",
            ))
            cwds[56000] = tmp_path if defect == "foreign" else root
    assert watchdog.start_owned_watchdog(root, apply=True).success is False
    assert calls == {} and sent == []


def test_native_start_singleton_lock_prevents_concurrent_spawn(_start_fixture: StartFixture) -> None:
    from filelock import FileLock

    (root, _records, _cwds, _deadlines, sent), _child, calls, _spawn = _start_fixture
    with FileLock(str(root / ".gate-logs/task-watchdog-launch.lock"), timeout=0):
        assert watchdog.start_owned_watchdog(root, apply=True).success is False
    assert calls == {} and sent == []


@pytest.mark.parametrize("failure", ["spawn", "immediate-exit", "wrong-pid", "wrong-session", "reused", "unknown-cwd"])
def test_native_start_failure_never_publishes_success_or_signals_an_uncertain_identity(
    monkeypatch: pytest.MonkeyPatch, _start_fixture: StartFixture, failure: str,
) -> None:
    (root, records, cwds, _deadlines, sent), child, _calls, original_spawn = _start_fixture
    reads = 0

    def spawn(command: list[str], **kwargs: object) -> MagicMock:
        if failure == "spawn":
            raise OSError("spawn unavailable")
        result = original_spawn(command, **kwargs)
        if failure == "immediate-exit":
            child.poll.return_value = 1
            records.clear()
        elif failure == "wrong-pid":
            (root / ".gate-logs/task-watchdog.pid").write_text("99998", encoding="utf-8")
        elif failure == "wrong-session":
            monkeypatch.setattr(os, "getsid", lambda pid: pid + 1)
        elif failure == "unknown-cwd":
            cwds[child.pid] = None
        return result

    def snapshot(_root: Path) -> list[cleanup_owner.ProcessRecord]:
        nonlocal reads
        if records:
            reads += 1
            if failure == "reused" and reads > 1:
                old = records[0]
                records[0] = cleanup_owner.ProcessRecord(
                    old.pid, 1, 0, old.command, pgid=old.pid, started_at="reused-start",
                )
        return list(records)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(cleanup_owner, "_records", snapshot)
    result = watchdog.start_owned_watchdog(root, apply=True)
    assert result.success is False
    assert sent == ([(child.pid, signal.SIGTERM)] if failure == "wrong-pid" else [])
    assert (root / ".gate-logs/task-watchdog.log").read_text(encoding="utf-8").startswith("retained prior log\n")


def test_direct_daemon_never_overwrites_an_existing_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "existing.pid"
    marker.write_text("99998", encoding="utf-8")
    monkeypatch.setattr(watchdog, "WATCHDOG_PID_FILE", str(marker))
    monkeypatch.setattr(watchdog, "WATCHDOG_LOG", str(tmp_path / "log"))
    with patch("scripts.task_watchdog.run_once") as poll:
        assert watchdog.main([]) == 1
    poll.assert_not_called()
    assert marker.read_text(encoding="utf-8") == "99998"


@pytest.mark.parametrize("mutation", ["content", "inode", "symlink"])
def test_already_running_start_refuses_a_changed_pid_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _start_fixture: StartFixture, mutation: str,
) -> None:
    (root, records, _cwds, _deadlines, sent), _child, _calls, _spawn = _start_fixture
    assert watchdog.start_owned_watchdog(root, apply=True).success
    marker = root / ".gate-logs/task-watchdog.pid"
    reads = 0

    def replaced(_root: Path) -> list[cleanup_owner.ProcessRecord]:
        nonlocal reads
        reads += 1
        if reads == 2:
            if mutation == "content":
                marker.write_text("99998", encoding="utf-8")
            else:
                marker.unlink()
                if mutation == "symlink":
                    foreign = tmp_path / "foreign-pointer"
                    foreign.write_text("56000", encoding="utf-8")
                    marker.symlink_to(foreign)
                else:
                    marker.write_text("56000", encoding="utf-8")
        return list(records)

    monkeypatch.setattr(cleanup_owner, "_records", replaced)
    assert watchdog.start_owned_watchdog(root, apply=True).success is False
    assert sent == [] and len(records) == 1


def test_existing_singleton_does_not_depend_on_startup_log_retention(_start_fixture: StartFixture) -> None:
    (root, records, _cwds, _deadlines, sent), _child, _calls, _spawn = _start_fixture
    assert watchdog.start_owned_watchdog(root, apply=True).success
    with (root / ".gate-logs/task-watchdog.log").open("a", encoding="utf-8") as log:
        log.write("ordinary daemon output\n" * 1000)
    result = watchdog.start_owned_watchdog(root, apply=True)
    assert result.success and result.process is None and result.identity == records[0]
    assert len(records) == 1 and sent == []


def test_native_start_target_contract_and_auto_reuse_owner() -> None:
    root = Path(__file__).resolve().parents[2]
    contract = json.loads((root / "config/make_target_contract.json").read_text(encoding="utf-8"))
    entry = next(item for item in contract["targets"] if item["name"] == "task-watchdog-start")
    assert entry["make_variables"] == ["TASK_WATCHDOG_START_VALIDATE_ONLY"]
    assert entry["behavior"] == "make task-watchdog-start TASK_WATCHDOG_START_VALIDATE_ONLY=1"
    source = (root / "make/80-agent-enforcement.mk").read_text(encoding="utf-8")
    auto = source.split("\nwatchdog-auto:\n", 1)[1].split("\n\n", 1)[0]
    assert "task-watchdog-start TASK_WATCHDOG_START_VALIDATE_ONLY=0" in auto
    assert "nohup $(UV) run python3 scripts/task_watchdog.py" not in source


@pytest.mark.parametrize("changed", ["pointer", "log"])
def test_new_start_acknowledgement_rechecks_its_owned_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _start_fixture: StartFixture, changed: str,
) -> None:
    (root, records, _cwds, _deadlines, sent), child, _calls, _spawn = _start_fixture
    reads = 0

    def changed_snapshot(_root: Path) -> list[cleanup_owner.ProcessRecord]:
        nonlocal reads
        if records:
            reads += 1
            if reads == 2:
                if changed == "pointer":
                    (root / ".gate-logs/task-watchdog.pid").write_text("99998", encoding="utf-8")
                else:
                    log = root / ".gate-logs/task-watchdog.log"
                    log.unlink()
                    foreign = tmp_path / "foreign-log"
                    foreign.write_text(f"task_watchdog started: pid={child.pid} timeout=300\n", encoding="utf-8")
                    log.symlink_to(foreign)
        return list(records)

    monkeypatch.setattr(cleanup_owner, "_records", changed_snapshot)
    assert watchdog.start_owned_watchdog(root, apply=True).success is False
    assert sent == [(child.pid, signal.SIGTERM)]
    if changed == "pointer":
        assert (root / ".gate-logs/task-watchdog.pid").read_text(encoding="utf-8") == "99998"


def _assert_native_lifecycle_literal_policy(
    tmp_path: Path, target: str, variable: str, *, recursive: bool = False,
) -> None:
    """Exercise actual recipe environments, including the native recursive carrier."""
    root = Path(__file__).resolve().parents[2]
    suffix = "-recursive" if recursive else ""
    tripwire = tmp_path / (target + suffix + "-executed-policy-tripwire")
    literal = "$(shell touch " + str(tripwire) + ")"
    arguments = ["make", "--no-print-directory", target, variable + "=" + literal]
    if recursive:
        carrier = tmp_path / (target + "-carrier.mk")
        carrier.write_text(
            (root / "make/80-agent-enforcement.mk").read_text(encoding="utf-8")
            + "\nliteral-policy-carrier:\n\t@$(MAKE) --no-print-directory -C "
            + shlex.quote(str(root)) + " " + target + "\n",
            encoding="utf-8",
        )
        arguments[2:3] = ["-f", str(carrier), "literal-policy-carrier"]
    result = subprocess.run(
        arguments, cwd=root, capture_output=True, text=True, timeout=8, check=False,
    )
    assert result.returncode != 0 and "invalid choice" in result.stderr
    assert not tripwire.exists()


@pytest.mark.parametrize(("target", "variable", "recursive"), [
    ("task-watchdog-start", "TASK_WATCHDOG_START_VALIDATE_ONLY", False),
    ("task-watchdog-stop", "TASK_WATCHDOG_VALIDATE_ONLY", False),
    ("task-watchdog-start", "TASK_WATCHDOG_START_VALIDATE_ONLY", True),
])
def test_native_lifecycle_literal_policy_stays_inert_during_actual_make_execution(
    tmp_path: Path, target: str, variable: str, recursive: bool,
) -> None:
    _assert_native_lifecycle_literal_policy(tmp_path, target, variable, recursive=recursive)


def test_native_start_serializes_existing_acknowledgement(_start_fixture: StartFixture) -> None:
    """A published child cannot bypass the actual creator's held native lock."""
    from filelock import FileLock

    (root, records, _cwds, _deadlines, sent), child, calls, spawn = _start_fixture
    spawn([sys.executable, str(root / "scripts/task_watchdog.py")])
    calls.clear()
    log = root / ".gate-logs/task-watchdog.log"
    log.write_text("retained prior log\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with FileLock(str(root / ".gate-logs/task-watchdog-launch.lock"), timeout=0):
        before_plan = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
        plan = watchdog.start_owned_watchdog(root, apply=False)
        assert plan.success and plan.process is None and plan.identity == records[0]
        assert before_plan == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
        result = watchdog.start_owned_watchdog(root, apply=True)
    assert result.success is False
    assert result.process is None and calls == {} and sent == []
    assert len(records) == 1 and records[0].pid == child.pid
    assert all(path.read_bytes() == content for path, content in before.items())


def test_failed_start_keeps_serialization_through_verified_child_drain(
    monkeypatch: pytest.MonkeyPatch, _start_fixture: StartFixture,
) -> None:
    from filelock import FileLock, Timeout

    (root, records, _cwds, _deadlines, sent), child, _calls, spawn = _start_fixture
    probes: list[tuple[bool, bool]] = []
    original_terminate = watchdog._terminate_verified

    def no_ack(command: list[str], **kwargs: object) -> MagicMock:
        result = spawn(command, **kwargs)
        (root / ".gate-logs/task-watchdog.log").write_text("retained prior log\n", encoding="utf-8")
        return result

    def drain(
        record: cleanup_owner.ProcessRecord, roots: tuple[Path, ...], *,
        reap: Callable[[], int | None] | None = None,
    ) -> bool:
        held = False
        try:
            with FileLock(str(root / ".gate-logs/task-watchdog-launch.lock"), timeout=0):
                pass
        except Timeout:
            held = True
        adopter = watchdog.start_owned_watchdog(root, apply=True)
        probes.append((held, adopter.success))
        return original_terminate(record, roots, reap=reap)

    monkeypatch.setattr(subprocess, "Popen", no_ack)
    monkeypatch.setattr(watchdog, "_terminate_verified", drain)
    result = watchdog.start_owned_watchdog(root, apply=True)
    assert result.success is False
    assert probes == [(True, False)]
    assert records == [] and sent == [(child.pid, signal.SIGTERM)]


def test_daemon_does_not_publish_a_pid_when_checked_readiness_cannot_be_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "never-ready.pid"
    unavailable_log = tmp_path / "not-a-log-file"
    unavailable_log.mkdir()
    monkeypatch.setattr(watchdog, "WATCHDOG_PID_FILE", str(marker))
    monkeypatch.setattr(watchdog, "WATCHDOG_LOG", str(unavailable_log))
    with patch("scripts.task_watchdog.run_once", side_effect=KeyboardInterrupt) as poll:
        assert watchdog.main([]) == 1
    poll.assert_not_called()
    assert not marker.exists()


def test_daemon_commits_its_pid_only_after_checked_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "ready.pid"
    log = tmp_path / "ready.log"
    monkeypatch.setattr(watchdog, "WATCHDOG_PID_FILE", str(marker))
    monkeypatch.setattr(watchdog, "WATCHDOG_LOG", str(log))
    publications: list[tuple[str, bool, bool]] = []
    original_log = watchdog._log

    def observe(message: str, *, required: bool = False) -> None:
        publications.append((message, required, marker.exists()))
        if required:
            original_log(message, required=True)
        else:
            original_log(message)

    monkeypatch.setattr(watchdog, "_log", observe)
    with patch("scripts.task_watchdog.run_once", side_effect=KeyboardInterrupt):
        assert watchdog.main([]) == 0
    assert publications[0][0].startswith(f"task_watchdog ready: pid={os.getpid()} ")
    assert publications[0][1:] == (True, False)
    assert publications[1][0].startswith(f"task_watchdog started: pid={os.getpid()} ")
    assert publications[1][1:] == (False, True)
    assert not marker.exists()


def test_interrupted_creator_cannot_leave_an_adoptable_unacknowledged_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real creator dies before the child's checked ready publication."""
    root, environment = _native_start_fixture_checkout(tmp_path)
    script = root / "scripts/task_watchdog.py"
    entered = root / "before-ready"
    release = root / "release-ready"
    barrier = (
        '    _fixture_original_log = _log\n'
        '    def _blocked_log(message, **kwargs):\n'
        '        if message.startswith(("task_watchdog ready:", "task_watchdog started:")):\n'
        f'            Path({str(entered)!r}).write_text(str(_SELF_PID))\n'
        '            wait_until = time.monotonic() + 8\n'
        f'            while not Path({str(release)!r}).exists():\n'
        '                if time.monotonic() >= wait_until:\n'
        '                    raise RuntimeError("fixture readiness barrier expired")\n'
        '                time.sleep(0.02)\n'
        '        return _fixture_original_log(message, **kwargs)\n'
        '    _log = _blocked_log\n'
    )
    source = script.read_text(encoding="utf-8")
    assert source.count("    POLL_SECS = 0.02\n") == 1
    script.write_text(source.replace("    POLL_SECS = 0.02\n", "    POLL_SECS = 0.02\n" + barrier), encoding="utf-8")
    monkeypatch.setattr(watchdog, "_repository_roots", lambda: (root,))
    creator = subprocess.Popen(
        [sys.executable, str(script), "--start", "--validate-only", "0"],
        cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    identity: cleanup_owner.ProcessRecord | None = None
    marker = root / ".gate-logs/task-watchdog.pid"
    try:
        deadline = time.monotonic() + 4
        while not entered.exists() and creator.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert entered.exists() and creator.poll() is None
        child_pid = int(entered.read_text(encoding="utf-8"))
        identity = next(record for record in cleanup_owner._records(root) if record.pid == child_pid)
        assert watchdog._startup_identity(identity, root)
        assert os.getpgid(creator.pid) == creator.pid != child_pid
        os.killpg(creator.pid, signal.SIGTERM)
        creator.communicate(timeout=4)
        assert creator.returncode == -signal.SIGTERM
        result = watchdog.start_owned_watchdog(root, apply=True)
        assert result.success is False and result.process is None
        assert not marker.exists()
        current = next(record for record in cleanup_owner._records(root) if record.pid == child_pid)
        assert cleanup_owner._same_identity(identity, current) and watchdog._startup_identity(current, root)
        assert (root / "killed").read_text(encoding="utf-8") == "[]"
    finally:
        release.write_text("release", encoding="utf-8")
        if creator.poll() is None:
            creator.terminate()
        creator.communicate(timeout=4)
        if identity is not None:
            deadline = time.monotonic() + 4
            while watchdog._read_gate_pid(str(marker)) != identity.pid and time.monotonic() < deadline:
                time.sleep(0.02)
            if watchdog._read_gate_pid(str(marker)) == identity.pid:
                assert watchdog.stop_owned_watchdog(root, apply=True) is True
            else:
                assert watchdog._terminate_verified(identity, (root,)) is True
    assert (root / "killed").read_text(encoding="utf-8") == "[]"
    assert not (root / ".venv").exists() and not (root / "uv.lock").exists()


def test_unit_watchdog_fault_logs_belong_to_the_test_namespace(tmp_path: Path) -> None:
    """Check ownership before deliberate failures can emit any real log line."""
    local_log = tmp_path / "watchdog.log"
    assert Path(watchdog.WATCHDOG_LOG).resolve() == local_log.resolve()
    assert not local_log.exists()
    deadlines = tmp_path / "deadlines.json"
    deadlines.write_text(json.dumps({"stale-task": time.time() * 1000 - 400_000}), encoding="utf-8")
    killed = tmp_path / "killed.json"
    arguments = (str(deadlines), str(tmp_path / "stale.json"), str(killed))
    with (
        patch("scripts.task_watchdog._repository_roots", side_effect=OSError("git unavailable")),
        patch("scripts.task_watchdog.find_hung_processes") as scan,
        patch("scripts.task_watchdog.os.kill") as send,
    ):
        assert watchdog.run_once(*arguments, timeout_ms=300_000) == {"stale": 0, "killed": 0}
    scan.assert_not_called()
    send.assert_not_called()
    with (
        patch("scripts.task_watchdog.load_deadlines", side_effect=RuntimeError("unexpected read failure")),
        patch("scripts.task_watchdog.find_hung_processes") as scan,
        patch("scripts.task_watchdog.os.kill") as send,
    ):
        assert watchdog.run_once(*arguments, timeout_ms=300_000) == {"stale": 0, "killed": 0}
    scan.assert_not_called()
    send.assert_not_called()
    text = local_log.read_text(encoding="utf-8")
    assert text.count("git unavailable") == 1
    assert text.count("unexpected read failure") == 1
    assert "POLL ERROR: git unavailable" in text and "POLL ERROR: unexpected read failure" in text
    assert not killed.exists()
