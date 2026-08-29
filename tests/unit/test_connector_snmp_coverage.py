"""Branch contracts for the SNMP connector's fail-closed boundaries."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors import snmp
from general_ludd.connectors._errors import SSRFError
from general_ludd.connectors.snmp import SnmpSource


def test_invalid_configuration_falls_back_to_bounded_defaults() -> None:
    source = SnmpSource({"mode": "unknown", "port": "invalid", "timeout": "invalid"})
    nonpositive = SnmpSource({"timeout": 0})

    assert source.mode == "snmp"
    assert source.port == 161
    assert source.timeout == 5.0
    assert nonpositive.timeout == 5.0


def test_exporter_guard_accepts_public_literal_and_rejects_missing_host() -> None:
    source = SnmpSource()

    assert source._guard_url("https://8.8.8.8/metrics") == "https://8.8.8.8/metrics"
    with pytest.raises(SSRFError, match="missing host"):
        source._guard_url("http:///metrics")


@pytest.mark.parametrize(
    ("resolver", "message"),
    [
        (lambda _host: [], "did not resolve"),
        (lambda _host: ["not-an-address"], "unparseable resolved address"),
    ],
)
def test_exporter_guard_rejects_invalid_resolution(
    resolver: Any,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(snmp, "is_url_blocked", lambda *_args, **_kwargs: False)
    source = SnmpSource(resolver=resolver)

    with pytest.raises(SSRFError, match=message):
        source._guard_url("http://snmp-exp:9116/metrics")


def test_exporter_guard_converts_resolver_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_host: str) -> list[str]:
        raise OSError("resolver offline")

    monkeypatch.setattr(snmp, "is_url_blocked", lambda *_args, **_kwargs: False)
    source = SnmpSource(resolver=unavailable)

    with pytest.raises(SSRFError, match="cannot resolve host"):
        source._guard_url("http://snmp-exp:9116/metrics")


def test_exporter_health_reports_each_transport_boundary() -> None:
    no_url = SnmpSource({"mode": "exporter"})
    no_transport = SnmpSource(
        {"mode": "exporter", "base_url": "http://snmp-exp:9116", "allow_private": True}
    )

    def failed_transport(_url: str, _timeout: float) -> tuple[int, str]:
        raise RuntimeError("probe failed")

    failed = SnmpSource(
        {"mode": "exporter", "base_url": "http://snmp-exp:9116", "allow_private": True},
        transport=failed_transport,
    )
    healthy = SnmpSource(
        {"mode": "exporter", "base_url": "http://snmp-exp:9116", "allow_private": True},
        transport=lambda _url, _timeout: (204, ""),
    )
    unhealthy = SnmpSource(
        {"mode": "exporter", "base_url": "http://snmp-exp:9116", "allow_private": True},
        transport=lambda _url, _timeout: (503, "unavailable"),
    )

    assert no_url.health() == {"ok": False, "detail": "exporter mode requires base_url"}
    assert no_transport.health() == {"ok": False, "detail": "no HTTP transport configured"}
    assert failed.health()["detail"] == "exporter probe failed: probe failed"
    assert healthy.health() == {"ok": True, "detail": "snmp_exporter 204"}
    assert unhealthy.health() == {"ok": False, "detail": "exporter http 503"}


def test_direct_health_and_query_require_owned_configuration() -> None:
    assert SnmpSource().health() == {"ok": False, "detail": "snmp mode requires host"}
    assert SnmpSource().query() == []
    assert SnmpSource({"host": "switch.example"}).query() == []


def test_query_skips_malformed_pairs() -> None:
    def getter(*_args: Any) -> list[object]:
        return [("valid", 1), ("short",), "invalid"]

    records = SnmpSource(getter=getter).query({"oids": ["valid"]})

    assert [record["message"] for record in records] == ["valid"]


def test_opaque_getter_signature_uses_modern_call_shape() -> None:
    class OpaqueGetter:
        @property
        def __signature__(self) -> object:
            raise ValueError("opaque callable")

        def __call__(self, *_args: Any) -> list[object]:
            return []

    assert SnmpSource(getter=OpaqueGetter()).query() == []


def test_exporter_query_skips_invalid_lines_and_preserves_unknown_value() -> None:
    body = 'invalid\nmetric_unknown not-a-number\n{label="value"} 1'
    source = SnmpSource(
        {"mode": "exporter", "base_url": "http://snmp-exp:9116", "allow_private": True},
        transport=lambda _url, _timeout: (200, body),
    )

    records = source.query()

    assert len(records) == 1
    assert records[0]["message"] == "metric_unknown"
    assert records[0]["value"] is None


def test_exporter_query_without_transport_is_empty() -> None:
    source = SnmpSource(
        {"mode": "exporter", "base_url": "http://snmp-exp:9116", "allow_private": True}
    )

    assert source.query() == []


def test_prometheus_helpers_cover_boolean_redaction_and_quoted_labels() -> None:
    assert snmp._coerce_numeric(True) == (1.0, "ok")
    assert snmp._scrub("plain", None) == "plain"
    assert snmp._parse_prom_line("invalid") is None
    unknown_value = snmp._parse_prom_line("metric invalid")
    assert unknown_value is not None
    assert unknown_value[2] is None
    assert snmp._parse_prom_line('{label="value"} 1') is None
    assert snmp._split_labels('first="a,b", second="c"') == [
        'first="a,b"',
        'second="c"',
    ]
    assert snmp._split_labels("") == []
