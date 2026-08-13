"""Unit tests for the SNMP connector.

The SNMP getter and the HTTP transport are both mocked: no real device, no real
network. A recurring assertion is that the community string never leaks.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.connectors import snmp as snmp_mod
from general_ludd.connectors.snmp import (
    COMMUNITY_REDACTED,
    SnmpSource,
    SSRFError,
)

COMMUNITY = "super-secret-community"


# -- getter doubles ---------------------------------------------------------


def make_getter(pairs: list[tuple[str, Any]], recorder: dict | None = None):
    def _getter(host, port, community, oids, timeout):
        if recorder is not None:
            recorder["community"] = community
            recorder["oids"] = list(oids)
            recorder["host"] = host
        return pairs

    return _getter


EXPORTER_METRICS = """
# HELP snmp_ifInOctets ifInOctets
# TYPE snmp_ifInOctets counter
snmp_ifInOctets{oid="1.3.6.1.2.1.2.2.1.10",instance="switch-1"} 12345
snmp_ifOperStatus{oid="1.3.6.1.2.1.2.2.1.8",instance="switch-1"} 1
""".strip()


# -- direct snmp normalization ----------------------------------------------


def test_snmp_query_normalizes_records(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", COMMUNITY)
    src = SnmpSource(
        {"name": "core-sw", "host": "10.0.0.9", "community_env": "SNMP_COMMUNITY",
         "oids": ["1.3.6.1.2.1.1.3.0"]},
        getter=make_getter([("1.3.6.1.2.1.1.3.0", 99887766)]),
    )
    records = src.query()
    assert len(records) == 1
    rec = records[0]
    for key in ("ts", "source", "kind", "level_or_status", "message",
                "value", "labels", "raw"):
        assert key in rec
    assert rec["kind"] == "metrics"
    assert rec["value"] == 99887766.0
    assert rec["labels"]["oid"] == "1.3.6.1.2.1.1.3.0"
    assert rec["labels"]["host"] == "10.0.0.9"


def test_snmp_non_numeric_value_keeps_record_with_none_value(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", COMMUNITY)
    src = SnmpSource(
        {"host": "10.0.0.9", "community_env": "SNMP_COMMUNITY"},
        getter=make_getter([("sysDescr", "Cisco IOS Software")]),
    )
    records = src.query({"oids": ["sysDescr"]})
    assert records[0]["value"] is None
    assert records[0]["message"] == "sysDescr"


def test_spec_oids_override_config_oids(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", COMMUNITY)
    rec_box: dict = {}
    src = SnmpSource(
        {"host": "h", "community_env": "SNMP_COMMUNITY", "oids": ["a"]},
        getter=make_getter([("b", 1)], recorder=rec_box),
    )
    src.query({"oids": ["b"]})
    assert rec_box["oids"] == ["b"]


# -- exporter-mode normalization --------------------------------------------


def test_exporter_query_normalizes(monkeypatch):
    def transport(url, timeout):
        assert url.endswith("/metrics")
        return 200, EXPORTER_METRICS

    src = SnmpSource(
        {"mode": "exporter", "base_url": "http://snmp-exp:9116",
         "allow_private": True},
        transport=transport,
    )
    records = src.query()
    assert len(records) == 2
    octets = next(r for r in records if r["message"] == "snmp_ifInOctets")
    assert octets["value"] == 12345.0
    assert octets["labels"]["oid"] == "1.3.6.1.2.1.2.2.1.10"
    assert octets["labels"]["host"] == "switch-1"
    assert octets["labels"]["community"] == COMMUNITY_REDACTED


def test_exporter_non_2xx_returns_empty():
    src = SnmpSource(
        {"mode": "exporter", "base_url": "http://snmp-exp:9116",
         "allow_private": True},
        transport=lambda url, timeout: (500, "err"),
    )
    assert src.query() == []


# -- SSRF / internal handling (exporter mode) -------------------------------


def test_exporter_ssrf_blocks_private_by_default():
    src = SnmpSource(
        {"mode": "exporter", "base_url": "http://snmp-exp:9116"},
        transport=lambda url, timeout: (200, EXPORTER_METRICS),
        resolver=lambda host: ["192.168.1.10"],
    )
    with pytest.raises(SSRFError):
        src.query()


def test_exporter_ssrf_blocks_loopback_literal():
    src = SnmpSource(
        {"mode": "exporter", "base_url": "http://127.0.0.1:9116"},
        transport=lambda url, timeout: (200, ""),
    )
    with pytest.raises(SSRFError):
        src.query()


def test_exporter_rejects_non_http_scheme():
    src = SnmpSource(
        {"mode": "exporter", "base_url": "ftp://host:9116"},
        transport=lambda url, timeout: (200, ""),
    )
    with pytest.raises(SSRFError):
        src.query()


def test_exporter_ssrf_blocks_resolved_cgnat_address():
    # 100.70.1.1 sits in the 100.64.0.0/10 CGNAT range: is_private is False
    # for it in Python's ipaddress module. The OLD local _is_blocked_ip
    # classifier lacked the `not is_global` flag; the canonical
    # _ip_addr_is_blocked import closes that gap.
    src = SnmpSource(
        {"mode": "exporter", "base_url": "http://snmp-exp:9116"},
        transport=lambda url, timeout: (200, EXPORTER_METRICS),
        resolver=lambda host: ["100.70.1.1"],
    )
    with pytest.raises(SSRFError):
        src.query()


def test_exporter_ssrf_blocks_metadata_name_without_resolver_call():
    # "metadata" is a literal blocked NAME via the canonical is_url_blocked
    # pre-check; it must short-circuit before the injected DI resolver is ever
    # consulted.
    def _boom_resolver(host):
        raise AssertionError("resolver must not be consulted for a blocked name")

    src = SnmpSource(
        {"mode": "exporter", "base_url": "http://metadata:9116"},
        transport=lambda url, timeout: (200, EXPORTER_METRICS),
        resolver=_boom_resolver,
    )
    with pytest.raises(SSRFError):
        src.query()


# -- pysnmp-unavailable health ----------------------------------------------


def test_health_pysnmp_unavailable(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", COMMUNITY)

    def unavailable(host, port, community, oids, timeout):
        raise snmp_mod._PysnmpUnavailable("No module named 'pysnmp'")

    src = SnmpSource(
        {"host": "10.0.0.9", "community_env": "SNMP_COMMUNITY"},
        getter=unavailable,
    )
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "pysnmp unavailable"


def test_query_pysnmp_unavailable_returns_empty(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", COMMUNITY)

    def unavailable(host, port, community, oids, timeout):
        raise snmp_mod._PysnmpUnavailable("absent")

    src = SnmpSource(
        {"host": "10.0.0.9", "community_env": "SNMP_COMMUNITY"},
        getter=unavailable,
    )
    assert src.query() == []


def test_default_getter_guards_import(monkeypatch):
    # Force the pysnmp import to fail and assert the guard converts it into the
    # internal sentinel rather than a raw ImportError.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("pysnmp"):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(snmp_mod._PysnmpUnavailable):
        snmp_mod._default_snmp_getter("h", 161, "c", ["1.2.3"], 1.0)


def test_default_getter_uses_loaded_pysnmp_adapter(monkeypatch):
    class _PrettyValue:
        def prettyPrint(self) -> str:
            return "ready"

    def _get_cmd(*_args: Any, **_kwargs: Any):
        return iter([(None, None, None, [("1.2.3", _PrettyValue())])])

    def _constructor(*_args: Any, **_kwargs: Any) -> object:
        return object()

    fake_hlapi = SimpleNamespace(
        CommunityData=_constructor,
        ContextData=_constructor,
        ObjectIdentity=_constructor,
        ObjectType=_constructor,
        SnmpEngine=_constructor,
        UdpTransportTarget=_constructor,
        getCmd=_get_cmd,
    )
    monkeypatch.setattr(importlib, "import_module", lambda _name: fake_hlapi)

    assert snmp_mod._default_snmp_getter("host", 161, "community", ["1.2.3"], 1.0) == [
        ("1.2.3", "ready")
    ]


def test_health_ok_when_getter_reachable(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", COMMUNITY)
    src = SnmpSource(
        {"host": "10.0.0.9", "community_env": "SNMP_COMMUNITY"},
        getter=make_getter([]),
    )
    h = src.health()
    assert h["ok"] is True


def test_health_missing_community(monkeypatch):
    monkeypatch.delenv("SNMP_COMMUNITY", raising=False)
    src = SnmpSource(
        {"host": "10.0.0.9", "community_env": "SNMP_COMMUNITY"},
        getter=make_getter([]),
    )
    h = src.health()
    assert h["ok"] is False
    assert "community" in h["detail"].lower()


# -- secret leak prevention -------------------------------------------------


def test_community_never_in_records(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", COMMUNITY)
    src = SnmpSource(
        {"host": "10.0.0.9", "community_env": "SNMP_COMMUNITY"},
        getter=make_getter([("1.3.6.1.2.1.1.3.0", 5), ("oid2", "text")]),
    )
    records = src.query({"oids": ["1.3.6.1.2.1.1.3.0", "oid2"]})
    blob = repr(records)
    assert COMMUNITY not in blob
    for rec in records:
        assert rec["labels"]["community"] == COMMUNITY_REDACTED
        assert rec["raw"]["community"] == COMMUNITY_REDACTED


def test_community_passed_to_getter_not_stored_plain(monkeypatch):
    # The real community reaches the getter (so SNMP works) but the redaction
    # marker is what surfaces in any emitted/labeled output.
    monkeypatch.setenv("SNMP_COMMUNITY", COMMUNITY)
    rec_box: dict = {}
    src = SnmpSource(
        {"host": "h", "community_env": "SNMP_COMMUNITY"},
        getter=make_getter([("oid", 1)], recorder=rec_box),
    )
    src.query({"oids": ["oid"]})
    assert rec_box["community"] == COMMUNITY  # getter gets the real value


def test_health_error_detail_scrubs_community(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", COMMUNITY)

    def leaky(host, port, community, oids, timeout):
        raise RuntimeError(f"auth failed with community {community}")

    src = SnmpSource(
        {"host": "10.0.0.9", "community_env": "SNMP_COMMUNITY"},
        getter=leaky,
    )
    h = src.health()
    assert h["ok"] is False
    assert COMMUNITY not in h["detail"]
    assert COMMUNITY_REDACTED in h["detail"]


def test_kind_class_attribute():
    assert SnmpSource.KIND == "metrics"
    assert SnmpSource({}).kind == "metrics"


def test_legacy_injected_getter_shape_is_supported(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def legacy_getter(host, community, oid):
        calls.append((host, community, oid))
        return oid, "123"

    src = SnmpSource(getter=legacy_getter)
    records = src.query({"oids": ["1.3.6.1.2.1.1.3"]})
    assert records[0]["value"] == 123.0
    assert calls == [("", "***redacted***", "1.3.6.1.2.1.1.3")]


def test_legacy_getter_health_uses_compatibility_adapter(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", COMMUNITY)

    def legacy_getter(host, community, oid):
        return oid or "probe", "1"

    source = SnmpSource(
        {"host": "10.0.0.9", "community_env": "SNMP_COMMUNITY"},
        getter=legacy_getter,
    )
    assert source.health()["ok"] is True
