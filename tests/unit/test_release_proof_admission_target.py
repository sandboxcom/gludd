"""Behavioral contracts for cheap, head-independent final-proof admission."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
PHASES = (
    "disk-cleanup-preflight", "gate-admission-config", "check-task-registration",
    "check-task-integrity", "check-gate-failure-promotions",
    "check-resource-ownership", "check-test-env-writes", "test-files",
    "_pytest-runtime-compatibility-admission",
)


def _recipe() -> str:
    source = compose_makefile(ROOT / "Makefile")
    marker = "\nrelease-proof-admission:\n"
    assert marker in source, "Missing final-proof admission owner"
    return source.split(marker, 1)[1].split("\n\n", 1)[0]


def _run(
    tmp_path: Path, *, mode: str = "0", failed_phase: str = "",
    claimed_head: str = "a" * 40, native_focus: bool = False, task: str = "",
) -> subprocess.CompletedProcess[str]:
    targets = "".join(
        f"{phase}:\n\t@printf '%s\\n' '{phase}' >> phases.log\n"
        f"\t@test '{phase}' != '$(FAILED_PHASE)'\n"
        + (
            "\t@printf '%s\\n' '$(TESTFILES)' '$(PYTEST_ARGS)' > source-contract-selection\n"
            if phase == "test-files" else ""
        )
        for phase in PHASES
    )
    uv = "true"
    recipe = _recipe()
    if native_focus:
        selected_uv = tmp_path / "selected-uv"
        selected_uv.write_text(
            '#!/bin/sh\n[ "$1" = run ] || exit 91\nshift\n'
            '[ "$1" = python ] || exit 92\nshift\n'
            f'exec {shlex.quote(sys.executable)} "$@"\n', encoding="utf-8",
        )
        selected_uv.chmod(0o700)
        uv = shlex.quote(str(selected_uv))
        recipe = recipe.replace("scripts/item_progress.py", str(ROOT / "scripts/item_progress.py"))
        (tmp_path / "TASKS.md").write_text(
            "v0.12.0 milestone is the exact task set F12.1-F12.3\n"
            "- [ ] F12.1 — primary | status: pending\n", encoding="utf-8",
        )
    (tmp_path / "Makefile").write_text(
        f"RELEASE_PROOF_ADMISSION_VALIDATE_ONLY ?= 0\nUV := {uv}\n"
        + "release-proof-admission:\n" + recipe + "\n\n" + targets
        + "\nrun-watched:\n\t@printf '%s\\n' '$(CMD)' '$(MAX_SECS)' '$(STALL_SECS)' "
        "'$(OBSERVED_HEARTBEAT_SECS)' > runtime-watch\n\t@$(CMD)\n",
        encoding="utf-8",
    )
    selected_make = shutil.which("make")
    assert selected_make is not None
    return subprocess.run(
        [selected_make, "--no-print-directory", f"MAKE={selected_make}", "release-proof-admission",
         f"RELEASE_PROOF_ADMISSION_VALIDATE_ONLY={mode}",
         f"FAILED_PHASE={failed_phase}", f"CLAIMED_HEAD={claimed_head}", f"WORK_ITEM_ID={task}",
         f"UV={uv}"],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=30,
    )



def test_runtime_admission_uses_original_selected_runtime_and_native_ninety_sixty_owner(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    command, total, quiet, heartbeat = (tmp_path / "runtime-watch").read_text().splitlines()
    selected_make = shutil.which("make")
    assert selected_make is not None
    assert shlex.split(command) == [selected_make, "--no-print-directory", "_pytest-runtime-compatibility-admission"]
    assert (total, quiet, heartbeat) == ("90", "60", "10")
    assert (tmp_path / "phases.log").read_text().splitlines() == list(PHASES)
    assert not (tmp_path / ".gate-status").exists()
    owner = _recipe()
    assert "UV_PYTHON=3.11" not in owner
    assert "sync " not in owner and "install " not in owner



@pytest.mark.parametrize("task", ["", "not-an-active-task", "F12.1"])
def test_runtime_prepares_real_focus_before_any_watched_child(tmp_path: Path, task: str) -> None:
    result = _run(tmp_path, native_focus=True, task=task)
    if task == "F12.1":
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "runtime-watch").exists()
        assert (tmp_path / ".gludd" / "item-progress.json").exists()
    else:
        assert result.returncode != 0
        assert not (tmp_path / "runtime-watch").exists()
        assert not (tmp_path / ".gludd" / "item-progress.json").exists()
        assert (tmp_path / "phases.log").read_text().splitlines() == list(PHASES[:-1])
        assert "WORK_ITEM_ID" in result.stderr or "active task" in result.stderr
        assert "PROOF-ADMISSION-BLOCKED phase=_pytest-runtime-compatibility-admission" in result.stdout



@pytest.mark.parametrize("owner", ["release-proof-admission", "gate-resume"])
@pytest.mark.parametrize("syntax", ["make-function", "shell-substitution", "backticks", "quotes"])
def test_plan_refuses_literal_task_injection_without_evaluation_or_children(
    tmp_path: Path, owner: str, syntax: str,
) -> None:
    tripwire = tmp_path / "literal-task-evaluated"
    values = {
        "make-function": f"$(shell touch {tripwire})",
        "shell-substitution": f"$$(touch {tripwire})",
        "backticks": f"`touch {tripwire}`",
        "quotes": f'x"; touch {tripwire}; : "',
    }
    (tmp_path / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
    result = subprocess.run(
        ["make", "-I", str(ROOT), "-f", str(ROOT / "Makefile"), owner,
         "RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=1", "GATE_BACKGROUND_VALIDATE_ONLY=1",
         "GATE_TEST_PROFILE=release", "GATE_MAX_FILES_PER_BATCH=16", "GATE_BATCH_WORKERS=1",
         "PYTEST_CMD=", "PYTEST_ARGS=", "PYTEST_ADDOPTS=", "MYPY_MAX=0",
         f"WORK_ITEM_ID={values[syntax]}"],
        cwd=tmp_path, check=False, capture_output=True, text=True, timeout=20,
    )
    assert not tripwire.exists(), result.stdout + result.stderr
    assert result.returncode != 0
    assert "WORK_ITEM_ID" in result.stdout + result.stderr
    assert not (tmp_path / ".gate-logs").exists()
    assert not (tmp_path / ".gludd").exists()



def test_facade_contract_does_not_advertise_the_fixed_runtime_heartbeat() -> None:
    import json

    contract = json.loads((ROOT / "config/make_target_contract.json").read_text(encoding="utf-8"))
    assert isinstance(contract, dict)
    targets = contract["targets"]
    assert isinstance(targets, list)
    for target in targets:
        assert isinstance(target, dict)
        if target.get("name") == "release-proof-admission":
            variables = target["make_variables"]
            assert isinstance(variables, list)
            assert "OBSERVED_HEARTBEAT_SECS" not in variables
            behavior = target["behavior"]
            assert isinstance(behavior, str)
            assert "OBSERVED_HEARTBEAT_SECS" not in behavior
            return
    pytest.fail("Missing existing release-proof-admission contract")


def test_plan_mode_does_not_run_owners_or_claim_admission(tmp_path: Path) -> None:
    result = _run(tmp_path, mode="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROOF-ADMISSION-PLAN" in result.stdout
    assert "PROOF-ADMISSION-PASS" not in result.stdout
    assert not (tmp_path / "phases.log").exists()


def test_invalid_mode_stops_before_any_owner(tmp_path: Path) -> None:
    result = _run(tmp_path, mode="yes")
    assert result.returncode != 0
    assert not (tmp_path / "phases.log").exists()


@pytest.mark.parametrize("failed_phase", PHASES)
def test_failed_owner_stops_later_work_without_proof_or_success(
    tmp_path: Path, failed_phase: str,
) -> None:
    result = _run(tmp_path, failed_phase=failed_phase)
    assert result.returncode != 0
    assert (tmp_path / "phases.log").read_text().splitlines() == list(
        PHASES[:PHASES.index(failed_phase) + 1]
    )
    assert f"PROOF-ADMISSION-BLOCKED phase={failed_phase}" in result.stdout
    assert "PROOF-ADMISSION-PASS" not in result.stdout
    assert not (tmp_path / ".gate-status").exists()
    assert not (tmp_path / "attestation.json").exists()


def test_changed_head_does_not_clear_an_unchanged_capacity_blocker(tmp_path: Path) -> None:
    for head in ("a" * 40, "b" * 40):
        result = _run(tmp_path, failed_phase=PHASES[0], claimed_head=head)
        assert result.returncode != 0
        assert "PROOF-ADMISSION-BLOCKED phase=disk-cleanup-preflight" in result.stdout
    assert (tmp_path / "phases.log").read_text().splitlines() == [PHASES[0], PHASES[0]]


def test_pass_runs_all_owners_in_order_but_is_not_release_proof(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "phases.log").read_text().splitlines() == list(PHASES)
    assert "PROOF-ADMISSION-PASS admitted_for_proof=true release_ready=false gate_evidence=false" in result.stdout
    assert not (tmp_path / ".gate-status").exists()
    assert not (tmp_path / "attestation.json").exists()


@pytest.mark.parametrize("native_focus", (False, True))
@pytest.mark.parametrize("mutation", ("owned", "omitted", "opposite"))
def test_admission_probe_owns_selected_uv_through_real_recursive_make(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_focus: bool, mutation: str,
) -> None:
    """The original focus/phase assertions must detect ambient UV execution."""
    tripwire = tmp_path / "ambient-uv-ran"
    poison = tmp_path / "ambient recorder.py"
    poison.write_text(
        "from pathlib import Path\n"
        f"Path({str(tripwire)!r}).write_text('ambient UV')\nraise SystemExit(86)\n",
        encoding="utf-8",
    )
    ambient_uv = f"{shlex.quote(sys.executable)} {shlex.quote(str(poison))}"
    native_run = subprocess.run
    inherited = os.environ.copy()
    invocations: list[list[str]] = []

    def execute(
        arguments: list[str], *, cwd: Path, capture_output: bool, text: bool,
        check: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output and text and not check and timeout == 30
        command = arguments.copy()
        if mutation == "owned":
            assert len([arg for arg in command if arg.startswith("UV=")]) == 1
        else:
            command = [arg for arg in command if not arg.startswith("UV=")]
            if mutation == "opposite":
                command.append(f"UV={ambient_uv}")
        invocations.append(command)
        carrier = tmp_path / "carrier.mk"
        literal_arguments = shlex.join(command[1:]).replace("$", "$$")
        carrier.write_text(f"all:\n\t@$(MAKE) {literal_arguments}\n", encoding="utf-8")
        environment = inherited.copy()
        environment["UV"] = ambient_uv
        return native_run(
            [arguments[0], "--no-print-directory", *(["-e"] if not native_focus else []),
             "-f", str(carrier), f"MAKE={arguments[0]}", f"UV={ambient_uv}"],
            cwd=cwd, env=environment, capture_output=True, text=True,
            check=False, timeout=30,
        )

    def original_probe() -> None:
        if native_focus:
            test_runtime_prepares_real_focus_before_any_watched_child(tmp_path, "F12.1")
        else:
            test_runtime_admission_uses_original_selected_runtime_and_native_ninety_sixty_owner(tmp_path)

    with monkeypatch.context() as scoped:
        scoped.setattr(subprocess, "run", execute)
        if mutation == "owned":
            original_probe()
        else:
            with pytest.raises(AssertionError):
                original_probe()
    assert len(invocations) == 1
    if mutation == "owned":
        assert len([arg for arg in invocations[0] if arg.startswith("UV=")]) == 1
        assert not tripwire.exists()
    elif native_focus or mutation == "opposite":
        assert tripwire.exists()
    assert subprocess.run is native_run
    assert os.environ == inherited

def test_source_contract_admission_keeps_original_nodes_and_warning_policy(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    selected, arguments = (tmp_path / "source-contract-selection").read_text().splitlines()
    assert shlex.split(selected) == [
        "tests/unit/test_guardrails.py::TestBashGuardrailPrompting::test_agents_md_lists_make_targets",
        "tests/unit/test_guardrails.py::TestTDDGuardrail::test_agents_md_tdd_lists_workflow_steps",
        "tests/unit/test_guardrails.py::TestGuardrailIntegrity::test_agents_md_has_guardrail_integrity_policy",
        "tests/unit/test_guardrails.py::TestTaskCompletionGuardrail::test_agents_md_has_completion_policy",
        "tests/unit/test_guardrails.py::TestTaskCompletionGuardrail::test_agents_md_completion_policy_forbids_early_stop",
        "tests/unit/test_no_home_directory_access.py::test_agents_md_documents_allowed_boundary",
        "tests/unit/test_no_home_directory_access.py::test_agents_md_lists_structural_test_as_enforcement",
        "tests/unit/test_markdown_docs_deep.py::test_all_internal_links_resolve",
        "tests/unit/test_oversized_document_splits.py",
    ]
    assert shlex.split(arguments) == ["-q", "-W", "error", "-n", "0"]


def test_real_owner_keeps_live_capacity_read_only_and_release_policy_fixed() -> None:
    owner = _recipe()
    assert "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0" in owner
    assert "DISK_CLEANUP_PREFLIGHT_DRY_RUN=1" in owner
    assert "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800" in owner
    assert "GATE_TEST_PROFILE=release" in owner
    assert "GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1" in owner
    assert "RESOURCE_OWNERSHIP_WRITE=0" in owner
    assert "release-promote" not in owner and "git " not in owner
