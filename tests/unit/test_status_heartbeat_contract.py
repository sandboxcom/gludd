"""Contract tests for the five-minute pipeline heartbeat target."""

from pathlib import Path


MAKEFILE = Path(__file__).parents[2] / "Makefile"


def test_status_heartbeat_target_has_five_minute_default_and_bounded_count() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "status-heartbeat:" in text
    assert "INTERVAL ?= 300" in text
    assert "COUNT ?= 1" in text
    assert "heartbeat" in text


def test_status_heartbeat_target_records_auditable_state() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    start = text.index("status-heartbeat:")
    recipe = text[start : text.find("\n\n", start)]
    assert "active-work-status" in recipe
    assert "gate-status" in recipe
    assert "pipeline-status" in recipe
    assert "status-heartbeat" in recipe
