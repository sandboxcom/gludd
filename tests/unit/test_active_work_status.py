"""Behavioral tests for the cross-terminal active-work audit command."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import scripts.active_work_status as active_work_status
from scripts.active_work_status import _task_label

ROOT = Path(__file__).resolve().parents[2]


def test_active_work_status_is_auditable_json() -> None:
    result = subprocess.run(
        ["make", "active-work-status"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload["processes"], list)
    assert isinstance(payload["observed_processes"], list)
    assert isinstance(payload["workstreams"], dict)
    assert all("task" in process for process in payload["processes"])
    assert isinstance(payload["open_task_ids"], list)
    assert isinstance(payload["gate"], dict)
    assert isinstance(payload["git"], dict)
    assert payload["git"]["head"]
    assert payload["audit_contract"]["ps_command"] == "make ps"
    assert payload["audit_contract"]["agent_pids"] is False


def test_process_labels_separate_test_workstreams() -> None:
    assert _task_label("pytest tests/unit/test_example.py") == "unit-tests"
    assert _task_label("pytest tests/e2e/test_opencode_plugin_load.py") == "opencode-e2e"
    assert _task_label("pytest tests/e2e/test_api_routers.py") == "e2e-tests"
    assert _task_label("python scripts/task_watchdog.py") == "watchdog"


@pytest.mark.parametrize(
    ("command", "expected"),
    (
        ("python scripts/audit_coverage.py", "coverage-audit"),
        ("detect-secrets scan", "coverage-audit-support"),
        ("python -c from multiprocessing.resource_tracker import main", "python-worker"),
        ("python -m llama_cpp.server --port 12001", "local-inference"),
        ("llama-server --model model.gguf", "local-inference"),
        ("python scripts/test_hook_runtime.py", "hook-runtime"),
        ("python scripts/adaptive_test.py", "test-supervisor"),
        ("make gate", "gate-refresh"),
        ("pytest tests/e2e/test_opencode_live.py", "opencode-e2e"),
        ("pytest tests/e2e/test_api.py", "e2e-tests"),
        ("pytest plugin/tests.py", "pytest"),
        ("mypy scripts/tool.py", "typecheck"),
        ("python -m general_ludd.cli daemon start", "e2e-daemon"),
        ("python unrelated.py", "other"),
    ),
)
def test_process_labels_cover_every_tracked_role(command: str, expected: str) -> None:
    assert _task_label(command) == expected


def test_owned_process_inventory_spans_registered_roots_and_resource_roots() -> None:
    """Main, linked-worktree, and resource-only workers remain observable."""

    process_table = "\n".join(
        (
            "101 1 /repo/gludd/.venv/bin/python -m pytest tests/unit/test_main.py",
            (
                "102 101 /private/tmp/gludd-worktrees/feature/.venv/bin/python "
                "scripts/run_ci_shards_serial.py"
            ),
            (
                "103 102 /usr/bin/python -m pytest tests/unit/test_worker.py "
                "--basetemp=/private/tmp/gludd-resources/feature-a1b2/ci-shards/batch"
            ),
            "201 1 /repo/other/.venv/bin/python -m pytest tests/unit/test_other.py",
            (
                "202 1 /usr/bin/python -m pytest tests/unit/test_other.py "
                "--basetemp=/private/tmp/gludd-resources/other-c3d4/ci-shards/batch"
            ),
        )
    )

    processes = active_work_status._owned_processes_from_output(
        process_table,
        repository_roots=(Path("/repo/gludd"), Path("/private/tmp/gludd-worktrees/feature")),
        resource_roots=(Path("/private/tmp/gludd-resources/feature-a1b2"),),
    )

    assert [process["pid"] for process in processes] == ["101", "102", "103"]
    assert processes[1]["task"] == "ci-shard-supervisor"


def test_owned_process_inventory_uses_path_boundaries() -> None:
    """A checkout-name prefix must not admit another project's worker."""

    processes = active_work_status._owned_processes_from_output(
        "301 1 /repo/gludd-copy/.venv/bin/python -m pytest tests/unit/test_copy.py",
        repository_roots=(Path("/repo/gludd"),),
        resource_roots=(Path("/tmp/gludd-resources/gludd-a1b2"),),
    )

    assert processes == []


def test_owned_process_inventory_surfaces_pathless_local_inference_servers() -> None:
    """Orphaned model servers remain visible even after losing their worktree path."""

    process_table = "\n".join(
        (
            "46215 1 /usr/bin/python -m llama_cpp.server --port 9999",
            "46216 1 llama-server --model /models/qwen.gguf",
            "46217 1 /repo/other/.venv/bin/python -m pytest tests/unit/test_other.py",
        )
    )

    processes = active_work_status._owned_processes_from_output(
        process_table,
        repository_roots=(Path("/repo/gludd"),),
        resource_roots=(Path("/tmp/gludd-resources/gludd-a1b2"),),
    )

    assert [process["pid"] for process in processes] == ["46215", "46216"]
    assert {process["task"] for process in processes} == {"local-inference"}


def test_owned_process_inventory_keeps_tracked_supervisor_tree() -> None:
    """Relative controller argv inherits ownership from its rooted child."""

    process_table = "\n".join(
        (
            "401 1 make gate",
            (
                "402 401 /repo/gludd/.venv/bin/python "
                "scripts/run_ci_shards_serial.py"
            ),
            "403 402 python -m pytest tests/unit/test_worker.py",
            "501 1 make gate",
        )
    )

    processes = active_work_status._owned_processes_from_output(
        process_table,
        repository_roots=(Path("/repo/gludd"),),
        resource_roots=(Path("/tmp/gludd-resources/gludd-a1b2"),),
    )

    assert [process["pid"] for process in processes] == ["401", "402", "403"]


def test_observer_inventory_surfaces_live_self_improve_process_tree(
    tmp_path: Path,
) -> None:
    """A live observer status admits its real owner, child, and descendants."""

    label = "self-improve-live"
    status_dir = tmp_path / ".gate-logs" / "observed" / label
    status_dir.mkdir(parents=True)
    status_dir.joinpath("current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "observed_command",
                "label": label,
                "state": "running",
                "owner_pid": 700,
                "child_pid": 701,
                "agent_pid": 999,
            }
        ),
        encoding="utf-8",
    )
    process_table = "\n".join(
        (
            (
                "700 1 python scripts/stream_command.py --label self-improve-live "
                "-- make test-self-improve TARGET=catalog"
            ),
            "701 700 make test-self-improve TARGET=catalog",
            (
                "702 701 python scripts/self_improve_local_proposal.py "
                "--model-path /models/qwen.gguf"
            ),
            "703 702 python -c from multiprocessing.resource_tracker import main",
            "704 700 python scripts/self_improve_local_proposal.py --unrecorded-sibling",
            "999 1 opencode model-agent --name imaginary",
        )
    )

    processes = active_work_status._observer_owned_processes_from_output(
        process_table,
        repository_roots=(tmp_path,),
    )

    assert [process["pid"] for process in processes] == ["700", "701", "702", "703"]
    assert [process["task"] for process in processes] == [
        "self-improve-observer",
        "self-improve",
        "self-improve-model-worker",
        "python-worker",
    ]
    assert {process["observer_label"] for process in processes} == {label}
    assert [process["observer_role"] for process in processes] == [
        "owner",
        "child",
        "descendant",
        "descendant",
    ]
    assert "999" not in {process["pid"] for process in processes}


def test_observer_inventory_rejects_spoofed_or_terminal_statuses(tmp_path: Path) -> None:
    """Status files cannot invent PIDs or retain completed command trees."""

    observed_root = tmp_path / ".gate-logs" / "observed"
    for label, state, owner_pid in (
        ("spoofed", "running", 800),
        ("finished", "passed", 810),
    ):
        status_dir = observed_root / label
        status_dir.mkdir(parents=True)
        status_dir.joinpath("current.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "observed_command",
                    "label": label,
                    "state": state,
                    "owner_pid": owner_pid,
                    "child_pid": owner_pid + 1,
                }
            ),
            encoding="utf-8",
        )
    process_table = "\n".join(
        (
            "800 1 python unrelated.py --label spoofed",
            "801 800 python scripts/self_improve_local_proposal.py",
            "810 1 python scripts/stream_command.py --label finished",
            "811 810 make test-self-improve TARGET=catalog",
        )
    )

    assert (
        active_work_status._observer_owned_processes_from_output(
            process_table,
            repository_roots=(tmp_path,),
        )
        == []
    )


def test_observer_inventory_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Observer discovery cannot make status output grow without bound."""

    label = "self-improve-bounded"
    status_dir = tmp_path / ".gate-logs" / "observed" / label
    status_dir.mkdir(parents=True)
    status_dir.joinpath("current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "observed_command",
                "label": label,
                "state": "running",
                "owner_pid": 900,
                "child_pid": 901,
            }
        ),
        encoding="utf-8",
    )
    process_table = "\n".join(
        (
            "900 1 python scripts/stream_command.py --label self-improve-bounded",
            "901 900 make test-self-improve TARGET=catalog",
            "902 901 python scripts/self_improve_local_proposal.py",
        )
    )
    monkeypatch.setattr(active_work_status, "_OBSERVER_PROCESS_LIMIT", 2)

    processes = active_work_status._observer_owned_processes_from_output(
        process_table,
        repository_roots=(tmp_path,),
    )

    assert [process["pid"] for process in processes] == ["900", "901"]


def test_observer_status_bound_prefers_recent_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Old terminal labels cannot crowd a newer live pointer out of discovery."""

    observed_root = tmp_path / ".gate-logs" / "observed"
    old = observed_root / "aaa-old" / "current.json"
    recent = observed_root / "zzz-recent" / "current.json"
    old.parent.mkdir(parents=True)
    recent.parent.mkdir(parents=True)
    old.write_text("{}", encoding="utf-8")
    recent.write_text("{}", encoding="utf-8")
    os.utime(old, (1, 1))
    os.utime(recent, (2, 2))
    monkeypatch.setattr(active_work_status, "_OBSERVER_STATUS_LIMIT", 1)

    assert active_work_status._observer_status_paths((tmp_path,)) == (recent,)


def test_git_porcelain_discovers_main_and_linked_worktree_paths() -> None:
    payload = """worktree /repo/gludd
HEAD aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
branch refs/heads/development

worktree /private/tmp/gludd-worktrees/feature
HEAD bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
branch refs/heads/feature
"""

    assert active_work_status._parse_worktree_roots(payload) == (
        Path("/repo/gludd"),
        Path("/private/tmp/gludd-worktrees/feature"),
    )


def test_git_porcelain_fails_closed_without_registered_worktree() -> None:
    try:
        active_work_status._parse_worktree_roots("HEAD deadbeef\n")
    except ValueError as exc:
        assert "no registered worktrees" in str(exc)
    else:  # pragma: no cover - explicit fail-closed assertion
        raise AssertionError("missing worktree registry must fail closed")


def test_git_porcelain_rejects_relative_and_deduplicates_paths() -> None:
    with pytest.raises(ValueError, match="invalid worktree path"):
        active_work_status._parse_worktree_roots("worktree relative/path\n")

    assert active_work_status._parse_worktree_roots(
        "worktree /repo/gludd\n\nworktree /repo/gludd\n"
    ) == (Path("/repo/gludd"),)


def test_repository_roots_uses_git_porcelain(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(stdout="worktree /repo/gludd\n")

    monkeypatch.setattr("scripts.active_work_status.subprocess.run", fake_run)

    assert active_work_status._repository_roots() == (Path("/repo/gludd"),)
    assert observed["command"] == ["git", "worktree", "list", "--porcelain"]
    assert observed["timeout"] == 10


def test_owned_resource_roots_are_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        active_work_status,
        "resource_root",
        lambda _root: Path("/tmp/gludd-resources/shared"),
    )

    assert active_work_status._owned_resource_roots(
        (Path("/repo/gludd"), Path("/repo/gludd-worktree"))
    ) == (Path("/tmp/gludd-resources/shared"),)


def test_process_query_uses_registered_and_resource_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        active_work_status, "_repository_roots", lambda: (Path("/repo/gludd"),)
    )
    monkeypatch.setattr(
        active_work_status,
        "_owned_resource_roots",
        lambda _roots: (Path("/tmp/gludd-resources/gludd-a1b2"),),
    )

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        assert command == ["ps", "-axo", "pid=,ppid=,command="]
        assert kwargs["timeout"] == 10
        return SimpleNamespace(
            stdout="101 1 /repo/gludd/.venv/bin/python -m pytest tests/unit/test_a.py\n"
        )

    monkeypatch.setattr("scripts.active_work_status.subprocess.run", fake_run)

    assert [process["pid"] for process in active_work_status._processes()] == ["101"]


def test_process_table_renderer_is_bounded_and_explicit_when_empty() -> None:
    assert active_work_status._render_process_table([]) == "No matching project processes\n"

    rendered = active_work_status._render_process_table(
        [
            {
                "pid": "101",
                "ppid": "1",
                "task": "unit-tests",
                "command": "/repo/gludd/.venv/bin/python -m pytest tests/unit/test_main.py",
            }
        ]
    )
    assert "PID" in rendered and "TASK" in rendered
    assert "101" in rendered and "unit-tests" in rendered


def test_process_table_renderer_marks_bounded_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(active_work_status, "_PROCESS_DISPLAY_LIMIT", 1)
    processes = [
        {"pid": str(pid), "ppid": "1", "task": "pytest", "command": "pytest"}
        for pid in (101, 102)
    ]

    rendered = active_work_status._render_process_table(processes)

    assert "101" in rendered
    assert "102" not in rendered
    assert "1 additional owned processes not displayed" in rendered


def test_process_table_renderer_bounds_long_commands() -> None:
    rendered = active_work_status._render_process_table(
        [
            {
                "pid": "101",
                "ppid": "1",
                "task": "pytest",
                "command": "pytest " + "x" * 1_000,
            }
        ]
    )

    process_line = rendered.splitlines()[1]
    assert len(process_line) <= 290
    assert process_line.endswith("...")


def test_process_table_cli_uses_shared_owned_inventory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        active_work_status,
        "_processes",
        lambda: [
            {"pid": "101", "ppid": "1", "task": "pytest", "command": "pytest"}
        ],
    )

    assert active_work_status.main(["--process-table"]) == 0
    assert "101" in capsys.readouterr().out


def test_make_status_commands_surface_live_observer_owned_pids() -> None:
    """Both public status views expose a real observer tree while it is live."""

    label = f"self-improve-ps-{os.getpid()}"
    run_id = f"run-{os.getpid()}"
    observed_root = ROOT / ".gate-logs" / "observed"
    label_dir = observed_root / label
    observer = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts" / "stream_command.py"),
            "--root",
            str(observed_root),
            "--label",
            label,
            "--run-id",
            run_id,
            "--heartbeat-secs",
            "0.05",
            "--max-secs",
            "10",
            "--quiet",
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
            "self-improve-model-worker",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        current = label_dir / "current.json"
        deadline = time.monotonic() + 3
        child_pid: int | None = None
        while time.monotonic() < deadline:
            if current.is_file():
                status = json.loads(current.read_text(encoding="utf-8"))
                if status.get("state") == "running":
                    child_pid = status.get("child_pid")
                    break
            time.sleep(0.01)
        assert isinstance(child_pid, int)

        table = subprocess.run(
            ["make", "ps"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
        payload = json.loads(
            subprocess.run(
                ["make", "active-work-status"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            ).stdout
        )

        assert str(observer.pid) in table
        assert str(child_pid) in table
        assert "self-improve-observer" in table
        assert {record["pid"] for record in payload["observed_processes"]}.issuperset(
            {str(observer.pid), str(child_pid)}
        )
        assert payload["audit_contract"]["agent_pids"] is False
    finally:
        observer.terminate()
        observer.communicate(timeout=5)
        shutil.rmtree(label_dir, ignore_errors=True)


def test_workstreams_group_processes_by_task() -> None:
    streams = active_work_status._workstreams(
        [
            {"pid": "101", "ppid": "1", "task": "pytest", "command": "pytest"},
            {"pid": "102", "ppid": "101", "task": "pytest", "command": "pytest"},
        ]
    )

    assert streams == {"pytest": {"process_count": 2, "pids": ["101", "102"]}}


def test_git_snapshot_reads_branch_and_head(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        value = "feature\n" if command[-2:] == ["branch", "--show-current"] else "abc123\n"
        return SimpleNamespace(stdout=value)

    monkeypatch.setattr("scripts.active_work_status.subprocess.run", fake_run)

    assert active_work_status._git() == {"branch": "feature", "head": "abc123"}


def test_gate_snapshot_handles_terminal_stale_and_live_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(active_work_status, "ROOT", tmp_path)
    status_path = tmp_path / ".gate-status"
    pid_path = tmp_path / ".gate-background.pid"

    assert active_work_status._gate()["state"] == "UNKNOWN"

    status_path.write_text("=== GATE: PASSED ===\n", encoding="utf-8")
    assert active_work_status._gate()["state"] == "PASS"

    status_path.write_text("=== GATE: FAILED ===\n", encoding="utf-8")
    pid_path.write_text("not-a-pid\n", encoding="utf-8")
    failed = active_work_status._gate()
    assert failed["state"] == "FAIL"
    assert failed["running_pid"] == ""

    pid_path.write_text("123\n", encoding="utf-8")
    monkeypatch.setattr("scripts.active_work_status.os.kill", lambda _pid, _signal: None)
    assert active_work_status._gate()["running_pid"] == "123"


@pytest.mark.parametrize(
    ("configured", "expected"),
    (("invalid", 8), ("0", 1), ("999", 128), ("12", 12)),
)
def test_worker_limit_is_valid_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: int,
) -> None:
    monkeypatch.setenv("GLUDD_WORKER_LIMIT", configured)
    assert active_work_status._worker_limit() == expected


def test_gate_owner_reads_contended_lock_and_handles_io_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "gate-refresh.lock"
    lock_path.write_text("pid=123\n", encoding="utf-8")
    monkeypatch.setattr(active_work_status, "resource_path", lambda *_args: lock_path)

    def contended_lock(_fd: int, operation: int) -> None:
        if operation & fcntl.LOCK_NB:
            raise BlockingIOError

    monkeypatch.setattr("scripts.active_work_status.fcntl.flock", contended_lock)
    assert active_work_status._active_gate_refresh_owner("namespace") == "123"

    missing = tmp_path / "missing" / "gate-refresh.lock"
    monkeypatch.setattr(active_work_status, "resource_path", lambda *_args: missing)
    assert active_work_status._active_gate_refresh_owner("namespace") is None


def test_worker_accounting_reports_duplicate_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(active_work_status, "_SINGLETON_WORKER_LEASES", frozenset({"unit-tests"}))
    monkeypatch.setattr(active_work_status, "_active_gate_refresh_owner", lambda _namespace: None)
    processes = [
        {"pid": "101", "ppid": "1", "task": "unit-tests", "command": "pytest"},
        {"pid": "102", "ppid": "1", "task": "unit-tests", "command": "pytest"},
    ]

    accounting = active_work_status._worker_accounting(processes, "namespace")

    assert accounting["leased_worker_count"] == 1
    assert accounting["duplicate_worker_leases"] == ["unit-tests"]


def test_collect_status_promotes_observed_gate_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "TASKS.md").write_text("- [ ] S1 — pending\n", encoding="utf-8")
    processes = [
        {"pid": "101", "ppid": "1", "task": "gate-refresh", "command": "make gate"}
    ]
    monkeypatch.setattr(active_work_status, "ROOT", tmp_path)
    monkeypatch.setattr(active_work_status, "_processes", lambda: processes)
    monkeypatch.setattr(
        active_work_status,
        "_gate",
        lambda: {"status_file": "status", "state": "UNKNOWN", "running_pid": ""},
    )
    monkeypatch.setattr(active_work_status, "_git", lambda: {"branch": "feature", "head": "abc"})
    monkeypatch.setattr(active_work_status, "_resource_observability", lambda _items: {})

    payload = active_work_status.collect_status()

    gate = payload["gate"]
    assert isinstance(gate, dict)
    assert gate["state"] == "RUNNING"
    assert gate["running_pid"] == "101"
    assert payload["open_task_ids"] == ["S1"]


def test_json_cli_prints_collected_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(active_work_status, "collect_status", lambda: {"status": "ok"})

    assert active_work_status.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok"}
