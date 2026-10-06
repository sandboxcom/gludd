"""Tests for the background-gate infrastructure (Makefile targets + markers).

Reads the Makefile as text and asserts the session launcher, PID file, and
streaming phase markers exist. Mirrors test_guardrails.py::TestMakefileTargets.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _content() -> str:
    assert MAKEFILE.exists(), "Makefile must exist"
    return MAKEFILE.read_text()


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
        assert marker in content, (
            f"Gate recipe missing phase marker {marker!r}"
        )


def test_gate_writes_terminal_marker() -> None:
    """Gate recipe emits a terminal PASSED/FAILED marker status-check can detect."""
    content = _content()
    assert "=== GATE: PASSED ===" in content, (
        "Gate recipe missing terminal '=== GATE: PASSED ===' marker"
    )
    assert "=== GATE: FAILED ===" in content, (
        "Gate recipe missing terminal '=== GATE: FAILED ===' marker"
    )


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
    recipe_block = content[idx:idx + 2000]
    launcher = (ROOT / "scripts/start_gate_background.py").read_text()
    assert "scripts/start_gate_background.py" in recipe_block
    assert 'pid_file=resolved / ".gate-background.pid"' in launcher


def test_gate_status_check_reads_pid_file() -> None:
    """gate-status-check reads the same PID file gate-background writes."""
    content = _content()
    idx = content.find("gate-status-check:")
    recipe_block = content[idx:idx + 2000]
    assert ".gate-background.pid" in recipe_block, (
        "gate-status-check must reference .gate-background.pid"
    )


def test_gate_background_observed_keeps_the_launch_owner_alive() -> None:
    """Automation gets one observable command that launches and waits."""
    content = _content()
    idx = content.find("gate-background-observed:")
    assert idx != -1, "Makefile missing 'gate-background-observed:' target"
    recipe_block = content[idx : idx + 800]

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
    end = content.index("# Managed command runners", start)
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

    assert 'EXPECTED_PID=$$(cat .gate-background.pid' in observed_block
    assert 'GATE_EXPECTED_PID="$$EXPECTED_PID"' in observed_block
    assert 'PID="$(GATE_EXPECTED_PID)"' in status_block


def test_gate_background_duplicate_does_not_replace_live_pid(
    tmp_path: Path,
) -> None:
    """The real target refuses a live owner without reaching its launch step."""
    owner = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        pid_file = tmp_path / ".gate-background.pid"
        pid_file.write_text(f"{owner.pid}\n")
        result = subprocess.run(
            [
                "make",
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
    finally:
        owner.terminate()
        owner.wait(timeout=10)


def test_gate_background_dry_run_has_no_filesystem_side_effects(
    tmp_path: Path,
) -> None:
    """The real ``make -n`` target prints its recipe and creates nothing."""
    subprocess.run(
        [
            "make",
            "-n",
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
