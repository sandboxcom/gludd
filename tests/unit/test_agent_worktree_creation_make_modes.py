"""Native Make parallel and inspection controls for actual creation recipes."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from tests.unit.test_agent_worktree_creation_fail_closed import WorktreeCreation, _creation_fixture


def _owned_snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    """Reads may change atime; inspection must preserve bytes, mtime and mode."""
    return {
        str(path.relative_to(root)): (
            path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode,
        )
        for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    }


def _install_registration_make_probe(
    fixture: WorktreeCreation, *, close_pipe_channel: bool = False,
) -> None:
    """Observe the native channel at nested Make entry, then exec that same Make."""
    probe = fixture.root / "tools" / "registration-make-probe"
    probe.write_text(
        f"#!{sys.executable} -P\n"
        "import json, os, pathlib, re, shlex, stat, sys\n"
        "prefix = re.split(r'(?:^|\\s)--(?=\\s|$)', os.environ.get('MAKEFLAGS', ''), maxsplit=1)[0]\n"
        "channels = []\n"
        "for token in shlex.split(prefix):\n"
        "    if token.startswith('--jobserver-auth=fifo:'):\n"
        "        path = token.split('fifo:', 1)[1]\n"
        "        try: live = stat.S_ISFIFO(os.stat(path).st_mode) and os.access(path, os.R_OK | os.W_OK)\n"
        "        except OSError: live = False\n"
        "        channels.append(live)\n"
        "        continue\n"
        "    pair = re.fullmatch(r'--jobserver-(?:auth|fds)=([0-9]+),([0-9]+)', token)\n"
        "    if pair is None: continue\n"
        "    left, right = int(pair[1]), int(pair[2])\n"
        f"    if {close_pipe_channel!r}:\n"
        "        for descriptor in (left, right):\n"
        "            try: os.close(descriptor)\n"
        "            except OSError: pass\n"
        "    try:\n"
        "        a, b = os.fstat(left), os.fstat(right)\n"
        "        live = left != right and min(left, right) >= 3\n"
        "        live = live and stat.S_ISFIFO(a.st_mode) and stat.S_ISFIFO(b.st_mode)\n"
        "        live = live and os.get_inheritable(left) and os.get_inheritable(right)\n"
        "    except OSError: live = False\n"
        "    channels.append(live)\n"
        f"with pathlib.Path({str(fixture.calls)!r}).open('a') as stream:\n"
        "    row = {'kind': 'make-entry', 'jobserver_live': bool(channels) and all(channels),\n"
        "           'args': sys.argv[1:]}\n"
        "    stream.write(json.dumps(row) + '\\n')\n"
        f"os.execve({fixture.real_make!r}, [{fixture.real_make!r}, *sys.argv[1:]], os.environ.copy())\n",
        encoding="utf-8",
    )
    probe.chmod(0o700)
    original = fixture.makefile.read_text(encoding="utf-8")
    header, separator, rest = original.partition("\n")
    assert separator and header == (
        "MAKE := " + shlex.quote(fixture.real_make) + " --no-print-directory -f "
        + shlex.quote(str(fixture.makefile))
    )
    fixture.makefile.write_text(
        "MAKE := " + shlex.quote(str(probe)) + " --no-print-directory -f "
        + shlex.quote(str(fixture.makefile)) + separator + rest,
        encoding="utf-8",
    )


def test_native_parallel_creation_keeps_jobserver_for_registration(tmp_path: Path) -> None:
    fixture = _creation_fixture(tmp_path)
    _install_registration_make_probe(fixture)
    result = fixture.invoke("agent-worktree", make_options=("-j2",))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "jobserver" not in result.stderr.lower(), result.stderr
    payload = cast(dict[str, object], json.loads(fixture.registry.read_text(encoding="utf-8")))
    entry = cast(dict[str, object], cast(dict[str, object], payload["workstreams"])[fixture.branch])
    assert entry["worktree"] == str(fixture.worktree)
    assert entry["status"] == "active"
    registrations = [row for row in fixture.rows() if row["kind"] == "registry"]
    assert len(registrations) == 1
    flags = cast(str, registrations[0]["makeflags"])
    assert "--jobserver-fds=" in flags or "--jobserver-auth=" in flags
    assert registrations[0]["carrier"] == "retained"
    assert f"WORKTREE_PATH={fixture.worktree}" in result.stdout
    entries = [row for row in fixture.rows() if row["kind"] == "make-entry"]
    assert len(entries) == 1
    assert entries[0]["jobserver_live"] is True
    assert entries[0]["args"] == [
        "--no-print-directory", "-f", str(fixture.makefile), "--no-print-directory",
        "workstream-register", f"BRANCH={fixture.branch}", f"WORKTREE={fixture.worktree}",
        f"ACTIVE_WORKSTREAM_REGISTRY={fixture.registry}",
    ]


def test_native_registration_probe_rejects_serial_channel_absence(tmp_path: Path) -> None:
    fixture = _creation_fixture(tmp_path)
    _install_registration_make_probe(fixture)
    result = fixture.invoke("agent-worktree")
    assert result.returncode == 0, result.stdout + result.stderr
    entries = [row for row in fixture.rows() if row["kind"] == "make-entry"]
    assert len(entries) == 1
    assert entries[0]["jobserver_live"] is False
    assert len([row for row in fixture.rows() if row["kind"] == "registry"]) == 1


@pytest.mark.parametrize("close_channel", [False, True])
def test_native_registration_probe_detects_closed_pipe_channel(
    tmp_path: Path, close_channel: bool,
) -> None:
    fixture = _creation_fixture(tmp_path)
    help_result = subprocess.run(
        [fixture.real_make, "--help"], cwd=fixture.root, env=fixture.environment,
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    make_options: tuple[str, ...] = ("-j2",)
    if "--jobserver-style" in help_result.stdout:
        make_options += ("--jobserver-style=pipe",)
    _install_registration_make_probe(fixture, close_pipe_channel=close_channel)
    result = fixture.invoke("agent-worktree", make_options=make_options)
    assert result.returncode == 0, result.stdout + result.stderr
    entries = [row for row in fixture.rows() if row["kind"] == "make-entry"]
    assert len(entries) == 1
    assert entries[0]["jobserver_live"] is (not close_channel)
    assert len([row for row in fixture.rows() if row["kind"] == "registry"]) == 1


@pytest.mark.parametrize("creation_native_mode,creation_native_status", [
    ("-n", 0), ("-q", 1), ("-t", 0),
])
@pytest.mark.parametrize("creation_native_override", [False, True])
def test_native_creation_inspection_preserves_owned_state_without_registration(
    tmp_path: Path, creation_native_mode: str, creation_native_status: int,
    creation_native_override: bool,
) -> None:
    fixture = _creation_fixture(tmp_path)
    observed = fixture.root / ".gate-logs/observed/retained"
    observed.mkdir(parents=True)
    (observed / "owned-receipt.txt").write_bytes(b"retained observer fixture\n")
    before = _owned_snapshot(fixture.root)
    refs = fixture.git("for-each-ref", "--format=%(refname) %(objectname)").stdout
    result = fixture.invoke(
        "agent-worktree", make_options=(creation_native_mode,),
        environment_override=creation_native_override,
    )
    assert result.returncode == creation_native_status, result.stdout + result.stderr
    assert _owned_snapshot(fixture.root) == before
    assert fixture.git("for-each-ref", "--format=%(refname) %(objectname)").stdout == refs
    assert not fixture.worktree_root.exists()
    assert not fixture.registry.exists() and not fixture.rows()
    assert not any(line.startswith(("WORKTREE_PATH=", "Worktree ready at "))
                   for line in result.stdout.splitlines())
    assert "outcome=passed" not in result.stderr
    assert not (fixture.root / "agent-worktree").exists()
