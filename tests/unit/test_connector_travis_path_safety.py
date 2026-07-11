from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from general_ludd.connectors.travis import TravisSource


class _FakeTransport:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def __call__(self, method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        self.calls.append((method, url, dict(headers), timeout))
        return self.status, self.body


def _config(**overrides: object) -> dict[str, object]:
    base = {"slug": "acme/api", "token_env": "TRAVIS_TEST_TOKEN"}
    base.update(overrides)
    return base


class TestSlugWithTraversalEncoded:
    def test_slug_with_traversal_encoded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRAVIS_TOKEN", "fake")
        transport = _FakeTransport(200, b'{"builds": []}')
        src = TravisSource(_config(slug="org/repo/../../evil"), transport=transport)
        src.query()
        url = transport.calls[-1][1]
        assert "%2F..%2F..%2Fevil" in url
        assert "../" not in url


class TestSlugQueryParamEncoded:
    def test_slug_query_param_encoded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRAVIS_TOKEN", "fake")
        transport = _FakeTransport(200, b'{"builds": []}')
        src = TravisSource(_config(slug="org/repo?x=y"), transport=transport)
        src.query()
        url = transport.calls[-1][1]
        assert "%3F" in url


class TestJobIdEncoded:
    def test_job_id_encoded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRAVIS_TOKEN", "fake")
        transport = _FakeTransport(200, json.dumps({"content": "log"}).encode())
        src = TravisSource(_config(), transport=transport)
        src.fetch_log(job_id="1/../2")
        url = transport.calls[-1][1]
        assert "../" not in url
        assert "1%2F..%2F2" in url


class TestNormalSlugUnaffected:
    def test_normal_slug_unaffected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRAVIS_TOKEN", "fake")
        transport = _FakeTransport(200, b'{"builds": []}')
        src = TravisSource(_config(slug="owner/name"), transport=transport)
        src.query()
        url = transport.calls[-1][1]
        assert "owner%2Fname" in url
