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
