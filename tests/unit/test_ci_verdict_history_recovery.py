"""Regression tests for the prior-push CI verdict recovery contract."""

from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def _target_block(start_marker: str, end_marker: str) -> str:
    text = MAKEFILE.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def test_history_guard_recovery_names_the_unverified_sha() -> None:
    """The recovery command must query the SHA that actually armed the latch."""
    block = _target_block("_ci-verdict-history-guard:", "# AA032b")

    assert "make ci-verdict-safe SHA=$$LAST_SHA" in block
    assert "make ci-verdict-safe BRANCH=" not in block


def test_safe_verdict_forwards_and_records_an_explicit_sha() -> None:
    """The prescribed SHA parameter must reach both lookup and persisted verdict."""
    block = _target_block("ci-verdict-safe:", "# ci-record-verdict")

    assert "ci-verdict SHA=$(SHA)" in block
    assert 'if [ -n "$(SHA)" ]; then SHA="$(SHA)"; fi' in block
    assert "record-verdict $$V $$SHA" in block
