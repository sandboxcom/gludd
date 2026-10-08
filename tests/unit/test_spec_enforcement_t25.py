"""T25: Background gate must report phases.

When `make gate-background` runs, it MUST emit per-phase markers to the
log file. The gate target must include `=== GATE PHASE:` markers for
each phase (lint, typecheck, collect, test, etc.).
"""

import re
from pathlib import Path

from scripts.makefile_layout import compose_makefile

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"
LAUNCHER = MAKEFILE.parent / "scripts/start_gate_background.py"


class TestT25BackgroundGatePhaseMarkers:
    """T25 — gate-background emits phase markers."""

    def test_gate_background_target_exists(self) -> None:
        content = compose_makefile(MAKEFILE)
        assert "\ngate-background:" in content, "T25: gate-background target must exist in Makefile"

    def test_gate_background_writes_to_gate_logs(self) -> None:
        content = compose_makefile(MAKEFILE)
        idx = content.find("\ngate-background:")
        assert idx != -1
        end = content.find("\n\n", idx)
        if end == -1:
            end = len(content)
        recipe = content[idx:end]
        assert "scripts/start_gate_background.py" in recipe
        launcher = LAUNCHER.read_text()
        assert 'f"gate-{timestamp}-{run_id[-8:]}.log"' in launcher
        assert "start_new_session=True" in launcher

    def test_gate_target_has_minimum_phase_markers(self) -> None:
        content = compose_makefile(MAKEFILE)
        idx = content.find("\ngate:")
        assert idx != -1
        next_blank = content.find("\n\n", idx)
        if next_blank == -1:
            next_blank = len(content)
        recipe = content[idx:next_blank]
        phases = re.findall(r"=== GATE PHASE: (\w+) ===", recipe)
        required = {"lint", "typecheck", "collect", "test", "smoke"}
        found = set(phases)
        missing = required - found
        assert not missing, (
            f"T25: gate target missing phase markers: {sorted(missing)}. "
            f"Found: {sorted(found)}. "
            "Each phase must emit '=== GATE PHASE: <name> ==='."
        )

    def test_gate_lite_has_minimum_phase_markers(self) -> None:
        content = compose_makefile(MAKEFILE)
        idx = content.find("\ngate-lite:")
        assert idx != -1, "T25: gate-lite target must exist"
        next_blank = content.find("\n\n", idx)
        if next_blank == -1:
            next_blank = len(content)
        recipe = content[idx:next_blank]
        phases = re.findall(r"=== GATE-LITE PHASE: ([\w-]+) ===", recipe)
        required = {"lint", "typecheck", "collect"}
        found = set(phases)
        missing = required - found
        assert not missing, f"T25: gate-lite target missing phase markers: {sorted(missing)}."

    def test_gate_status_check_reports_phase(self) -> None:
        content = compose_makefile(MAKEFILE)
        idx = content.find("\ngate-status-check:")
        assert idx != -1, "T25: gate-status-check target must exist"
        end = content.find("\n\n", idx)
        if end == -1:
            end = len(content)
        recipe = content[idx:end]
        assert "Phase:" in recipe, "T25: gate-status-check must report the current phase"

    def test_gate_background_has_timeout_protection(self) -> None:
        content = compose_makefile(MAKEFILE)
        idx = content.find("\ngate-background:")
        assert idx != -1
        end = content.find("\n\n", idx)
        if end == -1:
            end = len(content)
        recipe = content[idx:end]
        assert "GATE_TIMEOUT" in recipe, "T25: gate-background must have timeout protection"
