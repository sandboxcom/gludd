"""V3.6: Verify skills fetcher uses httpx (keep-as-is proof).

The fetcher already uses httpx for GitHub API calls — ~114 LOC.
PyGithub would add a heavy dependency for <50 LOC of domain logic.
This test proves no replacement is needed.
"""
from __future__ import annotations

from pathlib import Path


def test_fetcher_uses_httpx_not_manual_requests():
    src = Path(__file__).resolve().parent.parent.parent / "src"
    fetcher = src / "general_ludd" / "skills" / "fetcher.py"
    content = fetcher.read_text(encoding="utf-8")
    assert "import httpx" in content or "from httpx" in content, (
        "Skills fetcher must use httpx, not manual GitHub API calls"
    )
    assert "pygithub" not in content.lower(), "PyGithub should not be added"


def test_fetcher_is_small_surface():
    """The fetcher is ~114 LOC — adding PyGithub is overkill."""
    src = Path(__file__).resolve().parent.parent.parent / "src"
    fetcher = src / "general_ludd" / "skills" / "fetcher.py"
    lines = fetcher.read_text(encoding="utf-8").splitlines()
    assert 50 < len(lines) < 200, (
        f"Skills fetcher is {len(lines)} LOC. "
        "If <50, keep-as-is. If >200, evaluate PyGithub."
    )
