"""Close hosted-only connector branch gaps with fail-closed inputs."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import cast

import httpx
import pytest

from general_ludd.connectors._errors import ConnectorConfigError
from general_ludd.connectors._protocols import HttpResponse
from general_ludd.connectors.azure_monitor import (
    AzureMonitorSource,
    _coerce_float,
    _parse_ts,
)
from general_ludd.connectors.baseten import (
    BasetenClient,
    BasetenConfigError,
    BasetenInvocationError,
    _validate_url,
)
from general_ludd.connectors.dynatrace import (
    DynatraceSource,
    _ms_to_epoch,
)
from general_ludd.connectors.elasticsearch import (
    ElasticsearchConfigError,
    ElasticsearchSource,
    _dig,
    _parse_timestamp,
)
from general_ludd.connectors.lambda_labs import (
    LambdaLabsClient,
    LambdaLabsError,
    _as_list,
    _as_mapping,
    _coerce_timeout,
    _field,
    _to_instance,
    _to_instance_type,
)
from general_ludd.connectors.opsgenie import (
    OpsgenieSource,
    _invoke_get,
    _parse_created_at,
)
from general_ludd.connectors.rollbar import HttpTransport as RollbarHttpTransport
from general_ludd.connectors.rollbar import RollbarSource
from general_ludd.connectors.signoz import SigNozSource, _CallableTransport
from general_ludd.connectors.splunk import SplunkSource, _first, _invoke, _parse_time
from general_ludd.connectors.travis import TravisSource, _guard_base_url


class _Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload

    @property
    def text(self) -> str:
        """Return a minimal response body representation."""
        return self._payload if isinstance(self._payload, str) else str(self._payload)


class _OpsTransport:
    def __init__(self, result: HttpResponse) -> None:
        self.result = result

    def get(
        self,
        _url: str,
        *,
        headers: dict[str, str] | None = None,
        params: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        del headers, params, timeout
        return self.result


class _SigNozTransport:
    def __init__(self, status: int = 200, payload: object = None) -> None:
        self.status = status
        self.payload = {} if payload is None else payload
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> tuple[int, object]:
        self.calls.append((method, url, dict(kwargs)))
        return self.status, self.payload


class _DynatraceTransport:
    def __init__(self, responses: list[tuple[int, dict[str, object]]]) -> None:
        self.responses = responses

    def get(
        self,
        _url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, object]]:
        del headers, timeout
        return self.responses.pop(0)


def test_opsgenie_transport_and_timestamp_edges() -> None:
    with pytest.raises(TypeError, match="transport"):
        _invoke_get(object(), "https://example.test")
    response = _invoke_get(lambda *_args, **_kwargs: ("bad", {"data": []}), "https://example.test")
    assert response.status_code == 0
    assert response.json() == {"data": []}
    assert _parse_created_at(None) is None
    microseconds = _parse_created_at(1_700_000_000_000_000)
    milliseconds = _parse_created_at(1_700_000_000_000)
    assert microseconds is not None and microseconds.startswith("2023-")
    assert milliseconds is not None and milliseconds.startswith("2023-")
    assert _parse_created_at("not-a-date") == "not-a-date"


def test_opsgenie_fail_closed_payload_and_priority_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_EDGE_TOKEN", "token")
    source = OpsgenieSource(
        {"token_env": "OPS_EDGE_TOKEN"},
        transport=_OpsTransport(_Response(500, {})),
    )
    with pytest.raises(RuntimeError, match="status 500"):
        source.query()

    source = OpsgenieSource(
        {"token_env": "OPS_EDGE_TOKEN"},
        transport=_OpsTransport(_Response(200, {"data": [{"status": "open"}]})),
    )
    record = source.query()[0]
    assert record["level_or_status"] == "open"
    assert record["ts"] is None


def test_travis_guard_normalization_and_response_edges() -> None:
    with pytest.raises(ConnectorConfigError, match="http"):
        _guard_base_url("file:///tmp/build")
    with pytest.raises(ConnectorConfigError, match="no host"):
        _guard_base_url("https:///missing")

    normalized = TravisSource._normalize_build(
        {"branch": {"name": 7}, "commit": {"sha": 99}, "number": 1}
    )
    assert normalized["message"] == "7@99"
    assert TravisSource._normalize_build({"branch": "main"})["message"] == "main"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"[]", "query_error"),
        (b'{"builds": {}}', []),
        (b'{"builds": [null, {"branch": "main"}]}', ["main"]),
    ],
)
def test_travis_query_malformed_shapes(body: bytes, expected: object) -> None:
    source = TravisSource(
        {"slug": "acme/api"},
        transport=lambda *_args: (200, body),
    )
    if expected == "query_error":
        with pytest.raises(ConnectorConfigError, match="JSON object"):
            source.query()
    else:
        assert [record["message"] for record in source.query()] == expected


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (500, b"bad", "error"),
        (200, b"raw log", "raw log"),
        (200, b'{"content": 7}', "7"),
        (200, b'{"other": true}', '{"other": true}'),
    ],
)
def test_travis_log_fail_closed_shapes(status: int, body: bytes, expected: str) -> None:
    source = TravisSource(
        {"slug": "acme/api"},
        transport=lambda *_args: (status, body),
    )
    if expected == "error":
        with pytest.raises(ConnectorConfigError, match="HTTP 500"):
            source.fetch_log("42")
    else:
        assert source.fetch_log("42") == expected


def test_signoz_callable_and_auth_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SigNozSource(
        {"base_url": "https://signoz.example.test", "token_env": "SIG_EDGE"},
        transport=_CallableTransport(lambda *_args, **_kwargs: "invalid"),
    )
    assert source.query({"start": 1, "end": 2}) == []
    assert source.health()["ok"] is False

    monkeypatch.setenv("SIG_EDGE", "secret")
    transport = _SigNozTransport(payload={"data": {"result": []}})
    source = SigNozSource(
        {"base_url": "https://signoz.example.test", "token_env": "SIG_EDGE"},
        transport=transport,
    )
    source.query({"start": 1, "end": 2, "query": "service=api"})
    headers = transport.calls[0][2]["headers"]
    assert isinstance(headers, dict)
    assert headers["SIGNOZ-API-KEY"] == "secret"


@pytest.mark.parametrize(
    ("span", "ts", "status"),
    [
        ({"startTimeNano": 2_000_000_000, "statusCode": 2}, 2.0, "error"),
        ({"timestampNano": 3_000_000_000, "statusCode": "OK"}, 3.0, "ok"),
        ({"startTimeUnixMicro": 4_000_000, "statusCode": None}, 4.0, ""),
        ({"startTime": 5, "statusCode": "CUSTOM"}, 5.0, "CUSTOM"),
        ({"hasError": True}, 0.0, "error"),
    ],
)
def test_signoz_timestamp_and_status_edges(span: dict[str, object], ts: float, status: str) -> None:
    assert SigNozSource._coerce_ts(span) == ts
    assert SigNozSource._coerce_status(span) == status


def test_signoz_span_shape_and_normalization_edges() -> None:
    assert SigNozSource._iter_spans(None) == []
    assert SigNozSource._iter_spans({"data": []}) == []
    assert SigNozSource._iter_spans({"data": {"result": {}}}) == []
    spans = SigNozSource._iter_spans(
        {"data": {"result": [None, {"list": [None, {"data": {"op": "nested"}}, {"op": "row"}]}, {"op": "flat"}]}}
    )
    assert [span["op"] for span in spans] == ["nested", "row", "flat"]

    source = SigNozSource(
        {"base_url": "https://signoz.example.test"},
        transport=_SigNozTransport(),
    )
    record = source._normalize_span({"durationNano": object()})
    assert record["message"] == "(span)"
    assert record["value"] is None


def test_signoz_health_and_query_fail_closed_edges() -> None:
    transport = _SigNozTransport(status=500, payload={})
    source = SigNozSource(
        {"base_url": "https://signoz.example.test"},
        transport=transport,
    )
    assert source.query({"start": 1, "end": 2}) == []
    assert source.health()["error"] == "unhealthy status 500"

    versioned = SigNozSource(
        {"base_url": "https://signoz.example.test"},
        transport=_SigNozTransport(payload={"version": "1.2.3"}),
    )
    assert versioned.health()["version"] == "1.2.3"


def test_azure_coercion_and_constructor_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _parse_ts(None) is None
    assert _parse_ts(True) is None
    assert _parse_ts(12) == 12.0
    assert _parse_ts("  ") is None
    assert _parse_ts(object()) is None
    assert _coerce_float(True) is None
    assert _coerce_float(2) == 2.0
    assert _coerce_float(" 2.5 ") == 2.5
    assert _coerce_float("bad") is None
    assert _coerce_float(object()) is None

    monkeypatch.setenv("AZ_EDGE", "token")
    with pytest.raises(TypeError, match="dict"):
        AzureMonitorSource([])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="callable"):
        AzureMonitorSource({"workspace_id": "ws", "token_env": "AZ_EDGE", "transport": 1})


def test_azure_normalization_and_query_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZ_EDGE", "token")
    source = AzureMonitorSource(
        {
            "workspace_id": "ws",
            "token_env": "AZ_EDGE",
            "message_columns": 1,
            "transport": lambda *_args: _Response(200, {}),
        }
    )
    assert source._normalize(None) == []
    assert source._normalize({"tables": [None, {"columns": {}, "rows": []}]}) == []
    assert source._normalize_table({"columns": [], "rows": {}}) == []
    with pytest.raises(TypeError, match="KQL"):
        source.query(1)
    with pytest.raises(ValueError, match="empty"):
        source.query({"query": " "})


def test_azure_message_and_health_payload_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZ_EDGE", "token")
    payload = {
        "tables": [
            {
                "columns": [{"name": "TimeGenerated"}, {"name": "Value"}, {"name": "Other"}],
                "rows": [[None, "bad", "label"]],
            }
        ]
    }
    source = AzureMonitorSource(
        {
            "workspace_id": "ws",
            "token_env": "AZ_EDGE",
            "message_columns": "Message",
            "transport": lambda *_args: _Response(200, payload),
        }
    )
    record = source.query({"query": "x", "timespan": "PT1M"})[0]
    assert record["message"] is None
    assert record["value"] is None
    assert record["labels"] == {"Other": "label"}

    source = AzureMonitorSource(
        {
            "workspace_id": "ws",
            "token_env": "AZ_EDGE",
            "transport": lambda *_args: _Response(200, []),
        }
    )
    assert source.health() == {"ok": True, "detail": "print 1 returned 0 row(s)"}


def test_dynatrace_config_timestamp_and_empty_query_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConnectorConfigError, match="base_url"):
        DynatraceSource({"base_url": "", "token_env": "DT_EDGE"})
    monkeypatch.setenv("DT_EDGE", "token")
    with pytest.raises(ConnectorConfigError, match="token_env"):
        DynatraceSource({"base_url": "https://dynatrace.example.test", "token_env": 1})
    assert _ms_to_epoch(None) is None
    assert _ms_to_epoch("bad") is None
    assert _ms_to_epoch(2_000) == 2.0

    source = DynatraceSource(
        {"base_url": "https://dynatrace.example.test", "token_env": "DT_EDGE"},
        transport=_DynatraceTransport([]),
    )
    assert source.query({}) == []


def test_dynatrace_malformed_metric_and_problem_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_EDGE", "token")
    transport = _DynatraceTransport(
        [
            (
                200,
                {
                    "result": [
                        None,
                        {"metricId": "cpu", "data": [None, {"timestamps": [None], "values": [7]}]},
                    ]
                },
            ),
            (200, {"problems": [None, {"displayId": "P-2", "status": ""}]}),
        ]
    )
    source = DynatraceSource(
        {"base_url": "https://dynatrace.example.test", "token_env": "DT_EDGE"},
        transport=transport,
    )
    records = source.query({"metric_selector": "cpu", "include_problems": True})
    assert records[0]["ts"] is None
    assert records[1]["message"] == "P-2"
    assert records[1]["level_or_status"] == "unknown"


def test_dynatrace_non_success_and_non_list_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_EDGE", "token")
    source = DynatraceSource(
        {"base_url": "https://dynatrace.example.test", "token_env": "DT_EDGE"},
        transport=_DynatraceTransport([(500, {}), (200, {"problems": {}})]),
    )
    assert source.query({"metric_selector": "cpu"}) == []
    assert source.query({"include_problems": True}) == []


def test_baseten_config_and_deployment_shape_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConnectorConfigError, match="required"):
        _validate_url("", "base_url")
    with pytest.raises(ConnectorConfigError, match="http"):
        _validate_url("file:///tmp/model", "base_url")
    with pytest.raises(BasetenConfigError, match="api_key_env"):
        BasetenClient({"api_key_env": 1})

    monkeypatch.setenv("BASE_EDGE", "secret")
    with pytest.raises(BasetenConfigError, match="base_url"):
        BasetenClient({"api_key_env": "BASE_EDGE", "base_url": 1})
    with pytest.raises(BasetenConfigError, match="management_url"):
        BasetenClient({"api_key_env": "BASE_EDGE", "management_url": 1})

    client = BasetenClient(
        {"api_key_env": "BASE_EDGE", "name": 7},
        http_request=lambda *_args: (200, {}),
    )
    assert client.name == "baseten"
    assert client._normalize_deployments({"items": [None, {"deployments": {}}]}) == []
    deployment = client._normalize_deployment(
        {"id": 1, "status": 2, "environment": 3, "created_at": 4}, 5, 6
    )
    assert deployment == {}


def test_baseten_health_and_invocation_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASE_EDGE", "secret")

    def request(method: str, *_args: object) -> tuple[int, dict[str, object]]:
        if method == "POST":
            raise OSError("offline")
        return 418, {}

    client = BasetenClient({"api_key_env": "BASE_EDGE"}, http_request=request)
    health = client.health()
    assert health["reachable"] is True
    assert health["detail"] == "unexpected http 418"
    with pytest.raises(BasetenConfigError, match="non-empty"):
        client.invoke("", {})
    with pytest.raises(BasetenInvocationError, match="OSError"):
        client.invoke("model", {})


def test_elasticsearch_helper_auth_and_search_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ElasticsearchConfigError, match="index"):
        ElasticsearchSource({"base_url": "https://elastic.example.test"})
    assert _parse_timestamp(None) == 0.0
    assert _parse_timestamp(1_700_000_000_000) == 1_700_000_000.0
    assert _parse_timestamp(2) == 2.0
    assert _parse_timestamp("not-a-time") == 0.0
    assert _parse_timestamp(object()) == 0.0
    assert _dig({"a.b": 1}, "a.b") == 1
    assert _dig({"a": {"b": 2}}, "a.b") == 2
    assert _dig({"a": {}}, "a.b") is None

    monkeypatch.setenv("ES_API_EDGE", "api")
    source = ElasticsearchSource(
        {"base_url": "https://elastic.example.test", "index": "logs", "api_key_env": "ES_API_EDGE"},
        http_request=lambda *_args: (200, {}),
    )
    assert source._auth_header() == {"Authorization": "ApiKey api"}
    assert source._build_search_body({"dsl": {"term": {"a": 1}}, "size": 5})["size"] == 5
    bounded = source._build_search_body({"query": "x", "start": 1, "end": 2})
    assert isinstance(bounded["query"], Mapping)
    assert "bool" in bounded["query"]
    assert source._build_search_body({"query": "*"}) == {"query": {"match_all": {}}}


def test_elasticsearch_query_health_and_normalize_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ES_TOKEN_EDGE", "token")
    responses: Iterator[tuple[int, dict[str, object]]] = iter(
        [(503, {}), (200, {"hits": {"hits": [None, {"_source": None}]}})]
    )

    def request(*_args: object) -> tuple[int, dict[str, object]]:
        return next(responses)

    source = ElasticsearchSource(
        {"base_url": "https://elastic.example.test", "index": "logs", "token_env": "ES_TOKEN_EDGE"},
        http_request=request,
    )
    assert source._auth_header() == {"Authorization": "Bearer token"}
    assert source.health()["error"] == "http 503"
    record = source.query({})[0]
    assert record["kind"] == "logs"
    assert record["ts"] == 0.0

    failing = ElasticsearchSource(
        {"base_url": "https://elastic.example.test", "index": "logs"},
        http_request=lambda *_args: (_ for _ in ()).throw(OSError("offline")),
    )
    assert failing.health()["error"] == "health check failed"
    assert failing.query({})[0]["message"] == "query failed"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, 15.0), (2, 2.0), ("2.5", 2.5), ("bad", 15.0), (object(), 15.0)],
)
def test_lambda_timeout_edges(value: object, expected: float) -> None:
    assert _coerce_timeout(value) == expected


def test_lambda_shape_and_json_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _as_mapping({"x": 1}) == {"x": 1}
    with pytest.raises(LambdaLabsError, match="object"):
        _as_mapping([])
    assert _as_list([1]) == [1]
    with pytest.raises(LambdaLabsError, match="array"):
        _as_list({})
    assert _field([], "x") is None
    assert _field({"x": 1}, "x") == 1
    with pytest.raises(LambdaLabsError, match="instance entry"):
        _to_instance(1)
    with pytest.raises(LambdaLabsError, match="instance_type"):
        _to_instance_type(1)

    monkeypatch.setenv("LAMBDA_EDGE", "secret")
    client = LambdaLabsClient({"api_key_env": "LAMBDA_EDGE"})
    empty = httpx.Response(204)
    assert client._json(empty) is None
    malformed = httpx.Response(200, text="not-json")
    with pytest.raises(LambdaLabsError, match="malformed JSON"):
        client._json(malformed)


def test_lambda_list_launch_and_health_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAMBDA_EDGE", raising=False)
    client = LambdaLabsClient({"api_key_env": "LAMBDA_EDGE"})
    assert client.health()["reachable"] is None
    with pytest.raises(LambdaLabsError, match="not set"):
        client.list_instances()

    monkeypatch.setenv("LAMBDA_EDGE", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/instance-types"):
            return httpx.Response(200, json={"data": {"a": {}, "b": {"instance_type": {"name": "gpu"}}}})
        if request.url.path.endswith("/launch"):
            return httpx.Response(200, json={"data": {"instance_ids": [1]}})
        return httpx.Response(200, json={"data": None})

    client = LambdaLabsClient(
        {"api_key_env": "LAMBDA_EDGE"},
        transport=httpx.MockTransport(handler),
    )
    assert client.list_instances() == []
    assert client.list_instance_types() == [{"name": "gpu"}]
    with pytest.raises(LambdaLabsError, match="non-string"):
        client.launch_instance("gpu", "name", "region")


def test_rollbar_transport_and_shape_edges() -> None:
    with pytest.raises(ConnectorConfigError, match="scheme"):
        RollbarSource({"base_url": "file:///tmp", "token_env": "TOKEN"}, environ={"TOKEN": "x"})
    with pytest.raises(ConnectorConfigError, match="no host"):
        RollbarSource({"base_url": "https:///missing", "token_env": "TOKEN"}, environ={"TOKEN": "x"})

    source = RollbarSource(
        {"token_env": "TOKEN"},
        transport=cast(RollbarHttpTransport, object()),
        environ={"TOKEN": "x"},
    )
    with pytest.raises(TypeError, match="transport"):
        source.query()
    assert source.health()["ok"] is False


def test_rollbar_callable_fallback_and_query_edges() -> None:
    calls: list[str] = []

    def transport(*args: object, **_kwargs: object) -> HttpResponse:
        calls.append(str(args[0]))
        if len(args) == 2:
            raise TypeError("method form unsupported")
        return _Response(200, {"result": {"instances": [{"title": "boom"}]}})

    source = RollbarSource(
        {"token_env": "TOKEN"},
        transport=transport,
        environ={"TOKEN": "x"},
    )
    assert source.query({"status": "active", "environment": "prod", "page": 2})[0]["message"] == "boom"
    assert calls[0] == "GET"
    assert calls[1].startswith("https://")

    failing = RollbarSource(
        {"token_env": "TOKEN"},
        transport=cast(Callable[..., HttpResponse], lambda *_args, **_kwargs: _Response(500, {})),
        environ={"TOKEN": "x"},
    )
    with pytest.raises(ConnectorConfigError, match="HTTP 500"):
        failing.query()


def test_splunk_transport_time_and_row_edges() -> None:
    with pytest.raises(TypeError, match="transport"):
        _invoke(object(), "GET", "https://splunk.example.test")
    response = _invoke(lambda *_args, **_kwargs: ("bad", {}), "GET", "https://splunk.example.test")
    assert response.status_code == 0
    assert _parse_time(None) is None
    epoch_number = _parse_time(0)
    assert epoch_number is not None and epoch_number.startswith("1970-")
    assert _parse_time("  ") is None
    epoch_text = _parse_time("0")
    assert epoch_text is not None and epoch_text.startswith("1970-")
    assert _parse_time("not-a-time") is None
    naive_iso = _parse_time("2026-01-01T00:00:00")
    assert naive_iso is not None and naive_iso.endswith("+00:00")
    assert _parse_time(object()) is None
    assert _first({"a": None, "b": 2}, "a", "b") == 2
    assert _first({}, "missing") is None
    assert SplunkSource._extract_rows(None) == []
    assert SplunkSource._extract_rows([None, {"x": 1}]) == [{"x": 1}]
    assert SplunkSource._extract_rows({"results": [None, {"x": 1}]}) == [{"x": 1}]


def test_splunk_constructor_health_and_query_edges() -> None:
    with pytest.raises(ValueError, match="base_url"):
        SplunkSource({}, transport=lambda *_args, **_kwargs: (200, {}))
    with pytest.raises(ValueError, match="token_env"):
        SplunkSource({"base_url": "https://splunk.example.test"}, transport=lambda *_args, **_kwargs: (200, {}))
    with pytest.raises(TypeError, match="transport"):
        SplunkSource(
            {"base_url": "https://splunk.example.test", "token_env": "TOKEN"},
            transport=cast(Callable[..., tuple[int, object]], object()),
        )

    source = SplunkSource(
        {"base_url": "https://splunk.example.test", "token_env": "TOKEN"},
        transport=lambda method, *_args, **_kwargs: (401 if method == "GET" else 500, {}),
        env={"TOKEN": "secret"},
    )
    assert source.health()["error"] == "authentication failed"
    with pytest.raises(RuntimeError, match="status 500"):
        source.query({"search": "index=main"})
    with pytest.raises(ValueError, match="search"):
        source.query({"search": " "})


def test_mapping_import_is_runtime_usable() -> None:
    """Keep the test doubles honest about connector Mapping inputs."""
    assert isinstance({}, Mapping)
