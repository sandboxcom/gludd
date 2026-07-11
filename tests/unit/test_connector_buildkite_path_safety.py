"""Unit tests for Buildkite connector URL path-injection safety."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from general_ludd.connectors.buildkite import BuildkiteSource


class _FakeTransport:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def __call__(self, method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        self.calls.append((method, url, dict(headers), timeout))
        return self.status, self.body


def _config(**overrides: object) -> dict[str, object]:
    base = {"org": "acme", "pipeline": "api", "token_env": "BUILDKITE_TOKEN"}
    base.update(overrides)
    return base


def test_org_with_traversal_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILDKITE_TOKEN", "tok")
    transport = _FakeTransport(200, b"[]")
    src = BuildkiteSource(_config(org="myorg/../../evil"), transport=transport)
    src.query()
    url = transport.calls[-1][1]
    assert "/../" not in url
    assert "%2F" in url


def test_pipeline_special_chars_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILDKITE_TOKEN", "tok")
    transport = _FakeTransport(200, b"[]")
    src = BuildkiteSource(_config(pipeline="main?x=y"), transport=transport)
    src.query()
    url = transport.calls[-1][1]
    assert "%3F" in url


def test_job_id_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILDKITE_TOKEN", "tok")
    transport = _FakeTransport(200, json.dumps({"content": "log"}).encode())
    src = BuildkiteSource(_config(), transport=transport)
    src.fetch_log(job_id="123/../456")
    url = transport.calls[-1][1]
    assert "/../" not in url
    assert "%2F" in url


def test_normal_values_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILDKITE_TOKEN", "tok")
    transport = _FakeTransport(200, b"[]")
    src = BuildkiteSource(_config(), transport=transport)
    src.query()
    url = transport.calls[-1][1]
    assert url == "https://api.buildkite.com/v2/organizations/acme/pipelines/api/builds"
    assert "buildkite.com/v2/organizations/acme/pipelines/api/builds" in url
