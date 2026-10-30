"""Native refusal controls for the retired, unguarded integration entrypoints."""

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.check_make_target_contract import _stanzas
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts" / "worktree_merge_all.py"

RECORDER = r'''
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["LEGACY_FIXTURE_ROOT"]).resolve()
cwd = Path.cwd().resolve()
if not cwd.is_relative_to(root):
    raise SystemExit("fixture tool refused an outside-root command")
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with Path(os.environ["LEGACY_FIXTURE_EVENTS"]).open("a") as stream:
    stream.write(json.dumps({"tool": tool, "args": args, "cwd": str(cwd)}) + "\n")
if tool == "git":
    if args == ["worktree", "list", "--porcelain"] and os.environ["LEGACY_CASE"] == "inventory-error":
        print("owned inventory failure", file=sys.stderr)
        raise SystemExit(71)
    raise SystemExit(subprocess.call([os.environ["LEGACY_REAL_GIT"], *args]))
if tool == "uv":
    assert args[:2] == ["run", "python"], args
    os.execve(sys.executable, [sys.executable, *args[2:]], dict(os.environ))
if tool == "make":
    raise SystemExit(17 if os.environ["LEGACY_CASE"] == "retirement-error" else 0)
raise SystemExit("unknown fixture tool")
'''


@dataclass
class NativeWorkspace:
    root: Path
    main: Path
    peer: Path | None
    git: str
    make: str
    env: dict[str, str] = field(repr=False)
    events: Path
    gate: Path | None = None

    def git_at(self, path: Path, *args: str) -> str:
        result = subprocess.run(
            [self.git, *args], cwd=path, env=self.env,
            capture_output=True, text=True, check=True, timeout=10,
        )
        return result.stdout.strip()

    def snapshot(self) -> dict[str, object]:
        state: dict[str, object] = {}
        for path in [self.main, *([self.peer] if self.peer else [])]:
            admin = Path(self.git_at(path, "rev-parse", "--absolute-git-dir"))
            state[str(path)] = {
                "head": self.git_at(path, "rev-parse", "HEAD"),
                "branch": self.git_at(path, "symbolic-ref", "HEAD"),
                "refs": self.git_at(path, "for-each-ref", "--format=%(refname) %(objectname)"),
                "index": (admin / "index").read_bytes(),
                "admin": {
                    name: (admin / name).read_bytes() if (admin / name).is_file() else None
                    for name in ("HEAD", "ORIG_HEAD", "MERGE_HEAD", "MERGE_MSG", "locked")
                },
                "files": {
                    str(item.relative_to(path)): item.read_bytes()
                    for item in path.rglob("*")
                    if item.is_file() and ".git" not in item.relative_to(path).parts[:-1]
                },
            }
        return state

    def run(self, entry: str, *, environment_precedence: bool = False) -> subprocess.CompletedProcess[str]:
        if entry == "script":
            command = [sys.executable, str(self.main / "scripts" / "worktree_merge_all.py")]
        else:
            command = [
                self.make, "--no-print-directory",
                *(["-e"] if environment_precedence else []), "-f", "Makefile", entry,
                f"PYTHON={sys.executable}", f"UV={self.root / 'bin' / 'uv'}",
                f"MAKE={self.root / 'bin' / 'make'}",
            ]
        return subprocess.run(
            command, cwd=self.main, env=self.env, check=False,
            capture_output=True, text=True, timeout=15,
        )

    def recorded(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def release_gate(self) -> None:
        if self.gate is not None:
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "gate_run_lock.py"),
                 "release", str(self.gate), str(os.getpid())],
                cwd=self.main, env=self.env, capture_output=True, text=True,
                check=True, timeout=10,
            )


def _workspace(tmp_path: Path, case: str) -> NativeWorkspace:
    real_git, real_make = shutil.which("git"), shutil.which("make")
    assert real_git is not None and real_make is not None
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("GIT_", "GH_", "GITHUB_"))
        and key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "PYTEST_ARGS"}
    }
    main, bin_path = tmp_path / "main", tmp_path / "bin"
    main.mkdir()
    bin_path.mkdir()
    private_home = tmp_path / "home"
    private_home.mkdir()
    env.update({
        "HOME": str(private_home), "XDG_CONFIG_HOME": str(private_home),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file", "LEGACY_FIXTURE_ROOT": str(tmp_path),
        "LEGACY_FIXTURE_EVENTS": str(tmp_path / "events.jsonl"),
        "LEGACY_REAL_GIT": real_git, "LEGACY_CASE": case,
    })
    workspace = NativeWorkspace(tmp_path, main, None, real_git, real_make, env, tmp_path / "events.jsonl")
    workspace.git_at(main, "-c", "init.templateDir=", "init", "--initial-branch=master")
    workspace.git_at(main, "config", "user.name", "Owned native fixture")
    workspace.git_at(main, "config", "user.email", "fixture@example.invalid")
    (main / "note").write_text("base\n")
    workspace.git_at(main, "add", "note")
    workspace.git_at(main, "commit", "-m", "owned base")
    workspace.git_at(main, "checkout", "-b", "development")
    (main / "development").write_text("development candidate\n")
    workspace.git_at(main, "add", "development")
    workspace.git_at(main, "commit", "-m", "owned development")
    workspace.git_at(main, "checkout", "-b", "release-candidate/owned", "master")
    (main / "candidate").write_text("candidate branch must not receive a bulk merge\n")
    workspace.git_at(main, "add", "candidate")
    workspace.git_at(main, "commit", "-m", "owned candidate")
    info = main / ".git" / "info"
    info.mkdir(exist_ok=True)
    (info / "exclude").write_text(
        "Makefile\nscripts/\n.proof-*\nworktree-merge-all\ndevelopment-merge-to-master\n"
    )
    for target in ("worktree-merge-all", "development-merge-to-master"):
        (main / target).write_text("same-name file must not suppress native refusal\n")
    (main / ".proof-checkpoint").write_text("retain main checkpoint\n")
    (main / ".proof-owner-note").write_text("retain this unrelated owner note\n")
    if case == "active-gate":
        workspace.gate = main / ".proof-active-gate"
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "gate_run_lock.py"),
             "acquire", str(workspace.gate), str(os.getpid())],
            cwd=main, env=env, capture_output=True, text=True, check=True, timeout=10,
        )
        env["GATE_RUN_LOCK"] = str(workspace.gate)
    if case != "empty":
        peer = tmp_path / "peer"
        workspace.git_at(main, "worktree", "add", "-b", "agent-owned-peer", str(peer), "master")
        workspace.peer = peer
        (peer / "peer").write_text("peer committed source\n")
        workspace.git_at(peer, "add", "peer")
        workspace.git_at(peer, "commit", "-m", "owned peer")
        (peer / ".proof-checkpoint").write_text("retain peer checkpoint\n")
        if case == "dirty":
            (peer / "note").write_text("foreign dirty source must never be staged\n")
            (peer / "foreign").write_text("foreign untracked source\n")
        if case == "locked":
            workspace.git_at(main, "worktree", "lock", "--reason", "owned active peer", str(peer))
    scripts = main / "scripts"
    scripts.mkdir()
    wrapper = (
        "import runpy\n"
        f"namespace = runpy.run_path({str(SOURCE)!r}, run_name='owned_legacy_entrypoint')\n"
        "entry = namespace['main']\n"
        f"entry.__globals__['MAIN_CHECKOUT'] = {str(main)!r}\n"
        f"entry.__globals__['WORKTREE_ROOT'] = {str(tmp_path)!r}\n"
        "raise SystemExit(entry())\n"
    )
    (scripts / "worktree_merge_all.py").write_text(wrapper)
    makefile = compose_makefile(ROOT / "Makefile")
    stanzas = _stanzas(makefile)
    targets = ("worktree-merge-all", "development-merge-to-master")
    phony = [
        line for line in makefile.splitlines() if line.startswith(".PHONY:")
        and any(target in line.split()[1:] for target in targets)
    ]
    (main / "Makefile").write_text(
        ".PHONY: merge-ready\n" + "\n".join(phony) + "\n"
        + "\n\n".join(stanzas[target] for target in targets)
        + "\n\nmerge-ready:\n\t@:\n"
    )
    for tool in ("git", "make", "uv"):
        recorder = bin_path / tool
        recorder.write_text(f"#!{sys.executable}\n" + RECORDER)
        recorder.chmod(0o700)
    workspace.events.write_text("")
    env["PATH"] = str(bin_path) + os.pathsep + env["PATH"]
    return workspace


@pytest.mark.parametrize("entry", ["script", "worktree-merge-all", "development-merge-to-master"])
@pytest.mark.parametrize("case", [
    "candidate", "dirty", "locked", "active-gate", "inventory-error", "retirement-error", "empty",
])
@pytest.mark.parametrize("environment_precedence", [False, True])
def test_retired_entrypoint_refuses_without_git_or_cleanup(
    tmp_path: Path, entry: str, case: str, environment_precedence: bool,
) -> None:
    """Every old destructive context now refuses without consulting Git or cleanup."""
    workspace = _workspace(tmp_path, case)
    before = workspace.snapshot()

    try:
        result = workspace.run(entry, environment_precedence=environment_precedence)

        assert result.returncode == 2, result.stdout + result.stderr
        assert workspace.snapshot() == before, "HEAD, index, refs, source, locks and checkpoints must survive"
        assert workspace.recorded() == [], "retirement must not run inventory, merge, autocommit or prune"
        output = result.stdout + result.stderr
        assert "AGENT_NEXT" in output
        assert "outcome=retired" in output
        assert "gate_evidence=false" in output
        assert "release_ready=false" in output
        if entry == "development-merge-to-master":
            assert "release-promote" in output
            assert "REVIEWED_HEAD_INTEGRATION_RECEIPT" in output
        else:
            assert "development-merge-forward" in output
            assert "SOURCE" in output
            assert "WORKTREE" in output
    finally:
        workspace.release_gate()


@pytest.mark.parametrize("run_name", ["owned_import", "__main__"])
def test_retired_module_has_no_process_side_effects(
    run_name: str, capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise both native module entry branches without letting legacy code spawn."""
    with patch("subprocess.run") as child:
        child.return_value.returncode = 71
        child.return_value.stdout = ""
        child.return_value.stderr = "owned refusal witness"
        if run_name == "__main__":
            with pytest.raises(SystemExit) as stopped:
                runpy.run_path(str(SOURCE), run_name=run_name)
            status = stopped.value.code
        else:
            namespace = runpy.run_path(str(SOURCE), run_name=run_name)
            status = namespace["main"]()

    assert status == 2
    child.assert_not_called()
    assert "outcome=retired" in capsys.readouterr().err
