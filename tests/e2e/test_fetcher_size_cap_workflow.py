"""E2E regression coverage for remote skill response-size limits."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.skills.fetcher import RemoteSkillFetcher

_URL = "https://raw.githubusercontent.com/example/repo/main/SKILL.md"
_SKILL = "---\nname: e2e_cap\ndescription: bounded\n---\n# Body\n"


def _response(*, body: bytes, content_length: str | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.content = body
    response.text = body.decode()
    response.headers = (
        {"content-length": content_length}
        if content_length is not None
        else {}
    )
    return response


def test_fetch_rejects_declared_response_larger_than_cap() -> None:
    response = _response(body=b"small", content_length="1000001")
    with patch("general_ludd.skills.fetcher.httpx.get", return_value=response):
        assert RemoteSkillFetcher().fetch(_URL) is None


def test_fetch_rejects_oversized_body_when_header_is_missing() -> None:
    response = _response(body=b"x" * 1_000_001)
    with patch("general_ludd.skills.fetcher.httpx.get", return_value=response):
        assert RemoteSkillFetcher().fetch(_URL) is None


def test_fetch_parses_body_at_or_below_cap() -> None:
    body = _SKILL.encode()
    response = _response(body=body, content_length=str(len(body)))
    with patch("general_ludd.skills.fetcher.httpx.get", return_value=response):
        skill = RemoteSkillFetcher().fetch(_URL)
    assert skill is not None
    assert skill.name == "e2e_cap"
    assert skill.description == "bounded"
