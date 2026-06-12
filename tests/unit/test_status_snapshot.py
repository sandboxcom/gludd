"""Tests for status-snapshot in-place writing and SESSION.md drift detector."""

from pathlib import Path

import pytest

from general_ludd.quality.preflight import check_session_drift


class TestStatusSnapshot:
    def test_script_exists(self):
        script = Path(__file__).parent.parent.parent / "scripts" / "status_snapshot.py"
        assert script.exists(), "scripts/status_snapshot.py must exist"

    def test_session_md_has_markers(self):
        session = Path(__file__).parent.parent.parent / "SESSION.md"
        content = session.read_text()
        assert "<!-- gate:begin -->" in content, "SESSION.md missing gate:begin marker"
        assert "<!-- gate:end -->" in content, "SESSION.md missing gate:end marker"

    def test_makefile_status_snapshot_uses_script(self):
        makefile = Path(__file__).parent.parent.parent / "Makefile"
        content = makefile.read_text()
        assert "status_snapshot.py" in content, "Makefile status-snapshot must use status_snapshot.py"

    def test_makefile_status_snapshot_writes_in_place(self):
        makefile = Path(__file__).parent.parent.parent / "Makefile"
        content = makefile.read_text()
        start = content.index("status-snapshot:")
        end = content.index("\n\n", start) if "\n\n" in content[start:] else len(content)
        section = content[start:end]
        assert "/tmp/" not in section, "status-snapshot must write in-place, not to /tmp"


class TestSessionDriftDetector:
    def test_session_not_drifted(self):
        session = Path(__file__).parent.parent.parent / "SESSION.md"
        gate = Path(__file__).parent.parent.parent / ".gate-status"
        if not session.exists() or not gate.exists():
            pytest.skip("SESSION.md or .gate-status missing")
        result = check_session_drift()
        assert result["passed"], (
            "SESSION.md gate block drifted from .gate-status:\n"
            + "\n".join(result.get("violations", []))
        )

    def test_drift_detected_with_stale_block(self):
        result = check_session_drift()
        assert "passed" in result
        assert "violations" in result

    def test_drift_detected_when_markers_missing(self, tmp_path):
        session = tmp_path / "SESSION.md"
        session.write_text("# No markers here\n")
        gate = tmp_path / ".gate-status"
        gate.write_text("lint PASS 0\ntypecheck PASS 18\n")
        import general_ludd.quality.preflight as pf
        orig = pf.REPO_ROOT
        pf.REPO_ROOT = tmp_path
        try:
            result = pf.check_session_drift()
            assert not result["passed"]
        finally:
            pf.REPO_ROOT = orig
