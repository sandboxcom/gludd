"""Unit tests for the self-contained Parca continuous-profiling connector.

Every test drives an *injected* mock transport — no real HTTP, no DNS, no shell.
The connector under test must:

- normalize a canned Parca Connect/QueryRange payload into records carrying
  (ts, source, kind, level_or_status, message, value, labels, raw);
- compute per-function/label-set cumulative sample ``value`` and put the top
  function in ``message``;
- read its bearer secret from the env var *named* by ``token_env``;
- block literal internal/private ``base_url`` hosts unless ``allow_private``;
- expose ``health()`` that returns ``{'ok', 'detail'}`` and never raises.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.parca import ParcaSource

# --------------------------------------------------------------------------- #
# Mock transport
# --------------------------------------------------------------------------- #


class _Resp:
    """Minimal response stand-in (status_code + .json())."""

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _MockTransport:
    """Records the last POST and returns a queued response.

    Signature matches the injectable transport contract:
    ``post(url, *, json, headers, timeout) -> response``.
    """

    def __init__(self, response: _Resp | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> _Resp:
        self.calls.append(
            {"url": url, "json": json, "headers": headers or {}, "timeout": timeout}
        )
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


# A canned Parca QueryRange payload. Parca's Connect API returns a series list,
# each series a label-set + samples (timestamp ms + cumulative value).
_CANNED_QUERYRANGE: dict[str, Any] = {
    "series": [
        {
            "labelset": {
                "labels": [
                    {"name": "function", "value": "runtime.mallocgc"},
                    {"name": "comm", "value": "server"},
                    {"name": "job", "value": "api"},
                ]
            },
            "samples": [
                {"timestamp": "1718000000000", "value": "100"},
                {"timestamp": "1718000010000", "value": "150"},
            ],
        },
        {
            "labelset": {
                "labels": [
                    {"name": "function", "value": "net/http.(*conn).serve"},
                    {"name": "comm", "value": "server"},
                    {"name": "job", "value": "api"},
                ]
            },
            "samples": [
                {"timestamp": "1718000000000", "value": "40"},
            ],
        },
    ]
}


def _source(transport: _MockTransport, **overrides: Any) -> ParcaSource:
    config: dict[str, Any] = {
        "name": "parca-prod",
        "base_url": "https://parca.example.com",
        "token_env": "PARCA_TOKEN",
    }
    config.update(overrides)
    return ParcaSource(config, transport=transport)


# --------------------------------------------------------------------------- #
# Contract / identity
# --------------------------------------------------------------------------- #


def test_kind_is_traces_class_attr() -> None:
    assert ParcaSource.KIND == "traces"


def test_name_from_config() -> None:
    src = _source(_MockTransport(_Resp(200, _CANNED_QUERYRANGE)))
    assert src.name == "parca-prod"


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def test_query_normalizes_records() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_QUERYRANGE))
    src = _source(transport)

    records = src.query({"from": 1718000000.0, "to": 1718000100.0})

    assert isinstance(records, list)
    assert len(records) == 2
    rec = records[0]
    # Eight-key normalized shape.
    for key in (
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    ):
        assert key in rec
    assert rec["source"] == "parca-prod"
    assert rec["kind"] == "traces"


def test_value_is_cumulative_samples_per_series() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_QUERYRANGE))
    src = _source(transport)
    records = src.query({})

    by_fn = {r["labels"]["function"]: r for r in records}
    # Series 1: 100 + 150 = 250 cumulative samples.
    assert by_fn["runtime.mallocgc"]["value"] == 250.0
    # Series 2: single sample of 40.
    assert by_fn["net/http.(*conn).serve"]["value"] == 40.0


def test_message_is_top_function_and_labels_populated() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_QUERYRANGE))
    src = _source(transport)
    records = src.query({})

    top = max(records, key=lambda r: r["value"])
    assert top["message"] == "runtime.mallocgc"
    assert top["labels"]["function"] == "runtime.mallocgc"
    assert top["labels"]["comm"] == "server"
    assert top["labels"]["job"] == "api"


def test_ts_is_latest_sample_epoch_seconds() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_QUERYRANGE))
    src = _source(transport)
    records = src.query({})
    by_fn = {r["labels"]["function"]: r for r in records}
    # Latest sample ts in ms -> seconds.
    assert by_fn["runtime.mallocgc"]["ts"] == pytest.approx(1718000010.0)


def test_raw_preserves_series_payload() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_QUERYRANGE))
    src = _source(transport)
    records = src.query({})
    assert records[0]["raw"] is not None


def test_query_is_time_bound() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_QUERYRANGE))
    src = _source(transport, timeout=7.5)
    src.query({})
    assert transport.calls
    assert transport.calls[0]["timeout"] == 7.5


def test_query_posts_to_queryrange_endpoint() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_QUERYRANGE))
    src = _source(transport)
    src.query({})
    url = transport.calls[0]["url"]
    assert url.endswith("/parca.query.v1alpha1.QueryService/QueryRange")
    assert url.startswith("https://parca.example.com")


def test_empty_series_yields_no_records() -> None:
    transport = _MockTransport(_Resp(200, {"series": []}))
    src = _source(transport)
    assert src.query({}) == []


# --------------------------------------------------------------------------- #
# Auth from env
# --------------------------------------------------------------------------- #


def test_bearer_token_read_from_named_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARCA_TOKEN", "s3cr3t")
    transport = _MockTransport(_Resp(200, _CANNED_QUERYRANGE))
    src = _source(transport)
    src.query({})
    assert transport.calls[0]["headers"].get("Authorization") == "Bearer s3cr3t"


def test_no_auth_header_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARCA_TOKEN", raising=False)
    transport = _MockTransport(_Resp(200, _CANNED_QUERYRANGE))
    src = _source(transport)
    src.query({})
    assert "Authorization" not in transport.calls[0]["headers"]


# --------------------------------------------------------------------------- #
# SSRF guard
# --------------------------------------------------------------------------- #


def test_internal_base_url_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        ParcaSource(
            {
                "name": "p",
                "base_url": "http://10.0.0.5:7070",
                "token_env": "PARCA_TOKEN",
            },
            transport=_MockTransport(_Resp(200, _CANNED_QUERYRANGE)),
        )


def test_localhost_base_url_rejected() -> None:
    with pytest.raises(ValueError):
        ParcaSource(
            {"name": "p", "base_url": "http://localhost:7070"},
            transport=_MockTransport(_Resp(200, _CANNED_QUERYRANGE)),
        )


def test_metadata_ip_rejected() -> None:
    with pytest.raises(ValueError):
        ParcaSource(
            {"name": "p", "base_url": "http://169.254.169.254/"},
            transport=_MockTransport(_Resp(200, _CANNED_QUERYRANGE)),
        )


def test_private_base_url_allowed_with_opt_in() -> None:
    src = ParcaSource(
        {
            "name": "p",
            "base_url": "http://10.0.0.5:7070",
            "allow_private": True,
        },
        transport=_MockTransport(_Resp(200, _CANNED_QUERYRANGE)),
    )
    assert src.name == "p"


def test_non_http_scheme_rejected() -> None:
    with pytest.raises(ValueError):
        ParcaSource(
            {"name": "p", "base_url": "file:///etc/passwd", "allow_private": True},
            transport=_MockTransport(_Resp(200, _CANNED_QUERYRANGE)),
        )


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost/",
        "http://metadata.google.internal/",
        "http://169.254.169.254/",
        "http://100.100.100.200/",  # Alibaba/Azure metadata (canonical guard catches it)
        "http://metadata.goog/",  # GCP alias missed by the old per-connector blocklist
        "http://foo.localhost/",  # RFC 6761 .localhost TLD missed by the old guard
    ],
)
def test_canonical_guard_refuses_internal_hosts(bad_url: str) -> None:
    """Default config refuses every loopback/metadata alias via is_url_blocked."""
    with pytest.raises(ValueError):
        ParcaSource(
            {"name": "p", "base_url": bad_url},
            transport=_MockTransport(_Resp(200, _CANNED_QUERYRANGE)),
        )


def test_public_base_url_constructs() -> None:
    src = ParcaSource(
        {"name": "p", "base_url": "https://parca.example.com"},
        transport=_MockTransport(_Resp(200, _CANNED_QUERYRANGE)),
    )
    assert src.name == "p"


def test_localhost_allowed_with_opt_in() -> None:
    src = ParcaSource(
        {"name": "p", "base_url": "http://localhost:7070", "allow_private": True},
        transport=_MockTransport(_Resp(200, _CANNED_QUERYRANGE)),
    )
    assert src.name == "p"


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #


def test_health_ok() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_QUERYRANGE))
    src = _source(transport)
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_http_error() -> None:
    transport = _MockTransport(_Resp(503, {"error": "down"}))
    src = _source(transport)
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_exception() -> None:
    transport = _MockTransport(RuntimeError("connection refused"))
    src = _source(transport)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "transport error: RuntimeError"


def test_query_returns_empty_on_transport_exception() -> None:
    transport = _MockTransport(RuntimeError("boom"))
    src = _source(transport)
    assert src.query({}) == []
