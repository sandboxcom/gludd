"""Keep legacy cleanup entry points inside native ownership protections."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_worktree_retirement import git as native_git

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("environment_overrides", [False, True])
@pytest.mark.parametrize("target", ["agent-cleanup", "clean-stale-worktrees", "wt-prune-safe"])
@pytest.mark.parametrize("policy", [None, "0", "1", "0 1", "$(shell touch TRIPWIRE)"])
def test_cleanup_targets_reuse_native_owner_with_literal_policy(
    tmp_path: Path, environment_overrides: bool, target: str, policy: str | None,
) -> None:
    tmp_path = tmp_path.resolve()
    native_git(tmp_path, "init", "--quiet")
    recorder = tmp_path / "capture.py"
    recorder.write_text("import json,sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    git = binaries / "git"
    git.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"Path({str(tmp_path / 'forbidden-git')!r}).write_text('called')\n"
        "print('fixture-branch')\n", encoding="utf-8",
    )
    git.chmod(0o700)
    probe_makefile = tmp_path / "probe.mk"
    probe_makefile.write_text(
        f"include {ROOT / 'make/30-ci-and-release.mk'}\n"
        f"include {ROOT / 'make/40-cross-version-and-worktrees.mk'}\n", encoding="utf-8",
    )
    variable = {
        "agent-cleanup": "AGENT_CLEANUP_VALIDATE_ONLY",
        "clean-stale-worktrees": "CLEAN_STALE_WORKTREES_VALIDATE_ONLY",
        "wt-prune-safe": "WT_PRUNE_VALIDATE_ONLY",
    }[target]
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL"}
    }
    environment["PATH"] = str(binaries) + os.pathsep + environment["PATH"]
    selected = tmp_path / "selected worktree"
    registry = tmp_path / "registry-`touch TRIPWIRE`.json"
    result = subprocess.run(
        [
            "make", "--no-print-directory", *(["-e"] if environment_overrides else []),
            "-f", str(probe_makefile), target,
            *([f"{variable}={policy}"] if policy is not None else []),
            "BRANCH=contract/retirement", f"WORKTREE={selected}",
            f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
            f"WT_PRUNE_RETIRE_MERGED={1 if target == 'clean-stale-worktrees' else 0}",
            "WT_PRUNE_RETIRE_VALIDATE_ONLY=1",
            f"UV={shlex.quote(sys.executable)} {shlex.quote(str(recorder))}",
            f"MAKE=make --no-print-directory -f {shlex.quote(str(probe_makefile))}",
        ],
        cwd=tmp_path, env=environment, check=False, capture_output=True, text=True, timeout=20,
    )
    assert not (tmp_path / "TRIPWIRE").exists()
    assert not (tmp_path / "forbidden-git").exists()
    if policy not in {None, "0", "1"}:
        assert result.returncode != 0
        return
    assert result.returncode == 0, result.stdout + result.stderr
    selected_policy = policy if policy is not None else ("0" if target == "wt-prune-safe" else "1")
    argv = ["run", "python", "-m", "scripts.prune_worktrees_safe"]
    if target == "agent-cleanup":
        argv += [
            "--retire-merged", "--branch", "contract/retirement", "--worktree", str(selected),
            "--registry", str(registry), "--retire-validate-only", selected_policy,
        ]
    else:
        argv += ["--registry", str(registry)]
        if selected_policy == "1":
            argv += ["--validate-only"]
    assert json.loads(result.stdout) == argv
