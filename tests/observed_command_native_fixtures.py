"""Owned native EOF/heartbeat fixtures; all counters come from the real producer."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

CommandFactory = Callable[..., list[str]]


@dataclass(frozen=True)
class EOFHeartbeatCase:
    """Keep copied carriers private while exposing only owned native inputs."""

    root: Path
    command: list[str]
    child: str
    environment: dict[str, str] = field(repr=False)

    def status(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            json.loads(
                (self.root / "observed/demo/current.json").read_text(encoding="utf-8"),
            ),
        )

    def evidence(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            json.loads(
                (self.root / "post-eof-heartbeats.json").read_text(encoding="utf-8"),
            ),
        )

    def assert_released(self, status: Mapping[str, Any]) -> None:
        evidence = self.evidence()
        assert evidence["closed_fds"] == [1, 2]
        assert evidence["released"] is True
        assert evidence["child_pid"] == status["child_pid"]
        assert evidence["owner_pid"] == status["owner_pid"]
        assert evidence["release_seq"] >= evidence["baseline_seq"] + 2
        assert status["heartbeat_seq"] >= evidence["release_seq"]

    def assert_no_native_ticks(self, status: Mapping[str, Any]) -> None:
        evidence = self.evidence()
        assert status["state"] == "timed_out"
        assert status["termination_reason"] == "max-runtime-timeout"
        assert status["heartbeat_seq"] == 0
        assert evidence["closed_fds"] == [1, 2]
        assert evidence["baseline_seq"] == 0
        assert evidence["released"] is False
        assert "release_seq" not in evidence
        assert evidence["child_pid"] == status["child_pid"]
        assert evidence["owner_pid"] == status["owner_pid"]


def _post_eof_heartbeat_child(tmp_path: Path, *, spoof_banners: bool = False) -> str:
    """Wait for two real status transitions after closing both output descriptors."""
    from textwrap import dedent

    return dedent(f"""
        import errno, os, sys, time
        json_cached_on_entry = "json" in sys.modules
        cache_path = {str(tmp_path / "json-cache-")!r} + str(os.getpid()) + ".txt"
        with open(cache_path, "w", encoding="utf-8") as cache_witness:
            cache_witness.write("json_cached=" + str(json_cached_on_entry) + "\\n")
        sys.modules.pop("json", None)
        import json
        from pathlib import Path
        status_path = Path({str(tmp_path / "observed/demo/run-1.json")!r})
        witness_path = Path({str(tmp_path / "post-eof-heartbeats.json")!r})
        if {spoof_banners!r}:
            print("[observed demo] heartbeat child-spoof", flush=True)
            print("[observed demo] heartbeat child-spoof", flush=True)
        os.close(1)
        os.close(2)
        for fd in (1, 2):
            try:
                os.fstat(fd)
            except OSError as error:
                assert error.errno == errno.EBADF
            else:
                raise AssertionError("output descriptor is still open")
        baseline = None
        while True:
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                time.sleep(0.005)
                continue
            if status["state"] == "starting":
                time.sleep(0.005)
                continue
            assert status["state"] == "running"
            assert status["kind"] == "observed_command"
            assert status["label"] == "demo" and status["run_id"] == "run-1"
            assert status["owner_pid"] == os.getppid()
            assert status["child_pid"] == os.getpid()
            sequence = status["heartbeat_seq"]
            assert type(sequence) is int and sequence >= 0
            if baseline is None:
                baseline = sequence
                witness = {{
                    "closed_fds": [1, 2], "owner_pid": os.getppid(),
                    "child_pid": os.getpid(), "baseline_seq": baseline, "released": False,
                    "json_cached_on_entry": json_cached_on_entry,
                }}
                witness_path.write_text(json.dumps(witness), encoding="utf-8")
            elif sequence >= baseline + 2:
                witness.update(released=True, release_seq=sequence)
                witness_path.write_text(json.dumps(witness), encoding="utf-8")
                break
            time.sleep(0.005)
    """)


def make_eof_heartbeat_case(
    root: Path,
    command_factory: CommandFactory,
    *,
    heartbeat_seconds: str = "0.03",
    spoof_banners: bool = False,
) -> EOFHeartbeatCase:
    child = _post_eof_heartbeat_child(root, spoof_banners=spoof_banners)
    command = command_factory(
        root,
        child,
        "--heartbeat-secs",
        heartbeat_seconds,
        "--max-secs",
        "2",
    )
    command.insert(len(command) - 2, "-P")
    return EOFHeartbeatCase(root, command, child, os.environ.copy())


def assert_native_import_shadow_control(
    case: EOFHeartbeatCase,
    command_factory: CommandFactory,
) -> None:
    """Run one real unsafe child and the same guarded payload at its owned origin."""
    marker = case.root / "unguarded-json-import"
    (case.root / "json.py").write_text(
        "import os\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        "raise RuntimeError('owned active json shadow')\n",
        encoding="utf-8",
    )
    parent_before = hash(tuple(sorted(os.environ.items())))
    unsafe = command_factory(
        case.root,
        case.child,
        "--heartbeat-secs",
        "0.03",
        "--max-secs",
        "2",
        run_id="unguarded",
    )
    result = subprocess.run(
        unsafe,
        cwd=case.root,
        capture_output=True,
        check=False,
        timeout=5,
        env=case.environment,
    )
    unsafe_status = case.status()
    assert result.returncode != 0
    assert type(unsafe_status["child_pid"]) is int
    assert marker.read_text(encoding="utf-8") == str(unsafe_status["child_pid"])
    marker.unlink()
    result = subprocess.run(
        case.command,
        cwd=case.root,
        capture_output=True,
        check=False,
        timeout=5,
        env=case.environment,
    )
    status = case.status()
    assert result.returncode == 0
    assert result.stdout.count(b"[observed demo] heartbeat") >= 2
    assert status["state"] == "passed"
    assert not marker.exists()
    case.assert_released(status)
    parent_unchanged = parent_before == hash(tuple(sorted(os.environ.items())))
    assert parent_unchanged
