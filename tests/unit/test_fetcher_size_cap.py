"""Unit tests for RemoteSkillFetcher response-size cap (M-12).

A remote SKILL.md endpoint that returns a huge body (or advertises one via
content-length) must be rejected before the bytes are fully buffered, to
prevent memory-exhaustion DoS.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.skills.fetcher import RemoteSkillFetcher


def _mock_resp(
    status_code: int = 200,
    content_length: str | None = None,
    body: bytes = b"",
    text: str = "",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.content = body
    headers: dict[str, str] = {}
    if content_length is not None:
        headers["content-length"] = content_length
    resp.headers = headers
    return resp


_SAFE_SKILL_MD = (
    "---\n"
    "name: safe_skill\n"
    "description: A small valid skill\n"
    "---\n"
    "# Safe skill body\n"
)


class TestRemoteSkillFetcherSizeCap:
    _URL = "https://raw.githubusercontent.com/owner/repo/main/SKILL.md"

    def test_oversized_content_length_header_returns_none(self):
        """A content-length header larger than 1 MB must cause fetch() to
        return None without reading the body."""
        resp = _mock_resp(
            content_length="2000000",  # 2 MB — over the 1 MB cap
            body=b"x" * 10,  # body itself is tiny; cap fires on header alone
            text="irrelevant",
        )
        fetcher = RemoteSkillFetcher()
        with patch("general_ludd.skills.fetcher.httpx.get", return_value=resp):
            result = fetcher.fetch(self._URL)
        assert result is None

    def test_oversized_body_without_content_length_returns_none(self):
        """When no content-length header is present but the body exceeds 1 MB,
        fetch() must return None."""
        big_body = b"x" * 1_000_001  # 1 byte over the 1 MB cap
        resp = _mock_resp(
            content_length=None,
            body=big_body,
            text="irrelevant",
        )
        fetcher = RemoteSkillFetcher()
        with patch("general_ludd.skills.fetcher.httpx.get", return_value=resp):
            result = fetcher.fetch(self._URL)
        assert result is None

    def test_small_valid_skill_body_is_parsed(self):
        """A small skill response (well under 1 MB) must be parsed and
        returned normally."""
        resp = _mock_resp(
            content_length=str(len(_SAFE_SKILL_MD.encode())),
            body=_SAFE_SKILL_MD.encode(),
            text=_SAFE_SKILL_MD,
        )
        fetcher = RemoteSkillFetcher()
        with patch("general_ludd.skills.fetcher.httpx.get", return_value=resp):
            result = fetcher.fetch(self._URL)
        assert result is not None
        assert result.name == "safe_skill"
        assert result.description == "A small valid skill"
