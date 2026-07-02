"""Unit tests for the connector wiring layer (registry.py).

These exercise the ConnectorRegistry that makes the ~50 connectors in
``general_ludd.connectors.*`` reachable from one place (#72/#73): build from an
operator config list (each entry naming a connector module/class + a config dict
that carries only ``*_env`` secret NAMES — never raw secret values), group by
KIND, and offer list()/get()/health_all()/query(name, spec).

Hard invariants under test:
- secrets are never materialized in config or in list() output — only env-var
  NAMES travel through the registry;
- SSRF / least-privilege: query() runs only against an OPERATOR-REGISTERED source
  resolved by name — there is no raw-URL path from a caller into a connector;
- build is best-effort and total: a bad entry is skipped (recorded as an error),
  never aborting the whole build.

Connectors are mocked (registered via the in-process ``register_factory`` hook)
so no real network / DNS / import side effects occur.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.registry import ConnectorRegistry


class _FakeSource:
    """A minimal in-memory Source double honoring the base.Source contract."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = dict(config)
        self.name = str(config.get("name") or "fake")
        self.KIND = str(config.get("kind") or "logs")
        self._records = list(config.get("_records") or [])
        self._healthy = bool(config.get("_healthy", True))
        # Record that no raw secret value ever reached construction.
        self.token_env = config.get("token_env")

    def health(self) -> dict[str, Any]:
        return {"ok": self._healthy, "source": self.name}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        if spec.get("boom"):
            raise RuntimeError("connector blew up")
        return [dict(r, _spec=spec) for r in self._records]


class _BadInit:
    KIND = "logs"

    def __init__(self, config: dict[str, Any]) -> None:
        raise ValueError("cannot construct")


def _reg(*configs: dict[str, Any]) -> ConnectorRegistry:
    """Build a registry from configs, injecting the fake class by factory."""
    factories = {
        "fake": _FakeSource,
        "bad": _BadInit,
    }
    return ConnectorRegistry.from_config(list(configs), factories=factories)


class _ClosableSource(_FakeSource):
    """A source with a background resource to tear down (like MqttSource)."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.disconnected = False

    def disconnect(self) -> None:
        self.disconnected = True


class TestClose:
    def test_close_calls_disconnect_on_each_source(self) -> None:
        reg = ConnectorRegistry.from_config(
            [{"name": "mqtt-ish", "kind": "logs", "factory": "closable"}],
            factories={"closable": _ClosableSource},
        )
        src = reg.get("mqtt-ish")
        assert src is not None
        reg.close()
        assert src.disconnected is True

    def test_close_skips_sources_without_teardown_and_never_raises(self) -> None:
        # _FakeSource has no disconnect/close → close() is a graceful no-op.
        reg = _reg({"name": "plain", "kind": "logs", "factory": "fake"})
        reg.close()  # must not raise


# --------------------------------------------------------------------------- #
# build from config
# --------------------------------------------------------------------------- #
class TestBuildFromConfig:
    def test_build_instantiates_and_registers_named_source(self) -> None:
        reg = _reg({"name": "prod-logs", "kind": "logs", "factory": "fake"})
        src = reg.get("prod-logs")
        assert src is not None
        assert src.name == "prod-logs"
        assert src.KIND == "logs"

    def test_list_returns_metadata_only_no_secrets(self) -> None:
        reg = _reg(
            {
                "name": "datadog-prod",
                "kind": "logs",
                "factory": "fake",
                "token_env": "DD_API_KEY",  # NAME only, not a value
            }
        )
        listing = reg.list_sources()
        assert listing == [
            {"name": "datadog-prod", "kind": "logs", "family": "datadog"}
        ]
        # No secret material — not even the env NAME — leaks into the listing.
        blob = repr(listing)
        assert "DD_API_KEY" not in blob

    def test_group_by_kind(self) -> None:
        reg = _reg(
            {"name": "a", "kind": "logs", "factory": "fake"},
            {"name": "b", "kind": "metrics", "factory": "fake"},
            {"name": "c", "kind": "logs", "factory": "fake"},
        )
        by_kind = reg.by_kind()
        assert sorted(by_kind["logs"]) == ["a", "c"]
        assert by_kind["metrics"] == ["b"]

    def test_bad_entry_is_skipped_not_fatal(self) -> None:
        reg = _reg(
            {"name": "ok", "kind": "logs", "factory": "fake"},
            {"name": "broken", "kind": "logs", "factory": "bad"},
        )
        # The good one is reachable; the bad one is recorded, not raised.
        assert reg.get("ok") is not None
        assert reg.get("broken") is None
        errors = reg.errors()
        assert any(e["name"] == "broken" for e in errors)

    def test_unknown_factory_is_skipped(self) -> None:
        reg = _reg({"name": "x", "kind": "logs", "factory": "does-not-exist"})
        assert reg.get("x") is None
        assert any(e["name"] == "x" for e in reg.errors())

    def test_duplicate_names_last_wins(self) -> None:
        reg = _reg(
            {"name": "dup", "kind": "logs", "factory": "fake", "_healthy": True},
            {"name": "dup", "kind": "logs", "factory": "fake", "_healthy": False},
        )
        assert len(reg.list_sources()) == 1
        assert reg.get("dup").health()["ok"] is False


# --------------------------------------------------------------------------- #
# health_all
# --------------------------------------------------------------------------- #
class TestHealthAll:
    def test_health_all_reports_every_source(self) -> None:
        reg = _reg(
            {"name": "up", "kind": "logs", "factory": "fake", "_healthy": True},
            {"name": "down", "kind": "logs", "factory": "fake", "_healthy": False},
        )
        health = reg.health_all()
        assert health["up"]["ok"] is True
        assert health["down"]["ok"] is False

    def test_health_all_never_raises_on_a_throwing_source(self) -> None:
        class _Throws:
            name = "boom"
            KIND = "logs"

            def __init__(self, config: dict[str, Any]) -> None: ...

            def health(self) -> dict[str, Any]:
                raise RuntimeError("health exploded")

            def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
                return []

        reg = ConnectorRegistry.from_config(
            [{"name": "boom", "kind": "logs", "factory": "t"}],
            factories={"t": _Throws},
        )
        health = reg.health_all()
        assert health["boom"]["ok"] is False
        assert "error" in health["boom"]


# --------------------------------------------------------------------------- #
# query routing
# --------------------------------------------------------------------------- #
class TestQueryRouting:
    def test_query_routes_to_named_source(self) -> None:
        reg = _reg(
            {
                "name": "s1",
                "kind": "logs",
                "factory": "fake",
                "_records": [{"message": "hello"}],
            }
        )
        out = reg.query("s1", {"q": "*"})
        assert out == [{"message": "hello", "_spec": {"q": "*"}}]

    def test_query_unknown_source_raises_keyerror(self) -> None:
        reg = _reg({"name": "s1", "kind": "logs", "factory": "fake"})
        with pytest.raises(KeyError):
            reg.query("not-registered", {"q": "*"})

    def test_query_swallows_connector_exception_as_error_record(self) -> None:
        reg = _reg({"name": "s1", "kind": "logs", "factory": "fake"})
        out = reg.query("s1", {"boom": True})
        assert len(out) == 1
        assert out[0]["level_or_status"] == "error"
        assert out[0]["source"] == "s1"


# --------------------------------------------------------------------------- #
# SSRF / least-privilege
# --------------------------------------------------------------------------- #
class TestSsrfLeastPrivilege:
    def test_no_raw_url_source_can_be_built(self) -> None:
        # A config that tries to smuggle a raw target URL as the *source name*
        # is still just a named, operator-registered source — query() can only
        # be reached by that registered NAME, never by an arbitrary URL.
        reg = _reg(
            {"name": "http://169.254.169.254/latest", "kind": "logs", "factory": "fake"}
        )
        # It is reachable ONLY via its exact registered name, not as a fetchable
        # URL — and a caller cannot inject a different URL into query().
        assert reg.get("http://169.254.169.254/latest") is not None
        with pytest.raises(KeyError):
            reg.query("http://attacker.example/exfil", {})

    def test_real_connector_is_discoverable_via_module_selector(self) -> None:
        # Prove the ~50 SHIPPED connectors are genuinely reachable through the
        # registry's discovery (not just mocks): build a real OktaSource from a
        # config that names its module. Construction is offline (literal-host
        # SSRF guard only; no DNS/network), and only the token ENV NAME travels.
        reg = ConnectorRegistry.from_config(
            [
                {
                    "name": "corp-okta",
                    "kind": "events",
                    "module": "okta",
                    "org_url": "https://example.okta.com",
                    "token_env": "OKTA_API_TOKEN",
                }
            ]
        )
        src = reg.get("corp-okta")
        assert src is not None
        assert type(src).__name__ == "OktaSource"
        assert src.name == "corp-okta"
        # No raw secret value anywhere — only the env-var NAME was configured.
        assert getattr(src, "token_env", None) == "OKTA_API_TOKEN"

    def test_query_takes_no_url_argument(self) -> None:
        # The query surface is (name, spec) — there is no parameter through which
        # a caller could pass a destination URL. This is the SSRF firewall: the
        # only egress targets are the ones an operator registered by config.
        import inspect

        params = list(inspect.signature(ConnectorRegistry.query).parameters)
        assert params == ["self", "name", "spec"]
        assert "url" not in params


# --------------------------------------------------------------------------- #
# class-level interface validation (reject before construction)
# --------------------------------------------------------------------------- #
class _InterfaceSentinel:
    """Marks whether a factory's __init__ was actually invoked."""

    constructed = False

    def __init__(self, config: dict[str, Any]) -> None:
        type(self).constructed = True
        raise RuntimeError("should never be constructed — class is invalid")


class _NoQuery(_InterfaceSentinel):
    """Has KIND + health but is missing the query() method."""

    KIND = "logs"

    def health(self) -> dict[str, Any]:
        return {}


class _NoHealth(_InterfaceSentinel):
    """Has KIND + query but is missing the health() method."""

    KIND = "logs"

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class _NoMethods(_InterfaceSentinel):
    """Has KIND but neither health() nor query()."""

    KIND = "logs"


class TestClassInterfaceValidation:
    """A factory whose class doesn't structurally satisfy the Source contract
    (missing health()/query(), or not callable) MUST be rejected BEFORE its
    __init__ runs — a malformed connector should never be constructed (and
    thus never incur network/secret side effects) just to be discarded."""

    def test_class_missing_query_is_rejected_before_construction(self) -> None:
        _NoQuery.constructed = False
        reg = ConnectorRegistry.from_config(
            [{"name": "bad", "kind": "logs", "factory": "no_query"}],
            factories={"no_query": _NoQuery},
        )
        assert reg.get("bad") is None
        assert _NoQuery.constructed is False
        assert len(reg.errors()) == 1
        err = reg.errors()[0]
        assert err["name"] == "bad"
        # Classified as a discovery failure (bad class), NOT a construct failure.
        assert "discovery" in err["error"]
        assert "query" in err["error"]

    def test_class_missing_health_is_rejected_before_construction(self) -> None:
        _NoHealth.constructed = False
        reg = ConnectorRegistry.from_config(
            [{"name": "bad", "kind": "logs", "factory": "no_health"}],
            factories={"no_health": _NoHealth},
        )
        assert reg.get("bad") is None
        assert _NoHealth.constructed is False
        err = reg.errors()[0]
        assert "discovery" in err["error"]
        assert "health" in err["error"]

    def test_class_missing_both_methods_lists_all_gaps(self) -> None:
        _NoMethods.constructed = False
        reg = ConnectorRegistry.from_config(
            [{"name": "bad", "kind": "logs", "factory": "no_methods"}],
            factories={"no_methods": _NoMethods},
        )
        assert reg.get("bad") is None
        assert _NoMethods.constructed is False
        msg = reg.errors()[0]["error"]
        assert "health" in msg
        assert "query" in msg

    def test_non_callable_factory_is_rejected(self) -> None:
        # A factory entry that isn't even callable (e.g. a bare instance) is a
        # discovery error, not a construct error.
        reg = ConnectorRegistry.from_config(
            [{"name": "bad", "kind": "logs", "factory": "nc"}],
            factories={"nc": object()},  # not callable
        )
        assert reg.get("bad") is None
        msg = reg.errors()[0]["error"]
        assert "discovery" in msg
        assert "callable" in msg

    def test_conforming_class_still_passes(self) -> None:
        # Regression guard: a class that DOES satisfy the interface is still
        # accepted (the new check must not produce false positives).
        reg = ConnectorRegistry.from_config(
            [{"name": "ok", "kind": "logs", "factory": "fake"}],
            factories={"fake": _FakeSource},
        )
        assert reg.get("ok") is not None
        assert reg.errors() == []


# --------------------------------------------------------------------------- #
# Transport-requiring connectors must NOT be silently dropped (regression).
#
# Eight real connectors used to hard-require an injected transport and raised at
# construction. Since the registry only ever calls ``factory(config)`` (single
# arg, no transport), those raises meant every such source landed in ``errors()``
# and never appeared in ``list_sources()`` — silently dropped. They now fall back
# to a real stdlib transport, so building them from operator config (the real
# ``module`` selector path) succeeds and they are reachable. No network is
# touched: construction only validates the (public, non-internal) base_url.
# --------------------------------------------------------------------------- #
_TRANSPORT_CONNECTOR_CONFIGS = [
    {"name": "prom", "kind": "metrics", "module": "prometheus",
     "base_url": "https://prom.example.com:9090"},
    {"name": "dd", "kind": "logs", "module": "datadog",
     "site": "https://api.datadoghq.com"},
    {"name": "thanos", "kind": "metrics", "module": "thanos",
     "base_url": "https://thanos.example.com:10902"},
    {"name": "nagios", "kind": "metrics", "module": "nagios",
     "base_url": "https://nagios.example.com"},
    {"name": "nats", "kind": "metrics", "module": "nats",
     "base_url": "https://nats.example.com"},
    {"name": "kafka", "kind": "metrics", "module": "kafka_exporter",
     "base_url": "https://kafka-exporter.example.com:9308"},
    {"name": "rabbit", "kind": "metrics", "module": "rabbitmq",
     "base_url": "https://rabbit.example.com"},
    {"name": "splunk", "kind": "metrics", "module": "splunk_observability",
     "base_url": "https://api.us0.signalfx.com"},
]


class TestTransportConnectorsNotDropped:
    def test_all_eight_build_with_no_injected_transport(self) -> None:
        # The registry calls factory(config) with NO transport — exactly the
        # production path. All eight must construct and appear in list_sources().
        reg = ConnectorRegistry.from_config(
            [dict(c) for c in _TRANSPORT_CONNECTOR_CONFIGS]
        )
        assert reg.errors() == [], reg.errors()
        listed = {s["name"] for s in reg.list_sources()}
        assert listed == {c["name"] for c in _TRANSPORT_CONNECTOR_CONFIGS}
        # And each is a live, addressable source.
        for cfg in _TRANSPORT_CONNECTOR_CONFIGS:
            assert reg.get(cfg["name"]) is not None
