"""Tests for the gate-lite infrastructure: Makefile targets + phase markers + .gate-lite-failed tracking.

Reads the Makefile as text and asserts the gate-lite target, per-phase markers
in .gate-lite-status, the .gate-lite-failed tracking file, and the kill target.
Mirrors test_gate_background_targets.py.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _content() -> str:
    assert MAKEFILE.exists(), "Makefile must exist"
    return MAKEFILE.read_text()


def _target_block(name: str) -> str:
    content = _content()
    needle = chr(10) + f"{name}:"
    start = content.find(needle)
    if start == -1:
        assert content.startswith(f"{name}:"), f"Makefile missing target {name!r}"
    else:
        start += 1
    lines = content[start:].splitlines(keepends=True)
    block = []
    for line in lines:
        is_top_level = line and not line.startswith(" ") and not line.startswith(chr(9))
        if block and is_top_level and ":" in line and not line.startswith("."):
            break
        block.append(line)
    return "".join(block)


def test_gate_lite_target_exists():
    content = _content()
    assert "gate-lite:" in content, "Makefile missing 'gate-lite:' target"


def test_gate_lite_kill_target_exists():
    content = _content()
    assert "gate-lite-kill:" in content, "Makefile missing 'gate-lite-kill:' target"


def test_gate_lite_writes_phase_markers_to_status():
    """gate-lite recipe emits per-phase markers into .gate-lite-status file."""
    recipe_block = _target_block("gate-lite")
    phases = [
        ("lint", "lint"),
        ("dead-code", "dead-code"),
        ("tdd-compliance", "tdd-compliance"),
        ("coverage-gaps", "coverage-gaps"),
        ("typecheck", "typecheck"),
        ("collect", "collect"),
        ("env-writes", "env-writes"),
        ("hook-runtime", "hook-runtime"),
        ("skills-frontmatter", "skills-frontmatter"),
        ("test", "test (unit, 2 workers, fail-fast)"),
        ("smoke", "smoke"),
    ]
    for label, marker_text in phases:
        marker = f"=== GATE-LITE PHASE: {marker_text} ==="
        assert marker in recipe_block, (
            f"gate-lite recipe missing phase marker for {label!r}"
        )


def test_gate_lite_writes_terminal_marker():
    """gate-lite recipe emits terminal PASSED/FAILED markers into .gate-lite-status."""
    recipe_block = _target_block("gate-lite")
    assert "=== GATE-LITE: PASSED ===" in recipe_block, (
        "gate-lite recipe missing PASSED terminal marker"
    )
    assert "=== GATE-LITE: FAILED ===" in recipe_block, (
        "gate-lite recipe missing FAILED terminal marker"
    )


def test_gate_lite_tracks_failed_file():
    """gate-lite recipe touches .gate-lite-failed on any phase failure."""
    recipe_block = _target_block("gate-lite")
    assert ".gate-lite-failed" in recipe_block, (
        "gate-lite recipe missing .gate-lite-failed tracking file"
    )


def test_gate_lite_background_uses_nohup():
    """gate-lite-background must use nohup so the launched gate-lite survives shell exit."""
    recipe_block = _target_block("gate-lite-background")
    assert "nohup" in recipe_block, (
        "gate-lite-background recipe must use nohup"
    )


def test_gate_lite_background_writes_pid_file():
    """gate-lite-background must write .gate-lite-background.pid for status-check."""
    recipe_block = _target_block("gate-lite-background")
    assert ".gate-lite-background.pid" in recipe_block, (
        "gate-lite-background recipe must write .gate-lite-background.pid"
    )


def test_gate_lite_status_check_target_exists():
    content = _content()
    assert "gate-lite-status-check:" in content, (
        "Makefile missing 'gate-lite-status-check:' target"
    )


def test_gate_lite_tail_target_exists():
    content = _content()
    assert "gate-lite-tail:" in content, (
        "Makefile missing 'gate-lite-tail:' target"
    )
