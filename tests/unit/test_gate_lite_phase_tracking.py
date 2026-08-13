"""Tests for the gate-lite infrastructure: Makefile targets + phase markers + .gate-lite-failed tracking.

Reads the Makefile as text and asserts the gate-lite target, per-phase markers
in .gate-lite-status, the .gate-lite-failed tracking file, and the kill target.
Mirrors test_gate_background_targets.py.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _content() -> str:
    assert MAKEFILE.exists(), "Makefile must exist"
    return MAKEFILE.read_text()


def _target_block(content: str, target: str) -> str:
    match = re.search(rf"^{re.escape(target)}:", content, re.MULTILINE)
    assert match is not None
    idx = match.start()
    following = content[match.end() :]
    end = len(content)
    for next_match in re.finditer(r"\n[A-Za-z0-9_.-]+:", following):
        end = match.end() + next_match.start()
        break
    return content[idx:end]


def test_gate_lite_target_exists():
    content = _content()
    assert "gate-lite:" in content, "Makefile missing 'gate-lite:' target"


def test_gate_lite_does_not_require_ignored_recovery_backup():
    """gate-lite must be reproducible from a clean checkout."""
    content = _content()
    dep_line = next(
        line
        for line in content.splitlines()
        if line.startswith("gate-lite:")
    )
    assert "verify-opencode-backup" not in dep_line, (
        "gate-lite must not require gitignored .opencode.orig recovery state"
    )


def test_gate_lite_kill_target_exists():
    content = _content()
    assert "gate-lite-kill:" in content, "Makefile missing 'gate-lite-kill:' target"


def test_gate_lite_writes_phase_markers_to_status():
    """gate-lite recipe emits per-phase markers into .gate-lite-status file."""
    content = _content()
    recipe_block = _target_block(content, "gate-lite")
    phases = [
        ("lint", "lint"),
        ("typecheck", "typecheck"),
        ("collect", "collect"),
        ("env-writes", "env-writes"),
        ("skills-frontmatter", "skills-frontmatter"),
        ("lint-specs", "lint-specs"),
        ("spec-enforcement-coverage", "spec-enforcement-coverage"),
        ("plugin-hook-invoke", "plugin-hook-invoke"),
        ("test", "test (unit, 2 workers, fail-fast)"),
        ("smoke", "smoke"),
    ]
    for label, marker_text in phases:
        marker = f"=== GATE-LITE PHASE: {marker_text} ==="
        assert marker in recipe_block, f"gate-lite recipe missing phase marker for {label!r}"


def test_gate_lite_writes_terminal_marker():
    """gate-lite recipe emits terminal PASSED/FAILED markers into .gate-lite-status."""
    content = _content()
    recipe_block = _target_block(content, "gate-lite")
    assert "=== GATE-LITE: PASSED ===" in recipe_block, (
        "gate-lite recipe missing terminal '=== GATE-LITE: PASSED ===' marker"
    )
    assert "=== GATE-LITE: FAILED ===" in recipe_block, (
        "gate-lite recipe missing terminal '=== GATE-LITE: FAILED ===' marker"
    )


def test_gate_lite_tracks_failed_file():
    """gate-lite recipe touches .gate-lite-failed on any phase failure."""
    content = _content()
    recipe_block = _target_block(content, "gate-lite")
    assert ".gate-lite-failed" in recipe_block, "gate-lite recipe missing .gate-lite-failed tracking file"


def test_gate_lite_background_uses_nohup():
    """gate-lite-background must use nohup so the launched gate-lite survives shell exit."""
    content = _content()
    idx = content.find("gate-lite-background:")
    assert idx != -1
    recipe_block = content[idx : idx + 2000]
    assert "nohup" in recipe_block, "gate-lite-background recipe must use nohup"


def test_gate_lite_background_writes_pid_file():
    """gate-lite-background must write .gate-lite-background.pid for status-check."""
    content = _content()
    idx = content.find("gate-lite-background:")
    recipe_block = content[idx : idx + 2000]
    assert ".gate-lite-background.pid" in recipe_block, (
        "gate-lite-background recipe must write .gate-lite-background.pid"
    )


def test_gate_lite_status_check_target_exists():
    content = _content()
    assert "gate-lite-status-check:" in content, "Makefile missing 'gate-lite-status-check:' target"


def test_gate_lite_tail_target_exists():
    content = _content()
    assert "gate-lite-tail:" in content, "Makefile missing 'gate-lite-tail:' target"


def test_gate_lite_prerequisite_lint_specs():
    """gate-lite includes lint-specs as a prerequisite."""
    content = _content()
    dep_line = next(
        line
        for line in content.split("\n")
        if line.lstrip().startswith("gate-lite:") and not line.lstrip().startswith("#")
    )
    assert "lint-specs" in dep_line, f"lint-specs not in gate-lite prerequisites: {dep_line}"


def test_gate_lite_prerequisite_spec_enforcement_coverage():
    """gate-lite includes check-spec-enforcement-coverage as a prerequisite."""
    content = _content()
    dep_line = next(
        line
        for line in content.split("\n")
        if line.lstrip().startswith("gate-lite:") and not line.lstrip().startswith("#")
    )
    assert "check-spec-enforcement-coverage" in dep_line, (
        f"check-spec-enforcement-coverage not in gate-lite prerequisites: {dep_line}"
    )


def test_gate_lite_prerequisite_plugin_hook_invoke():
    """gate-lite includes check-plugin-hook-invoke as a prerequisite."""
    content = _content()
    dep_line = next(
        line
        for line in content.split("\n")
        if line.lstrip().startswith("gate-lite:") and not line.lstrip().startswith("#")
    )
    assert "check-plugin-hook-invoke" in dep_line, (
        f"check-plugin-hook-invoke not in gate-lite prerequisites: {dep_line}"
    )
