"""Native read-only Git status acceptance through the actual Make recipe."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

from tests.unit.test_git_merge_nc_safety import REAL_GIT, run_git

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class StatusFixture:
    root: Path
    makefile: Path
    environment: dict[str, str] = field(repr=False)


@pytest.fixture
def status_repository(tmp_path: Path) -> StatusFixture:
    root = tmp_path / "repository"
    root.mkdir()
    private_home = tmp_path / "status-home"
    private_home.mkdir()
    empty_config = tmp_path / "empty-git-config"
    empty_config.write_text("", encoding="utf-8")
    hooks = tmp_path / "empty-hooks"
    hooks.mkdir()
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith("GIT_") and name not in {
            "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "MAKEFILES", "GNUMAKEFLAGS",
        }
    }
    environment.update(
        HOME=str(private_home), XDG_CONFIG_HOME=str(private_home / "xdg"),
        GIT_CONFIG_GLOBAL=str(empty_config), GIT_CONFIG_SYSTEM=str(empty_config),
        GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
        GIT_CEILING_DIRECTORIES=str(tmp_path),
    )
    run_git(root, environment, "init", "-b", "status-fixture")
    (root / "tracked.txt").write_text("original\n", encoding="utf-8")
    run_git(root, environment, "add", "tracked.txt")
    run_git(
        root, environment, "-c", "user.name=Status fixture",
        "-c", "user.email=status@example.invalid", "-c", "commit.gpgsign=false",
        "-c", f"core.hooksPath={hooks}", "commit", "-m", "owned fixture",
    )
    makefile = tmp_path / "status.mk"
    composed = compose_makefile(ROOT / "Makefile")
    marker = "\ngit-status:\n"
    assert composed.count(marker) == 1
    body = composed.split(marker, 1)[1].split("\ngit-show:\n", 1)[0]
    makefile.write_text("git-status:\n" + body, encoding="utf-8")
    return StatusFixture(root, makefile, environment)


def _status(fixture: StatusFixture, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "-f", str(fixture.makefile), "git-status"],
        cwd=cwd or fixture.root, env=fixture.environment,
        text=True, capture_output=True, check=False, timeout=15,
    )


def _index_state(root: Path) -> tuple[bytes, int, int]:
    index = root / ".git/index"
    metadata = index.stat()
    return index.read_bytes(), metadata.st_mtime_ns, metadata.st_ino


@pytest.mark.parametrize("status_optional_env", [None, "1"], ids=("default", "inherited-one"))
def test_status_never_refreshes_native_index(
    status_repository: StatusFixture, status_optional_env: str | None,
) -> None:
    fixture = status_repository
    if status_optional_env is not None:
        fixture.environment["GIT_OPTIONAL_LOCKS"] = status_optional_env
    tracked = fixture.root / "tracked.txt"
    metadata = tracked.stat()
    os.utime(tracked, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 2_000_000_000))
    original = _index_state(fixture.root)
    result = _status(fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _index_state(fixture.root) == original
    assert "AGENT_NEXT target=git-status outcome=observed native_status=0" in result.stderr
    # Prove this fixture really requires a refresh under the predecessor command.
    witness = subprocess.run(
        [REAL_GIT, "status", "--short"], cwd=fixture.root,
        env={**fixture.environment, "GIT_OPTIONAL_LOCKS": "1"},
        capture_output=True, check=False, timeout=15,
    )
    assert witness.returncode == 0, witness.stderr
    assert _index_state(fixture.root) != original


def test_status_keeps_competing_lock_and_real_dirty_output(status_repository: StatusFixture) -> None:
    fixture = status_repository
    tracked = fixture.root / "tracked.txt"
    tracked.write_text("staged\n", encoding="utf-8")
    run_git(fixture.root, fixture.environment, "add", "tracked.txt")
    tracked.write_text("working\n", encoding="utf-8")
    (fixture.root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    lock = fixture.root / ".git/index.lock"
    lock.write_bytes(b"competing-owner")
    lock.chmod(0o600)
    before_lock = (lock.read_bytes(), lock.stat().st_mtime_ns, lock.stat().st_ino, lock.stat().st_mode)
    before_index = _index_state(fixture.root)
    result = _status(fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MM tracked.txt" in result.stdout
    assert "?? untracked.txt" in result.stdout
    assert _index_state(fixture.root) == before_index
    assert (lock.read_bytes(), lock.stat().st_mtime_ns, lock.stat().st_ino, lock.stat().st_mode) == before_lock


def test_status_outside_repository_retains_native_failure(status_repository: StatusFixture, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    result = _status(status_repository, cwd=outside)
    assert result.returncode == 2
    assert "not a git repository" in result.stderr
    assert "outcome=failed native_status=128" in result.stderr
    assert "outcome=observed" not in result.stderr
    assert "Not a git repo" not in result.stdout


def test_status_failure_guidance_keeps_child_status_and_argv(
    status_repository: StatusFixture, tmp_path: Path,
) -> None:
    binaries = tmp_path / "status-bin"
    binaries.mkdir()
    argv = tmp_path / "status-argv"
    child = binaries / "git"
    child.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > " + shlex.quote(str(argv))
        + "\nprintf '%s\\n' native-status-17 >&2\nexit 17\n", encoding="utf-8",
    )
    child.chmod(0o755)
    fixture = status_repository
    fixture.environment["PATH"] = str(binaries) + os.pathsep + fixture.environment["PATH"]
    result = _status(fixture)
    assert result.returncode == 2
    assert argv.read_text(encoding="utf-8").splitlines() == ["--no-optional-locks", "status", "--short"]
    assert "native-status-17" in result.stderr
    assert "outcome=failed native_status=17" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    assert "outcome=observed" not in result.stderr


def test_status_contract_has_a_safe_zero_variable_example() -> None:
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text(encoding="utf-8"))
    matches = [entry for entry in contract["targets"] if entry["name"] == "git-status"]
    assert len(matches) == 1
    assert matches[0]["make_variables"] == []
    assert matches[0]["behavior"] == "make git-status"


def test_status_fixture_repr_keeps_owned_environment_private(tmp_path: Path) -> None:
    environment = {
        "GLUDD_FAKE_PRIVATE_CARRIER": "owned-private-repr-marker",
        "GLUDD_FAKE_RETAINED_CARRIER": "owned-retained-repr-marker",
    }
    fixture = StatusFixture(tmp_path, tmp_path / "owned.mk", environment)

    rendered = repr(fixture)

    assert all(name not in rendered and value not in rendered for name, value in environment.items())
    assert fixture.environment is environment
    assert fixture.root == tmp_path
    assert str(fixture.makefile) in rendered
