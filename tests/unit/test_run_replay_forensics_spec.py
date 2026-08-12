"""Structural contract for the run replay and forensic-bundle specification."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs/design/specs/SPEC_RUN_REPLAY_FORENSICS.md"


def _spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_replay_spec_is_implementation_ready_and_grounded() -> None:
    text = _spec_text()

    assert "Status: READY-TO-IMPLEMENT" in text
    assert "src/general_ludd/replay/recorder.py" in text
    assert "src/general_ludd/routers/replays.py" in text
    assert "src/general_ludd/cli.py" in text
    assert "RunRecorder" in text


def test_replay_spec_pins_user_forum_evidence() -> None:
    text = _spec_text()

    forum_urls = (
        "https://forum.cursor.com/t/exporting-transcript-doesnt-export-agent-commands/155837",
        "https://forum.cursor.com/t/accessing-the-full-agent-transcript-in-cursor/157311",
        "https://github.com/cline/cline/issues/1213",
        "https://github.com/cline/cline/issues/4578",
    )
    assert all(url in text for url in forum_urls)


def test_replay_spec_covers_operational_and_quality_contracts() -> None:
    text = _spec_text()

    required_sections = (
        "## 4. Versioned bundle contract",
        "## 6. Security and privacy",
        "## 7. Zero-downtime rollout and rollback",
        "## 8. Observability and operations",
        "## 9. Testing and coverage",
        "## 10. Acceptance criteria",
        "## 11. Landing plan",
    )
    assert all(section in text for section in required_sections)
    assert "85%" in text
    assert "75%" in text
    assert "RR-AC-01" in text
    assert "RR-AC-12" in text
    assert "read-only" in text.lower()
    assert "disposable worktree" in text.lower()
    assert "rollback" in text.lower()


def test_replay_spec_does_not_claim_the_feature_is_implemented() -> None:
    text = _spec_text()

    assert "**Status: IMPLEMENTED**" not in text
    assert "- [x] RR-" not in text
