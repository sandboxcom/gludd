"""Daemon-only projection of the existing project-owned native process snapshot."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import active_work_status as subject
else:
    from scripts import active_work_status as subject

ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / "scripts/active_work_status.py"


def _snapshot(monkeypatch: pytest.MonkeyPatch, rows: str) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(subject, "_repository_roots", lambda: (Path("/repo/gludd"),))
    monkeypatch.setattr(subject, "_owned_resource_roots", lambda _roots: ())
    monkeypatch.setattr(subject, "_observer_status_paths", lambda _roots: ())

    def output() -> str:
        calls.append("ps")
        return rows

    monkeypatch.setattr(subject, "_process_output", output)
    return calls


def _table(capsys: pytest.CaptureFixture[str]) -> str:
    assert subject.main(["--daemon-process-table"]) == 0
    return capsys.readouterr().out


def test_daemon_mode_keeps_owned_detached_watchdogs_and_daemons(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _snapshot(monkeypatch, (
        "101 1 /repo/gludd/.venv/bin/python scripts/agent_watchdog.py\n"
        "102 1 /repo/gludd/.venv/bin/python scripts/task_watchdog.py\n"
        "103 55 /repo/gludd/.venv/bin/python -m general_ludd.cli daemon start\n"
        "104 55 /repo/gludd/.venv/bin/gunicorn api:app\n"
        "105 1 /repo/gludd/.venv/bin/python -m pytest tests/unit/test_x.py\n"
        "106 1 /repo/gludd/.venv/bin/python scripts/audit_coverage.py\n"
        "201 1 /repo/foreign/.venv/bin/python scripts/agent_watchdog.py\n"
        "202 1 /repo/gludd-copy/.venv/bin/gunicorn api:app\n"
        "203 1 python scripts/task_watchdog.py\n"
        "204 1 /repo/gludd/.venv/bin/python -m llama_cpp.server\n"
    ))
    rendered = _table(capsys)
    pids = [line.split()[0] for line in rendered.splitlines()[1:]]
    assert pids == ["101", "102", "103", "104"]
    assert "watchdog" in rendered and "e2e-daemon" in rendered
    assert len(calls) == 1


@pytest.mark.parametrize(("command", "expected"), [
    ("python -m pytest tests/unit/test_agent_watchdog.py", "unit-tests"),
    ("python -m pytest tests/unit/test_task_watchdog.py", "unit-tests"),
    ("pytest tests/e2e/test_agent_watchdog.py", "e2e-tests"),
    ("/tool/bin/pytest tests/unit/test_task_watchdog.py", "unit-tests"),
    ("python -mpytest scripts/agent_watchdog.py", "pytest"),
    ("python -m pytest scripts/task_watchdog.py", "pytest"),
    ("python scripts/audit_coverage.py --source scripts/agent_watchdog.py", "coverage-audit"),
    ("detect-secrets scan scripts/task_watchdog.py", "coverage-audit-support"),
    ("mypy scripts/agent_watchdog.py", "typecheck"),
])
def test_audit_execution_cannot_become_a_daemon_from_a_filename(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    command: str, expected: str,
) -> None:
    calls = _snapshot(monkeypatch, f"101 1 /repo/gludd/.venv/bin/{command}\n")
    assert subject._task_label(command) == expected
    assert _table(capsys) == "No matching project processes\n"
    assert len(calls) == 1


@pytest.mark.parametrize("command", [
    "python scripts/agent_watchdog.py --label pytest-owner",
    "python scripts/agent_watchdog.py --label pytest",
    "python scripts/task_watchdog.py --log /tmp/gludd-pytest-watchdog.log",
])
def test_watchdog_arguments_do_not_turn_a_real_watchdog_into_a_test(command: str) -> None:
    assert subject._task_label(command) == "watchdog"


def test_daemon_mode_filters_classifications_and_reuses_the_renderer_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    rows = [
        {"pid": "101", "ppid": "1", "task": "watchdog", "command": "watchdog"},
        {"pid": "102", "ppid": "1", "task": "unit-tests", "command": "agent_watchdog.py"},
        {"pid": "103", "ppid": "1", "task": "e2e-daemon", "command": "daemon"},
        {"pid": "104", "ppid": "1", "task": "other", "command": "agent_watchdog.py"},
    ]
    original = [dict(row) for row in rows]
    render = subject._render_process_table

    def processes() -> list[dict[str, str]]:
        calls.append("inventory")
        return rows

    def shared_renderer(selected: list[dict[str, str]]) -> str:
        calls.append("renderer")
        assert selected == [rows[0], rows[2]]
        return render(selected)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("daemon projection attempted a write, signal, alternate query or gate read")

    monkeypatch.setattr(subject, "_processes", processes)
    monkeypatch.setattr(subject, "_render_process_table", shared_renderer)
    monkeypatch.setattr(subject, "collect_status", forbidden)
    monkeypatch.setattr(subject, "_inspect_pid", forbidden)
    monkeypatch.setattr(subject, "_gate", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(os, "kill", forbidden)
    for method in ("write_text", "write_bytes", "mkdir", "unlink"):
        monkeypatch.setattr(Path, method, forbidden)
    assert "101" in _table(capsys)
    assert calls == ["inventory", "renderer"] and rows == original


def test_daemon_mode_applies_existing_bounds_after_filtering(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        {"pid": str(pid), "ppid": "1", "task": "unit-tests", "command": "pytest"}
        for pid in range(100, 700)
    ] + [
        {"pid": str(pid), "ppid": "1", "task": "watchdog", "command": "w" * 1000}
        for pid in range(800, 804)
    ]
    monkeypatch.setattr(subject, "_processes", lambda: rows)
    monkeypatch.setattr(subject, "_PROCESS_DISPLAY_LIMIT", 2)
    rendered = _table(capsys)
    lines = rendered.splitlines()
    assert [line.split()[0] for line in lines[1:3]] == ["800", "801"]
    assert all(len(line) <= 290 and line.endswith("...") for line in lines[1:3])
    assert lines[3] == "... 2 additional owned processes not displayed"
    assert len(lines) == 4 and "pytest" not in rendered


@pytest.mark.parametrize("other", [["--process-table"], ["--inspect-pid=101"]])
def test_daemon_mode_refuses_other_modes_before_any_native_query(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], other: list[str],
) -> None:
    def forbidden() -> list[dict[str, str]]:
        pytest.fail("invalid mode combination queried processes")

    monkeypatch.setattr(subject, "_processes", forbidden)
    with pytest.raises(SystemExit) as raised:
        subject.main(["--daemon-process-table", *other])
    captured = capsys.readouterr()
    assert raised.value.code == 2 and captured.out == ""
    assert "not allowed with argument" in captured.err


def test_daemon_projection_does_not_change_default_process_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _snapshot(monkeypatch, (
        "101 1 /repo/gludd/.venv/bin/python scripts/agent_watchdog.py\n"
        "102 1 /repo/gludd/.venv/bin/python -m pytest tests/unit/test_x.py\n"
    ))
    assert subject.main(["--process-table"]) == 0
    rendered = capsys.readouterr().out
    assert "101" in rendered and "102" in rendered and "unit-tests" in rendered


def test_default_json_keeps_its_original_schema(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {"processes": [], "audit_contract": {"agent_pids": False}, "sentinel": "retained"}
    monkeypatch.setattr(subject, "collect_status", lambda: payload)
    assert subject.main([]) == 0
    assert json.loads(capsys.readouterr().out) == payload


def test_failed_fresh_inventory_does_not_reuse_a_previous_daemon_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _snapshot(monkeypatch, "101 1 /repo/gludd/.venv/bin/python scripts/agent_watchdog.py\n")
    assert "101" in _table(capsys)

    def unavailable() -> str:
        raise subprocess.CalledProcessError(1, ["ps"])

    monkeypatch.setattr(subject, "_process_output", unavailable)
    with pytest.raises(subprocess.CalledProcessError):
        subject.main(["--daemon-process-table"])
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("rows", [
    "101 1 /repo/gludd/.venv/bin/python scripts/agent_watchdog.py\n"
    "102 1 /repo/foreign/.venv/bin/python scripts/agent_watchdog.py\n"
    "103 1 /repo/gludd/.venv/bin/python -m pytest tests/unit/test_agent_watchdog.py\n",
    "",
])
def test_direct_cli_uses_one_native_snapshot_and_the_registered_project(
    tmp_path: Path, rows: str,
) -> None:
    native_bin = tmp_path / "native-bin"
    native_bin.mkdir()
    receipt = tmp_path / "ps-calls.txt"
    fixture_root = tmp_path / "project"
    fixture_root.mkdir()
    rows = rows.replace("/repo/gludd", str(fixture_root))
    porcelain = f"worktree {fixture_root}\nHEAD fixture\n"
    executables = {
        "git": "assert sys.argv[1:] == ['worktree', 'list', '--porcelain']\n"
               f"sys.stdout.write({porcelain!r})\n",
        "ps": "assert sys.argv[1:] == ['-axo', 'pid=,ppid=,command=']\n"
              f"with open({str(receipt)!r}, 'a', encoding='utf-8') as calls:\n"
              "    calls.write('snapshot\\n')\n"
              f"sys.stdout.write({rows!r})\n",
    }
    for name, body in executables.items():
        executable = native_bin / name
        executable.write_text(f"#!{sys.executable}\nimport sys\n" + body, encoding="utf-8")
        executable.chmod(0o700)
    environment = dict(os.environ, PATH=str(native_bin) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(
        [sys.executable, str(OWNER), "--daemon-process-table"], cwd=ROOT,
        env=environment, capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert receipt.read_text(encoding="utf-8") == "snapshot\n"
    if rows:
        assert [line.split()[0] for line in result.stdout.splitlines()[1:]] == ["101"]
    else:
        assert result.stdout == "No matching project processes\n"


def test_direct_cli_reads_the_live_native_inventory_without_creating_a_daemon() -> None:
    result = subprocess.run(
        [sys.executable, str(OWNER), "--daemon-process-table"], cwd=ROOT,
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    if result.stdout != "No matching project processes\n":
        lines = result.stdout.splitlines()
        assert lines[0].split() == ["PID", "PPID", "TASK", "COMMAND"]
        for line in lines[1:]:
            if line.startswith("..."):
                continue
            pid, ppid, task, _command = line.split(None, 3)
            assert pid.isdigit() and ppid.isdigit()
            assert task in {"watchdog", "e2e-daemon"}
            assert pid != str(os.getpid())
