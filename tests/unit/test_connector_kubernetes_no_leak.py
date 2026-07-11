"""Tests for exception text leak prevention and SSRF guard at construction."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.kubernetes import KubernetesSource

TOKEN_ENV = "TEST_K8S_SA_TOKEN"
TOKEN_VALUE = "sa-bearer-token-xyz"


class FakeResponse:
    def __init__(self, status_code: int, *, text: str = "", json_body: Any = None) -> None:
        self.status_code = status_code
        self.text = text
        self._json = json_body

    def json(self) -> Any:
        return self._json


@pytest.fixture(autouse=True)
def _set_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)


def _make_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "api_server": "https://k8s.example.com:6443",
        "namespace": "default",
        "token_env": TOKEN_ENV,
        "timeout_s": 5.0,
    }
    config.update(overrides)
    return config


class RaisingTransport:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        raise self._exc


def test_query_no_exception_text_in_error_record() -> None:
    transport = RaisingTransport(
        RuntimeError("connect to https://k8s.internal:6443?token=SEKRET")
    )
    config = _make_config(transport=transport)
    src = KubernetesSource(config)
    recs = src.query({"mode": "logs", "pod": "p", "container": "c"})
    assert len(recs) == 1
    assert recs[0]["level_or_status"] == "error"
    msg = str(recs[0]["message"])
    assert "SEKRET" not in msg
    assert "token=" not in msg
    assert msg == "query failed"


def test_health_no_exception_leak() -> None:
    transport = RaisingTransport(
        RuntimeError("connect to https://k8s.internal:6443?token=SEKRET")
    )
    config = _make_config(transport=transport)
    src = KubernetesSource(config)
    h = src.health()
    assert h["ok"] is False
    detail = str(h.get("detail", ""))
    assert "SEKRET" not in detail
    assert "token=" not in detail


def test_ssrf_guard_at_construction() -> None:
    with pytest.raises(ValueError):
        KubernetesSource(_make_config(api_server="http://localhost:6443"))


def test_ssrf_guard_at_construction_public_ok() -> None:
    KubernetesSource(_make_config(api_server="https://k8s.example.com:6443"))
