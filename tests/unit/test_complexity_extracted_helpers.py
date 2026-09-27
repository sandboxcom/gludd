"""Focused coverage for helpers extracted by the complexity-budget refactor."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.connectors import _aws_config_types as aws_types
from general_ludd.connectors._slack_transport import CallableTransportAdapter, invoke_transport


class _Response:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    @property
    def text(self) -> str:
        return str(self._body)

    def json(self) -> object:
        return self._body


def test_invoke_transport_adapts_request_tuple() -> None:
    class RequestOnly:
        def request(self, method: str, url: str, **kwargs: object) -> tuple[int, object]:
            return 201, {"method": method, "url": url, "kwargs": kwargs}

    response = invoke_transport(
        RequestOnly(),
        "POST",
        "https://slack.example/messages",
        json={"text": "hello"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "method": "POST",
        "url": "https://slack.example/messages",
        "kwargs": {"json": {"text": "hello"}},
    }


def test_invoke_transport_supports_legacy_get_and_non_integer_status() -> None:
    class GetOnly:
        def get(self, url: str, **kwargs: object) -> tuple[str, object]:
            return "not-an-integer", {"url": url, "kwargs": kwargs}

    response = invoke_transport(
        GetOnly(),
        "POST",
        "https://slack.example/legacy",
        timeout=2.0,
    )

    assert response.status_code == 0
    assert response.json() == {
        "url": "https://slack.example/legacy",
        "kwargs": {"timeout": 2.0},
    }


def test_invoke_transport_supports_callable_and_rejects_invalid_object() -> None:
    expected = _Response(204, None)

    def callback(method: str, url: str, **kwargs: object) -> _Response:
        assert (method, url, kwargs) == (
            "DELETE",
            "https://slack.example/message/1",
            {"timeout": 1.0},
        )
        return expected

    assert (
        invoke_transport(
            callback,
            "DELETE",
            "https://slack.example/message/1",
            timeout=1.0,
        )
        is expected
    )
    with pytest.raises(TypeError, match="transport must expose"):
        invoke_transport(object(), "POST", "https://slack.example/messages")


def test_callable_transport_adapter_exposes_response_text_and_json() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def callback(method: str, url: str, **kwargs: object) -> tuple[int, object]:
        calls.append((method, url, kwargs))
        if method == "GET":
            return 200, "plain response"
        return 202, {"ok": True}

    adapter = CallableTransportAdapter(callback)
    fetched = adapter.get(
        "https://slack.example/history",
        headers={"Authorization": "Bearer token"},
        params={"limit": 1},
        timeout=4.0,
    )
    posted = adapter.post(
        "https://slack.example/messages",
        headers={"Authorization": "Bearer token"},
        data={"fallback": True},
        json={"text": "hello"},
        timeout=5.0,
    )

    assert fetched.status_code == 200
    assert fetched.text == "plain response"
    assert fetched.json() == "plain response"
    assert posted.status_code == 202
    assert posted.text == "{'ok': True}"
    assert posted.json() == {"ok": True}
    assert [call[0] for call in calls] == ["GET", "POST"]


def test_default_aws_factory_returns_none_when_optional_sdk_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_import(_name: str) -> Any:
        raise ModuleNotFoundError("optional AWS SDK unavailable")

    monkeypatch.setattr(importlib, "import_module", missing_import)

    assert aws_types._default_factory("us-east-1", 3.0) is None


def test_default_aws_factory_builds_region_scoped_and_unscoped_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs: list[dict[str, object]] = []
    client_calls: list[tuple[str, dict[str, object]]] = []

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            configs.append(kwargs)

    class FakeBoto3:
        @staticmethod
        def client(service_name: str, **kwargs: object) -> object:
            client_calls.append((service_name, kwargs))
            return SimpleNamespace(service_name=service_name)

    def fake_import(name: str) -> object:
        if name == "boto3":
            return FakeBoto3
        if name == "botocore.config":
            return SimpleNamespace(Config=FakeConfig)
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(importlib, "import_module", fake_import)

    regional_factory = aws_types._default_factory("us-west-2", 7.5)
    unscoped_factory = aws_types._default_factory(None, 2.0)
    assert regional_factory is not None
    assert unscoped_factory is not None

    regional = regional_factory("config")
    unscoped = unscoped_factory("cloudtrail")

    assert regional is not None
    assert unscoped is not None
    assert regional.service_name == "config"
    assert unscoped.service_name == "cloudtrail"
    assert configs == [
        {"connect_timeout": 7.5, "read_timeout": 7.5, "retries": {"max_attempts": 2}},
        {"connect_timeout": 2.0, "read_timeout": 2.0, "retries": {"max_attempts": 2}},
    ]
    assert client_calls[0][1]["region_name"] == "us-west-2"
    assert "region_name" not in client_calls[1][1]
