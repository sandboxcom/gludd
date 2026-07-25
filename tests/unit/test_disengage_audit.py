"""Structural tests for RP.20 disengage audit logging.

Verifies:
  1. Makefile disengage-enforcement target appends to disengage-audit.jsonl
  2. Makefile displays cumulative disengage count
  3. shared.ts defines DISENGAGE_AUDIT_PATH and writes audit records
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
SHARED_TS = ROOT / ".opencode" / "lib" / "shared.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _recipe(name: str, content: str) -> str:
    """Extract the recipe block for a target from Makefile content."""
    match = re.search(rf"^{re.escape(name)}:\n((?:\t[^\n]*\n?)+)", content, re.MULTILINE)
    assert match is not None, f"{name} recipe block not found in Makefile"
    return match.group(1)


class TestDisengageEnforcementAuditFile:
    """disengage-enforcement writes an audit trail."""

    def test_audit_jsonl_path_exists_in_makefile(self):
        """The string disengage-audit.jsonl appears in the Makefile."""
        content = _read(MAKEFILE)
        assert "disengage-audit.jsonl" in content, (
            "disengage-audit.jsonl not found in Makefile"
        )

    def test_audit_append_in_disengage_recipe(self):
        """disengage-enforcement appends (>>) to the audit jsonl."""
        recipe = _recipe("disengage-enforcement", _read(MAKEFILE))
        assert "/tmp/gludd-disengage-audit.jsonl" in recipe, (
            "disengage-enforcement must reference /tmp/gludd-disengage-audit.jsonl"
        )
        assert ">>" in recipe, (
            "disengage-enforcement must append (>>) rather than overwrite the audit log"
        )

    def test_audit_count_display(self):
        """disengage-enforcement prints the cumulative disengage count."""
        recipe = _recipe("disengage-enforcement", _read(MAKEFILE))
        assert "Disengage count" in recipe, (
            "disengage-enforcement must display the cumulative disengage count"
        )
        assert "wc -l" in recipe, (
            "audit count must use wc -l on the audit jsonl"
        )

    def test_audit_max_recommendation(self):
        """The count display includes a max/session recommendation."""
        recipe = _recipe("disengage-enforcement", _read(MAKEFILE))
        assert "max" in recipe.lower(), (
            "disengage-enforcement count display should include max/session guidance"
        )


class TestSharedTsDisengageAuditLogic:
    """shared.ts contains the audit trail implementation."""

    def test_disengage_audit_path_constant(self):
        """DISENGAGE_AUDIT_PATH is defined as an exported constant."""
        content = _read(SHARED_TS)
        assert "DISENGAGE_AUDIT_PATH" in content, (
            "DISENGAGE_AUDIT_PATH constant not found in shared.ts"
        )
        assert "export const DISENGAGE_AUDIT_PATH" in content, (
            "DISENGAGE_AUDIT_PATH must be exported from shared.ts"
        )

    def test_disengage_audit_path_default_value(self):
        """DISENGAGE_AUDIT_PATH defaults to /tmp/gludd-disengage-audit.jsonl."""
        content = _read(SHARED_TS)
        assert "/tmp/gludd-disengage-audit.jsonl" in content, (
            "DISENGAGE_AUDIT_PATH must default to /tmp/gludd-disengage-audit.jsonl"
        )

    def test_disengage_audit_path_env_override(self):
        """DISENGAGE_AUDIT_PATH supports GLUDD_DISENGAGE_AUDIT_PATH env override."""
        content = _read(SHARED_TS)
        assert "GLUDD_DISENGAGE_AUDIT_PATH" in content, (
            "DISENGAGE_AUDIT_PATH must support GLUDD_DISENGAGE_AUDIT_PATH env override"
        )

    def test_append_file_sync_for_audit(self):
        """isDisengaged() writes audit records via appendFileSync."""
        content = _read(SHARED_TS)
        assert "appendFileSync" in content, (
            "isDisengaged() must use appendFileSync for audit log writes"
        )
        assert "DISENGAGE_AUDIT_PATH" in content, (
            "appendFileSync must target DISENGAGE_AUDIT_PATH"
        )

    def test_single_use_writes_audit_record(self):
        """The single-use (expires:1) branch writes an audit record with single:true."""
        content = _read(SHARED_TS)
        # The single-use branch must write an audit record
        assert '"single"' in content or "'single'" in content or "single:" in content or "single: true" in content, (
            "single-use disengage must tag the audit record as 'single'"
        )

    def test_session_uuid_in_audit_record(self):
        """Audit records include a sessionUuid for cross-process attribution."""
        content = _read(SHARED_TS)
        assert "_sessionUuid" in content or "sessionUuid" in content, (
            "audit records must include a session uuid for attribution"
        )

    def test_fail_open_on_audit_write(self):
        """Audit file writes are fail-open (appendFileSync in try block)."""
        content = _read(SHARED_TS)
        # Verify the structural pattern: try { ... appendFileSync(DISENGAGE_AUDIT_PATH, ...) ... } catch
        # by searching for appendFileSync followed by DISENGAGE_AUDIT_PATH
        lines = content.split("\n")
        audit_line_indices = [
            i for i, line in enumerate(lines)
            if "appendFileSync" in line and "DISENGAGE_AUDIT_PATH" in line
        ]
        assert len(audit_line_indices) >= 1, (
            "No appendFileSync(DISENGAGE_AUDIT_PATH) calls found in shared.ts"
        )
        # For each appendFileSync(DISENGAGE_AUDIT_PATH, ...) call, verify it is
        # inside a try block by scanning backwards for "try {" before a "} catch"
        # line-ending at the block level.  Ignore `}` inside JSON strings.
        for idx in audit_line_indices:
            found_try = False
            for scan in range(idx, max(idx - 10, -1), -1):
                stripped = lines[scan].strip()
                if stripped.startswith("try {"):
                    found_try = True
                    break
                # only block-level closing brace: "}" or "} catch" at start of line
                if stripped.startswith("}") or stripped.startswith("} catch"):
                    break
            assert found_try, (
                f"appendFileSync(DISENGAGE_AUDIT_PATH) at line {idx+1} "
                f"is not wrapped in a try block (fail-open required)"
            )


class TestDisengageAuditRunnable:
    """Smoke test: the audit jsonl is present and well-formed."""

    def test_audit_file_exists_and_is_jsonl(self):
        """The audit file exists and contains valid JSON records."""
        import json
        audit_path = Path("/tmp/gludd-disengage-audit.jsonl")

        # Run disengage-enforcement to ensure the file exists
        result = subprocess.run(
            ["make", "disengage-enforcement"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
            env={**os.environ, "GLUDD_DISENGAGE_AUDIT_PATH": str(audit_path)},
        )
        assert result.returncode == 0, (
            f"make disengage-enforcement failed: {result.stderr}"
        )
        assert audit_path.exists(), (
            "disengage-enforcement did not create the audit jsonl"
        )
        assert audit_path.stat().st_size > 0, "audit jsonl is empty"

        content = audit_path.read_text()
        valid_count = 0
        for raw_line in content.split("\n"):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
                if isinstance(record, dict) and "ts" in record:
                    valid_count += 1
            except json.JSONDecodeError:
                pass
        assert valid_count > 0, (
            "audit jsonl contains no valid records with 'ts' fields"
        )

    def test_disengage_enforcement_runs_successfully(self):
        """make disengage-enforcement exits 0 and prints confirmation."""
        result = subprocess.run(
            ["make", "disengage-enforcement"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, (
            f"disengage-enforcement exit code: {result.returncode}\nstderr: {result.stderr}"
        )
        assert "Disengage count" in result.stdout, (
            "disengage-enforcement must display the cumulative count"
        )
