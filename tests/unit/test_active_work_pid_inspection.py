"""Bounded read-only PID inspection through the existing active-work producer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    import active_work_status as subject
else:
    from scripts import active_work_status as subject


def _fixture(
    monkeypatch: pytest.MonkeyPatch, output: str,
    *, cwd: Path | None = Path("/repo/foreign"),
) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(subject, "_repository_roots", lambda: (Path("/repo/gludd"),))
    monkeypatch.setattr(
        subject, "_owned_resource_roots",
        lambda _roots: (Path("/tmp/gludd-resources/project"),),
    )
    monkeypatch.setattr(subject, "_observer_status_paths", lambda _roots: ())

    def native_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv == ["ps", "-axo", "pid=,ppid=,command="]
        assert kwargs["timeout"] == 10
        assert kwargs["check"] is True
        assert kwargs.get("shell", False) is False
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", native_run)
    # The existing CWD owner is imported only by the new diagnostic path.
    monkeypatch.setattr(subject, "_process_cwd", lambda _pid: cwd, raising=False)
    return calls


def _inspect(capsys: pytest.CaptureFixture[str], pid: str = "68501") -> tuple[int, dict[str, object]]:
    status = subject.main([f"--inspect-pid={pid}"])
    return status, cast(dict[str, object], json.loads(capsys.readouterr().out))


def _ownership(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["ownership"])


def _process(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["process"])


def _contract(payload: dict[str, object]) -> None:
    assert payload["audit_contract"] == {
        "agent_pids": False, "diagnostic_only": True,
        "gate_authority": False, "kill_authority": False,
    }


@pytest.mark.parametrize(
    "pid", ["", "0", "-1", "+1", "1.0", "abc", "1;kill", " 1", "1 ",
            "2147483648", "9" * 100, "\N{ARABIC-INDIC DIGIT ONE}"],
)
def test_malformed_pid_refuses_before_process_or_repository_queries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], pid: str,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid PID reached native inspection")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subject, "_repository_roots", forbidden)
    with pytest.raises(SystemExit) as raised:
        subject.main([f"--inspect-pid={pid}"])
    assert raised.value.code == 2
    assert capsys.readouterr().out == ""


def test_missing_pid_is_explicit_and_does_not_consult_stale_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _fixture(monkeypatch, "123 1 uv sync\n")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("missing PID consulted ownership or CWD")

    monkeypatch.setattr(subject, "_repository_roots", forbidden)
    monkeypatch.setattr(subject, "_process_cwd", forbidden)
    status, payload = _inspect(capsys)

    assert status == 0
    assert payload["requested_pid"] == 68501
    assert payload["state"] == "missing"
    assert payload["process"] is None
    assert _ownership(payload)["verified"] is False
    assert len(calls) == 1
    _contract(payload)


@pytest.mark.parametrize(
    "command",
    ["uv run --no-sync python scripts/dependency_profiles.py",
     "/repo/foreign/.venv/bin/python -m pytest tests/unit/test_x.py",
     "/repo/gludd-copy/.venv/bin/python -m pytest tests/unit/test_x.py",
     "uv run --directory /repo/gludd sync"],
)
def test_foreign_or_untracked_pid_is_visible_without_broadening_ownership(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str,
) -> None:
    calls = _fixture(monkeypatch, f"68501 68400 {command}\n")
    status, payload = _inspect(capsys)

    assert status == 0
    assert payload["state"] == "present"
    assert _process(payload) == {
        "pid": "68501", "ppid": "68400", "command": command,
        "task": subject._task_label(command), "command_truncated": False,
    }
    assert _ownership(payload)["verified"] is False
    assert _ownership(payload)["source"] == "unverified"
    assert _ownership(payload)["cwd"] == "/repo/foreign"
    assert _ownership(payload)["cwd_matches_repository"] is False
    assert len(calls) == 1
    _contract(payload)


@pytest.mark.parametrize(
    ("command", "cwd", "repository_match", "resource_match"),
    [
        ("uv sync", Path("/repo/gludd/subdir"), True, False),
        ("uv sync", Path("/tmp/gludd-resources/project/cache"), False, True),
        ("uv sync", Path("/repo/gludd-copy"), False, False),
        ("uv sync", None, False, False),
    ],
)
def test_cwd_evidence_does_not_admit_untracked_processes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    command: str, cwd: Path | None, repository_match: bool, resource_match: bool,
) -> None:
    _fixture(monkeypatch, f"68501 1 {command}\n", cwd=cwd)
    status, payload = _inspect(capsys)

    assert status == 0
    assert _ownership(payload)["verified"] is False
    assert _ownership(payload)["cwd"] == (str(cwd) if cwd else None)
    assert _ownership(payload)["cwd_matches_repository"] is repository_match
    assert _ownership(payload)["cwd_matches_resource_root"] is resource_match


def test_tracked_pid_reuses_verified_tree_ownership_and_one_native_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    output = (
        "68400 1 make gate\n"
        "68501 68400 /repo/gludd/.venv/bin/python -m pytest tests/unit/test_x.py\n"
        "68502 68501 python -m pytest tests/unit/test_y.py\n"
    )
    calls = _fixture(monkeypatch, output)
    status, payload = _inspect(capsys)

    assert status == 0
    assert _process(payload)["ppid"] == "68400"
    assert _ownership(payload)["verified"] is True
    assert _ownership(payload)["source"] == "tracked-command"
    assert len(calls) == 1
    assert "68502" not in json.dumps(payload)
    _contract(payload)


def test_diagnostic_never_uses_a_second_default_process_query(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _fixture(monkeypatch, "68501 1 uv sync\n")

    def forbidden() -> list[dict[str, str]]:
        pytest.fail("inspection launched the default inventory independently")

    monkeypatch.setattr(subject, "_processes", forbidden)

    assert _inspect(capsys)[0] == 0


def test_observer_ownership_requires_current_native_parent_child_relationship(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    pointer = tmp_path / "observed" / "bounded" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({
        "schema_version": 1, "kind": "observed_command", "label": "bounded",
        "state": "running", "owner_pid": 68400, "child_pid": 68501,
        "agent_pid": 999,
    }), encoding="utf-8")
    _fixture(monkeypatch, (
        "68400 1 python scripts/stream_command.py --label bounded\n"
        "68501 68400 uv sync\n"
    ))
    monkeypatch.setattr(subject, "_observer_status_paths", lambda _roots: (pointer,))
    status, payload = _inspect(capsys)
    assert status == 0
    assert _ownership(payload)["verified"] is True
    assert _ownership(payload)["source"] == "observed-command"
    assert "999" not in json.dumps(payload)

    _fixture(monkeypatch, (
        "68400 1 python scripts/stream_command.py --label bounded\n"
        "68501 1 uv sync\n"
    ))
    monkeypatch.setattr(subject, "_observer_status_paths", lambda _roots: (pointer,))
    status, payload = _inspect(capsys)
    assert status == 0
    assert _ownership(payload)["verified"] is False


def test_pid_reuse_or_absence_cannot_retain_previous_inspection_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _fixture(monkeypatch, "68501 1 /repo/gludd/.venv/bin/python -m pytest\n")
    assert _ownership(_inspect(capsys)[1])["verified"] is True

    _fixture(monkeypatch, "68501 1 /repo/foreign/.venv/bin/python -m pytest\n")
    assert _ownership(_inspect(capsys)[1])["verified"] is False

    _fixture(monkeypatch, "")
    status, missing = _inspect(capsys)
    assert status == 0
    assert missing["state"] == "missing"
    assert missing["process"] is None


@pytest.mark.parametrize("failure", [
    OSError("native ps unavailable"),
    subprocess.TimeoutExpired(["ps"], 10),
    subprocess.CalledProcessError(1, ["ps"], stderr="not-a-status-cache"),
])
def test_native_snapshot_error_is_unavailable_and_never_missing_or_cached_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    failure: Exception,
) -> None:
    _fixture(monkeypatch, "68501 1 uv sync\n")

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(subprocess, "run", unavailable)
    status, payload = _inspect(capsys)

    assert status == 2
    assert payload["state"] == "unavailable"
    assert payload["process"] is None
    assert _ownership(payload)["verified"] is False
    assert "not-a-status-cache" not in json.dumps(payload)
    _contract(payload)


def test_ownership_failure_retains_native_row_but_refuses_verification(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _fixture(monkeypatch, "68501 1 uv sync\n")

    def unavailable() -> tuple[Path, ...]:
        raise ValueError("worktree registry unavailable")

    monkeypatch.setattr(subject, "_repository_roots", unavailable)
    status, payload = _inspect(capsys)
    assert status == 2
    assert payload["state"] == "unavailable"
    assert _process(payload)["pid"] == "68501"
    assert _ownership(payload)["verified"] is False


def test_duplicate_requested_native_rows_refuse_ambiguous_identity(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _fixture(monkeypatch, "68501 1 uv sync\n68501 2 uv build\n")
    status, payload = _inspect(capsys)
    assert status == 2
    assert payload["state"] == "unavailable"
    assert _ownership(payload)["verified"] is False


def test_native_command_and_cwd_are_bounded_before_json_publication(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    command = "/repo/gludd/.venv/bin/python -m pytest " + "x" * 10_000
    cwd = Path("/repo/gludd/" + "y" * 10_000)
    _fixture(monkeypatch, f"68501 1 {command}\n", cwd=cwd)
    status, payload = _inspect(capsys)

    assert status == 0
    assert len(str(_process(payload)["command"])) == subject._COMMAND_DISPLAY_LIMIT
    assert str(_process(payload)["command"]).endswith("...")
    assert _process(payload)["command_truncated"] is True
    assert len(str(_ownership(payload)["cwd"])) == subject._LEASE_PATH_DISPLAY_LIMIT
    assert _ownership(payload)["cwd_matches_repository"] is True
    assert _ownership(payload)["cwd_truncated"] is True
    assert len(json.dumps(payload)) < 2_000


def test_inspection_does_not_write_signal_launch_shell_or_read_gate_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _fixture(monkeypatch, "68501 1 uv sync\n")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("read-only PID inspection attempted a mutation or gate read")

    for name in ("write_text", "write_bytes", "mkdir", "unlink"):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(os, "kill", forbidden)
    monkeypatch.setattr(subject, "_gate", forbidden)
    monkeypatch.setattr(subject, "collect_status", forbidden)
    assert _inspect(capsys)[0] == 0
    assert len(calls) == 1


def test_inspection_and_default_table_modes_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid mode combination queried processes")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(SystemExit) as raised:
        subject.main(["--process-table", "--inspect-pid=68501"])
    assert raised.value.code == 2


@pytest.mark.parametrize(
    "ppid", ["9" * 10_000, "2147483648"], ids=["overlong", "out-of-range"],
)
def test_malformed_native_parent_identifier_cannot_make_output_unbounded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ppid: str,
) -> None:
    _fixture(monkeypatch, f"68501 {ppid} uv sync\n")
    status, payload = _inspect(capsys)
    assert status == 2
    assert payload["state"] == "unavailable"
    assert payload["process"] is None
    assert len(json.dumps(payload)) < 1_000


def test_cwd_probe_failure_does_not_turn_optional_evidence_into_ownership(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _fixture(monkeypatch, "68501 1 uv sync\n")

    def unavailable(_pid: int) -> Path | None:
        raise OSError("native cwd unavailable")

    monkeypatch.setattr(subject, "_process_cwd", unavailable)
    status, payload = _inspect(capsys)
    assert status == 0
    assert payload["state"] == "present"
    assert _ownership(payload)["cwd"] is None
    assert _ownership(payload)["verified"] is False


def test_direct_cli_observes_its_live_native_caller_read_only() -> None:
    root = Path(__file__).resolve().parents[2]
    pid = os.getpid()
    completed = subprocess.run(
        [
            sys.executable, str(root / "scripts/active_work_status.py"),
            f"--inspect-pid={pid}",
        ],
        cwd=root, capture_output=True, text=True, check=False, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["requested_pid"] == pid
    assert payload["state"] == "present"
    assert payload["process"]["pid"] == str(pid)
    assert payload["process"]["ppid"] == str(os.getppid())
    assert payload["ownership"]["cwd_matches_repository"] is True
    assert len(payload["process"]["command"]) <= subject._COMMAND_DISPLAY_LIMIT
    _contract(payload)
