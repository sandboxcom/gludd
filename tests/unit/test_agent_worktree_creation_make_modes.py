"""Native Make parallel and inspection controls for actual creation recipes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from tests.unit.test_agent_worktree_creation_fail_closed import _creation_fixture


def _owned_snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    """Reads may change atime; inspection must preserve bytes, mtime and mode."""
    return {
        str(path.relative_to(root)): (
            path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode,
        )
        for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    }


def test_native_parallel_creation_keeps_jobserver_for_registration(tmp_path: Path) -> None:
    fixture = _creation_fixture(tmp_path)
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
