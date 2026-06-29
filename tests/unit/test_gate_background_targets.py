"""Tests for the background-gate infrastructure (Makefile targets + markers).

Reads the Makefile as text and asserts the new targets, nohup usage, PID file,
and streaming phase markers exist. Mirrors test_guardrails.py::TestMakefileTargets.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _content() -> str:
    assert MAKEFILE.exists(), "Makefile must exist"
    return MAKEFILE.read_text()


def test_gate_background_target_exists():
    content = _content()
    assert "gate-background:" in content, "Makefile missing 'gate-background:' target"


def test_gate_status_check_target_exists():
    content = _content()
    assert "gate-status-check:" in content, "Makefile missing 'gate-status-check:' target"


def test_gate_tail_target_exists():
    content = _content()
    assert "gate-tail:" in content, "Makefile missing 'gate-tail:' target"


def test_gate_kill_target_exists():
    content = _content()
    assert "gate-kill:" in content, "Makefile missing 'gate-kill:' target"


def test_gate_logs_target_exists():
    content = _content()
    assert "gate-logs:" in content, "Makefile missing 'gate-logs:' target"


def test_gate_writes_phase_markers():
    """Gate recipe emits unambiguous per-phase markers for status-check to grep."""
    content = _content()
    for phase in ("lint", "typecheck", "collect", "smoke", "test"):
        marker = f"=== GATE PHASE: {phase} ==="
        assert marker in content, (
            f"Gate recipe missing phase marker {marker!r}"
        )


def test_gate_writes_terminal_marker():
    """Gate recipe emits a terminal PASSED/FAILED marker status-check can detect."""
    content = _content()
    assert "=== GATE: PASSED ===" in content, (
        "Gate recipe missing terminal '=== GATE: PASSED ===' marker"
    )
    assert "=== GATE: FAILED ===" in content, (
        "Gate recipe missing terminal '=== GATE: FAILED ===' marker"
    )


def test_gate_background_uses_nohup():
    """gate-background must use nohup so the launched gate survives shell exit."""
    content = _content()
    # Isolate the gate-background recipe block.
    idx = content.find("gate-background:")
    assert idx != -1
    # The next top-level target marks the end of the recipe.
    tail = content[idx:]
    # Restrict to ~2000 chars so we don't accidentally match a later target.
    recipe_block = tail[:2000]
    assert "nohup" in recipe_block, (
        "gate-background recipe must use nohup to detach the gate from the shell"
    )


def test_gate_background_writes_pid_file():
    """gate-background must write a PID file (.gate-background.pid) for status-check."""
    content = _content()
    idx = content.find("gate-background:")
    recipe_block = content[idx:idx + 2000]
    assert ".gate-background.pid" in recipe_block, (
        "gate-background recipe must write .gate-background.pid"
    )


def test_gate_status_check_reads_pid_file():
    """gate-status-check reads the same PID file gate-background writes."""
    content = _content()
    idx = content.find("gate-status-check:")
    recipe_block = content[idx:idx + 2000]
    assert ".gate-background.pid" in recipe_block, (
        "gate-status-check must reference .gate-background.pid"
    )
