"""Prepared native regressions for the live self-improvement promotion repair.

Make adapter plans are controlled upstream inputs, not retirement or marker proof.
The alias controls execute the actual recipes against private real Git repositories.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from scripts.check_make_target_contract import _stanzas
from scripts.makefile_layout import compose_makefile

import general_ludd.self_improve.runtime as runtime
from general_ludd.self_improve.managed_runner import SelfImprovePolicyViolation
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard
from general_ludd.self_improve.promotion import (
    ManagedSelfImprovePromotionCoordinator,
    retire_owned_self_improve_worktree,
    self_improve_revision,
    validate_managed_promotion_inputs,
)
from tests.unit.test_legacy_integration_entrypoints import NativeWorkspace, _workspace
from tests.unit.test_managed_self_improve_promotion import (
    _artifact,
    _claim,
    _plan,
    _Repository,
)
from tests.unit.test_self_improve_runtime_private_policy import (
    _prompt as _baseline_prompt,
)
from tests.unit.test_self_improve_runtime_private_policy import (
    _reference as _baseline_reference,
)
from tests.unit.test_self_improve_runtime_private_policy import (
    _task as _baseline_task,
)
from tests.unit.test_self_improve_runtime_private_policy import (
    _write_policy,
)

pytest_plugins = ("tests.unit.test_worktree_retirement",)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _owned_git_identity_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confine ordinary fixture Git before setup without overriding later controls."""
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _make_environment(workspace: NativeWorkspace) -> dict[str, str]:
    """Own recursive Make inputs while preserving unrelated native carriers."""
    environment = {
        key: value for key, value in workspace.env.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "MAKEFILES", "GNUMAKEFLAGS", "PYTEST_ARGS"}
    }
    required = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]
    inherited = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join([*required, *([inherited] if inherited else [])])
    return environment


def _add_live_aliases(workspace: NativeWorkspace, *, namesakes: bool) -> None:
    """Receive the exact native rules and real gate guard, with private inputs."""
    makefile = compose_makefile(ROOT / "Makefile")
    stanzas = _stanzas(makefile)
    targets = ("agent-merge-dev", "agent-merge")
    phony = [
        line for line in makefile.splitlines() if line.startswith(".PHONY:")
        and any(target in line.split()[1:] for target in targets)
    ]
    with (workspace.main / "Makefile").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nGATE_RUN_LOCK := " + str(workspace.gate or workspace.main / ".proof-missing-gate")
            + "\n" + "\n".join(phony) + "\n"
            + "\n\n".join(stanzas[target] for target in (*targets, "_gate-mutation-guard"))
            + "\n"
        )
    (workspace.main / "scripts" / "gate_run_lock.py").write_text(
        (ROOT / "scripts" / "gate_run_lock.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    if namesakes:
        for target in targets:
            (workspace.main / target).write_text("target files cannot suppress refusal\n", encoding="utf-8")


@pytest.mark.parametrize("target", ["agent-merge-dev", "agent-merge"])
@pytest.mark.parametrize("case", ["candidate", "dirty", "locked", "active-gate"])
@pytest.mark.parametrize("environment_precedence", [False, True])
@pytest.mark.parametrize("namesakes", [False, True])
def test_raw_agent_alias_refuses_without_switching_or_merging(
    tmp_path: Path, target: str, case: str, environment_precedence: bool, namesakes: bool,
) -> None:
    """Raw aliases cannot change development, master or the caller's candidate."""
    workspace = _workspace(tmp_path, case)
    _add_live_aliases(workspace, namesakes=namesakes)
    before = workspace.snapshot()
    try:
        result = subprocess.run(
            [
                workspace.make, "--no-print-directory",
                *(["-e"] if environment_precedence else []),
                "-f", "Makefile", target, "BRANCH=agent-owned-peer",
                f"PYTHON={sys.executable}", f"UV={workspace.root / 'bin' / 'uv'}",
                f"MAKE={workspace.root / 'bin' / 'make'}",
                f"GATE_RUN_LOCK={workspace.gate or workspace.main / '.proof-missing-gate'}",
            ],
            cwd=workspace.main, env=_make_environment(workspace), capture_output=True,
            text=True, check=False, timeout=15,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert workspace.snapshot() == before
        assert workspace.recorded() == [], (
            "retired aliases must not start Git or a gate adapter\n" + result.stdout + result.stderr
        )
        output = result.stdout + result.stderr
        assert f"target={target} outcome=retired" in output
        assert "gate_evidence=false" in output
        assert "release_ready=false" in output
        assert "development-merge-forward" in output if target == "agent-merge-dev" else "release-promote" in output
    finally:
        workspace.release_gate()


class NativePlanRunner:
    """Return an upstream cleanup plan through a real, bounded Make invocation."""

    def __init__(self, workspace: NativeWorkspace, branch: str, *, candidate: bool = False) -> None:
        assert workspace.peer is not None
        self.workspace = workspace
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.outputs: list[str] = []
        self.plan = {
            "branch": branch, "worktree": str(workspace.peer),
            "head": workspace.git_at(workspace.peer, "rev-parse", "HEAD"),
            "validate_only": True, "native_ownership_verified": 1, "status": "planned",
        }
        self.makefile = workspace.root / f"native-plan-{'candidate' if candidate else 'root'}.mk"
        payload = shlex.quote(json.dumps(self.plan, sort_keys=True))
        development = workspace.git_at(workspace.main, "rev-parse", "development")
        merge_plan = shlex.quote(
            f"DEVELOPMENT-FAST-FORWARD-VALIDATED source={self.plan['head']} "
            f"before={development} after={self.plan['head']} apply=0"
        )
        state = shlex.quote(json.dumps({
            "branch": branch if candidate else "development", "is_clean": True,
            "path": str(workspace.peer if candidate else workspace.main),
            "head": self.plan["head"] if candidate else development,
        }))
        self.makefile.write_text(
            ".PHONY: agent-cleanup development-fast-forward self-improve-promotion-marker "
            "worktree-state _gate-fresh-check\n"
            "agent-cleanup:\n\t@printf '%s\\n' " + payload + "\n"
            "self-improve-promotion-marker:\n\t@printf '%s\\n' PROMOTION_COMMIT="
            + development + "\n"
            "development-fast-forward:\n\t@printf '%s\\n' " + merge_plan + "\n"
            "worktree-state:\n\t@printf '%s\\n' " + state + "\n"
            "_gate-fresh-check:\n\t@printf '%s\\n' controlled-upstream-acceptance\n",
            encoding="utf-8",
        )

    def run(
        self, target: str, variables: dict[str, str] | None = None, *,
        timeout: int = 120, read_only: bool = False,
    ) -> runtime.MakeResult:
        del read_only
        values = dict(variables or {})
        self.calls.append((target, values))
        command = [
            self.workspace.make, "--no-print-directory", "-f", str(self.makefile),
            target, *(f"{key}={value}" for key, value in values.items()),
        ]
        started = time.monotonic()
        result = subprocess.run(
            command, cwd=self.workspace.main, env=_make_environment(self.workspace),
            capture_output=True, text=True, check=False, timeout=min(timeout, 15),
        )
        self.outputs.append(result.stdout)
        return runtime.MakeResult(
            tuple(command), result.returncode, result.stdout, result.stderr,
            time.monotonic() - started,
        )

    def run_command(self, command: str, *, timeout: int = 900) -> runtime.MakeResult:
        del command, timeout
        raise AssertionError("plan fixture must not execute candidate commands")

    def run_observable(
        self, target: str, variables: dict[str, str], *, timeout: float,
    ) -> runtime.MakeResult:
        del target, variables, timeout
        raise AssertionError("plan fixture must not execute inference")


def test_native_make_plan_zero_never_sets_runtime_cleanup_passed(tmp_path: Path) -> None:
    """A truthful native Make plan cannot certify removal of an extant candidate."""
    workspace = _workspace(tmp_path, "candidate")
    runner = NativePlanRunner(workspace, "agent-owned-peer")
    state = runtime._AttemptState(time.monotonic(), [], [])
    before = workspace.snapshot()

    assert workspace.peer is not None
    result = runtime._cleanup_candidate(
        runner, "agent-owned-peer", state, None,
        worktree=workspace.peer, head=str(runner.plan["head"]),
    )

    assert result.returncode == 2, "the missing actual release must retain native refusal"
    assert any(json.loads(output)["status"] == "planned" for output in runner.outputs if output.strip())
    assert workspace.snapshot() == before
    assert workspace.peer is not None and workspace.peer.exists()
    assert state.cleanup_passed is False, "plan0 is not native retirement"
    assert all(values.get("AGENT_CLEANUP_VALIDATE_ONLY") != "0" for _, values in runner.calls)


async def test_recovered_marker_cannot_complete_after_only_a_cleanup_plan(tmp_path: Path) -> None:
    """A controlled existing-marker result does not clear unverified stale cleanup."""
    workspace = _workspace(tmp_path, "candidate")
    assert workspace.peer is not None
    workspace.git_at(workspace.peer, "branch", "-m", "self-improve-promote-stale-2")
    workspace.git_at(workspace.main, "checkout", "development")
    workspace.git_at(workspace.main, "merge", "--no-ff", "--no-edit", "self-improve-promote-stale-2")
    runner = NativePlanRunner(workspace, "self-improve-promote-stale-2")
    plan = _plan(workspace.main)
    artifact = _artifact(plan)
    repository = _Repository(_claim(artifact, stale_worktree_branch="self-improve-promote-stale-2"))
    coordinator = ManagedSelfImprovePromotionCoordinator(
        repository, root_runner=runner, owner="event-loop-promotion",
        make_runner_factory=lambda _path: pytest.fail("recovery must not create a candidate"),
    )
    before = workspace.snapshot()

    with pytest.raises(RuntimeError):
        await coordinator.promote(
            plan_artifact=plan.to_json(), result_artifact=artifact.to_json(),
            todo_id=plan.todo_id, project_id=plan.project_id,
            repo_root=workspace.main, return_id="RETURN-PROMOTION",
        )

    assert not any(target == "agent-cleanup" for target, _ in runner.calls)
    assert not repository.completed
    assert not repository.abandoned, "unknown stale ownership must retain its durable binding"
    assert workspace.snapshot() == before
    assert workspace.peer is not None and workspace.peer.exists()
    assert all(values.get("AGENT_CLEANUP_VALIDATE_ONLY") != "0" for _, values in runner.calls)


def test_merge_plan_zero_preserves_candidate_before_any_cleanup(tmp_path: Path) -> None:
    """A passing integration plan cannot authorize even a follow-on cleanup call."""
    workspace = _workspace(tmp_path, "candidate")
    runner = NativePlanRunner(workspace, "agent-owned-peer")
    state = runtime._AttemptState(
        time.monotonic(), [], [], commit_count=1, worktree_clean=True,
        expected_head=str(runner.plan["head"]),
    )
    candidate = NativePlanRunner(workspace, "agent-owned-peer", candidate=True)
    assert workspace.peer is not None
    before = workspace.snapshot()

    runtime._finish_candidate_worktree(
        runner, "agent-owned-peer", state, None, merge=True, commands_green=True,
        worktree=workspace.peer, runner=candidate,
    )

    assert [target for target, _ in runner.calls].count("development-fast-forward") == 2
    assert any("DEVELOPMENT-FAST-FORWARD-VALIDATED" in output for output in runner.outputs)
    assert workspace.snapshot() == before
    assert state.cleanup_passed is False
    assert not any(target == "agent-cleanup" for target, _ in runner.calls)


def test_baseline_finally_plan_zero_cannot_return_prepared_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real finally consumer with valid public-policy/prompt inputs."""
    workspace = _workspace(tmp_path, "candidate")
    assert workspace.peer is not None
    runner = NativePlanRunner(workspace, "agent-owned-peer")
    _write_policy(workspace.main)
    guard = SelfImproveRuntimePolicyGuard.load(
        workspace.main, lambda _event: None, SelfImprovePolicyViolation,
    )
    prompt = _baseline_prompt()
    monkeypatch.setattr(
        runtime, "create_worktree", lambda *_args: (workspace.peer, "agent-owned-peer"),
    )
    monkeypatch.setattr(runtime, "build_prompt", lambda *_args: prompt)
    monkeypatch.setattr(runtime, "generate_mechanical_proposal", lambda *_args: None)
    before = workspace.snapshot()

    with pytest.raises(RuntimeError):
        runtime._prepare_policy_bound_context(
            runner, runtime.MakeRunner, guard, _baseline_task(),
            replace(_baseline_reference(), baseline_sha=str(runner.plan["head"])), (),
        )

    assert any(target == "agent-cleanup" for target, _ in runner.calls)
    assert any('"status": "planned"' in output for output in runner.outputs)
    assert workspace.snapshot() == before
    assert workspace.peer.exists()


def _native_marker_coordinator(
    workspace: NativeWorkspace, monkeypatch: pytest.MonkeyPatch,
) -> tuple[ManagedSelfImprovePromotionCoordinator, runtime.MakeRunner]:
    """Run the exact native producer and actual MakeRunner inside owned Git."""
    stanzas = _stanzas(compose_makefile(ROOT / "Makefile"))
    (workspace.main / "Makefile").write_text(
        ".PHONY: self-improve-promotion-marker\n"
        + stanzas["self-improve-promotion-marker"] + "\n",
        encoding="utf-8",
    )
    environment = _make_environment(workspace)
    for key in tuple(os.environ):
        if key not in environment and (
            key.startswith(("GIT_", "GH_", "GITHUB_"))
            or key in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "MAKEFILES", "GNUMAKEFLAGS", "PYTEST_ARGS"}
        ):
            monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    # The real Make binary must run; the fixture shim records only Git here.
    monkeypatch.setenv("PATH", str(Path(workspace.make).parent) + os.pathsep + environment["PATH"])
    runner = runtime.MakeRunner(workspace.main)
    artifact = _artifact(_plan(workspace.main))
    coordinator = ManagedSelfImprovePromotionCoordinator(
        _Repository(_claim(artifact)), root_runner=runner, owner="event-loop-promotion",
    )
    return coordinator, runner


def _marker_inputs(workspace: NativeWorkspace) -> dict[str, str]:
    """Return the exact approved producer inputs independently of its output."""
    plan = _plan(workspace.main)
    artifact = _artifact(plan)
    return {
        "SELF_IMPROVE_PROMOTION_ARTIFACT_DIGEST": artifact.artifact_digest,
        "SELF_IMPROVE_PROMOTION_PLAN_DIGEST": plan.identity_digest,
        "SELF_IMPROVE_PROMOTION_ATTEMPT_DIGEST": plan.attempt_identity_digest,
        "SELF_IMPROVE_PROMOTION_VALIDATE_ONLY": "0",
    }


async def test_actual_make_marker_absence_is_not_a_lookup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GNU Make's actual nonzero absence must remain distinct from lookup errors."""
    workspace = _workspace(tmp_path, "candidate")
    coordinator, runner = _native_marker_coordinator(workspace, monkeypatch)
    plan = _plan(workspace.main)
    artifact = _artifact(plan)
    inputs = validate_managed_promotion_inputs(
        plan_artifact=plan.to_json(), result_artifact=artifact.to_json(),
        todo_id=plan.todo_id, project_id=plan.project_id,
        repo_root=workspace.main, return_id="RETURN-PROMOTION",
    )
    native_absence = runner.run("self-improve-promotion-marker", _marker_inputs(workspace))
    assert native_absence.returncode == 2, native_absence.stdout + native_absence.stderr
    assert native_absence.stdout.splitlines() == ["PROMOTION_ABSENT"]
    before = workspace.snapshot()

    assert await coordinator._find_verified_marker(inputs) is None

    assert workspace.snapshot() == before
    assert runner.operations[-1].returncode == 2


async def test_actual_marker_lookup_does_not_reuse_success_after_ref_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A previous success cannot authorize completion after native history changes."""
    workspace = _workspace(tmp_path, "candidate")
    coordinator, runner = _native_marker_coordinator(workspace, monkeypatch)
    plan = _plan(workspace.main)
    artifact = _artifact(plan)
    inputs = validate_managed_promotion_inputs(
        plan_artifact=plan.to_json(), result_artifact=artifact.to_json(),
        todo_id=plan.todo_id, project_id=plan.project_id,
        repo_root=workspace.main, return_id="RETURN-PROMOTION",
    )
    workspace.git_at(workspace.main, "checkout", "development")
    workspace.git_at(workspace.main, "mv", "development", "owned-development-note")
    workspace.git_at(workspace.main, "commit", "-m", "owned unambiguous marker witness")
    before_marker = workspace.git_at(workspace.main, "rev-parse", "HEAD")
    message = (
        f"Gludd-Self-Improve-Artifact={artifact.artifact_digest}\n"
        f"Gludd-Self-Improve-Plan={plan.identity_digest}\n"
        f"Gludd-Self-Improve-Attempt={plan.attempt_identity_digest}"
    )
    workspace.git_at(workspace.main, "commit", "--allow-empty", "-m", message)
    marked = workspace.git_at(workspace.main, "rev-parse", "HEAD")
    assert await coordinator._find_verified_marker(inputs) == marked
    workspace.git_at(workspace.main, "update-ref", "refs/heads/development", before_marker, marked)
    actual_absence = runner.run("self-improve-promotion-marker", _marker_inputs(workspace))
    assert actual_absence.returncode == 2
    assert actual_absence.stdout.splitlines() == ["PROMOTION_ABSENT"]
    before = workspace.snapshot()

    assert await coordinator._find_verified_marker(inputs) is None

    assert workspace.snapshot() == before
    assert len(runner.operations) == 3, "both coordinator observations must actually execute Make"


def test_native_marker_log_failure_never_becomes_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain the actual Git failure instead of interpreting empty output as absence."""
    workspace = _workspace(tmp_path, "candidate")
    _, runner = _native_marker_coordinator(workspace, monkeypatch)
    shim = workspace.root / "marker-bin"
    shim.mkdir()
    git = shim / "git"
    git.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys\n"
        "if sys.argv[1:2] == ['log']:\n"
        "    print('owned native marker log failure', file=sys.stderr)\n"
        "    raise SystemExit(71)\n"
        "raise SystemExit(subprocess.call([os.environ['LEGACY_REAL_GIT'], *sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    git.chmod(0o700)
    monkeypatch.setenv("PATH", str(shim) + os.pathsep + os.environ["PATH"])
    before = workspace.snapshot()

    result = runner.run("self-improve-promotion-marker", _marker_inputs(workspace))

    assert result.returncode != 0
    assert "owned native marker log failure" in result.stderr
    assert "PROMOTION_ABSENT" not in result.stdout
    assert workspace.snapshot() == before


@pytest.mark.parametrize("target", [
    "self-improve-promotion-marker", "git-show-commit", "repo-status",
    "worktree-state", "_gate-fresh-check",
])
def test_current_read_cache_policy_reobserves_actual_make_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    """Measure cache policy with native Make; controlled output grants no proof."""
    workspace = _workspace(tmp_path, "candidate")
    _, runner = _native_marker_coordinator(workspace, monkeypatch)
    value = workspace.main / ".proof-current-read"
    value.write_text("first\n", encoding="utf-8")
    (workspace.main / "Makefile").write_text(
        f".PHONY: {target}\n{target}:\n\t@cat {shlex.quote(str(value))}\n",
        encoding="utf-8",
    )
    values = {"C": "HEAD"} if target == "git-show-commit" else None
    first = runner.run(target, values, read_only=True)
    value.write_text("second\n", encoding="utf-8")

    second = runner.run(target, values, read_only=True)

    assert first.returncode == second.returncode == 0
    assert first.stdout == "first\n"
    assert second.stdout == "second\n"
    assert len(runner.operations) == 2, "current reads must execute the original owner twice"


async def test_known_coordinator_cleanup_plan_cannot_confirm_removal(tmp_path: Path) -> None:
    """Keep the direct coordinator plan consumer after stale contexts now defer."""
    workspace = _workspace(tmp_path, "candidate")
    assert workspace.peer is not None
    runner = NativePlanRunner(workspace, "agent-owned-peer")
    artifact = _artifact(_plan(workspace.main))
    repository = _Repository(_claim(artifact))
    coordinator = ManagedSelfImprovePromotionCoordinator(
        repository, root_runner=runner, owner="event-loop-promotion",
    )
    before = workspace.snapshot()

    with pytest.raises(RuntimeError, match="cleanup"):
        await coordinator._cleanup_branch(
            "agent-owned-peer", worktree=workspace.peer, head=str(runner.plan["head"]),
        )

    assert any('"status": "planned"' in output for output in runner.outputs)
    assert not repository.completed
    assert not repository.abandoned
    assert workspace.snapshot() == before
    assert workspace.peer.exists()
    assert all(values.get("AGENT_CLEANUP_VALIDATE_ONLY") != "0" for _, values in runner.calls)


def test_patch_equivalence_keeps_committed_sha_after_owned_branch_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real native patch owner receives the immutable commit after branch drift."""
    workspace = _workspace(tmp_path, "candidate")
    assert workspace.peer is not None
    _native_marker_coordinator(workspace, monkeypatch)
    stanzas = _stanzas(compose_makefile(ROOT / "Makefile"))
    (workspace.peer / "Makefile").write_text(
        ".PHONY: repo-status git-patch-equivalence\n"
        + "\n\n".join(stanzas[target] for target in ("repo-status", "git-patch-equivalence")) + "\n",
        encoding="utf-8",
    )
    pinned = workspace.git_at(workspace.peer, "rev-parse", "HEAD")
    (workspace.peer / "peer").write_text("later owned branch mutation\n", encoding="utf-8")
    workspace.git_at(workspace.peer, "add", "peer")
    workspace.git_at(workspace.peer, "commit", "-m", "owned branch drift after candidate pin")
    assert workspace.git_at(workspace.peer, "rev-parse", "HEAD") != pinned
    state = runtime._AttemptState(time.monotonic(), [], [], expected_head=pinned)
    reference = replace(_baseline_reference(), reference_sha=pinned)
    runner = runtime.MakeRunner(workspace.peer)
    before = workspace.snapshot()

    runtime._inspect_committed_candidate(runner, reference, "agent-owned-peer", state, None)

    patch = next(item for item in runner.operations if item.argv[1] == "git-patch-equivalence")
    assert patch.returncode == 0, patch.stdout + patch.stderr
    assert f"PATCH_HEAD={pinned}" in patch.argv
    assert "PATCH_UPSTREAM=" + pinned in patch.argv
    assert "PATCH_LIMIT=1" in patch.argv
    assert f"head={pinned}" in state.patch_identity
    assert f"head={workspace.git_at(workspace.peer, 'rev-parse', 'HEAD')}" not in state.patch_identity
    assert workspace.snapshot() == before


@pytest.mark.parametrize("purpose", ["merged", "self-improve-evaluation"])
@pytest.mark.parametrize("case", [
    "matching", "branch", "worktree", "head", "purpose", "trailing", "extra-line", "bare",
])
def test_active_owner_release_requires_exact_native_qualified_tuple(
    tmp_path: Path, purpose: str, case: str,
) -> None:
    """Measure current native-line consumer parsing, not native-provider eligibility."""
    workspace = _workspace(tmp_path, "candidate")
    assert workspace.peer is not None
    branch, head = "agent-owned-peer", workspace.git_at(workspace.peer, "rev-parse", "HEAD")
    runner = NativePlanRunner(workspace, branch)
    marker_branch = branch + "-foreign" if case == "branch" else branch
    marker_path = workspace.root / "foreign-peer" if case == "worktree" else workspace.peer
    marker_head = "f" * 40 if case == "head" else head
    marker_purpose = ("merged" if purpose == "self-improve-evaluation" else "self-improve-evaluation") \
        if case == "purpose" else purpose
    # This exact format is the frozen native owner's qualified preflight line.
    marker = (
        f"wt-prune-safe: retirement refused: {marker_purpose} owner is active; exact release required"
        f": branch={marker_branch!r} worktree={str(marker_path)!r} head={marker_head}"
    )
    if case == "trailing":
        marker += " "
    elif case == "extra-line":
        marker += "\nunrelated extra output"
    elif case == "bare":
        marker = f"wt-prune-safe: retirement refused: {purpose} owner is active; exact release required"
    runner.makefile.write_text(
        ".PHONY: agent-cleanup\nagent-cleanup:\n\t@printf '%s\\n' "
        + " ".join(shlex.quote(line) for line in marker.split("\n")) + "; exit 2\n",
        encoding="utf-8",
    )
    before = workspace.snapshot()

    outcome = retire_owned_self_improve_worktree(
        runner, branch=branch, worktree=workspace.peer, head=head, purpose=purpose, apply=True,
    )

    assert runner.outputs[0] == marker + "\n"
    assert outcome.steps[0].returncode == 2
    if case == "matching":
        assert [target for target, _ in runner.calls] == ["agent-cleanup", "workstream-unregister"]
    else:
        assert [target for target, _ in runner.calls] == ["agent-cleanup"], (
            "foreign, malformed or old bare text cannot authorize even ownership release"
        )
    assert not outcome.confirmed, "the controlled fixture has no actual native release owner"
    assert workspace.snapshot() == before
    assert workspace.peer.exists()
    assert all(values.get("AGENT_CLEANUP_VALIDATE_ONLY") != "0" for _, values in runner.calls)


@pytest.mark.parametrize("reference", ["HEAD", "owned-ref"])
def test_revision_reader_uses_an_existing_actual_project_make_owner(reference: str) -> None:
    """The real complete project graph must supply the current immutable revision."""
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    expected = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT, env=environment,
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=ROOT, env=environment,
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    assert len(expected) == 40 and branch
    selected = "HEAD" if reference == "HEAD" else f"refs/heads/{branch}"
    rules = _stanzas(compose_makefile(ROOT / "Makefile"))
    assert "git-show-commit" in rules
    runner = runtime.MakeRunner(ROOT)
    native = runner.run("git-show-commit", {"C": selected}, read_only=True)
    assert native.returncode == 0, native.stdout + native.stderr
    lines = native.stdout.splitlines()
    headers = [index for index, line in enumerate(lines) if line == f"--- {selected} summary ---"]
    assert len(headers) == 1, native.stdout
    assert lines[headers[0] + 1] == expected, native.stdout

    actual = self_improve_revision(runner, selected)

    assert actual == expected
    assert runner.operations[-1].argv[1] in rules, "a fabricated Make target is not an owner"


def _private_native_revision_runner(
    workspace: NativeWorkspace, monkeypatch: pytest.MonkeyPatch,
) -> runtime.MakeRunner:
    """Receive the one actual existing revision rule in confined real Git."""
    _, runner = _native_marker_coordinator(workspace, monkeypatch)
    rules = _stanzas(compose_makefile(ROOT / "Makefile"))
    (workspace.main / "Makefile").write_text(
        ".PHONY: git-show-commit\n" + rules["git-show-commit"] + "\n", encoding="utf-8",
    )
    return runner


@pytest.mark.parametrize("reference", ["HEAD", "owned-ref"])
def test_actual_revision_owner_reobserves_head_and_owned_ref_movement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: str,
) -> None:
    """The actual Git producer changes while the same read-only caller stays fresh."""
    workspace = _workspace(tmp_path, "candidate")
    runner = _private_native_revision_runner(workspace, monkeypatch)
    selected = "HEAD" if reference == "HEAD" else "refs/heads/release-candidate/owned"
    first = self_improve_revision(runner, selected)
    assert first == workspace.git_at(workspace.main, "rev-parse", "HEAD")
    workspace.git_at(workspace.main, "commit", "--allow-empty", "-m", "owned native revision change")
    expected = workspace.git_at(workspace.main, "rev-parse", "HEAD")
    assert expected != first
    before = workspace.snapshot()

    second = self_improve_revision(runner, selected)

    assert second == expected
    assert len(runner.operations) == 2
    assert all(result.argv == ("make", "git-show-commit", f"C={selected}") for result in runner.operations)
    assert workspace.snapshot() == before


def test_actual_revision_owner_retains_immutable_full_sha_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An actual immutable read stays cacheable after unrelated HEAD movement."""
    workspace = _workspace(tmp_path, "candidate")
    runner = _private_native_revision_runner(workspace, monkeypatch)
    pinned = workspace.git_at(workspace.main, "rev-parse", "HEAD")
    assert self_improve_revision(runner, pinned) == pinned
    workspace.git_at(workspace.main, "commit", "--allow-empty", "-m", "owned unrelated head movement")
    assert workspace.git_at(workspace.main, "rev-parse", "HEAD") != pinned
    before = workspace.snapshot()

    assert self_improve_revision(runner, pinned) == pinned

    assert len(runner.operations) == 1
    assert workspace.snapshot() == before


def test_actual_revision_owner_propagates_missing_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native unresolved reference cannot become an arbitrary successful hash."""
    workspace = _workspace(tmp_path, "candidate")
    runner = _private_native_revision_runner(workspace, monkeypatch)
    before = workspace.snapshot()

    with pytest.raises(RuntimeError, match="immutable worktree revision failed"):
        self_improve_revision(runner, "refs/heads/owned-absent-revision")

    result = runner.operations[-1]
    assert result.returncode == 2
    assert "owned-absent-revision" in result.stderr
    assert workspace.snapshot() == before


def test_actual_revision_owner_rejects_failure_after_a_valid_log_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second native Git child must succeed even after log emitted a valid SHA."""
    workspace = _workspace(tmp_path, "candidate")
    runner = _private_native_revision_runner(workspace, monkeypatch)
    shim = workspace.root / "revision-bin"
    shim.mkdir()
    git = shim / "git"
    git.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys\n"
        "if sys.argv[1:2] == ['show']:\n"
        "    print('owned native revision show failure', file=sys.stderr)\n"
        "    raise SystemExit(73)\n"
        "raise SystemExit(subprocess.call([os.environ['LEGACY_REAL_GIT'], *sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    git.chmod(0o700)
    monkeypatch.setenv("PATH", str(shim) + os.pathsep + os.environ["PATH"])
    expected = workspace.git_at(workspace.main, "rev-parse", "HEAD")
    before = workspace.snapshot()

    try:
        self_improve_revision(runner, "HEAD")
    except RuntimeError as exc:
        assert "owned native revision show failure" in str(exc)
    else:
        native = runner.operations[-1]
        pytest.fail(
            f"native revision producer masked a required child: status={native.returncode} "
            f"stdout={native.stdout!r} stderr={native.stderr!r}"
        )

    result = runner.operations[-1]
    assert result.returncode == 2
    assert expected in result.stdout, "the first real Git child genuinely emitted its SHA"
    assert "Error 73" in result.stderr
    assert workspace.snapshot() == before


def test_actual_revision_owner_propagates_display_child_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain the existing final tail failure and suppress terminal-success guidance."""
    workspace = _workspace(tmp_path, "candidate")
    runner = _private_native_revision_runner(workspace, monkeypatch)
    shim = workspace.root / "display-bin"
    shim.mkdir()
    tail = shim / "tail"
    tail.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "print('owned native revision display failure', file=sys.stderr)\n"
        "raise SystemExit(74)\n",
        encoding="utf-8",
    )
    tail.chmod(0o700)
    monkeypatch.setenv("PATH", str(shim) + os.pathsep + os.environ["PATH"])
    expected = workspace.git_at(workspace.main, "rev-parse", "HEAD")
    before = workspace.snapshot()

    with pytest.raises(RuntimeError, match="owned native revision display failure"):
        self_improve_revision(runner, "HEAD")

    result = runner.operations[-1]
    assert result.returncode == 2
    assert expected in result.stdout
    assert "Error 74" in result.stderr
    assert "outcome=passed" not in result.stderr
    assert workspace.snapshot() == before


@pytest.mark.parametrize("case", [
    "bare-hash", "missing-row", "short-hash", "wrong-header", "duplicate-header", "immutable-mismatch",
])
def test_revision_consumer_rejects_native_zero_without_one_exact_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """Controlled upstream Make0 never supplies revision proof without its contract."""
    workspace = _workspace(tmp_path, "candidate")
    runner = _private_native_revision_runner(workspace, monkeypatch)
    selected = "a" * 40 if case == "immutable-mismatch" else "HEAD"
    header = f"--- {selected} summary ---"
    output = {
        "bare-hash": ["b" * 40],
        "missing-row": [header],
        "short-hash": [header, "b" * 39],
        "wrong-header": ["--- refs/heads/foreign summary ---", "b" * 40],
        "duplicate-header": [header, "b" * 40, header, "b" * 40],
        "immutable-mismatch": [header, "b" * 40],
    }[case]
    (workspace.main / "Makefile").write_text(
        ".PHONY: git-show-commit\ngit-show-commit:\n\t@printf '%s\\n' "
        + " ".join(shlex.quote(line) for line in output) + "\n", encoding="utf-8",
    )
    before = workspace.snapshot()

    with pytest.raises(RuntimeError, match="lacks one exact native summary"):
        self_improve_revision(runner, selected)

    result = runner.operations[-1]
    assert result.returncode == 0, "the controlled native Make provider genuinely returned zero"
    assert result.stdout.splitlines() == output
    assert workspace.snapshot() == before


def _retirement_git(root: Path, *arguments: str) -> str:
    """Use native Git only for this canonical private fixture."""
    return subprocess.run(
        ["git", *arguments], cwd=root, env=dict(os.environ), check=True,
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()


def _real_native_retirement_runner(
    root: Path, registry: Path, monkeypatch: pytest.MonkeyPatch,
) -> runtime.MakeRunner:
    """Bind the actual MakeRunner to current literal owners and installed Python."""
    for key in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "MAKEFILES", "GNUMAKEFLAGS", "PYTEST_ARGS"):
        monkeypatch.delenv(key, raising=False)
    pythonpath = os.pathsep.join((str(ROOT), str(ROOT / "src")))
    values = {
        "UV": os.environ.get("UV", "uv"), "UV_PROJECT_ENVIRONMENT": sys.prefix,
        "UV_PYTHON": sys.executable, "UV_NO_SYNC": "1", "UV_PYTHON_DOWNLOADS": "never",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": pythonpath,
        "PYTHON": sys.executable, "SYSTEM_PYTHON": sys.executable,
        "ACTIVE_WORKSTREAM_REGISTRY": str(registry),
    }
    probe = (
        "import hashlib,json,os,sys;from pathlib import Path;"
        "import scripts.prune_worktrees_safe as prune;import scripts.workstream_registry as registry;"
        "print(json.dumps({'cwd':str(Path.cwd().resolve()),'python':str(Path(sys.executable).resolve()),"
        "'pythonpath':os.environ.get('PYTHONPATH'),'prune':str(Path(prune.__file__).resolve()),"
        "'registry':str(Path(registry.__file__).resolve()),"
        "'prune_sha256':hashlib.sha256(Path(prune.__file__).read_bytes()).hexdigest(),"
        "'registry_sha256':hashlib.sha256(Path(registry.__file__).read_bytes()).hexdigest()}))"
    )
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("PATH", str(Path(sys.prefix) / "bin") + os.pathsep + os.environ["PATH"])
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    for name in ("Makefile", "make", "scripts", "src", "config", ".agents"):
        source = ROOT / name
        destination = root / name
        assert not destination.exists() and not destination.is_symlink()
        destination.symlink_to(source, target_is_directory=source.is_dir())
        assert destination.resolve(strict=True) == source
    probe_file = root / ".native-inputs.mk"
    probe_file.write_text(
        "\n".join(f"export {key} := {value}" for key, value in values.items()) + "\n"
        + ".PHONY: fixture-native-inputs fixture-native-registry-inputs\n"
        + "fixture-native-inputs:\n\t@$(UV) run python -c " + shlex.quote(probe) + "\n"
        + "fixture-native-registry-inputs:\n\t@UV_NO_SYNC=0 $(UV) run --no-project python -c "
        + shlex.quote(probe) + "\n",
        encoding="utf-8",
    )
    for target in ("fixture-native-inputs", "fixture-native-registry-inputs"):
        observed = subprocess.run(
            ["make", "--no-print-directory", "-f", str(probe_file), target],
            cwd=root, env=dict(os.environ), capture_output=True, text=True, check=False, timeout=60,
        )
        assert observed.returncode == 0, observed.stdout + observed.stderr
        identity = json.loads(observed.stdout)
        assert identity["cwd"] == str(root)
        assert identity["python"] == str(Path(sys.executable).resolve())
        assert identity["pythonpath"] == pythonpath
        for name, filename in (("prune", "prune_worktrees_safe.py"), ("registry", "workstream_registry.py")):
            source = ROOT / "scripts" / filename
            assert identity[name] == str(source)
            assert identity[name + "_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    return runtime.MakeRunner(root)


def _native_retirement_snapshot(root: Path, target: Path, registry: Path) -> dict[str, object]:
    """Observe refs, index bytes, protected evidence and the actual registry."""
    root_admin = Path(_retirement_git(root, "rev-parse", "--absolute-git-dir"))
    target_admin = Path(_retirement_git(target, "rev-parse", "--absolute-git-dir"))
    return {
        "refs": _retirement_git(root, "show-ref"), "root_head": _retirement_git(root, "rev-parse", "HEAD"),
        "root_index": (root_admin / "index").read_bytes(), "target_index": (target_admin / "index").read_bytes(),
        "locked": (target_admin / "locked").read_bytes() if (target_admin / "locked").is_file() else None,
        "registry": registry.read_bytes(),
        "files": {str(path.relative_to(target)): path.read_bytes() for path in target.rglob("*") if path.is_file()},
    }


@pytest.mark.parametrize("purpose", ["self-improve-evaluation", "merged"])
@pytest.mark.parametrize("case", ["eligible", "wrong-branch", "protected"])
def test_real_native_caller_retirement_lifecycle_and_refusal(
    evaluation_checkout: tuple[Path, Path, Path, str], monkeypatch: pytest.MonkeyPatch,
    purpose: str, case: str,
) -> None:
    """Measure the actual current caller, MakeRunner, native registry and pruner together."""
    root, target, registry, head = evaluation_checkout
    branch = "self-improve-codex-native-evaluation"
    if purpose == "merged":
        _retirement_git(root, "merge", "--ff-only", branch)
    runner = _real_native_retirement_runner(root, registry, monkeypatch)
    if case == "protected":
        _retirement_git(root, "worktree", "lock", "--reason", "owned caller protection", str(target))
    before = _native_retirement_snapshot(root, target, registry)
    selected = branch + "-foreign" if case == "wrong-branch" else branch
    state = runtime._AttemptState(time.monotonic(), [], [], expected_head=head)

    try:
        result = runtime._cleanup_candidate(
            runner, selected, state, None, worktree=target, head=head, purpose=purpose,
        )
        calls = runner.operations
        assert state.cleanup_attempted
        if case != "eligible":
            assert result.returncode == 2
            expected_reason = (
                "WORKTREE registration and BRANCH must match exactly" if case == "wrong-branch"
                else "git worktree lock protects this owner"
            )
            assert expected_reason in result.stdout, result.stdout + result.stderr
            assert not state.cleanup_passed
            assert [step.argv[1] for step in calls] == ["agent-cleanup"]
            assert _native_retirement_snapshot(root, target, registry) == before
            assert target.is_dir()
            return

        assert state.cleanup_passed, result.stdout + result.stderr
        assert result.returncode == 0
        assert [step.argv[1] for step in calls] == [
            "agent-cleanup", "workstream-unregister", "agent-cleanup", "agent-cleanup", "git-show-commit",
        ]
        assert [step.returncode for step in calls] == [2, 0, 0, 0, 0]
        expected_refusal = (
            f"wt-prune-safe: retirement refused: {purpose} owner is active; exact release required: "
            f"branch={branch!r} worktree={str(target)!r} head={head}"
        )
        assert calls[0].stdout.splitlines() == [expected_refusal]
        assert "phase=cleanup action=remove-worktree status=dry-run" in calls[0].stderr
        release = json.loads(calls[1].stdout)
        assert release == {
            "branch": branch, "worktree": str(target), "head": head,
            "status": "released", "native_ownership_verified": 1,
        }
        plan = json.loads(calls[2].stdout)
        assert plan["status"] == "planned" and plan["validate_only"] is True
        assert "phase=cleanup action=remove-worktree status=dry-run" in calls[2].stderr
        removed = json.loads(calls[3].stdout.splitlines()[-1])
        assert removed["status"] == "removed" and removed["validate_only"] is False
        assert removed["branch"] == branch and removed["worktree"] == str(target) and removed["head"] == head
        assert removed["native_ownership_verified"] == 1
        assert f"C=refs/heads/{branch}" in calls[-1].argv
        assert not target.exists() and not target.is_symlink()
        assert _retirement_git(root, "rev-parse", f"refs/heads/{branch}") == head
        assert _retirement_git(root, "show-ref") == before["refs"]
        root_admin = Path(_retirement_git(root, "rev-parse", "--absolute-git-dir"))
        assert (root_admin / "index").read_bytes() == before["root_index"]
        retained = json.loads(registry.read_text(encoding="utf-8"))["workstreams"][branch]
        assert retained["status"] == "released" and retained["head"] == head
        assert retained["worktree"] == str(target)
        archives = list((root / ".git" / "gludd-release-evidence").rglob("failed-proof.log"))
        assert len(archives) == 1 and archives[0].read_bytes() == b"FAILED original proof\n"
    finally:
        if case == "protected":
            _retirement_git(root, "worktree", "unlock", str(target))
