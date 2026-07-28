"""Structural pin: System-Load Gate Before Dispatch Waves codification.

Verifies:
  - Makefile target ``check-system-load`` exists
  - ``scripts/check_system_load.py`` exists and is executable
  - AGENTS.md section with key enforcement phrases
  - Script produces valid output (load, CPU count, verdict) when invoked
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
SCRIPT = ROOT / "scripts" / "check_system_load.py"
AGENTS_MD = ROOT / "AGENTS.md"

# ── Makefile target existence ────────────────────────────────────────────────


def _makefile_text() -> str:
    return MAKEFILE.read_text()


def test_make_target_check_system_load_exists():
    """Makefile must define the ``check-system-load`` target."""
    content = _makefile_text()
    assert "check-system-load:" in content, (
        "Makefile missing 'check-system-load:' target. "
        "AGENTS.md 'System-Load Gate Before Dispatch Waves' section requires it."
    )


# ── Script existence and runnability ─────────────────────────────────────────


def test_script_exists():
    """Script must exist at scripts/check_system_load.py."""
    assert SCRIPT.is_file(), (
        f"Regression: {SCRIPT} is missing. "
        "The system-load check script required by the System-Load Gate policy "
        "does not exist."
    )


def test_script_is_executable():
    """Script must have the owner execute bit set."""
    st = SCRIPT.stat()
    assert st.st_mode & stat.S_IXUSR, (
        f"{SCRIPT} is not executable (owner x-bit missing). Run: chmod +x scripts/check_system_load.py"
    )


def test_script_has_shebang():
    """Script must start with a shebang line so it is directly invocable."""
    first_line = SCRIPT.read_text().splitlines()[0]
    assert first_line.startswith("#!"), (
        f"{SCRIPT} missing shebang on line 1. Script must start with #!/usr/bin/env python3 or equivalent."
    )


# ── AGENTS.md policy section ─────────────────────────────────────────────────


def _agents_text() -> str:
    return AGENTS_MD.read_text()


def test_agents_md_has_system_load_section():
    """AGENTS.md must contain the System-Load Gate Before Dispatch Waves section."""
    text = _agents_text()
    assert "## CRITICAL: System-Load Gate Before Dispatch Waves" in text, (
        "AGENTS.md missing 'CRITICAL: System-Load Gate Before Dispatch Waves' section."
    )


def test_agents_md_contains_before_every_dispatch_phrase():
    """AGENTS.md must enforce load check before every dispatch wave."""
    text = _agents_text()
    assert "Before EVERY dispatch wave" in text, "AGENTS.md missing prose 'Before EVERY dispatch wave' phrase."


def test_agents_md_contains_2x_cpu_count_phrase():
    """AGENTS.md must define the 2x CPU count threshold for trimming waves."""
    text = _agents_text()
    assert "2x the CPU count" in text, "AGENTS.md missing '2x the CPU count' threshold phrase."


def test_agents_md_contains_3x_cpu_count_phrase():
    """AGENTS.md must define the 3x CPU count halt-dispatch threshold."""
    text = _agents_text()
    assert "3x CPU count" in text, "AGENTS.md missing '3x CPU count' halt-dispatch threshold phrase."


def test_agents_md_contains_halt_dispatch_phrase():
    """AGENTS.md must contain the HALT dispatch instruction for 3x overload."""
    text = _agents_text()
    assert "HALT dispatch entirely" in text, "AGENTS.md missing 'HALT dispatch entirely' instruction."


def test_agents_md_references_make_target():
    """AGENTS.md must reference ``make check-system-load`` in the section."""
    text = _agents_text()
    assert "make check-system-load" in text, "AGENTS.md missing reference to 'make check-system-load' target."


# ── Script output validation ─────────────────────────────────────────────────


def _run_script() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_script_produces_valid_output():
    """Script must exit 0 and produce load, CPU count, and verdict on stdout."""
    result = _run_script()
    assert result.returncode == 0, f"check_system_load.py exited {result.returncode}:\n{result.stderr}"
    stdout = result.stdout
    # Output must contain CPU count (an integer)
    assert any(word.isdigit() for word in stdout.split()), f"No integer found in script output:\n{stdout}"

    # Output must contain load values (floats like 2.34 or 0.85)
    assert any("." in token and any(c.isdigit() for c in token) for token in stdout.split()), (
        f"No float-like token (load value) found in script output:\n{stdout}"
    )

    # Output must contain a verdict keyword
    assert any(keyword in stdout.lower() for keyword in ("ok", "warning", "halt", "safe", "overload", "load")), (
        f"No verdict keyword found in script output:\n{stdout}"
    )
