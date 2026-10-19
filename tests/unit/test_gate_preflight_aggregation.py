"""Behavioral contracts for fail-fast gate admission and terminal evidence."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from scripts.gate_status_attestation import (
    repository_state_id,
    sign_terminal_status,
    verify_terminal_status,
)
from scripts.makefile_layout import compose_makefile

_MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def _makefile() -> str:
    return compose_makefile(_MAKEFILE)



def _release_admission_fixture(
    tmp_path: Path, *, failed_owner: str = "", profile: str = "release",
    canonical: bool = False,
) -> subprocess.CompletedProcess[str]:
    import shlex
    import sys

    source = _makefile()
    marker = "\n_gate-release-proof-admission:\n"
    assert marker in source, "Missing canonical logged release admission stage"
    stage = source.split(marker, 1)[1].split("\n\n", 1)[0]
    terminal = source.split("\n_gate-validation-admission:\n", 1)[1].split("\n\n", 1)[0]
    gate = "gate:" + source.split("\ngate:", 1)[1].split("\n# gate-lite:", 1)[0]
    for script in ("stream_command.py", "gate_status_attestation.py", "gate_run_lock.py"):
        stage = stage.replace(f"scripts/{script}", str(_MAKEFILE.parent / "scripts" / script))
        terminal = terminal.replace(f"scripts/{script}", str(_MAKEFILE.parent / "scripts" / script))
        gate = gate.replace(f"scripts/{script}", str(_MAKEFILE.parent / "scripts" / script))
    uv = tmp_path / "selected-uv"
    uv.write_text(
        "#!/bin/sh\n[ \"$1\" = run ] || exit 91\nshift\n"
        "[ \"$1\" = python ] || exit 92\nshift\n"
        f"exec {shlex.quote(sys.executable)} \"$@\"\n",
        encoding="utf-8",
    )
    uv.chmod(0o700)
    (tmp_path / ".gitignore").write_text(".gate-logs/\n.gate-status*\n.gate-failed\n", encoding="utf-8")
    (tmp_path / ".gate-status.next").write_text(
        "=== GATE fixture ===\ngate-policy profile=release max_files_per_batch=16 workers=1\n",
        encoding="utf-8",
    )
    owners = (
        "disk-cleanup-preflight", "gate-admission-config", "check-task-registration",
        "check-task-integrity", "check-gate-failure-promotions", "check-resource-ownership",
        "_pytest-runtime-compatibility-admission",
    )
    facade = "release-proof-admission:\n"
    for owner in owners:
        facade += f"\t@echo 'PROOF-ADMISSION-START phase={owner}'\n"
        if owner == failed_owner:
            facade += (
                f"\t@echo 'ERROR: actual failing input for {owner}'\n"
                f"\t@echo 'PROOF-ADMISSION-BLOCKED phase={owner} rc=7'; exit 7\n"
            )
            break
        facade += f"\t@echo 'PROOF-ADMISSION-PHASE-PASS phase={owner}'\n"
    else:
        facade += "\t@echo 'PROOF-ADMISSION-PASS admitted_for_proof=true release_ready=false gate_evidence=false'\n"
    (tmp_path / "Makefile").write_text(
        f"UV := {shlex.quote(str(uv))}\nGATE_TEST_PROFILE := {profile}\n"
        "GATE_ADMISSION_STATUS := .gate-status.next\nGATE_ADMISSION_FINAL_STATUS := .gate-status\n"
        "GATE_ADMISSION_FAILED_FILE := .gate-failed\nGATE_RUN_LOCK :=\n"
        "GATE_MAX_FILES_PER_BATCH := 16\nGATE_BATCH_WORKERS := 1\n"
        "gate-admission-config _gate-release-state-check _gate-run-lock-acquire:\n\t@:\n"
        "_gate-release-proof-admission:\n" + stage + "\n\n"
        "_gate-validation-admission:\n" + terminal + "\n\n" + facade
        + ("\n" + gate if canonical else ""),
        encoding="utf-8",
    )
    for arguments in (
        ("init", "--quiet"), ("config", "user.name", "Gate fixture"),
        ("config", "user.email", "gate@example.invalid"), ("add", "."),
        ("commit", "--quiet", "-m", "fixture"),
    ):
        subprocess.run(["git", "-C", str(tmp_path), *arguments], check=True, capture_output=True)
    if canonical:
        (tmp_path / ".gate-status").write_text("historical failed bytes\n", encoding="utf-8")
    result = subprocess.run(
        ["make", "--no-print-directory", "gate" if canonical else "_gate-release-proof-admission"],
        cwd=tmp_path, check=False, capture_output=True, text=True, timeout=20,
        env={**os.environ, "GLUDD_GATE_KEY_PATH": str(tmp_path / ".gate-logs" / "fixture.key")},
    )
    if canonical:
        (tmp_path / ".gate-logs" / "gate-20261019000000-admission.log").write_text(
            result.stdout + result.stderr, encoding="utf-8",
        )
    return result


def test_canonical_gate_reports_blocked_admission_without_starting_later_phases(
    tmp_path: Path,
) -> None:
    from general_ludd.gate.progress import estimate_progress, failure_summary, parse_gate_log

    owner = "check-resource-ownership"
    result = _release_admission_fixture(tmp_path, failed_owner=owner, canonical=True)
    assert result.returncode != 0
    status = (tmp_path / ".gate-status").read_text(encoding="utf-8")
    assert f"admission FAIL owner={owner}" in status, result.stdout + result.stderr
    assert "test NOT-STARTED" in status and "smoke NOT-STARTED" in status
    for phase in ("preflights", "lint", "typecheck", "collect", "test", "smoke"):
        assert f"=== GATE PHASE: {phase} ===" not in result.stdout
    archived = list((tmp_path / ".gate-logs").glob("gate-resume-prior-*.status"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == b"historical failed bytes\n"
    log = tmp_path / ".gate-logs" / "gate-20261019000000-admission.log"
    run = parse_gate_log(log)
    assert run.terminal == "FAILED"
    assert run.admission is not None and run.admission.owner == owner
    assert run.admission.state == "blocked"
    report = estimate_progress(log, [], now=run.start_time + 10)
    assert report.percent_complete == 0 and report.completed_segments == 0
    assert not report.remaining_known
    detail = failure_summary(run)
    assert owner in detail and f"actual failing input for {owner}" in detail
    assert "gate-admission-" in detail
    key = bytes.fromhex((tmp_path / ".gate-logs" / "fixture.key").read_text().strip())
    verified = verify_terminal_status(tmp_path / ".gate-status", state_id=repository_state_id(tmp_path), key=key)
    assert verified.ok, verified.reason
    print("NATIVE MOCKED GATE LOG\n" + log.read_text(encoding="utf-8"))
    print("NATIVE MOCKED GATE STATUS\n" + status)
    print(f"NATIVE PROGRESS complete={report.percent_complete} ETA-known={report.remaining_known}\n{detail}")



def test_native_gate_profile_does_not_run_release_admission(tmp_path: Path) -> None:
    result = _release_admission_fixture(tmp_path, canonical=True, profile="native")
    assert result.returncode != 0
    assert "=== GATE PHASE: preflights ===" in result.stdout
    assert "=== GATE PHASE: admission ===" not in result.stdout
    assert "PROOF-ADMISSION-START" not in result.stdout
    assert "admission PASS" not in (tmp_path / ".gate-status").read_text(encoding="utf-8")


def test_canonical_admission_pass_precedes_preflights_without_hiding_their_failure(tmp_path: Path) -> None:
    result = _release_admission_fixture(tmp_path, canonical=True)
    assert result.returncode != 0
    assert result.stdout.index("PROOF-ADMISSION-PASS") < result.stdout.index("=== GATE PHASE: preflights ===")
    status = (tmp_path / ".gate-status").read_text(encoding="utf-8")
    assert "admission PASS" in status
    assert "=== GATE: FAILED ===" in status
    assert "=== GATE: PASSED ===" not in status


@pytest.mark.parametrize(
    "owner",
    ["disk-cleanup-preflight", "gate-admission-config", "check-task-registration",
     "check-task-integrity", "check-gate-failure-promotions", "check-resource-ownership",
     "_pytest-runtime-compatibility-admission"],
)
def test_release_admission_terminalizes_exact_owner_before_gate_phases(
    tmp_path: Path, owner: str,
) -> None:
    result = _release_admission_fixture(tmp_path, failed_owner=owner)
    assert result.returncode != 0
    final = tmp_path / ".gate-status"
    content = final.read_text(encoding="utf-8")
    assert f"admission FAIL owner={owner}" in content
    assert "test NOT-STARTED" in content and "smoke NOT-STARTED" in content
    assert content.count("=== GATE: FAILED ===") == 1
    assert "GATE PHASE: lint" not in result.stdout
    key = bytes.fromhex((tmp_path / ".gate-logs" / "fixture.key").read_text().strip())
    authenticated = verify_terminal_status(final, state_id=repository_state_id(tmp_path), key=key)
    assert authenticated.ok, authenticated.reason


def test_release_admission_pass_is_only_partial_not_a_green_gate(tmp_path: Path) -> None:
    result = _release_admission_fixture(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "admission PASS" in (tmp_path / ".gate-status.next").read_text()
    assert not (tmp_path / ".gate-status").exists()
    assert not (tmp_path / ".gate-logs" / "fixture.key").exists()
    assert "=== GATE: PASSED ===" not in result.stdout



def test_release_admission_uses_a_fresh_native_log_without_overwriting_prior_bytes(tmp_path: Path) -> None:
    result = _release_admission_fixture(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    logs = tmp_path / ".gate-logs"
    first = list(logs.glob("gate-admission-*"))
    assert len(first) == 1 and "XXXXXXXX" not in first[0].name
    original = first[0].read_bytes()
    second = subprocess.run(
        ["make", "--no-print-directory", "_gate-release-proof-admission"],
        cwd=tmp_path, check=False, capture_output=True, text=True, timeout=20,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert len(list(logs.glob("gate-admission-*"))) == 2
    assert first[0].read_bytes() == original


def test_gate_preflights_are_recipe_work_not_fail_fast_prerequisites() -> None:
    makefile = _makefile()
    gate_header = makefile.split("\ngate:", 1)[1].splitlines()[0]

    # Pure profile/identity admission rejects invalid release inputs before any
    # heavy work; the complete preflight list still executes inside the recipe.
    assert gate_header.strip() == (
        "gate-admission-config "
        "$(if $(filter release,$(GATE_TEST_PROFILE)),_gate-release-state-check,) "
        "_gate-run-lock-acquire"
    )
    assert "GATE_PREFLIGHT_TARGETS :=" in makefile
    assert "for target in $(GATE_PREFLIGHT_TARGETS)" in makefile


def test_gate_terminalizes_the_first_preflight_failure() -> None:
    makefile = _makefile()

    assert ".gate-logs/gate-preflights.status" in makefile
    assert 'touch "$(GATE_PREFLIGHT_FAILED_FILE)"' in makefile
    assert "terminating admission immediately" in makefile
    assert "scripts/gate_status_attestation.py sign-terminal .gate-status.next" in makefile
    gate_recipe = makefile.split("\ngate:", 1)[1].split("\n\ngate-fast:", 1)[0]
    release = 'scripts/gate_run_lock.py release "$(GATE_RUN_LOCK)" "$$PPID"'
    assert gate_recipe.index(release) < gate_recipe.index("sign-terminal")
    assert gate_recipe.index("sign-terminal") < gate_recipe.index("GATE PHASE: lint")


def test_gate_preflight_runner_does_not_execute_later_checks_after_failure(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "preflights.status"
    gate_status_path = tmp_path / "gate.status"
    failed_path = tmp_path / "gate.failed"
    resume_receipt_path = tmp_path / "resume-receipt.status"

    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "_gate-preflights",
            "GATE_PREFLIGHT_TARGETS=_gate-preflight-fixture-pass-one "
            "_gate-preflight-fixture-fail _gate-preflight-fixture-pass-two",
            f"GATE_PREFLIGHT_STATUS={status_path}",
            f"GATE_PREFLIGHT_GATE_STATUS={gate_status_path}",
            f"GATE_PREFLIGHT_FAILED_FILE={failed_path}",
            f"GATE_PREFLIGHT_RESUME_RECEIPT={resume_receipt_path}",
            "GATE_EXACT_SHA_RESUME=0",
        ],
        cwd=_MAKEFILE.parent,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert status_path.read_text(encoding="utf-8").splitlines() == [
        "_gate-preflight-fixture-pass-one PASS",
        "_gate-preflight-fixture-fail FAIL 2",
    ]
    assert gate_status_path.read_text(encoding="utf-8").strip().startswith("FAIL 1 first=_gate-preflight-fixture-fail")
    assert failed_path.is_file()
    assert "terminating admission immediately" in result.stdout


def _preflight_resume_fixture(
    tmp_path: Path, *, mutation: str = "", fail_subset: bool = False,
) -> subprocess.CompletedProcess[str]:
    import shlex
    import sys

    recipe = _makefile().split("\n_gate-preflights:\n", 1)[1].split("\n\n", 1)[0]
    recipe = recipe.replace(
        "scripts/gate_status_attestation.py",
        str(_MAKEFILE.parent / "scripts/gate_status_attestation.py"),
    )
    uv = tmp_path / "selected-uv"
    uv.write_text(
        '#!/bin/sh\n[ "$1" = run ] || exit 91\nshift\n'
        '[ "$1" = python ] || exit 92\nshift\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n', encoding="utf-8",
    )
    uv.chmod(0o700)
    (tmp_path / ".gitignore").write_text(
        ".gate-logs/\n*.status\ngate.failed\n", encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text(
        f"UV := {shlex.quote(str(uv))}\n"
        "GATE_PREFLIGHT_STATUS := preflights.status\n"
        "GATE_PREFLIGHT_GATE_STATUS := gate.status\n"
        "GATE_PREFLIGHT_FAILED_FILE := gate.failed\n"
        "GATE_PREFLIGHT_RESUME_RECEIPT := resume-receipt.status\n"
        "GATE_PREFLIGHT_TARGETS := _gate-preflight-fixture-pass-two\n"
        "_gate-preflights:\n" + recipe
        + "\n\n_gate-preflight-fixture-pass-one _gate-preflight-fixture-pass-two:\n\t@:\n"
        "_gate-preflight-fixture-fail:\n\t@exit 7\n", encoding="utf-8",
    )
    for arguments in (
        ("init", "--quiet"), ("config", "user.name", "Gate fixture"),
        ("config", "user.email", "gate@example.invalid"), ("add", "."),
        ("commit", "--quiet", "-m", "fixture"),
    ):
        subprocess.run(["git", "-C", str(tmp_path), *arguments], check=True, capture_output=True)
    key = b"p" * 32
    key_path = tmp_path / ".gate-logs" / "fixture.key"
    key_path.parent.mkdir()
    key_path.write_text(key.hex(), encoding="ascii")
    receipt = tmp_path / "resume-receipt.status"
    receipt.write_text(
        "=== GATE fixture ===\n"
        f"gate-policy profile={'release' if mutation == 'policy' else 'native'} max_files_per_batch=16 workers=1\n"
        "preflights PASS 35\nlint FAIL 1\n=== GATE: FAILED ===\n", encoding="utf-8",
    )
    if mutation != "unsigned":
        sign_terminal_status(receipt, state_id=repository_state_id(tmp_path), key=key)
    if mutation == "head":
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--quiet", "--allow-empty", "-m", "changed head"],
            check=True, capture_output=True,
        )
    elif mutation == "tamper":
        receipt.write_text(receipt.read_text().replace("PASS 35", "PASS 36"), encoding="utf-8")
    subset = "_gate-preflight-fixture-pass-one " + (
        "_gate-preflight-fixture-fail" if fail_subset else "_gate-preflight-fixture-pass-two"
    )
    return subprocess.run(
        ["make", "--no-print-directory", "_gate-preflights",
         "GATE_EXACT_SHA_RESUME=1", "GATE_TEST_PROFILE=native",
         "GATE_MAX_FILES_PER_BATCH=16", "GATE_BATCH_WORKERS=1",
         f"GATE_PREFLIGHT_RESUME_ALWAYS={subset}"],
        cwd=tmp_path, env={**os.environ, "GLUDD_GATE_KEY_PATH": str(key_path)},
        check=False, capture_output=True, text=True, timeout=20,
    )


@pytest.mark.parametrize("mutation", ["unsigned", "head", "policy", "tamper"])
def test_preflight_resume_cannot_reuse_unverified_or_changed_status(
    tmp_path: Path, mutation: str,
) -> None:
    result = _preflight_resume_fixture(tmp_path, mutation=mutation, fail_subset=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PREFLIGHT-COLD:" in result.stdout
    assert "PREFLIGHT-RESUME:" not in result.stdout
    assert (tmp_path / "preflights.status").read_text().splitlines() == [
        "_gate-preflight-fixture-pass-two PASS",
    ]
    assert not (tmp_path / "gate.failed").exists()


def test_gate_preflights_resume_reruns_working_tree_subset(tmp_path: Path) -> None:
    status_path = tmp_path / "preflights.status"
    gate_status_path = tmp_path / "gate.status"
    failed_path = tmp_path / "gate.failed"
    result = _preflight_resume_fixture(tmp_path)

    assert result.returncode == 0
    assert not failed_path.exists()
    status = status_path.read_text(encoding="utf-8")
    assert "_gate-preflight-fixture-pass-one PASS" in status
    assert "_gate-preflight-fixture-pass-two PASS" in status
    assert "PREFLIGHT-RESUME: re-ran" in result.stdout
    assert gate_status_path.read_text(encoding="utf-8").strip() == "PASS 2"


def test_gate_preflights_resume_working_tree_subset_fails_closed(tmp_path: Path) -> None:
    status_path = tmp_path / "preflights.status"
    gate_status_path = tmp_path / "gate.status"
    failed_path = tmp_path / "gate.failed"
    result = _preflight_resume_fixture(tmp_path, fail_subset=True)

    assert result.returncode != 0
    assert "_gate-preflight-fixture-pass-one PASS" in status_path.read_text(encoding="utf-8")
    assert "FAIL 1 first=_gate-preflight-fixture-fail" in gate_status_path.read_text(encoding="utf-8")
    assert failed_path.is_file()


def test_validation_failure_is_authenticated_before_test_admission(
    tmp_path: Path,
) -> None:
    private_status = tmp_path / "gate.status.next"
    final_status = tmp_path / "gate.status"
    failed_path = tmp_path / "gate.failed"
    key_path = tmp_path / "gate.key"
    private_status.write_text(
        "=== GATE test ===\nlint FAIL 1\ntypecheck PASS 0\ncollect PASS 0\n",
        encoding="utf-8",
    )
    failed_path.touch()

    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "_gate-validation-admission",
            f"GATE_ADMISSION_STATUS={private_status}",
            f"GATE_ADMISSION_FINAL_STATUS={final_status}",
            f"GATE_ADMISSION_FAILED_FILE={failed_path}",
            "GATE_RUN_LOCK=",
            "GATE_RUN_LOCK_OWNER_PID=0",
        ],
        cwd=_MAKEFILE.parent,
        env={**os.environ, "GLUDD_GATE_KEY_PATH": str(key_path)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not private_status.exists()
    assert not failed_path.exists()
    content = final_status.read_text(encoding="utf-8")
    assert "test NOT-STARTED validation-failed" in content
    assert "smoke NOT-STARTED validation-failed" in content
    assert content.count("=== GATE: FAILED ===") == 1
    key = bytes.fromhex(key_path.read_text(encoding="ascii").strip())
    authenticated = verify_terminal_status(
        final_status,
        state_id=repository_state_id(_MAKEFILE.parent),
        key=key,
    )
    assert authenticated.ok, authenticated.reason
