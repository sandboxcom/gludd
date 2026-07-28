"""Structural pin: BUGS.md contains Session 53 macOS binary crash incident entry."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BUGS_MD = PROJECT_ROOT / "BUGS.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_entry(content: str, keyword: str) -> str:
    """Extract the BUGS.md incident block containing `keyword`."""
    idx = content.find(keyword)
    assert idx >= 0, f"BUGS.md missing entry containing '{keyword}'."
    tail = content[idx:]
    next_incident = tail.find("\n### 2026-", 1)
    return tail if next_incident < 0 else tail[:next_incident]


def test_binary_crash_entry_present() -> None:
    """BUGS.md must contain the macOS binary crash incident dated 2026-07-25."""
    content = _read(BUGS_MD)
    assert "macOS binary crashes" in content, "BUGS.md missing macOS binary crash incident entry."
    assert "Missing base YAML definition file" in content, (
        "BUGS.md must cite the exact ansible error message."
    )


def test_binary_crash_root_cause() -> None:
    """Entry must name the root cause: Analysis datas=datas fix."""
    content = _read(BUGS_MD)
    block = _extract_entry(content, "macOS binary crashes")
    assert "Analysis(datas=" in block, (
        "BUGS.md binary crash entry must reference Analysis(datas=...) root cause."
    )
    assert "datas=datas" in block, (
        "BUGS.md must name the specific fix: Analysis(datas=datas)."
    )


def test_binary_crash_fix_commits() -> None:
    """Entry must list the fix commits."""
    content = _read(BUGS_MD)
    block = _extract_entry(content, "macOS binary crashes")
    assert "bd92fd8a" in block, "BUGS.md must name spec fix commit bd92fd8a."
    assert "af24bde0" in block, "BUGS.md must name CI binary smoke commit af24bde0."
    assert "3d110fa7" in block, "BUGS.md must name bundled resources test commit 3d110fa7."


def test_binary_crash_lesson() -> None:
    """Entry must include the Lesson section."""
    content = _read(BUGS_MD)
    block = _extract_entry(content, "macOS binary crashes")
    assert "Lesson" in block, "BUGS.md binary crash entry must have a Lesson section."
    assert "collect_data_files" in block, (
        "BUGS.md lesson must mention collect_data_files()."
    )


def test_binary_crash_entry_in_incident_log() -> None:
    """The macOS binary crash entry remains in the chronological incident log."""
    content = _read(BUGS_MD)
    log_idx = content.find("## Incident Log")
    assert log_idx >= 0, "BUGS.md must contain an '## Incident Log' section."
    entry_idx = content.find("macOS binary crashes", log_idx)
    assert entry_idx > log_idx, (
        "BUGS.md macOS binary crash incident must remain in the Incident Log."
    )


def test_binary_crash_guardrail_failure() -> None:
    """Entry must document why the guardrail failed."""
    content = _read(BUGS_MD)
    block = _extract_entry(content, "macOS binary crashes")
    assert "Why guardrail failed" in block or "guardrail failed" in block, (
        "BUGS.md binary crash entry must document guardrail failure."
    )
    assert "smoke test" in block.lower(), (
        "BUGS.md must mention the missing binary smoke test guardrail gap."
    )
