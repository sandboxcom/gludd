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
from typing import TYPE_CHECKING
from unittest.mock import patch

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
