"""Native Make must execute owners and guards while keeping reads lightweight."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.unit.test_make_readonly_version import _search_fixture

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class NativeMake:
    root: Path
    environment: dict[str, str]
    native_git: str

    def run(self, *arguments: str, without_runtime: bool = False) -> subprocess.CompletedProcess[str]:
        environment = dict(self.environment)
        if without_runtime:
            environment.pop("VIRTUAL_ENV", None)
            environment.pop("UV_PROJECT_ENVIRONMENT", None)
        return subprocess.run(
            ["make", "--no-print-directory", *arguments], cwd=self.root, env=environment,
            capture_output=True, text=True, timeout=30, check=False,
        )

    def authority(self) -> dict[str, bytes]:
        metadata = self.root / ".git"
        result = {name: (metadata / name).read_bytes() for name in ("HEAD", "index", "config")}
        result.update({
            str(path.relative_to(metadata)): path.read_bytes()
            for path in (metadata / "refs").rglob("*") if path.is_file()
        })
        result["pyproject.toml"] = (self.root / "pyproject.toml").read_bytes()
        result["input.txt"] = (self.root / "input.txt").read_bytes()
        return result


@pytest.fixture
def native_make(tmp_path: Path) -> NativeMake:
    git = shutil.which("git")
    assert git is not None and shutil.which("uv") is not None
    names = subprocess.run(
        [git, "rev-parse", "--local-env-vars"], cwd=tmp_path, env={"PATH": os.defpath},
        capture_output=True, text=True, check=True, timeout=5,
    ).stdout.split()
    assert names and all(name.startswith("GIT_") and name.isascii() and name.isidentifier() for name in names)
    environment = dict(os.environ)
    for key in (*names, "MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL", "GNUMAKEFLAGS", "PYTEST_ADDOPTS"):
        environment.pop(key, None)
    environment.update({
        "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", "PYTHONDONTWRITEBYTECODE": "1",
        "GLUDD_PROJECT_ROOT": str(tmp_path), "PYTHONPATH": str(tmp_path / "src"),
    })
    fixture = NativeMake(tmp_path, environment, git)
    _search_fixture(tmp_path)
    (tmp_path / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "native-make-fixture"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (tmp_path / "input.txt").write_text("native owned fixture\n", encoding="utf-8")
    application = tmp_path / "src" / "general_ludd"
    application.mkdir(parents=True)
    (application / "__init__.py").write_text("raise AssertionError('application import forbidden')\n", encoding="utf-8")
    for arguments in (
        ("init", "-q", "--initial-branch=fixture-main"),
        ("config", "user.name", "Native Make fixture"),
        ("config", "user.email", "native-make@example.invalid"),
        ("add", "input.txt", "pyproject.toml"),
        ("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "native fixture"),
    ):
        result = subprocess.run(
            [git, *arguments], cwd=tmp_path, env=environment,
            capture_output=True, text=True, check=False, timeout=10,
        )
        assert result.returncode == 0, result.stderr
    return fixture


@pytest.mark.parametrize("state_first", [True, False])
def test_mixed_read_only_and_register_runs_real_owner_exactly_once(native_make: NativeMake, state_first: bool) -> None:
    registry = native_make.root / "active.json"
    goals = ("worktree-state", "workstream-register") if state_first else ("workstream-register", "worktree-state")
    before = native_make.authority()
    parent = dict(os.environ)
    result = native_make.run(
        *goals, "BRANCH=agent-owned", f"WORKTREE={native_make.root}", f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert registry.is_file(), "successful owner execution must persist its actual owned registration"
    payload = json.loads(registry.read_text(encoding="utf-8"))
    assert list(payload["workstreams"]) == ["agent-owned"]
    assert payload["workstreams"]["agent-owned"]["worktree"] == str(native_make.root)
    assert payload["workstreams"]["agent-owned"]["status"] == "active"
    assert result.stdout.count("registered active workstream: agent-owned") == 1
    assert native_make.authority() == before and os.environ == parent
    assert not (native_make.root / ".venv").exists() and not (native_make.root / "uv.lock").exists()


@pytest.mark.parametrize("state_first", [True, False])
def test_mixed_read_only_preserves_real_owner_failure(native_make: NativeMake, state_first: bool) -> None:
    registry = native_make.root / "refused.json"
    goals = ("worktree-state", "workstream-register") if state_first else ("workstream-register", "worktree-state")
    before = native_make.authority()
    result = native_make.run(
        *goals, "BRANCH=invalid..branch", f"WORKTREE={native_make.root}", f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
    )
    assert result.returncode != 0, "a printed owner command is not successful owner execution"
    assert "invalid workstream branch" in result.stdout + result.stderr
    assert not registry.exists() and native_make.authority() == before


def _capture_checkout(fixture: NativeMake) -> Path:
    binary = fixture.root / "bin"
    binary.mkdir()
    event = fixture.root / "checkout-called"
    git = binary / "git"
    git.write_text(
        f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
        "if sys.argv[1:] == ['checkout', 'fixture-target']:\n"
        f"    Path({str(event)!r}).write_text('called')\n"
        "    print('NATIVE CHECKOUT BODY')\n    raise SystemExit(0)\n"
        f"os.execv({fixture.native_git!r}, [{fixture.native_git!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    git.chmod(0o700)
    fixture.environment["PATH"] = f"{binary}{os.pathsep}{fixture.environment['PATH']}"
    return event


@pytest.mark.parametrize("state_first", [False, True])
@pytest.mark.parametrize("active", [True, False])
def test_native_checkout_executes_gate_guard_before_harmless_body(
    native_make: NativeMake, state_first: bool, active: bool,
) -> None:
    event = _capture_checkout(native_make)
    lock = native_make.root / "gate.lock"
    if active:
        acquired = subprocess.run(
            [sys.executable, str(ROOT / "scripts/gate_run_lock.py"), "acquire", str(lock), str(os.getpid())],
            cwd=native_make.root, env=native_make.environment, capture_output=True, text=True, check=False, timeout=10,
        )
        assert acquired.returncode == 0, acquired.stderr
    before = native_make.authority()
    lock_before = lock.read_bytes() if lock.exists() else None
    goals = ("worktree-state", "git-checkout") if state_first else ("git-checkout",)
    result = native_make.run(*goals, "MSG=fixture-target", f"GATE_RUN_LOCK={lock}")
    output = result.stdout + result.stderr
    if active:
        assert result.returncode != 0 and "active gate" in output
        assert not event.exists(), "an active gate must block the mutation body, not print its guard"
        assert lock.read_bytes() == lock_before
    else:
        assert result.returncode == 0, output
        assert event.read_text() == "called"
        assert result.stdout.index("gate-run-lock: inactive") < result.stdout.index("NATIVE CHECKOUT BODY")
    assert native_make.authority() == before


@pytest.mark.parametrize("target", ["worktree-state", "search", "cat-file", "uv-cache-path"])
def test_native_lightweight_reads_need_no_uv_or_environment(native_make: NativeMake, target: str) -> None:
    binary = native_make.root / "bin"
    binary.mkdir()
    event = native_make.root / "uv-called"
    uv = binary / "uv"
    uv.write_text(f"#!/bin/sh\nprintf called > '{event}'\nexit 79\n", encoding="utf-8")
    uv.chmod(0o700)
    native_make.environment["PATH"] = f"{binary}{os.pathsep}{native_make.environment['PATH']}"
    arguments = (
        ("PATTERN=native", "SEARCH_PATH=input.txt") if target == "search"
        else ("FILE=input.txt",) if target == "cat-file" else ()
    )
    before = native_make.authority()
    result = native_make.run(target, *arguments, without_runtime=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not event.exists(), "dependency-free read-only recipes must not invoke uv at all"
    assert native_make.authority() == before
    assert not (native_make.root / ".venv").exists() and not (native_make.root / "uv.lock").exists()


def test_mixed_owner_preserves_explicit_uv_failure_instead_of_echo_success(native_make: NativeMake) -> None:
    uv = native_make.root / "unavailable-uv"
    uv.write_text("#!/bin/sh\nprintf 'selected uv unavailable\\n' >&2\nexit 73\n", encoding="utf-8")
    uv.chmod(0o700)
    registry = native_make.root / "active.json"
    result = native_make.run(
        "worktree-state", "workstream-register", f"UV={uv}", "BRANCH=agent-owned",
        f"WORKTREE={native_make.root}", f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
    )
    assert result.returncode != 0 and "selected uv unavailable" in result.stderr
    assert "Error 73" in result.stderr and not registry.exists()


def test_real_owner_keeps_global_no_sync_and_selected_toolchain(native_make: NativeMake) -> None:
    native_uv = shutil.which("uv", path=native_make.environment["PATH"])
    assert native_uv is not None
    policy = native_make.root / "runtime-policy.json"
    selected = native_make.root / "selected-uv"
    selected.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n"
        f"Path({str(policy)!r}).write_text(json.dumps({{key: os.environ.get(key) for key in "
        "('UV_NO_SYNC', 'UV_PROJECT_ENVIRONMENT', 'VIRTUAL_ENV', 'UV_PYTHON_DOWNLOADS')}))\n"
        f"os.execv({native_uv!r}, [{native_uv!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    selected.chmod(0o700)
    registry = native_make.root / "active.json"
    result = native_make.run(
        "worktree-state", "workstream-register", f"UV={selected}", "UV_NO_SYNC=0",
        "BRANCH=agent-owned", f"WORKTREE={native_make.root}", f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
    )
    assert result.returncode == 0 and registry.is_file(), result.stdout + result.stderr
    observed = json.loads(policy.read_text(encoding="utf-8"))
    assert observed == {
        "UV_NO_SYNC": "1", "UV_PYTHON_DOWNLOADS": "never",
        "UV_PROJECT_ENVIRONMENT": native_make.environment["UV_PROJECT_ENVIRONMENT"],
        "VIRTUAL_ENV": native_make.environment["VIRTUAL_ENV"],
    }


def test_checkout_first_multi_goal_refusal_still_precedes_all_bodies(native_make: NativeMake) -> None:
    event = _capture_checkout(native_make)
    before = native_make.authority()
    result = native_make.run("git-checkout", "worktree-state", "MSG=fixture-target")
    assert result.returncode != 0 and "Quote multi-word variable values for git-checkout" in result.stderr
    assert not event.exists() and native_make.authority() == before
