"""Unit tests for AzureResourceGraphSource (mocked transport, no network)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.azure_resource_graph import (
    AzureResourceGraphSource,
    _adapt_http_get,
    _parse_ts,
)

TOKEN_ENV = "GLUDD_TEST_ARM_TOKEN"
SUB = "00000000-0000-0000-0000-000000000001"


class FakeResponse:
    """Minimal stand-in for httpx.Response: status_code + json()."""

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class RecordingTransport:
    """Injectable transport recording calls; returns a queued response. No network."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, str], Any, float]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Any,
        json_body: Any,
        timeout: float,
    ) -> FakeResponse:
        self.calls.append((method, url, dict(headers), json_body, timeout))
        return self.response


def _canned_resources_payload() -> dict[str, Any]:
    return {
        "data": [
            {
                "id": "/subscriptions/sub/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1",
                "name": "vm1",
                "type": "Microsoft.Compute/virtualMachines",
                "resourceGroup": "rg1",
                "location": "eastus",
                "properties": {
                    "provisioningState": "Succeeded",
                    "timeCreated": "2026-06-01T08:30:00Z",
                },
            },
            {
                "id": "/subscriptions/sub/resourceGroups/rg2/providers/Microsoft.Storage/storageAccounts/sa2",
                "name": "sa2",
                "type": "Microsoft.Storage/storageAccounts",
                "resourceGroup": "rg2",
                "location": "westus",
                "properties": {"provisioningState": "Updating"},
            },
        ]
    }


def _make_source(
    monkeypatch: pytest.MonkeyPatch,
    transport: RecordingTransport,
    **overrides: Any,
) -> AzureResourceGraphSource:
    monkeypatch.setenv(TOKEN_ENV, "secret-arm-token")
    config: dict[str, Any] = {
        "name": "arg",
        "subscriptions": [SUB],
        "token_env": TOKEN_ENV,
        "transport": transport,
    }
    config.update(overrides)
    return AzureResourceGraphSource(config)


# -- contract / construction -----------------------------------------------------------


def test_kind_is_infra() -> None:
    assert AzureResourceGraphSource.KIND == "infra"


def test_requires_subscriptions() -> None:
    with pytest.raises(ValueError, match="subscriptions"):
        AzureResourceGraphSource({"token_env": TOKEN_ENV, "subscriptions": []})


def test_defaults_token_env_to_standard_azure_name() -> None:
    source = AzureResourceGraphSource({"subscriptions": [SUB]})
    assert source._token_env == "AZURE_TOKEN"


def test_subscription_id_and_http_get_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, "secret-arm-token")
    calls: list[tuple[str, str]] = []

    def http_get(method: str, url: str, **_: object) -> tuple[int, object]:
        calls.append((method, url))
        return 200, _canned_resources_payload()

    src = AzureResourceGraphSource(
        {"subscription_id": SUB, "token_env": TOKEN_ENV},
        http_get=http_get,
    )

    records = src.query({"query": "Resources"})

    assert src._subscriptions == [SUB]
    assert len(records) == 2
    assert records[0]["kind"] == "infra"
    assert calls == [
        (
            "POST",
            "https://management.azure.com/providers/"
            "Microsoft.ResourceGraph/resources?api-version=2021-03-01",
        )
    ]


def test_http_get_and_config_transport_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="http_get"):
        AzureResourceGraphSource(
            {
                "subscriptions": [SUB],
                "token_env": TOKEN_ENV,
                "transport": lambda *_: FakeResponse(200, {}),
            },
            http_get=lambda *_: (200, {}),
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (42, 42.0),
        ("", None),
        ("not-a-timestamp", None),
        (object(), None),
    ],
)
def test_parse_ts_handles_supported_and_invalid_values(value: object, expected: float | None) -> None:
    assert _parse_ts(value) == expected


def test_construction_rejects_invalid_config_shapes() -> None:
    invalid_config: Any = None
    with pytest.raises(TypeError, match="config must be a dict"):
        AzureResourceGraphSource(invalid_config)
    with pytest.raises(ValueError, match="subscriptions"):
        AzureResourceGraphSource({"subscriptions": object()})
    with pytest.raises(ValueError, match="token_env"):
        AzureResourceGraphSource({"subscriptions": [SUB], "token_env": ""})
    with pytest.raises(TypeError, match="transport"):
        AzureResourceGraphSource({"subscriptions": [SUB], "transport": object()})


def test_http_get_adapter_accepts_response_objects() -> None:
    response = FakeResponse(200, {"data": []})
    transport = _adapt_http_get(lambda *_args, **_kwargs: response)
    assert transport("GET", "https://example.com", {}, None, 1.0) is response


# -- query normalization ---------------------------------------------------------------


def test_query_normalizes_resource_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_resources_payload()))
    src = _make_source(monkeypatch, transport)

    records = src.query("Resources")

    assert len(records) == 2
    rec = records[0]
    assert set(rec) == {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    assert rec["source"] == "arg"
    assert rec["kind"] == "infra"
    # provisioningState -> level_or_status
    assert rec["level_or_status"] == "Succeeded"
    # message = name + type
    assert rec["message"] == "vm1 Microsoft.Compute/virtualMachines"
    # value is None for infra rows
    assert rec["value"] is None
    # labels = {id, resourceGroup, location, type}
    assert rec["labels"] == {
        "id": "/subscriptions/sub/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1",
        "resourceGroup": "rg1",
        "location": "eastus",
        "type": "Microsoft.Compute/virtualMachines",
    }
    # ts parsed from properties.timeCreated
    assert isinstance(rec["ts"], float)
    assert rec["ts"] > 0
    assert rec["raw"]["name"] == "vm1"


def test_query_ts_none_when_no_timecreated(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_resources_payload()))
    src = _make_source(monkeypatch, transport)
    records = src.query("Resources")
    # second row has no properties.timeCreated.
    assert records[1]["ts"] is None
    assert records[1]["level_or_status"] == "Updating"


def test_query_handles_table_shaped_data(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "data": {
            "columns": [
                {"name": "id"},
                {"name": "name"},
                {"name": "type"},
                {"name": "resourceGroup"},
                {"name": "location"},
                {"name": "properties"},
            ],
            "rows": [
                [
                    "/sub/rg/res",
                    "res1",
                    "Microsoft.Network/virtualNetworks",
                    "rg",
                    "eastus2",
                    {"provisioningState": "Succeeded", "timeCreated": "2026-06-02T00:00:00Z"},
                ]
            ],
        }
    }
    transport = RecordingTransport(FakeResponse(200, payload))
    src = _make_source(monkeypatch, transport)
    rec = src.query("Resources")[0]
    assert rec["message"] == "res1 Microsoft.Network/virtualNetworks"
    assert rec["level_or_status"] == "Succeeded"
    assert rec["labels"]["location"] == "eastus2"
    assert isinstance(rec["ts"], float)


# -- transport wiring: endpoint, body, bearer header -----------------------------------


def test_query_posts_to_arm_with_bearer_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_resources_payload()))
    src = _make_source(monkeypatch, transport)

    src.query("Resources | project name")

    assert len(transport.calls) == 1
    method, url, headers, body, _timeout = transport.calls[0]
    assert method == "POST"
    assert url == (
        "https://management.azure.com/providers/Microsoft.ResourceGraph/resources"
        "?api-version=2021-03-01"
    )
    assert headers["Authorization"] == "Bearer secret-arm-token"
    assert body == {"subscriptions": [SUB], "query": "Resources | project name"}


def test_missing_env_token_makes_query_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_resources_payload()))
    src = _make_source(monkeypatch, transport)
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    with pytest.raises(RuntimeError, match="missing bearer token"):
        src.query("Resources")


def test_non_200_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(401, {}))
    src = _make_source(monkeypatch, transport)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        src.query("Resources")


def test_query_rejects_invalid_or_empty_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, {}))
    src = _make_source(monkeypatch, transport)
    with pytest.raises(TypeError, match="spec"):
        src.query(object())
    with pytest.raises(ValueError, match="empty KQL"):
        src.query({})


def test_normalize_rejects_non_mapping_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, []))
    src = _make_source(monkeypatch, transport)
    assert src.query("Resources") == []


# -- SSRF: internal / metadata base_url rejected ---------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "https://localhost",
        "http://10.1.2.3",
        "http://192.168.0.1",
        "http://172.31.255.255",
        "http://169.254.169.254",
        "http://100.100.100.200",  # Alibaba/Azure metadata (canonical guard catches it)
        "https://metadata.google.internal",  # GCP metadata name via canonical guard
        "https://metadata.azure.com",  # Azure metadata name (connector-specific extra)
        "http://metadata.goog/",  # GCP alias missed by the old per-connector blocklist
        "http://foo.localhost/",  # RFC 6761 .localhost TLD missed by the old guard
        "http://internal",  # single-label
    ],
)
def test_internal_base_url_rejected(monkeypatch: pytest.MonkeyPatch, bad_url: str) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    with pytest.raises(ValueError):
        AzureResourceGraphSource(
            {
                "subscriptions": [SUB],
                "token_env": TOKEN_ENV,
                "base_url": bad_url,
            }
        )


def test_public_base_url_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_resources_payload()))
    src = _make_source(monkeypatch, transport, base_url="https://management.azure.com")
    src.query("Resources")
    assert transport.calls[0][1].startswith("https://management.azure.com/")


# -- health ----------------------------------------------------------------------------


def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"data": [{"name": "x", "type": "Microsoft.Foo/bar", "properties": {}}]}
    transport = RecordingTransport(FakeResponse(200, payload))
    src = _make_source(monkeypatch, transport)
    result = src.health()
    assert result["ok"] is True
    assert "row" in result["detail"]
    assert transport.calls[0][3]["query"] == "Resources | limit 1"


def test_health_not_ok_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(503, {}))
    src = _make_source(monkeypatch, transport)
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "health check failed"


def test_health_never_raises_on_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, {}))
    src = _make_source(monkeypatch, transport)
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "health check failed"
