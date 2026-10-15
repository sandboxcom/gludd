"""Optional native Git transport bounds terminate and reap their direct child."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import staged_snapshot


@pytest.fixture
def native_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    executable = tmp_path / "git"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\nfrom pathlib import Path\n"
        "root = Path(sys.argv[2])\nmode = sys.argv[-1]\n"
        "(root / 'pid').write_text(str(os.getpid()))\n"
        "if mode in ('stdout', 'stderr'):\n"
        "    stream = 1 if mode == 'stdout' else 2\n"
        "    for index in range(100000):\n"
        # The cap can kill this writer between truncate and write. Keep the
        # previous complete counter observable until its replacement is ready.
        "        (root / 'chunks.pending').write_text(str(index))\n"
        "        (root / 'chunks.pending').replace(root / 'chunks')\n"
        "        os.write(stream, b'x' * 1024)\n"
        "elif mode == 'timeout':\n"
        "    time.sleep(60)\n"
        "elif mode == 'input':\n"
        "    os.write(1, sys.stdin.buffer.read())\n"
        "elif mode == 'failure':\n"
        "    os.write(2, b'denied')\n    raise SystemExit(7)\n"
        "elif mode == 'small-stderr':\n    os.write(2, b'e' * 600)\n"
        "elif mode == 'mixed':\n    os.write(1, b'o' * 300)\n    os.write(2, b'e' * 300)\n"
        "else:\n    os.write(1, b'ok')\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def _assert_reaped(root: Path) -> None:
    pid = int((root / "pid").read_text(encoding="utf-8"))
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


def test_native_git_progress_survives_exit_during_next_counter_write(native_git: Path) -> None:
    """Abrupt termination must not erase the last fully published counter."""
    executable = native_git / "git"
    interrupted_writer = (
        "original_write_text = Path.write_text\n"
        "def interrupted_write_text(path, data, *args, **kwargs):\n"
        "    if path.name in ('chunks', 'chunks.pending') and data == '1':\n"
        "        with path.open('w'):\n"
        "            os._exit(0)\n"
        "    return original_write_text(path, data, *args, **kwargs)\n"
        "Path.write_text = interrupted_write_text\n"
    )
    executable.write_text(
        executable.read_text().replace("root = Path(sys.argv[2])", interrupted_writer + "root = Path(sys.argv[2])"),
    )
    assert staged_snapshot._git(native_git, ["stdout"], max_output_bytes=1024, timeout_seconds=5) == b"x" * 1024
    assert (native_git / "chunks").read_text() == "0"
    _assert_reaped(native_git)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_git_output_cap_stops_before_full_stream_allocation_and_reaps_child(native_git: Path, stream: str) -> None:
    with pytest.raises(staged_snapshot.SnapshotError, match=r"output.*cap"):
        staged_snapshot._git(native_git, [stream], max_output_bytes=1024, timeout_seconds=5)
    assert int((native_git / "chunks").read_text(encoding="utf-8")) < 100000 - 1
    _assert_reaped(native_git)


def test_git_remaining_deadline_kills_and_reaps_child(native_git: Path) -> None:
    with pytest.raises(staged_snapshot.SnapshotError, match=r"timed out|deadline"):
        staged_snapshot._git(native_git, ["timeout"], max_output_bytes=1024, timeout_seconds=0.3)
    _assert_reaped(native_git)


@pytest.mark.parametrize("data", [None, b"literal input"])
def test_optional_bounded_git_retains_normal_output_and_input(native_git: Path, data: bytes | None) -> None:
    result = staged_snapshot._git(
        native_git, ["ok" if data is None else "input"], input_data=data,
        max_output_bytes=1024, timeout_seconds=5,
    )
    assert result == (b"ok" if data is None else data)
    _assert_reaped(native_git)


def test_optional_bounded_git_keeps_original_nonzero_error_detail(native_git: Path) -> None:
    with pytest.raises(staged_snapshot.SnapshotError, match="exited 7: denied"):
        staged_snapshot._git(native_git, ["failure"], max_output_bytes=1024, timeout_seconds=5)
    _assert_reaped(native_git)


def test_unbounded_default_git_still_uses_existing_run_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def completed(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(kwargs)
        assert command == ["git", "-C", str(tmp_path), "status"]
        return subprocess.CompletedProcess(command, 0, b"legacy", b"")

    monkeypatch.setattr(subprocess, "run", completed)
    assert staged_snapshot._git(tmp_path, ["status"]) == b"legacy"
    assert calls[0]["timeout"] == staged_snapshot.GIT_TIMEOUT_SECONDS
    assert calls[0]["capture_output"] is True and calls[0]["shell"] is False


@pytest.mark.parametrize("cap", [-1, True])
def test_invalid_output_bound_refuses_before_starting_git(native_git: Path, cap: int) -> None:
    with pytest.raises(staged_snapshot.SnapshotError, match="output cap"):
        staged_snapshot._git(native_git, ["ok"], max_output_bytes=cap)
    assert not (native_git / "pid").exists()


@pytest.mark.parametrize("timeout", [0.0, float("nan")])
def test_invalid_remaining_deadline_refuses_before_starting_git(native_git: Path, timeout: float) -> None:
    with pytest.raises(staged_snapshot.SnapshotError, match="deadline"):
        staged_snapshot._git(native_git, ["ok"], max_output_bytes=1024, timeout_seconds=timeout)
    assert not (native_git / "pid").exists()


@pytest.mark.parametrize("mode", ["small-stderr", "mixed"])
def test_two_successful_git_commands_charge_both_streams_to_shared_cleanup_budget(
    native_git: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    from scripts import automatic_disk_cleanup_bytecode as bytecode

    monkeypatch.setattr(bytecode, "MAX_GIT_OUTPUT_BYTES", 1024)
    budget = bytecode.Budget()
    bytecode._git(native_git, [mode], budget)
    with pytest.raises(bytecode.BytecodeCapacity, match=r"output.*cap"):
        bytecode._git(native_git, [mode], budget)
    assert budget.output_bytes >= 1024
    _assert_reaped(native_git)


def test_failed_output_cap_is_charged_before_the_next_peer_git_command(
    native_git: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import automatic_disk_cleanup_bytecode as bytecode

    monkeypatch.setattr(bytecode, "MAX_GIT_OUTPUT_BYTES", 1024)
    budget = bytecode.Budget()
    with pytest.raises(bytecode.BytecodeCapacity):
        bytecode._git(native_git, ["stderr"], budget)
    assert budget.output_bytes >= 1024
    pid = (native_git / "pid").read_bytes()
    with pytest.raises(bytecode.BytecodeCapacity):
        bytecode._git(native_git, ["ok"], budget)
    assert (native_git / "pid").read_bytes() == pid
    _assert_reaped(native_git)
