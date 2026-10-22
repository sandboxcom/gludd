"""Verify SESSION.md is maintained correctly: required sections, HEAD matches
git log, test count is plausible, and the file is not stale.

Codified per AGENTS.md "Session Persistence Policy": SESSION.md must carry
last-updated date, last commit hash, and test suite status (counts).
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SESSION_MD = ROOT / "SESSION.md"

HASH_RE = re.compile(r"\b(?=[0-9a-f]{0,39}[a-f])([0-9a-f]{7,40})\b")
DATE_RE = re.compile(r"(?:Last\s*Updated|SESSION\s+\d+\s+(?:FINAL\s+)?—)\s*:?\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
TEST_COUNT_RE = re.compile(
    r"(?:tests?|TOTAL|total)\s*[:-]?\s*:?\s*\**\s*(\d[\d,]*)",
    re.IGNORECASE,
)


def _git_commit_exists(sha: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-t", sha],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "commit"


def _session_text() -> str:
    assert SESSION_MD.exists(), "SESSION.md does not exist"
    text = SESSION_MD.read_text()
    assert text.strip(), "SESSION.md is empty"
    return text


class TestSessionMdExistsAndNonEmpty:
    def test_session_md_exists(self) -> None:
        assert SESSION_MD.exists(), "SESSION.md must exist at repo root"

    def test_session_md_not_empty(self) -> None:
        text = SESSION_MD.read_text()
        assert text.strip(), "SESSION.md must not be empty"


class TestSessionMdRequiredSections:
    def test_has_date(self) -> None:
        text = _session_text()
        match = DATE_RE.search(text)
        assert match, "SESSION.md must have a date in 'Last Updated: YYYY-MM-DD' or 'SESSION N — YYYY-MM-DD' format"
        parsed = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        assert parsed <= date.today(), f"SESSION.md date {parsed} is in the future"
        assert parsed >= date(2025, 1, 1), f"SESSION.md date {parsed} is implausibly old"

    def test_has_head_hash(self) -> None:
        text = _session_text()
        match = HASH_RE.search(text)
        assert match, "SESSION.md must contain a git commit hash (7-40 hex chars)"

    def test_has_test_count(self) -> None:
        text = _session_text()
        match = TEST_COUNT_RE.search(text)
        assert match, "SESSION.md must include test count in format 'test count: N' or 'Total tests: N,NNN'"
        count_str = match.group(1).replace(",", "")
        count = int(count_str)
        assert count >= 100, f"Test count {count} is implausibly low (< 100)"


class TestHeadHashMatchesGitLog:
    def test_session_head_in_git_log(self) -> None:
        text = _session_text()
        session_hashes = HASH_RE.findall(text)
        assert session_hashes, "No commit hashes found in SESSION.md"
        first_session_hash = session_hashes[0]
        assert _git_commit_exists(first_session_hash), (
            f"SESSiON.md HEAD '{first_session_hash}' is not a valid git commit — "
            f"the SESSION.md HEAD may be stale or mistyped"
        )


class TestTestCountPlausible:
    def test_test_count_in_range(self) -> None:
        text = _session_text()
        match = TEST_COUNT_RE.search(text)
        if not match:
            pytest.skip("No test count found in SESSION.md")
        count_str = match.group(1).replace(",", "")
        count = int(count_str)
        assert count >= 1000, f"Test count {count} implausibly low for this repo (expected >= 1,000)"
        assert count <= 200_000, f"Test count {count} implausibly high (expected <= 200,000)"

    def test_test_count_is_number(self) -> None:
        text = _session_text()
        match = TEST_COUNT_RE.search(text)
        if not match:
            pytest.skip("No test count found in SESSION.md")
        count_str = match.group(1).replace(",", "")
        num = int(count_str)
        assert isinstance(num, int)
        assert num > 0


@pytest.mark.parametrize(
    ("session_hash_text", "session_expected_hashes"),
    [
        ("release-candidate/v012-git-mutex-20261022 abcdef0", ["abcdef0"]),
        ("20261022 129261 1234567", []),
        ("abcdef0", ["abcdef0"]),
        ("123456a", ["123456a"]),
        ("a123456", ["a123456"]),
        ("abc1234 abcdef0", ["abc1234", "abcdef0"]),
        ("abc123", []),
        ("a" * 41, []),
    ],
)
def test_session_hash_tokens_follow_the_documented_hex_letter_boundary(
    session_hash_text: str, session_expected_hashes: list[str],
) -> None:
    assert HASH_RE.findall(session_hash_text) == session_expected_hashes


def test_session_head_ignores_numeric_branch_date(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[str] = []

    def commit_exists(sha: str) -> bool:
        checked.append(sha)
        return sha == "abcdef0"

    monkeypatch.setattr(sys.modules[__name__], "_session_text",
                        lambda: "Published: release-candidate/v012-git-mutex-20261022 abcdef0")
    monkeypatch.setattr(sys.modules[__name__], "_git_commit_exists", commit_exists)
    TestHeadHashMatchesGitLog().test_session_head_in_git_log()
    assert checked == ["abcdef0"]


def test_first_real_session_hash_still_requires_a_git_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[str] = []

    def commit_exists(sha: str) -> bool:
        checked.append(sha)
        return False

    monkeypatch.setattr(sys.modules[__name__], "_session_text",
                        lambda: "20261022 abcdef0 eb772a658a01db4485087813e4478eb6c59cc328")
    monkeypatch.setattr(sys.modules[__name__], "_git_commit_exists", commit_exists)
    with pytest.raises(AssertionError, match="abcdef0"):
        TestHeadHashMatchesGitLog().test_session_head_in_git_log()
    assert checked == ["abcdef0"]


class TestSessionMdNotStale:
    def test_last_updated_not_ancient(self) -> None:
        text = _session_text()
        match = DATE_RE.search(text)
        if not match:
            pytest.skip("No date found in SESSION.md")
        parsed = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        age = date.today() - parsed
        assert age <= timedelta(days=30), (
            f"SESSiON.md last updated {parsed} is {age.days} days old — max allowed staleness is 30 days"
        )
