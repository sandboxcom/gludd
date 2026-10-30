"""Retire the legacy bulk cleanup through confined native CLI/Make controls."""

from __future__ import annotations

import json
import os
import runpy
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, cast

import pytest
from scripts import gate_run_lock
from scripts.makefile_layout import compose_makefile
from scripts.workstream_registry import WorkstreamRegistry

from tests.unit.test_agent_worktree_creation_fail_closed import WorktreeCreation, _creation_fixture
from tests.unit.test_single_native_commit_collection import _block

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class LegacyCleanup:
    """All state, native tools, failed writes and gate ownership are private."""

    creation: WorktreeCreation
    script: Path
    registry: Path
    proof: Path
    gate: Path
    target_index: Path

    def snapshot(self) -> dict[str, object]:
        fixture = self.creation
        peer = fixture.root.parent / "frozen peer"
        return {
            "head": fixture.git("rev-parse", "HEAD").stdout,
            "refs": fixture.git("for-each-ref", "--format=%(refname) %(objectname)").stdout,
            "index": (fixture.root / ".git/index").read_bytes(),
            "caller_note": (fixture.root / "note.txt").read_bytes(),
            "caller_staged": (fixture.root / "staged.txt").read_bytes(),
            "caller_untracked": (fixture.root / "untracked.txt").read_bytes(),
            "peer_note": (peer / "note.txt").read_bytes(),
            "peer_checkpoint": (peer / "checkpoint.json").read_bytes(),
            "inventory": fixture.git("worktree", "list", "--porcelain", "-z").stdout,
            "selected_exists": fixture.worktree.exists(),
            "proof": self.proof.read_bytes() if self.proof.exists() else None,
            "selected_index": self.target_index.read_bytes() if self.target_index.exists() else None,
            "registry": self.registry.read_bytes(),
            "gate": self.gate.read_bytes(),
        }

    def invoke(self, entrypoint: str, environment_override: bool) -> subprocess.CompletedProcess[str]:
        fixture = self.creation
        arguments = ([sys.executable, str(self.script)] if entrypoint == "cli" else [
            fixture.real_make, "--no-print-directory", *(["-e"] if environment_override else []),
            "-f", str(fixture.makefile), "worktree-cleanup-merged",
            f"UV={shlex.quote(str(fixture.uv))}", f"PYTHON={sys.executable}",
        ])
        return subprocess.run(
            arguments, cwd=fixture.root, env=fixture.environment.copy(),
            capture_output=True, text=True, check=False, timeout=30,
        )


@pytest.fixture
def legacy_cleanup(tmp_path: Path) -> Iterator[LegacyCleanup]:
    fixture = _creation_fixture(tmp_path)
    fixture.git("worktree", "add", "--quiet", "-b", fixture.branch,
                str(fixture.worktree), fixture.head)
    proof = fixture.worktree / ".gate-logs/ignored-release-proof.json"
    proof.parent.mkdir()
    proof.write_bytes(b'{"release":"retained","sha":"full-native-identity"}\n')
    assert not fixture.git("-C", str(fixture.worktree), "status", "--porcelain").stdout
    target_index = Path(fixture.git(
        "-C", str(fixture.worktree), "rev-parse", "--git-path", "index",
    ).stdout.strip())

    # Replace only explicitly owned old root constants; native code is unchanged.
    scripts = fixture.root / "scripts"
    assert scripts.is_symlink()
    scripts.unlink()
    scripts.mkdir()
    cleanup_source = (ROOT / "scripts/cleanup_merged_worktrees.py").read_text(encoding="utf-8")
    cleanup_source = cleanup_source.replace(
        'Path("/Users/shawnwilson/gludd")', f"Path({str(fixture.root)!r})",
    )
    health_source = (ROOT / "scripts/check_worktree_health.py").read_text(encoding="utf-8")
    health_source = health_source.replace(
        'MAIN_CHECKOUT = "/Users/shawnwilson/gludd"',
        f"MAIN_CHECKOUT = {str(fixture.root)!r}",
    ).replace('WORKTREE_ROOT = "/tmp/gludd-worktrees"',
              f"WORKTREE_ROOT = {str(fixture.worktree_root)!r}")
    assert "/Users/shawnwilson/gludd" not in cleanup_source + health_source
    script = scripts / "cleanup_merged_worktrees.py"
    script.write_text(cleanup_source, encoding="utf-8")
    (scripts / "check_worktree_health.py").write_text(health_source, encoding="utf-8")
    (scripts / "workstream_registry.py").write_text(
        (ROOT / "scripts/workstream_registry.py").read_text(encoding="utf-8"), encoding="utf-8",
    )

    registry = fixture.root / "registry-state/active.json"
    WorkstreamRegistry(registry).register(fixture.branch, fixture.worktree)
    fixture.environment["GLUDD_ACTIVE_WORKSTREAM_REGISTRY"] = str(registry)
    # Reads/lock acquisition work; the native atomic replacement cannot create a
    # temporary file. Thus any unregister error is real, never a fake return code.
    registry.parent.chmod(0o500)
    gate = fixture.root / ".gate-logs/gate-run.lock"
    gate.parent.mkdir()
    assert gate_run_lock.acquire(gate, os.getpid()) == 0
    assert gate_run_lock.assert_inactive(gate, os.getpid()) == 1

    # Trusted adapters preserve argv/environment and delegate to real Git/Make/Python.
    for name, program in {
        "git": (
            f"command = [{fixture.real_git!r}, *sys.argv[1:]]\n"
            "kind = 'git'\n"
        ),
        "make": (
            f"command = [{fixture.real_make!r}, *sys.argv[1:]]\n"
            "kind = 'make'\n"
        ),
        "uv": (
            "args = sys.argv[1:]\n"
            "if args[:3] == ['run', '--no-project', 'python']:\n"
            "    tail = args[3:]\n"
            "else:\n"
            "    assert args[:2] == ['run', 'python'], args\n"
            "    tail = args[2:]\n"
            f"command = [{sys.executable!r}, *tail]\n"
            "kind = 'uv'\n"
        ),
    }.items():
        adapter = fixture.root / "tools" / name
        adapter.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, subprocess, sys\n" + program
            + "result = subprocess.run(command, env=os.environ.copy(), capture_output=True, "
            "text=True, check=False)\n"
            + f"with pathlib.Path({str(fixture.calls)!r}).open('a') as output:\n"
            + "    output.write(json.dumps({'kind':kind, 'args':sys.argv[1:], "
            "'returncode':result.returncode, 'stderr':result.stderr}) + '\\n')\n"
            + "sys.stdout.write(result.stdout)\nsys.stderr.write(result.stderr)\n"
            + "raise SystemExit(result.returncode)\n",
            encoding="utf-8",
        )
        adapter.chmod(0o755)
    source = compose_makefile(ROOT / "Makefile")
    fixture.makefile.write_text(
        "MAKE := " + shlex.quote(fixture.real_make) + " --no-print-directory -f "
        + shlex.quote(str(fixture.makefile)) + "\n"
        + "UV := " + shlex.quote(str(fixture.uv)) + "\n"
        + f"PYTHON := {shlex.quote(sys.executable)}\n"
        + f"ACTIVE_WORKSTREAM_REGISTRY := {registry}\n"
        + ".PHONY: worktree-cleanup-merged workstream-unregister\n"
        + _block(source, "worktree-cleanup-merged") + "\n"
        + _block(source, "workstream-unregister"),
        encoding="utf-8",
    )
    owned = LegacyCleanup(fixture, script, registry, proof, gate, target_index)
    try:
        yield owned
    finally:
        registry.parent.chmod(0o700)
        if gate.exists():
            assert gate_run_lock.release(gate, os.getpid()) == 0


@pytest.mark.parametrize("legacy_entrypoint,legacy_environment_override", [
    ("cli", False), ("make", False), ("make", True),
])
def test_legacy_cleanup_refuses_without_touching_gate_proof_refs_or_registry(
    legacy_cleanup: LegacyCleanup, legacy_entrypoint: str, legacy_environment_override: bool,
) -> None:
    before = legacy_cleanup.snapshot()
    result = legacy_cleanup.invoke(legacy_entrypoint, legacy_environment_override)
    after = legacy_cleanup.snapshot()
    rows = legacy_cleanup.creation.rows()
    assert result.returncode == 2 and after == before and not rows, (
        f"native status={result.returncode}; selected_exists={after['selected_exists']}; "
        f"proof_retained={after['proof'] is not None}; commands={rows}; "
        f"stdout={result.stdout}; stderr={result.stderr}"
    )
    assert "outcome=refused" in result.stderr and "native_status=2" in result.stderr
    assert "agent-cleanup" in result.stderr and "WORKTREE=" in result.stderr
    assert "AGENT_CLEANUP_VALIDATE_ONLY=1" in result.stderr
    assert "Preserve the configured registry" in result.stderr
    assert "full source SHA" in result.stderr and "never pass an empty registry override" in result.stderr
    contract = cast(dict[str, object], json.loads(
        (ROOT / "config/make_target_contract.json").read_text(encoding="utf-8"),
    ))
    entries = cast(list[dict[str, object]], contract["targets"])
    entry = next(item for item in entries if item["name"] == "worktree-cleanup-merged")
    assert entry["make_variables"] == [] and entry["behavior"] == "make worktree-cleanup-merged"
    assert "Native status 2/refused" in cast(str, entry["expected_result"])
    assert "gate_evidence=false" in result.stderr and "release_ready=false" in result.stderr
    assert "removed:" not in result.stdout and "done:" not in result.stdout
    assert gate_run_lock.assert_inactive(legacy_cleanup.gate, os.getpid()) == 1


def _forbidden_process(*arguments: object, **keywords: object) -> NoReturn:
    raise AssertionError("Retired compatibility entry spawned a process")


def test_retired_module_main_has_no_process_or_git_execution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(subprocess, "run", _forbidden_process)
    monkeypatch.setattr(subprocess, "Popen", _forbidden_process)
    monkeypatch.setattr(os, "system", _forbidden_process)
    namespace = runpy.run_path(str(ROOT / "scripts/cleanup_merged_worktrees.py"),
                               run_name="legacy_cleanup_retired_import")
    main = cast(Callable[[], int], namespace["main"])
    assert main() == 2
    result = capsys.readouterr()
    assert not result.out
    assert "outcome=refused" in result.err and "native_status=2" in result.err


def test_retired_module_actual_main_entry_returns_two_without_children(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(subprocess, "run", _forbidden_process)
    monkeypatch.setattr(subprocess, "Popen", _forbidden_process)
    monkeypatch.setattr(os, "system", _forbidden_process)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(ROOT / "scripts/cleanup_merged_worktrees.py"), run_name="__main__")
    assert result.value.code == 2
    assert "outcome=refused" in capsys.readouterr().err
