"""Exercise terminal Make guidance with the existing native six-pin fixture."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit import test_gate_failure_promotion as promotion
from tests.unit import test_precommit_formatting_admission as formatting


@pytest.mark.parametrize("failed_owner", ("", *promotion.STRUCTURAL_PROMOTION_NODES, "validator"))
def test_promotion_make_guidance_waits_for_all_required_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_owner: str,
) -> None:
    """Earlier manifest output cannot grant a whole-target positive."""
    native_run = subprocess.run
    results: list[subprocess.CompletedProcess[str]] = []

    def execute(
        arguments: list[str], *, cwd: Path, capture_output: bool, text: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        probe = tmp_path / "record.py"
        source = probe.read_text(encoding="utf-8")
        marker = 'print("PROMOTION_CHILD_TERMINAL phase=" + phase, flush=True)\n'
        assert source.count("raise SystemExit(") == 1
        probe.write_text(source.replace("raise SystemExit(", marker + "raise SystemExit("), encoding="utf-8")
        result = native_run(
            arguments, cwd=cwd, capture_output=capture_output, text=text, timeout=timeout,
        )
        results.append(result)
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(subprocess, "run", execute)
        promotion.test_promotion_checker_executes_both_structural_pins_only_after_valid_manifest(
            tmp_path, failed_owner,
        )

    assert len(results) == 1
    result = results[0]
    combined = result.stdout + result.stderr
    assert "gate_evidence=false release_ready=false" in combined
    passed = "AGENT_NEXT target=check-gate-failure-promotions outcome=passed"
    if failed_owner:
        assert passed not in combined
        assert "AGENT_NEXT target=check-gate-failure-promotions outcome=failed" in combined
        assert "repair" in combined.lower()
        assert result.returncode == 2
        if failed_owner == "validator":
            assert "phase=manifest" in combined
            assert "PROMOTION_CHILD_TERMINAL phase=tests" not in combined
        else:
            assert "phase=eight-pins" in combined
    else:
        assert result.returncode == 0
        assert result.stdout.index(passed) > result.stdout.index("PROMOTION_CHILD_TERMINAL phase=tests")
        assert "complete changed owners" in result.stdout
        assert "eight-pins" in result.stdout


@pytest.mark.parametrize("environment_override", (False, True))
@pytest.mark.parametrize("unstaged", (False, True))
def test_atomicity_guidance_preserves_restored_edits_without_git_stash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    environment_override: bool, unstaged: bool,
) -> None:
    """Real pre-commit private patches do not authorize a Git stash mutation."""
    monkeypatch.setenv("GUARD_CARRIER", "atomicity unrelated carrier  n q t")
    monkeypatch.setenv("UV", "/usr/bin/false")
    inherited = dict(os.environ)
    root, adapter = formatting._native_workspace(tmp_path)
    filename = "owned staged change.txt"
    staged = "intended staged content\n"
    (root / filename).write_text(staged, encoding="utf-8")
    formatting._native_git(root, "add", "--", filename)
    unrelated = root / "anchor.txt"
    if unstaged:
        unrelated.write_text("intentional unrelated edit\n", encoding="utf-8")
    working = unrelated.read_bytes()
    before = formatting._native_identity(root)
    assert formatting._native_git(root, "stash", "list").stdout == ""

    implicit = subprocess.run(
        [sys.executable, "-m", "pre_commit", "run", "trailing-whitespace"],
        cwd=root, env=formatting._native_environment(), capture_output=True, text=True,
        check=False, timeout=30,
    )
    implicit_output = implicit.stdout + implicit.stderr
    assert implicit.returncode == 0, implicit_output
    if unstaged:
        assert "Stashing unstaged files to " in implicit_output, implicit_output
        assert "Restored changes from " in implicit_output, implicit_output
    assert formatting._native_identity(root) == before
    assert unrelated.read_bytes() == working
    assert formatting._native_git(root, "stash", "list").stdout == ""

    hooks = formatting._native_guard(root, adapter, environment_override=environment_override)
    hook_output = hooks.stdout + hooks.stderr
    assert hooks.returncode == 0, hook_output
    calls = formatting._native_calls(adapter)
    assert [call["argv"] for call in calls] == [
        ["run", "pre-commit", "run", hook, "--files", filename]
        for hook in formatting.NATIVE_HOOKS
    ]
    assert all(call["carrier"] == "atomicity unrelated carrier  n q t" for call in calls)
    if unstaged:
        assert "Unstaged files detected." in hook_output, hook_output
    assert formatting._native_identity(root) == before
    assert unrelated.read_bytes() == working
    assert formatting._native_git(root, "show", ":" + filename).stdout == staged
    assert formatting._native_git(root, "stash", "list").stdout == ""

    source = (formatting.ROOT / "make/50-development-and-guardrails.mk").read_text(encoding="utf-8")
    start = source.index("\n_edit-commit-atomicity-guard:") + 1
    rule = source[start:source.index("\n\n", start)]
    makefile = root / "Makefile"
    makefile.write_text(makefile.read_text(encoding="utf-8") + "\n" + rule + "\n", encoding="utf-8")
    result = subprocess.run(
        ["make", "--no-print-directory", *(("-e",) if environment_override else ()),
         "_edit-commit-atomicity-guard"],
        cwd=root, env=formatting._native_environment(), capture_output=True, text=True,
        check=False, timeout=15,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert output.rstrip().endswith("_edit-commit-atomicity-guard: PASS"), output
    assert formatting._native_identity(root) == before
    assert unrelated.read_bytes() == working
    assert formatting._native_git(root, "show", ":" + filename).stdout == staged
    assert formatting._native_git(root, "stash", "list").stdout == ""
    assert dict(os.environ) == inherited
    if unstaged:
        assert "EDIT-COMMIT-ATOMICITY: working tree has unstaged changes." in output
        assert "git-stash-pop" not in output, output
        assert "the commit will NOT include them" not in output, output
        assert "next_owner=worktree-state" in output, output
        assert "make git-staged" in output, output
        assert "checkpoint" in output, output
        assert "Preserve intentional unrelated edits" in output, output
        assert "gate_evidence=false release_ready=false" in output, output
    else:
        assert "EDIT-COMMIT-ATOMICITY:" not in output, output
        assert "AGENT_NEXT" not in output, output
