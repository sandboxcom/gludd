"""Tests for the background-gate infrastructure (Makefile targets + markers).

Reads the Makefile as text and asserts the session launcher, PID file, and
streaming phase markers exist. Mirrors test_guardrails.py::TestMakefileTargets.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.gate_status_attestation import repository_state_id, sign_terminal_status
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _content() -> str:
    assert MAKEFILE.exists(), "Makefile must exist"
    return compose_makefile(MAKEFILE)


def test_gate_background_target_exists() -> None:
    content = _content()
    assert "gate-background:" in content, "Makefile missing 'gate-background:' target"


def test_gate_status_check_target_exists() -> None:
    content = _content()
    assert "gate-status-check:" in content, "Makefile missing 'gate-status-check:' target"


def test_gate_tail_target_exists() -> None:
    content = _content()
    assert "gate-tail:" in content, "Makefile missing 'gate-tail:' target"


def test_gate_kill_target_exists() -> None:
    content = _content()
    assert "gate-kill:" in content, "Makefile missing 'gate-kill:' target"


def test_gate_logs_target_exists() -> None:
    content = _content()
    assert "gate-logs:" in content, "Makefile missing 'gate-logs:' target"


def test_gate_writes_phase_markers() -> None:
    """Gate recipe emits unambiguous per-phase markers for status-check to grep."""
    content = _content()
    for phase in ("lint", "typecheck", "collect", "smoke", "test"):
        marker = f"=== GATE PHASE: {phase} ==="
        assert marker in content, f"Gate recipe missing phase marker {marker!r}"


def test_gate_writes_terminal_marker() -> None:
    """Gate recipe emits a terminal PASSED/FAILED marker status-check can detect."""
    content = _content()
    assert "=== GATE: PASSED ===" in content, "Gate recipe missing terminal '=== GATE: PASSED ===' marker"
    assert "=== GATE: FAILED ===" in content, "Gate recipe missing terminal '=== GATE: FAILED ===' marker"


def test_gate_background_uses_repo_owned_session_launcher() -> None:
    """gate-background delegates admission, identity, and timeout ownership."""
    content = _content()
    # Isolate the gate-background recipe block.
    idx = content.find("gate-background:")
    assert idx != -1
    # The next top-level target marks the end of the recipe.
    end = content.index("# Managed command runners", idx)
    recipe_block = content[idx:end]
    assert "scripts/start_gate_background.py" in recipe_block
    assert '--timeout-seconds "$(GATE_TIMEOUT)"' in recipe_block
    assert '--validate-only "$(GATE_BACKGROUND_VALIDATE_ONLY)"' in recipe_block
    assert "nohup" not in recipe_block
    assert "( sleep" not in recipe_block


def test_gate_background_writes_pid_file() -> None:
    """gate-background must write a PID file (.gate-background.pid) for status-check."""
    content = _content()
    idx = content.find("gate-background:")
    recipe_block = content[idx : idx + 2000]
    launcher = (ROOT / "scripts/start_gate_background.py").read_text()
    assert "scripts/start_gate_background.py" in recipe_block
    assert 'pid_file=resolved / ".gate-background.pid"' in launcher


def test_gate_status_check_reads_pid_file() -> None:
    """gate-status-check reads the same PID file gate-background writes."""
    content = _content()
    idx = content.find("gate-status-check:")
    recipe_block = content[idx : idx + 2000]
    assert ".gate-background.pid" in recipe_block, "gate-status-check must reference .gate-background.pid"


def test_gate_status_check_reports_dead_running_owner_as_orphaned() -> None:
    """A dead PID must never be presented as a normally finished RUNNING gate."""
    content = _content()
    start = content.index("gate-status-check:")
    end = content.index("# Poll the background gate", start)
    recipe_block = content[start:end]

    assert "ORPHANED" in recipe_block
    assert "orphaned-running-status" in recipe_block


def test_gate_background_observed_keeps_the_launch_owner_alive() -> None:
    """Automation gets one observable command that launches and waits."""
    content = _content()
    idx = content.find("gate-background-observed:")
    assert idx != -1, "Makefile missing 'gate-background-observed:' target"
    recipe_block = content[idx:].split("# Launch gate-lite detached", 1)[0]

    launch = "$(_GATE_MAKE) --no-print-directory gate-background"
    wait = "$(_GATE_MAKE) --no-print-directory gate-wait"
    assert launch in recipe_block
    assert wait in recipe_block
    assert recipe_block.index(launch) < recipe_block.index(wait)
    assert 'GATE_TIMEOUT="$(GATE_TIMEOUT)"' in recipe_block
    assert 'GATE_POLL_INTERVAL="$(GATE_POLL_INTERVAL)"' in recipe_block
    assert "GATE_BACKGROUND_OBSERVED_VALIDATE_ONLY" in recipe_block


def test_gate_background_has_one_python_owned_launch_step() -> None:
    """One Python transaction owns duplicate admission through PID publication."""
    content = _content()
    start = content.index("gate-background:")
    # Isolate only the gate-background recipe: next top-level target declaration.
    next_target = re.search(r"\n[a-zA-Z_-][a-zA-Z0-9_-]*:", content[start:])
    end = start + next_target.start() if next_target else len(content)
    recipe_block = content[start:end]

    assert recipe_block.count("\n\t@") == 1
    assert "scripts/start_gate_background.py" in recipe_block
    assert "refusing to launch duplicate" not in recipe_block


def test_background_launchers_are_safe_under_make_dry_run() -> None:
    """GNU Make must not classify launcher recipes as recursive under ``-n``."""
    content = _content()
    background_start = content.index("gate-background:")
    background_end = content.index("# Managed command runners", background_start)
    observed_start = content.index("gate-background-observed:")
    observed_end = content.index("# Launch gate-lite detached", observed_start)

    assert "$(MAKE)" not in content[background_start:background_end]
    assert "$(MAKE)" not in content[observed_start:observed_end]


def test_observed_gate_waits_for_the_exact_launched_pid() -> None:
    """A later shared PID-file write cannot detach the observed owner."""
    content = _content()
    observed_start = content.index("gate-background-observed:")
    observed_end = content.index("# Launch gate-lite detached", observed_start)
    observed_block = content[observed_start:observed_end]
    status_start = content.index("gate-status-check:")
    status_end = content.index("# Poll the background gate", status_start)
    status_block = content[status_start:status_end]

    assert "EXPECTED_PID=$$(cat .gate-background.pid" in observed_block
    assert 'GATE_EXPECTED_PID="$$EXPECTED_PID"' in observed_block
    assert 'PID="$(GATE_EXPECTED_PID)"' in status_block


def test_gate_wait_has_a_hard_timeout_ceiling() -> None:
    """gate-wait must carry an internal deadline that no env var can exceed."""
    content = _content()
    start = content.index("gate-wait:")
    end = content.index("# Return immediately", start)
    recipe_block = content[start:end]

    assert re.search(r"HARD_MAX_WAIT\s*[:=]", recipe_block), "missing HARD_MAX_WAIT ceiling"
    assert "date +%s" in recipe_block, "missing wall-clock deadline tracking"
    assert "exit 1" in recipe_block, "missing timeout failure exit"


def test_gate_wait_exits_after_timeout_when_no_terminal_status(tmp_path: Path) -> None:
    """If gate-status-check never emits a terminal marker, gate-wait must still return."""
    metadata = (ROOT / "pyproject.toml").read_bytes()
    project_file = tmp_path / "pyproject.toml"
    project_file.write_bytes(metadata)

    override = tmp_path / "Makefile"
    override.write_text(
        f'include {MAKEFILE}\n\ngate-status-check:\n\t@echo "RUNNING (pid=99999)"\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "make",
            "-I",
            str(ROOT),
            "-f",
            str(override),
            "gate-wait",
            "GATE_POLL_INTERVAL=1",
            "GATE_WAIT_TIMEOUT=3",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "TIMEOUT" in result.stdout or "timeout" in result.stdout.lower(), result.stdout


def test_gate_background_duplicate_does_not_replace_live_pid(
    tmp_path: Path,
) -> None:
    """The real target refuses a live owner without reaching its launch step."""
    metadata = (ROOT / "pyproject.toml").read_bytes()
    project_file = tmp_path / "pyproject.toml"
    project_file.write_bytes(metadata)
    owner = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        pid_file = tmp_path / ".gate-background.pid"
        pid_file.write_text(f"{owner.pid}\n")
        result = subprocess.run(
            [
                "make",
                "-I",
                str(ROOT),
                "-f",
                str(MAKEFILE),
                "gate-background",
                "GATE_TIMEOUT=7200",
            ],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "refusing to launch duplicate" in result.stdout
        assert pid_file.read_text() == f"{owner.pid}\n"
        assert project_file.read_bytes() == metadata
        assert not (tmp_path / ".venv").exists()
    finally:
        owner.terminate()
        owner.wait(timeout=10)


def test_gate_background_dry_run_has_no_filesystem_side_effects(
    tmp_path: Path,
) -> None:
    """The real ``make -n`` target prints its recipe and creates nothing."""
    metadata = (ROOT / "pyproject.toml").read_bytes()
    project_file = tmp_path / "pyproject.toml"
    project_file.write_bytes(metadata)
    before_entries = set(tmp_path.iterdir())
    subprocess.run(
        [
            "make",
            "-n",
            "-I",
            str(ROOT),
            "-f",
            str(MAKEFILE),
            "gate-background",
            "GATE_TIMEOUT=7200",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not (tmp_path / ".gate-background.pid").exists()
    assert not (tmp_path / ".gate-logs").exists()
    assert project_file.read_bytes() == metadata
    assert set(tmp_path.iterdir()) == before_entries


def _resume_make_fixture(
    tmp_path: Path, *, mutation: str = "", plan: bool = False,
) -> subprocess.CompletedProcess[str]:
    import shlex

    recipe = _content().split("\ngate-resume: gate-admission-config\n", 1)[1].split("\n\n", 1)[0]
    recipe = recipe.replace(
        "scripts/gate_status_attestation.py", str(ROOT / "scripts/gate_status_attestation.py"),
    )
    uv = tmp_path / "selected-uv"
    uv.write_text(
        '#!/bin/sh\n[ "$1" = run ] || exit 91\nshift\n'
        '[ "$1" = python ] || exit 92\nshift\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n', encoding="utf-8",
    )
    uv.chmod(0o700)
    (tmp_path / ".gitignore").write_text(
        ".gate-logs/\n.gate-status\ncalled\n", encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text(
        f"UV := {shlex.quote(str(uv))}\n_GATE_MAKE := make\n"
        f"_GATE_REPOSITORY_ROOT := {tmp_path}\n_GATE_ENTRY_MAKEFILE := {tmp_path / 'Makefile'}\n"
        "gate-admission-config:\n\t@:\n"
        "gate-resume: gate-admission-config\n" + recipe
        + "\n\ngate-background:\n"
        "\t@echo resumed=$(GATE_EXACT_SHA_RESUME) profile=$(GATE_TEST_PROFILE) "
        "files=$(GATE_MAX_FILES_PER_BATCH) workers=$(GATE_BATCH_WORKERS) > called\n",
        encoding="utf-8",
    )
    for arguments in (
        ("init", "--quiet"), ("config", "user.name", "Resume fixture"),
        ("config", "user.email", "resume@example.invalid"), ("add", "."),
        ("commit", "--quiet", "-m", "fixture"),
    ):
        subprocess.run(["git", "-C", str(tmp_path), *arguments], check=True, capture_output=True)
    key = b"r" * 32
    logs = tmp_path / ".gate-logs"
    logs.mkdir()
    key_path = logs / "fixture.key"
    key_path.write_text(key.hex(), encoding="ascii")
    log = logs / "gate-20261019T000000Z-fixture.log"
    log.write_text("=== GATE: FAILED ===\n", encoding="utf-8")
    status = tmp_path / ".gate-status"
    status.write_text(
        "=== GATE fixture ===\n"
        f"gate-policy profile={'native' if mutation == 'policy' else 'release'} max_files_per_batch=16 workers=1\n"
        "preflights PASS 35\nlint FAIL 1\n=== GATE: FAILED ===\n", encoding="utf-8",
    )
    if mutation != "unsigned":
        sign_terminal_status(status, state_id=repository_state_id(tmp_path), key=key)
    if mutation == "head":
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--quiet", "--allow-empty", "-m", "new head"],
            check=True, capture_output=True,
        )
    elif mutation == "tamper":
        status.write_text(status.read_text().replace("PASS 35", "PASS 36"), encoding="utf-8")
    original = status.read_bytes()
    before = {path.name: path.read_bytes() for path in logs.iterdir()}
    result = subprocess.run(
        ["make", "--no-print-directory", "gate-resume", "GATE_TIMEOUT=3600",
         "GATE_TEST_PROFILE=release", "GATE_MAX_FILES_PER_BATCH=16", "GATE_BATCH_WORKERS=1",
         f"GATE_BACKGROUND_VALIDATE_ONLY={int(plan)}"],
        cwd=tmp_path, env={**os.environ, "GLUDD_GATE_KEY_PATH": str(key_path)},
        check=False, capture_output=True, text=True, timeout=20,
    )
    assert status.read_bytes() == original
    assert {path.name: path.read_bytes() for path in logs.iterdir()} == before
    return result


@pytest.mark.parametrize("mutation", ["", "head", "policy", "unsigned", "tamper"])
def test_resume_launch_uses_only_authenticated_current_state_and_policy(
    tmp_path: Path, mutation: str,
) -> None:
    result = _resume_make_fixture(tmp_path, mutation=mutation)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = 0 if mutation else 1
    assert (tmp_path / "called").read_text().strip() == (
        f"resumed={expected} profile=release files=16 workers=1"
    )
    assert ("PREFLIGHT-COLD:" in result.stdout) == bool(mutation)
    assert not list((tmp_path / ".gate-logs").glob("gate-resume-prior-*.status"))


def test_resume_plan_is_read_only_and_never_starts_a_child(tmp_path: Path) -> None:
    result = _resume_make_fixture(tmp_path, plan=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VALIDATE" in result.stdout and "gate-background" in result.stdout
    assert not (tmp_path / "called").exists()



def test_gate_resume_plan_in_a_fresh_checkout_has_no_side_effects(tmp_path: Path) -> None:
    metadata = (ROOT / "pyproject.toml").read_bytes()
    (tmp_path / "pyproject.toml").write_bytes(metadata)
    before = set(tmp_path.iterdir())
    result = subprocess.run(
        ["make", "-I", str(ROOT), "-f", str(MAKEFILE), "gate-resume",
         "GATE_TIMEOUT=3600", "GATE_BACKGROUND_VALIDATE_ONLY=1",
         "GATE_TEST_PROFILE=release", "GATE_MAX_FILES_PER_BATCH=16", "GATE_BATCH_WORKERS=1",
         "PYTEST_CMD=", "PYTEST_ARGS=", "PYTEST_ADDOPTS=", "MYPY_MAX=0"],
        cwd=tmp_path, check=False, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VALIDATE" in result.stdout and "gate-background" in result.stdout
    assert set(tmp_path.iterdir()) == before
    assert (tmp_path / "pyproject.toml").read_bytes() == metadata


def test_gate_resume_target_exists() -> None:
    content = _content()
    assert "gate-resume:" in content, "Makefile missing 'gate-resume:' target"


def test_gate_resume_forwards_resume_and_gate_policy() -> None:
    """gate-resume forwards policy and the authenticated reuse decision."""
    content = _content()
    idx = content.find("gate-resume:")
    assert idx != -1
    next_target = re.search(r"\n[a-zA-Z_-][a-zA-Z0-9_-]*:", content[idx:])
    end = idx + next_target.start() if next_target else len(content)
    recipe_block = content[idx:end]
    assert 'GATE_EXACT_SHA_RESUME="$$RESUME"' in recipe_block
    assert "verify-resume .gate-status" in recipe_block
    assert "RESUME=0" in recipe_block and "RESUME=1" in recipe_block
    assert "gate-background" in recipe_block
    assert "$(_GATE_MAKE)" in recipe_block


def test_gate_resume_refuses_without_gate_status(tmp_path: Path) -> None:
    metadata = (ROOT / "pyproject.toml").read_bytes()
    project_file = tmp_path / "pyproject.toml"
    project_file.write_bytes(metadata)
    result = subprocess.run(
        [
            "make",
            "-I",
            str(ROOT),
            "-f",
            str(MAKEFILE),
            "gate-resume",
            "GATE_TIMEOUT=7200",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "requires an existing .gate-status file" in result.stdout


def test_gate_resume_refuses_without_fail_marker(tmp_path: Path) -> None:
    metadata = (ROOT / "pyproject.toml").read_bytes()
    project_file = tmp_path / "pyproject.toml"
    project_file.write_bytes(metadata)
    (tmp_path / ".gate-status").write_text("=== GATE: PASSED ===\n", encoding="utf-8")
    result = subprocess.run(
        [
            "make",
            "-I",
            str(ROOT),
            "-f",
            str(MAKEFILE),
            "gate-resume",
            "GATE_TIMEOUT=7200",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "requires a failed gate" in result.stdout


def test_gate_resume_refuses_without_prior_gate_log(tmp_path: Path) -> None:
    metadata = (ROOT / "pyproject.toml").read_bytes()
    project_file = tmp_path / "pyproject.toml"
    project_file.write_bytes(metadata)
    (tmp_path / ".gate-status").write_text("=== GATE: FAILED ===\n", encoding="utf-8")
    result = subprocess.run(
        [
            "make",
            "-I",
            str(ROOT),
            "-f",
            str(MAKEFILE),
            "gate-resume",
            "GATE_TIMEOUT=7200",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "requires a prior gate log" in result.stdout


def test_gate_resume_validates_launch_with_failed_state(tmp_path: Path) -> None:
    metadata = (ROOT / "pyproject.toml").read_bytes()
    project_file = tmp_path / "pyproject.toml"
    project_file.write_bytes(metadata)
    (tmp_path / ".gate-status").write_text("=== GATE: FAILED ===\n", encoding="utf-8")
    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    (log_dir / "gate-20261016T000000Z-resume-test.log").write_text("prior gate failure log\n", encoding="utf-8")
    result = subprocess.run(
        [
            "make",
            "-I",
            str(ROOT),
            "-f",
            str(MAKEFILE),
            "gate-resume",
            "GATE_TIMEOUT=7200",
            "GATE_BACKGROUND_VALIDATE_ONLY=1",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VALIDATE" in result.stdout and "gate-background" in result.stdout
    assert (tmp_path / ".gate-status").read_text() == "=== GATE: FAILED ===\n"
    assert not list(log_dir.glob("gate-resume-prior-*.status"))


def test_gate_resume_preserves_prior_status_without_relabelling_it() -> None:
    """The locked gate owns immutable archival, rather than the resume facade."""
    content = _content()
    idx = content.find("gate-resume:")
    assert idx != -1
    next_target = re.search(r"\n[a-zA-Z_-][a-zA-Z0-9_-]*:", content[idx:])
    end = idx + next_target.start() if next_target else len(content)
    recipe_block = content[idx:end]
    assert "cp .gate-status" not in recipe_block
    assert "gate-resume-prior-$(GATE_CANDIDATE_SHA).status" not in recipe_block
    gate = content.split("\ngate:", 1)[1].split("\n# gate-lite:", 1)[0]
    assert gate.index("archive-prior .gate-status") < gate.index('printf "RUNNING')
    assert 'GATE_PREFLIGHT_RESUME_RECEIPT="$$PRIOR"' in gate


def test_gate_preflights_skip_when_resume_receipt_exists() -> None:
    """_gate-preflights must re-run the resume-always subset when an exact-SHA resume receipt exists."""
    content = _content()
    idx = content.find("_gate-preflights:")
    assert idx != -1
    next_target = re.search(r"\n[a-zA-Z_-][a-zA-Z0-9_-]*:", content[idx:])
    end = idx + next_target.start() if next_target else len(content)
    recipe_block = content[idx:end]
    assert "GATE_EXACT_SHA_RESUME" in recipe_block
    assert "GATE_PREFLIGHT_RESUME_RECEIPT" in recipe_block
    assert "GATE_PREFLIGHT_RESUME_ALWAYS" in recipe_block
    assert "PREFLIGHT-RESUME: re-ran" in recipe_block
    assert "exit 0" in recipe_block
