"""TDD tests for status-snapshot rewrite and drift detection.

Tests that `make status-snapshot` captures gate data into SESSION.md,
that the drift detector flags divergences, and that a clean snapshot passes.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


def _load_snapshot_mod():
    """Load scripts/status_snapshot.py as a module for testing."""
    spec = importlib.util.spec_from_file_location(
        "status_snapshot",
        REPO_ROOT / "scripts" / "status_snapshot.py",
    )
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestSnapshotCapturesGateData:
    """`make status-snapshot` must read .gate-status and write it into SESSION.md
    between the `<!-- gate:begin -->`..`<!-- gate:end -->` markers."""

    def test_snapshot_captures_gate_data(self, tmp_path):
        mod = _load_snapshot_mod()

        gate_file = tmp_path / ".gate-status"
        gate_file.write_text(
            "===== GATE =====\ntest PASS 89\nlint PASS 0\ntypecheck PASS 0\n"
            "---\nepoch 1751568000\n"
        )

        session_file = tmp_path / "SESSION.md"
        session_file.write_text(
            "## Old Gate Status (2025-01-01)\n"
            "<!-- gate:begin -->\n"
            "<!-- gate:end -->\n"
            "More text after.\n"
        )

        mod.SESSION_MD = session_file
        mod.GATE_STATUS = gate_file

        # Suppress stdout during rewrite
        saved = sys.stdout
        try:
            from io import StringIO
            sys.stdout = StringIO()
            mod.rewrite_session()
        finally:
            sys.stdout = saved

        content = session_file.read_text()
        assert "<!-- gate:begin -->" in content
        assert "<!-- gate:end -->" in content
        assert "- test PASS 89" in content
        assert "- lint PASS 0" in content
        assert "- typecheck PASS 0" in content
        # Must preserve text after the end marker
        assert "More text after." in content
        # Must not include filtered lines
        assert "epoch" not in content
        assert "=====" not in content
        assert "---" not in content


class TestDriftDetectorFlagsChanges:
    """The drift detector must report violations when the SESSION.md gate block
    diverges from the current .gate-status."""

    def test_drift_detector_flags_changes(self, tmp_path):
        gate_file = tmp_path / ".gate-status"
        gate_file.write_text(
            "=== GATE: PASSED ===\n"
            "test PASS 89\nlint PASS 0\ntypecheck PASS 0\nsmoke PASS\n"
        )

        # SESSION.md block is stale — missing "smoke PASS"
        session_file = tmp_path / "SESSION.md"
        session_file.write_text(
            "## Gate Status\n"
            "<!-- gate:begin -->\n"
            "- test PASS 89\n"
            "- lint PASS 0\n"
            "- typecheck PASS 0\n"
            "\n"
            "<!-- gate:end -->\n"
        )

        import general_ludd.quality.preflight as pf
        orig_root = pf.REPO_ROOT
        pf.REPO_ROOT = tmp_path
        try:
            result = pf.check_session_drift()
            assert not result["passed"], "expected drift to be detected"
            assert len(result["violations"]) == 1
            assert "smoke" in result["violations"][0]
        finally:
            pf.REPO_ROOT = orig_root


class TestCleanSnapshotPasses:
    """A SESSION.md gate block that exactly reflects the current .gate-status
    must pass the drift check with zero violations."""

    def test_clean_snapshot_passes(self, tmp_path):
        gate_text = (
            "=== GATE: PASSED ===\n"
            "test PASS 89\nlint PASS 0\ntypecheck PASS 0\nsmoke PASS\n"
        )
        gate_file = tmp_path / ".gate-status"
        gate_file.write_text(gate_text)

        def _snapshot_block(gate_lines):
            lines = []
            for line in gate_lines:
                stripped = line.strip()
                if (
                    not stripped
                    or stripped.startswith("===")
                    or stripped.startswith("---")
                    or stripped.startswith("epoch")
                ):
                    continue
                lines.append(f"- {stripped}")
            return "\n".join(lines)

        block_body = _snapshot_block(gate_text.splitlines())
        session_file = tmp_path / "SESSION.md"
        session_file.write_text(
            "## Gate Status\n"
            "<!-- gate:begin -->\n"
            f"{block_body}\n"
            "\n"
            "<!-- gate:end -->\n"
        )

        import general_ludd.quality.preflight as pf
        orig_root = pf.REPO_ROOT
        pf.REPO_ROOT = tmp_path
        try:
            result = pf.check_session_drift()
            assert result["passed"], (
                "expected clean snapshot to pass, got:\n"
                + "\n".join(result.get("violations", []))
            )
        finally:
            pf.REPO_ROOT = orig_root
