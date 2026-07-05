"""Integration tests for observability connector base, registry, and routing.

Proves the ConnectorRegistry + wire_observability pipeline end-to-end:
  - from_config with factories builds a live registry
  - list_sources / by_kind / names / errors work correctly
  - health_all probes every source without raising
  - query(name, spec) returns normalized records
  - wire_observability registers routes on a FastAPI app
  - close() tears down sources with background resources
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from general_ludd.connectors.registry import ConnectorRegistry, _validate_source_class
from general_ludd.routers.observe import (
    ObserveQueryRequest,
    wire_observability,
)

# ── Fake source for tests ───────────────────────────────────────────────


class _FakePrometheusSource:
    """A complete, passable Source: exposes name, KIND, health(), query()."""

    KIND: str = "metrics"

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any) -> None:
        cfg = {**(config or {}), **kwargs}
        self.name = str(cfg.get("name") or "prom-mock")
        self.KIND = str(cfg.get("kind") or "metrics")

    def health(self) -> dict[str, Any]:
        return {"ok": True, "status": "connected"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        limit = int(spec.get("limit", 5))
        return [
            {
                "ts": 1000.0 + i,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "info",
                "message": f"metric_{i}",
                "value": float(i * 10),
                "labels": {"env": "prod"},
                "raw": f"metric_{i} value={i * 10}",
            }
            for i in range(min(limit, 5))
        ]


class _FakeDatadogSource:
    KIND: str = "logs"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self.name = str(config.get("name") or "dd-mock")
        self.KIND = str(config.get("kind") or "logs")

    def health(self) -> dict[str, Any]:
        return {"ok": True, "last_event_ts": 1700000000.0}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "ts": 2000.0,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "warn",
                "message": "disk usage 85%",
                "value": 85.0,
                "labels": {"host": "web-1"},
                "raw": "disk usage at 85% on web-1",
            },
        ]


_FACTORIES = {
    "prometheus": _FakePrometheusSource,
    "datadog": _FakeDatadogSource,
}


# ── ConnectorRegistry Tests ─────────────────────────────────────────────


class TestConnectorRegistryFromConfig:
    """Building a registry from operator config via factories."""

    def test_from_config_with_factories_builds_registry(self) -> None:
        configs = [
            {"name": "prod-prom", "kind": "metrics", "factory": "prometheus"},
            {"name": "prod-dd", "kind": "logs", "factory": "datadog"},
        ]
        reg = ConnectorRegistry.from_config(configs, factories=_FACTORIES)

        assert len(reg.names()) == 2
        assert "prod-prom" in reg.names()
        assert "prod-dd" in reg.names()
        assert reg.errors() == []

    def test_empty_config_returns_empty_registry(self) -> None:
        reg = ConnectorRegistry.from_config(None, factories=_FACTORIES)
        assert reg.names() == []
        assert reg.list_sources() == []

        reg2 = ConnectorRegistry.from_config([], factories=_FACTORIES)
        assert reg2.names() == []

    def test_bad_config_entry_is_recorded_in_errors(self) -> None:
        configs = [
            {"name": "good", "factory": "prometheus"},
            "not_a_dict",  # malformed entry
            {"name": "missing-factory", "kind": "metrics"},  # no selector
        ]
        reg = ConnectorRegistry.from_config(configs, factories=_FACTORIES)

        assert reg.names() == ["good"]
        assert len(reg.errors()) == 2
        assert any("not a dict" in e["error"] for e in reg.errors())

    def test_unknown_factory_recorded_in_errors(self) -> None:
        configs = [{"name": "bad", "factory": "nonexistent"}]
        reg = ConnectorRegistry.from_config(configs, factories=_FACTORIES)

        assert reg.names() == []
        assert len(reg.errors()) == 1
        assert "discovery" in reg.errors()[0]["error"]

    def test_missing_name_recorded_in_errors(self) -> None:
        configs = [{"factory": "prometheus"}]
        reg = ConnectorRegistry.from_config(configs, factories=_FACTORIES)

        assert reg.names() == []
        assert len(reg.errors()) == 1
        assert "missing 'name'" in reg.errors()[0]["error"]


class TestConnectorRegistryReadSurface:
    """list_sources, by_kind, names, get return metadata without secrets."""

    def test_list_sources_returns_metadata_only(self) -> None:
        configs = [
            {"name": "prom-a", "kind": "metrics", "factory": "prometheus"},
        ]
        reg = ConnectorRegistry.from_config(configs, factories=_FACTORIES)

        sources = reg.list_sources()
        assert len(sources) == 1
        s = sources[0]
        assert s["name"] == "prom-a"
        assert s["kind"] == "metrics"
        assert "family" in s

    def test_by_kind_groups_sources(self) -> None:
        configs = [
            {"name": "prom-a", "kind": "metrics", "factory": "prometheus"},
            {"name": "prom-b", "kind": "metrics", "factory": "prometheus"},
            {"name": "dd-a", "kind": "logs", "factory": "datadog"},
        ]
        reg = ConnectorRegistry.from_config(configs, factories=_FACTORIES)

        grouped = reg.by_kind()
        assert grouped["metrics"] == ["prom-a", "prom-b"]
        assert grouped["logs"] == ["dd-a"]

    def test_get_returns_live_source(self) -> None:
        configs = [{"name": "prom-a", "factory": "prometheus"}]
        reg = ConnectorRegistry.from_config(configs, factories=_FACTORIES)

        source = reg.get("prom-a")
        assert source is not None
        assert source.name == "prom-a"

    def test_get_nonexistent_returns_none(self) -> None:
        reg = ConnectorRegistry.from_config([], factories=_FACTORIES)
        assert reg.get("nonexistent") is None


class TestConnectorRegistryHealthAndQuery:
    """health_all and query operate without raising."""

    def test_health_all_probes_every_source(self) -> None:
        configs = [
            {"name": "p1", "factory": "prometheus"},
            {"name": "d1", "factory": "datadog"},
        ]
        reg = ConnectorRegistry.from_config(configs, factories=_FACTORIES)

        health = reg.health_all()
        assert len(health) == 2
        assert health["p1"]["ok"] is True
        assert health["d1"]["ok"] is True

    def test_health_all_empty_registry(self) -> None:
        reg = ConnectorRegistry.from_config([], factories=_FACTORIES)
        assert reg.health_all() == {}

    def test_query_returns_normalized_records(self) -> None:
        configs = [{"name": "prom-a", "factory": "prometheus"}]
        reg = ConnectorRegistry.from_config(configs, factories=_FACTORIES)

        records = reg.query("prom-a", {"limit": 3})
        assert len(records) == 3
        for rec in records:
            assert rec["source"] == "prom-a"
            assert rec["kind"] == "metrics"

    def test_query_unknown_source_raises_key_error(self) -> None:
        reg = ConnectorRegistry.from_config([], factories=_FACTORIES)
        with pytest.raises(KeyError, match="nonexistent"):
            reg.query("nonexistent", {})

    def test_query_failure_returns_error_record(self) -> None:
        class _FailingSource:
            KIND = "metrics"

            def __init__(self, config=None):
                self.name = str(config["name"]) if config else "failer"

            def health(self):
                return {"ok": True}

            def query(self, spec):
                raise RuntimeError("simulated failure")

        reg = ConnectorRegistry.from_config(
            [{"name": "failer", "factory": "failing"}],
            factories={"failing": _FailingSource},
        )
        records = reg.query("failer", {})
        assert len(records) == 1
        assert records[0]["kind"] == "metrics"
        assert records[0]["level_or_status"] == "error"


class TestConnectorRegistryClose:
    """close() tears down sources with disconnect/close methods."""

    def test_close_calls_disconnect_on_sources(self) -> None:
        disconnect_calls: list[str] = []

        class _ClosableSource:
            KIND = "metrics"
            name = "close-test"

            def __init__(self, config=None):
                pass

            def health(self):
                return {"ok": True}

            def query(self, spec):
                return []

            def disconnect(self):
                disconnect_calls.append("disconnected")

        reg = ConnectorRegistry.from_config(
            [{"name": "close-me", "factory": "closeable"}],
            factories={"closeable": _ClosableSource},
        )
        reg.close()
        assert disconnect_calls == ["disconnected"]


class TestObserveQueryRequestValidation:
    """POST /api/observe/query body validation."""

    def test_valid_request(self) -> None:
        req = ObserveQueryRequest(source="prom-a", spec={"limit": 5})
        assert req.source == "prom-a"
        assert req.spec == {"limit": 5}

    def test_empty_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ObserveQueryRequest(source="")

    def test_source_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ObserveQueryRequest(source="x" * 257)

    def test_default_spec_is_empty_dict(self) -> None:
        req = ObserveQueryRequest(source="test")
        assert req.spec == {}


# ── wire_observability end-to-end ───────────────────────────────────────


class TestWireObservabilityE2E:
    """wire_observability builds registry + registers routes on FastAPI."""

    @pytest.mark.asyncio
    async def test_wire_observability_registers_routes(self) -> None:
        app = FastAPI()
        daemon_state: dict[str, Any] = {}

        configs = [
            {"name": "prod-prom", "kind": "metrics", "factory": "prometheus"},
            {"name": "prod-dd", "kind": "logs", "factory": "datadog"},
        ]
        reg = wire_observability(app, daemon_state, configs, factories=_FACTORIES)

        assert len(reg.names()) == 2

        # Verify registry is stored on app state
        stored = getattr(app.state, "_connector_registry", None)
        assert stored is reg

        # Verify routes are registered by hitting them via test client
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")

        # GET /api/observe/sources
        resp = await client.get("/api/observe/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sources"]) == 2
        assert data["count"] == 2
        assert "metrics" in data["by_kind"]

        # GET /api/observe/health
        resp = await client.get("/api/observe/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["health"]["prod-prom"]["ok"] is True

        # POST /api/observe/query
        resp = await client.post(
            "/api/observe/query",
            json={"source": "prod-prom", "spec": {"limit": 2}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "prod-prom"
        assert len(data["records"]) == 2

        # POST /api/observe/query wrong name → 404
        resp = await client.post(
            "/api/observe/query",
            json={"source": "nonexistent", "spec": {}},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_wire_observability_no_sources(self) -> None:
        app = FastAPI()
        daemon_state: dict[str, Any] = {}

        reg = wire_observability(app, daemon_state, None, factories=_FACTORIES)
        assert len(reg.names()) == 0

        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")

        resp = await client.get("/api/observe/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["sources"] == []

        resp = await client.get("/api/observe/health")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestValidateSourceClass:
    """Pre-construction validation of source classes."""

    def test_valid_source_class_passes(self) -> None:
        _validate_source_class(_FakePrometheusSource)  # should not raise

    def test_non_callable_raises(self) -> None:
        with pytest.raises(TypeError, match="not callable"):
            _validate_source_class("not_a_class")

    def test_missing_health_method_raises(self) -> None:
        class _NoHealth:
            def query(self, spec):
                return []

        with pytest.raises(TypeError, match="health"):
            _validate_source_class(_NoHealth)

    def test_missing_query_method_raises(self) -> None:
        class _NoQuery:
            def health(self):
                return {"ok": True}

        with pytest.raises(TypeError, match="query"):
            _validate_source_class(_NoQuery)
