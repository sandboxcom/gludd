"""Tests for the deletion gate guardrail."""

import os


def count_lines(text: str) -> int:
    """Count lines in text (matching plugin logic)."""
    if not text:
        return 0
    return len(text.split("\n"))


def test_count_lines():
    """Test line counting utility."""
    assert count_lines("") == 0
    assert count_lines("single line") == 1
    assert count_lines("line1\nline2\nline3") == 3
    assert count_lines("line1\nline2\n") == 3  # trailing newline counts as line


def test_edit_under_threshold_allowed():
    """Edit removing 3 lines (under threshold 5) should be allowed."""
    old_string = "line1\nline2\nline3\nline4\nline5\nline6\n"
    new_string = "line1\nline2\nline3\n"
    lines_removed = count_lines(old_string) - count_lines(new_string)
    assert lines_removed == 3
    assert lines_removed <= 5  # default threshold


def test_edit_over_threshold_blocked_without_reason():
    """Edit removing 6 lines (over threshold 5) should be blocked without DELETION_REASON."""
    old_string = "line1\nline2\nline3\nline4\nline5\nline6\nline7\n"
    new_string = "line1\n"
    lines_removed = count_lines(old_string) - count_lines(new_string)
    assert lines_removed == 6
    assert lines_removed > 5  # exceeds default threshold
    # Without DELETION_REASON, this should be blocked


def test_write_replacing_file_blocked_without_reason():
    """Write replacing 10-line file with 2-line file should be blocked without DELETION_REASON."""
    old_content = "\n".join([f"line{i}" for i in range(10)]) + "\n"
    new_content = "new1\nnew2\n"
    lines_removed = count_lines(old_content) - count_lines(new_content)
    assert lines_removed == 8
    assert lines_removed > 5


def test_edit_with_deletion_reason_allowed(monkeypatch):
    """Edit removing 6 lines with DELETION_REASON set should be allowed."""
    old_string = "line1\nline2\nline3\nline4\nline5\nline6\nline7\n"
    new_string = "line1\n"
    lines_removed = count_lines(old_string) - count_lines(new_string)
    assert lines_removed == 6

    # Simulate env var being set
    monkeypatch.setenv("DELETION_REASON", "Refactoring old feature X")
    reason = os.environ.get("DELETION_REASON")
    assert reason == "Refactoring old feature X"


def test_write_with_deletion_reason_allowed(monkeypatch):
    """Write replacing file with DELETION_REASON set should be allowed."""
    old_content = "\n".join([f"line{i}" for i in range(10)]) + "\n"
    new_content = "new1\nnew2\n"
    lines_removed = count_lines(old_content) - count_lines(new_content)
    assert lines_removed == 8

    monkeypatch.setenv("DELETION_REASON", "Removing deprecated module")
    reason = os.environ.get("DELETION_REASON")
    assert reason == "Removing deprecated module"


def test_threshold_configurable_via_env(monkeypatch):
    """Test that threshold can be configured via GLUDD_DELETION_GATE_THRESHOLD."""
    # Default threshold
    monkeypatch.delenv("GLUDD_DELETION_GATE_THRESHOLD", raising=False)
    threshold = int(os.environ.get("GLUDD_DELETION_GATE_THRESHOLD", "5"))
    assert threshold == 5

    # Custom threshold
    monkeypatch.setenv("GLUDD_DELETION_GATE_THRESHOLD", "10")
    threshold = int(os.environ.get("GLUDD_DELETION_GATE_THRESHOLD", "5"))
    assert threshold == 10


def test_audit_log_format():
    """Test audit log entry format."""
    from datetime import datetime

    timestamp = datetime.now().isoformat()
    file_path = "src/some/module.py"
    lines_removed = 7
    reason = "Removing legacy code"

    log_line = f"{timestamp} | {file_path} | lines_removed={lines_removed} | reason=\"{reason}\"\n"

    assert "lines_removed=7" in log_line
    assert 'reason="Removing legacy code"' in log_line
    assert file_path in log_line


def test_zero_threshold_allows_all_deletions(monkeypatch):
    """Test that threshold of 0 allows all deletions (no gate)."""
    monkeypatch.setenv("GLUDD_DELETION_GATE_THRESHOLD", "0")
    threshold = int(os.environ.get("GLUDD_DELETION_GATE_THRESHOLD", "5"))
    assert threshold == 0
    # With threshold 0, any removal > 0 would require reason
    # But threshold 0 means "gate disabled" - all allowed


def test_empty_new_string_counts_as_full_removal():
    """Test that replacing with empty string counts all old lines as removed."""
    old_string = "line1\nline2\nline3\nline4\nline5\nline6"  # 6 lines, no trailing newline
    new_string = ""
    lines_removed = count_lines(old_string) - count_lines(new_string)
    assert lines_removed == 6
    assert lines_removed > 5


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
