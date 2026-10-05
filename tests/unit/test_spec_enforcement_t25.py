"""T25: Background gate must report phases.

When `make gate-background` runs, it MUST emit per-phase markers to the
log file. The gate target must include `=== GATE PHASE:` markers for
each phase (lint, typecheck, collect, test, etc.).
"""

import re
from pathlib import Path

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


class TestT25BackgroundGatePhaseMarkers:
    """T25 — gate-background emits phase markers."""

    def test_gate_background_target_exists(self) -> None:
        content = MAKEFILE.read_text()
        assert "\ngate-background:" in content, "T25: gate-background target must exist in Makefile"

    def test_gate_background_writes_to_gate_logs(self) -> None:
        content = MAKEFILE.read_text()
        idx = content.find("\ngate-background:")
        assert idx != -1
        end = content.find("\n\n", idx)
        if end == -1:
            end = len(content)
        recipe = content[idx:end]
        assert ".gate-logs/gate-" in recipe, "T25: gate-background must write output to .gate-logs/gate-<ts>.log"
        assert "nohup" in recipe, "T25: gate-background must use nohup for detached execution"

    def test_gate_target_has_minimum_phase_markers(self) -> None:
        content = MAKEFILE.read_text()
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
        content = MAKEFILE.read_text()
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
        content = MAKEFILE.read_text()
        idx = content.find("\ngate-status-check:")
        assert idx != -1, "T25: gate-status-check target must exist"
        end = content.find("\n\n", idx)
        if end == -1:
            end = len(content)
        recipe = content[idx:end]
        assert "Phase:" in recipe, "T25: gate-status-check must report the current phase"

    def test_gate_background_has_timeout_protection(self) -> None:
        content = MAKEFILE.read_text()
        idx = content.find("\ngate-background:")
        assert idx != -1
        end = content.find("\n\n", idx)
        if end == -1:
            end = len(content)
        recipe = content[idx:end]
        assert "GATE_TIMEOUT" in recipe, "T25: gate-background must have timeout protection"
