"""TDD: gate-status must include a completion marker to prevent false-green commits.

BUGS.md incident #1 (2026-06-10): agent committed with a green-gate claim
while the test suite could not even collect. The root cause: `make test-failures`
grepped only `^FAILED`, missing collection ERRORs. The .gate-status file was
written incrementally but there was no completion marker — a gate killed mid-test
shows "FINISHED" without all PASS lines.

The fix: .gate-status MUST include a terminal marker line (=== GATE: PASSED ===
or === GATE: FAILED ===) and the _gate-fresh-check must require its presence.
"""

import os
import tempfile
from pathlib import Path


class TestGateCompletionMarker:
    """.gate-status needs a verifiable completion marker."""

    def test_gate_status_missing_terminal_marker_is_not_considered_fresh(self):
        """FAIL: a .gate-status without PASSED/FAILED marker is treated as valid."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".gate-status", delete=False) as f:
            f.write("=== GATE 2026-07-06T00:00:00Z ===\n")
            f.write("lint PASS 0\n")
            f.write("typecheck PASS 0\n")
            f.write("collect PASS 0\n")
            # NOTE: test phase was killed — no "test PASS" line, no terminal marker
            gate_path = f.name

        try:
            from scripts.gate_fresh_check import is_gate_complete

            # A gate file missing the terminal marker should NOT be considered complete
            assert not is_gate_complete(Path(gate_path)), (
                "Gate file without === GATE: PASSED === or === GATE: FAILED === "
                "terminal marker must be rejected as incomplete"
            )
        finally:
            os.unlink(gate_path)

    def test_gate_status_with_passed_marker_is_complete(self):
        """A completed gate with the PASSED marker must be recognized."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".gate-status", delete=False) as f:
            f.write("=== GATE 2026-07-06T00:00:00Z ===\n")
            f.write("lint PASS 0\n")
            f.write("typecheck PASS 0\n")
            f.write("collect PASS 0\n")
            f.write("test PASS 0\n")
            f.write("smoke PASS\n")
            f.write("=== GATE: PASSED ===\n")
            gate_path = f.name

        try:
            from scripts.gate_fresh_check import is_gate_complete
            assert is_gate_complete(Path(gate_path)), (
                "Gate file with PASSED marker must be recognized as complete"
            )
        finally:
            os.unlink(gate_path)

    def test_gate_status_with_failed_marker_is_complete_but_failed(self):
        """A gate that completed but FAILED must be recognized (just not green)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".gate-status", delete=False) as f:
            f.write("=== GATE 2026-07-06T00:00:00Z ===\n")
            f.write("lint PASS 0\n")
            f.write("typecheck PASS 0\n")
            f.write("test FAIL 5\n")
            f.write("=== GATE: FAILED ===\n")
            gate_path = f.name

        try:
            from scripts.gate_fresh_check import is_gate_complete, is_gate_passed
            assert is_gate_complete(Path(gate_path)), (
                "Gate file with FAILED marker is complete (ran to end)"
            )
            assert not is_gate_passed(Path(gate_path)), (
                "Gate file with FAILED marker is not green"
            )
        finally:
            os.unlink(gate_path)

    def test_gate_status_interrupted_mid_phase_is_not_complete(self):
        """A gate file that ends mid-phase (no terminal marker) is NOT complete."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".gate-status", delete=False) as f:
            f.write("=== GATE 2026-07-06T00:00:00Z ===\n")
            f.write("lint PASS 0\n")
            f.write("typecheck")  # truncated — gate was killed mid-write
            gate_path = f.name

        try:
            from scripts.gate_fresh_check import is_gate_complete
            assert not is_gate_complete(Path(gate_path)), (
                "Truncated gate file must be rejected as incomplete"
            )
        finally:
            os.unlink(gate_path)

    def test_gate_status_file_does_not_exist_is_not_complete(self):
        """No file = not complete."""
        from scripts.gate_fresh_check import is_gate_complete
        assert not is_gate_complete(Path("/tmp/nonexistent-gate-status-xyz")), (
            "Missing gate file is not complete"
        )
