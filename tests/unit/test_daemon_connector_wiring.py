"""Tests: connector/observability layer is reachable via daemon startup.

Verifies the previously-orphaned gap: connectors were built but
``create_daemon_app`` never imported them into its route table so an operator
could never reach them through the daemon.

Coverage (TDD):
- ``create_daemon_app()`` stores a ``ConnectorRegistry`` on ``app.state`` at
  app-creation time (not just at first request) so all observe routes have a
  live registry on day-0 startup.
- ``GET /api/observe/sources``, ``GET /api/observe/health``, and
  ``POST /api/observe/query`` are registered and routable (not 404 / 405).
- A no-config / no-connectors startup completes without error and returns
  an empty registry (never 500).
- ``load_startup_config`` returns a ``"connectors"`` key when the config dir
  contains a ``connectors.yml`` with a valid list.
- Connector entries in ``general-ludd.yml`` populate ``UserConfig.connectors``
  and flow into the daemon via ``_startup_config["connectors"]``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from general_ludd.connectors.registry import ConnectorRegistry
from general_ludd.daemon import create_daemon_app, load_startup_config


# ---------------------------------------------------------------------------
# Minimal fake connector so tests don't hit real network backends
# ---------------------------------------------------------------------------
class _FakeSource:
    """Minimal connector for daemon wiring tests."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.name = str(config.get("name") or "fake")
        self.KIND = str(config.get("kind") or "metrics")

    def health(self) -> dict[str, Any]:
        return {"ok": True, "source": self.name}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"source": self.name, "kind": self.KIND, "message": "ok"}]


_FACTORIES: dict[str, Any] = {"fake": _FakeSource}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_app_with_connectors(
    connector_entries: list[dict[str, Any]],
) -> Any:
    """Build a daemon app with connectors injected via the factories side-channel.

    We call ``wire_observability`` AFTER ``create_daemon_app`` to inject the
    factories dict (since ``create_daemon_app`` uses the config-based path that
    cannot accept a ``factories`` kwarg).  The point under test is that:
    (a) the observe routes ARE registered by ``create_daemon_app``, and
    (b) the ``_connector_registry`` on ``app.state`` is a ``ConnectorRegistry``
    instance (even if the initial build was empty without factories).

    For tests that need a *populated* registry we replace ``app.state._connector_registry``
    after the app is built — the route handlers read it at request time.
    """
    from general_ludd.routers.observe import wire_observability

    app = create_daemon_app()
    # Replace the registry with one built via the factories helper so we can
    # inject a _FakeSource without a real network backend.
    wire_observability(app, {}, connector_entries, factories=_FACTORIES)
    return app


# ---------------------------------------------------------------------------
# 1. No-config startup
# ---------------------------------------------------------------------------
class TestNoConfigStartup:
    """Empty/no-config startup must succeed and serve the observe routes."""

    def test_app_creation_does_not_crash(self) -> None:
        """create_daemon_app() with no config dir must not raise."""
        # Patch env so no accidental config file is loaded from ~
        with patch.dict(os.environ, {"HOME": "/nonexistent_home_xxx"}, clear=False):
            app = create_daemon_app()
        assert app is not None

    def test_connector_registry_on_state_is_set(self) -> None:
        """app.state._connector_registry MUST be a ConnectorRegistry after init."""
        with patch.dict(os.environ, {"HOME": "/nonexistent_home_xxx"}, clear=False):
            app = create_daemon_app()
        reg = getattr(app.state, "_connector_registry", None)
        assert isinstance(reg, ConnectorRegistry), (
            "app.state._connector_registry must be a ConnectorRegistry; "
            f"got {type(reg)!r} — the connector layer is still orphaned"
        )

    def test_observe_sources_route_registered(self) -> None:
        """GET /api/observe/sources returns 200 (not 404/405) with empty registry."""
        with patch.dict(os.environ, {"HOME": "/nonexistent_home_xxx"}, clear=False):
            app = create_daemon_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/observe/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["sources"] == []

    def test_observe_health_route_registered(self) -> None:
        """GET /api/observe/health returns 200 with empty health dict."""
        with patch.dict(os.environ, {"HOME": "/nonexistent_home_xxx"}, clear=False):
            app = create_daemon_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/observe/health")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_observe_query_unknown_source_404_not_unregistered(self) -> None:
        """POST /api/observe/query returns 404 on unknown name (route IS registered)."""
        with patch.dict(os.environ, {"HOME": "/nonexistent_home_xxx"}, clear=False):
            app = create_daemon_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/observe/query", json={"source": "nowhere", "spec": {}})
        # 404 = route registered, unknown source name  (NOT 405 = route missing)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 2. Observe routes with configured connectors
# ---------------------------------------------------------------------------
class TestConfiguredConnectors:
    """When connectors are configured the routes expose them."""

    @pytest.fixture
    def client(self) -> TestClient:
        app = _build_app_with_connectors(
            [
                {"name": "ci-metrics", "kind": "metrics", "factory": "fake"},
                {"name": "prod-logs", "kind": "logs", "factory": "fake"},
            ]
        )
        return TestClient(app, raise_server_exceptions=False)

    def test_sources_lists_configured_connectors(self, client: TestClient) -> None:
        resp = client.get("/api/observe/sources")
        assert resp.status_code == 200
        data = resp.json()
        names = {s["name"] for s in data["sources"]}
        assert names == {"ci-metrics", "prod-logs"}
        assert data["count"] == 2

    def test_sources_grouped_by_kind(self, client: TestClient) -> None:
        resp = client.get("/api/observe/sources")
        by_kind = resp.json()["by_kind"]
        assert by_kind["metrics"] == ["ci-metrics"]
        assert by_kind["logs"] == ["prod-logs"]

    def test_health_covers_configured_connectors(self, client: TestClient) -> None:
        resp = client.get("/api/observe/health")
        assert resp.status_code == 200
        health = resp.json()["health"]
        assert health["ci-metrics"]["ok"] is True
        assert health["prod-logs"]["ok"] is True

    def test_query_named_source_returns_records(self, client: TestClient) -> None:
        resp = client.post(
            "/api/observe/query",
            json={"source": "ci-metrics", "spec": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "ci-metrics"
        assert len(data["records"]) == 1

    def test_query_unknown_source_is_still_404(self, client: TestClient) -> None:
        resp = client.post("/api/observe/query", json={"source": "ghost", "spec": {}})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. load_startup_config — connectors.yml support
# ---------------------------------------------------------------------------
class TestLoadStartupConfigConnectors:
    """load_startup_config() surfaces connectors from both config sources."""

    def test_no_config_dir_returns_empty_connectors(self) -> None:
        with patch.dict(os.environ, {"HOME": "/nonexistent_home_xxx"}, clear=False):
            cfg = load_startup_config(config_dir=None)
        assert cfg["connectors"] == []

    def test_connectors_yml_loaded_from_config_dir(self, tmp_path: Path) -> None:
        entries = [
            {"name": "prom", "kind": "metrics", "module": "prometheus"},
            {"name": "dd", "kind": "metrics", "module": "datadog"},
        ]
        conn_file = tmp_path / "connectors.yml"
        conn_file.write_text(yaml.safe_dump({"connectors": entries}))
        cfg = load_startup_config(config_dir=str(tmp_path))
        assert cfg["connectors"] == entries

    def test_connectors_key_in_general_ludd_yml(self, tmp_path: Path) -> None:
        entries = [{"name": "elastic", "kind": "logs", "module": "elasticsearch"}]
        gl_file = tmp_path / "general-ludd.yml"
        gl_file.write_text(yaml.safe_dump({"connectors": entries}))
        cfg = load_startup_config(config_dir=str(tmp_path))
        assert cfg["connectors"] == entries

    def test_connectors_yml_overrides_general_ludd_yml(self, tmp_path: Path) -> None:
        """When both sources exist, connectors.yml wins (last-write-wins)."""
        gl_entries = [{"name": "from-yml", "kind": "logs", "module": "elasticsearch"}]
        file_entries = [{"name": "from-file", "kind": "metrics", "module": "prometheus"}]
        (tmp_path / "general-ludd.yml").write_text(
            yaml.safe_dump({"connectors": gl_entries})
        )
        (tmp_path / "connectors.yml").write_text(
            yaml.safe_dump({"connectors": file_entries})
        )
        cfg = load_startup_config(config_dir=str(tmp_path))
        assert cfg["connectors"] == file_entries

    def test_missing_connectors_yml_does_not_crash(self, tmp_path: Path) -> None:
        """Config dir exists but no connectors file → empty list, no exception."""
        cfg = load_startup_config(config_dir=str(tmp_path))
        assert cfg["connectors"] == []

    def test_malformed_connectors_yml_is_skipped_gracefully(
        self, tmp_path: Path
    ) -> None:
        """A connectors.yml with syntax errors must not abort startup."""
        (tmp_path / "connectors.yml").write_text("connectors: [invalid: yaml: {{")
        # Should not raise; errors are logged and connectors fall back to []
        cfg = load_startup_config(config_dir=str(tmp_path))
        assert cfg["connectors"] == []


# ---------------------------------------------------------------------------
# 4. UserConfig connectors field
# ---------------------------------------------------------------------------
class TestUserConfigConnectorsField:
    """UserConfig must accept a 'connectors' field."""

    def test_userconfig_accepts_connectors_list(self) -> None:
        from general_ludd.config.user_config import UserConfig

        uc = UserConfig(
            connectors=[{"name": "test-src", "kind": "metrics", "module": "prometheus"}]
        )
        assert len(uc.connectors) == 1
        assert uc.connectors[0]["name"] == "test-src"

    def test_userconfig_defaults_connectors_to_empty_list(self) -> None:
        from general_ludd.config.user_config import UserConfig

        uc = UserConfig()
        assert uc.connectors == []

    def test_connector_cfg_from_userconfig_flows_into_startup_config(
        self, tmp_path: Path
    ) -> None:
        """Connectors in general-ludd.yml → UserConfig.connectors → startup_config."""
        entries = [{"name": "kafka", "kind": "metrics", "module": "kafka_exporter"}]
        (tmp_path / "general-ludd.yml").write_text(yaml.safe_dump({"connectors": entries}))
        cfg = load_startup_config(config_dir=str(tmp_path))
        assert cfg["connectors"] == entries
