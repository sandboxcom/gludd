"""Path-injection safety tests for Argo Workflows connector URL construction."""

from __future__ import annotations

import json

import pytest

from general_ludd.connectors.argo_workflows import ArgoWorkflowsSource


class _FakeTransport:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
        self.calls.append((method, url, dict(headers), timeout))
        return self.status, self.body


def _config(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "base_url": "https://argo.example.com",
        "namespace": "argo",
        "token_env": "ARGO_TEST_TOKEN",
    }
    base.update(overrides)
    return base


def test_normal_namespace_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGO_TEST_TOKEN", "tok")
    transport = _FakeTransport(200, b'{"items": []}')
    src = ArgoWorkflowsSource(_config(namespace="argo"), transport=transport)
    src.query()
    url = transport.calls[-1][1]
    assert url.endswith("/workflows/argo")
    assert "?" not in url


def test_namespace_with_traversal_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGO_TEST_TOKEN", "tok")
    transport = _FakeTransport(200, b'{"items": []}')
    src = ArgoWorkflowsSource(_config(namespace="argo/../../evil"), transport=transport)
    src.query()
    url = transport.calls[-1][1]
    assert "%2F..%2F..%2Fevil" in url


def test_namespace_query_param_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGO_TEST_TOKEN", "tok")
    transport = _FakeTransport(200, b'{"items": []}')
    src = ArgoWorkflowsSource(_config(namespace="argo?x=y"), transport=transport)
    src.query()
    url = transport.calls[-1][1]
    assert "%3F" in url


def test_namespace_other_specials_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGO_TEST_TOKEN", "tok")
    transport = _FakeTransport(200, b'{"items": []}')
    src = ArgoWorkflowsSource(_config(namespace="ns#frag"), transport=transport)
    src.query()
    url = transport.calls[-1][1]
    assert "%23" in url
