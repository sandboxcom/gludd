"""Structural tests for RP.20: granular disengage-next + audit logging.

Verifies the Makefile exposes the disengage-next target, that the
disengage-enforcement target writes an audit trail, and that shared.ts
isDisengaged() supports single-use mode.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
SHARED_TS = ROOT / ".opencode" / "lib" / "shared.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestDisengageNextTarget:
    """The new single-operation disengage target."""

    def test_target_exists_in_makefile(self):
        """disengage-next is declared as a target in the Makefile."""
        content = _read(MAKEFILE)
        # target header line
        assert re.search(r"^disengage-next:\s*$", content, re.MULTILINE), (
            "disengage-next target header not found in Makefile"
        )

    def test_target_in_phony_list(self):
        """disengage-next is listed alongside the other enforcement targets."""
        content = _read(MAKEFILE)
        # The target appears in the categorized target listing.
        assert "disengage-next" in content, (
            "disengage-next not referenced anywhere in Makefile"
        )

    def test_target_writes_disengage_file(self):
        """The recipe writes the dedicated single-use disengage marker."""
        content = _read(MAKEFILE)
        # locate the disengage-next recipe block
        match = re.search(
            r"disengage-next:\n((?:\t[^\n]*\n?)+)",
            content,
        )
        assert match is not None, "disengage-next recipe block not found"
        recipe = match.group(1)
        assert "/tmp/gludd-disengage-next" in recipe, (
            "disengage-next must write to /tmp/gludd-disengage-next"
        )

    def test_target_runnable(self):
        """make disengage-next executes and writes the single-use marker."""
        target = "/tmp/gludd-disengage-next"
        with contextlib.suppress(FileNotFoundError):
            os.remove(target)
        try:
            result = subprocess.run(
                ["make", "disengage-next"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(ROOT),
            )
            assert result.returncode == 0, result.stderr
            marker = Path(target)
            assert marker.exists(), "disengage-next did not write the marker"
            assert marker.read_text(encoding="utf-8").strip(), (
                "disengage-next marker must include its creation timestamp"
            )
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(target)


class TestDisengageAuditLogging:
    """The disengage-enforcement target now appends to an audit trail."""

    def test_audit_append_in_makefile(self):
        """disengage-enforcement appends to /tmp/gludd-disengage-audit.jsonl."""
        content = _read(MAKEFILE)
        # locate the disengage-enforcement recipe block
        match = re.search(
            r"disengage-enforcement:\n((?:\t[^\n]*\n?)+)",
            content,
        )
        assert match is not None, "disengage-enforcement recipe block not found"
        recipe = match.group(1)
        assert "/tmp/gludd-disengage-audit.jsonl" in recipe, (
            "disengage-enforcement must reference the audit jsonl"
        )
        # must use append (>>) not overwrite (>)
        assert ">>" in recipe or "disengage-audit.jsonl" in recipe

    def test_count_display_in_makefile(self):
        """disengage-enforcement prints a cumulative disengage count."""
        content = _read(MAKEFILE)
        match = re.search(
            r"disengage-enforcement:\n((?:\t[^\n]*\n?)+)",
            content,
        )
        assert match is not None
        recipe = match.group(1)
        assert "Disengage count" in recipe, (
            "disengage-enforcement must display cumulative count"
        )

    def test_audit_jsonl_path_referenced(self):
        """The audit file path appears in the Makefile."""
        content = _read(MAKEFILE)
        assert "disengage-audit.jsonl" in content


class TestSharedTsSingleUse:
    """shared.ts isDisengaged() supports the dedicated single-use marker."""

    def test_single_use_branch_exists(self):
        """isDisengaged() checks the dedicated marker path."""
        content = _read(SHARED_TS)
        assert "DISENGAGE_NEXT_PATH" in content, (
            "isDisengaged() has no dedicated single-use marker branch"
        )

    def test_single_use_deletes_file(self):
        """The single-use branch unlinks its marker after reading."""
        content = _read(SHARED_TS)
        assert re.search(
            r"(?:unlinkSync|rmSync)\(DISENGAGE_NEXT_PATH",
            content,
        ), (
            "single-use disengage must delete the file after reading"
        )

    def test_node_v26_compatible(self):
        """The single-use branch uses no forbidden Node v26 patterns."""
        content = _read(SHARED_TS)
        # catch { try  is forbidden under --experimental-strip-types
        assert "catch { try" not in content and "catch (e) { try" not in content
