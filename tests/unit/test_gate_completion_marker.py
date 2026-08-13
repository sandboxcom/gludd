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
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


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

    def test_failed_marker_after_passed_marker_is_not_green(self, tmp_path: Path):
        from scripts.gate_fresh_check import is_gate_passed

        gate_path = tmp_path / ".gate-status"
        gate_path.write_text(
            "lint PASS 0\n=== GATE: PASSED ===\n"
            "test FAIL 1\n=== GATE: FAILED ===\n",
            encoding="utf-8",
        )

        assert not is_gate_passed(gate_path)

    def test_stale_passed_gate_is_rejected(self, tmp_path: Path):
        from scripts.gate_fresh_check import is_gate_fresh_and_passed

        gate_path = tmp_path / ".gate-status"
        gate_path.write_text(
            "lint PASS 0\n=== GATE: PASSED ===\n",
            encoding="utf-8",
        )
        stale = time.time() - 3600
        os.utime(gate_path, (stale, stale))

        assert not is_gate_fresh_and_passed(gate_path, max_age_seconds=1800)

    def test_check_command_fails_closed_for_red_gate(self, tmp_path: Path):
        gate_path = tmp_path / ".gate-status"
        gate_path.write_text(
            "verify-hot-reload FAIL\n=== GATE: FAILED ===\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "gate_fresh_check.py"),
                "check",
                str(gate_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "not fresh and green" in result.stderr

    def test_make_target_executes_checker_instead_of_echoing_it(self):
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        recipe = makefile.split("\ncheck-gate-fresh:", 1)[1].split("\n\n", 1)[0]

        assert "gate_fresh_check.py check" in recipe
        assert "@echo run python" not in recipe
