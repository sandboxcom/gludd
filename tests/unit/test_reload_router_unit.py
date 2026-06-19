"""Unit tests for src/general_ludd/routers/reload.py.

Uses FastAPI TestClient with mocked subsystems.
No real subprocess, no real event bus, no real filesystem access.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.reload import register

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_app() -> FastAPI:
    app = FastAPI()
    app.state._config_dir = "/tmp/gl-test-config"
    app.state._templates_dir = "/tmp/gl-test-templates"
    app.state._playbooks_dir = "/tmp/gl-test-playbooks"
    register(app, {})
    return app


def make_subsys() -> dict:
    bus = MagicMock()
    bus.get_history.return_value = []

    hooks = MagicMock()
    hooks.list_hooks.return_value = []
    hooks.register_webhook.return_value = "hook-123"

    broadcaster = MagicMock()
    broadcaster.list_workers.return_value = []
    broadcaster.ping_all.return_value = {}

    return {"bus": bus, "hooks": hooks, "broadcaster": broadcaster}


def make_ext_subsys() -> dict:
    metrics = MagicMock()
    metrics.list_agents.return_value = []
    metrics.get_full_report.return_value = {}
    metrics.get_cost_estimate.return_value = MagicMock(
        total_cost_usd=0.0,
        subscription_name="",
        subscription_cost_usd_per_month=0.0,
        tokens_per_week=0,
        tokens_used=0,
        cost_as_pct_of_subscription=0.0,
        tokens_as_pct_of_weekly=0.0,
        tokens_remaining_this_week=0,
    )
    return {"metrics": metrics}


# ---------------------------------------------------------------------------
# GET /admin/reload/status
# ---------------------------------------------------------------------------

def test_reload_status_empty_history():
    subsys = make_subsys()
    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.get("/admin/reload/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "recent_events" in data
    assert "total_events" in data
    assert data["recent_events"] == []
    assert data["total_events"] == 0


def test_reload_status_with_history():
    subsys = make_subsys()
    event = MagicMock()
    event.type = "reload"
    event.payload = {}
    event.timestamp = 1234567890.0
    subsys["bus"].get_history.return_value = [event] * 5

    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.get("/admin/reload/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_events"] == 5
    assert len(data["recent_events"]) == 5


# ---------------------------------------------------------------------------
# GET /admin/templates
# ---------------------------------------------------------------------------

def test_list_templates_no_registry():
    """When no _prompt_registry on app.state, returns empty list."""
    subsys = make_subsys()
    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.get("/admin/templates")
    assert resp.status_code == 200
    assert resp.json() == {"templates": []}


def test_list_templates_with_registry():
    """When _prompt_registry is set, returns its list_templates() result."""
    registry_mock = MagicMock()
    registry_mock.list_templates.return_value = ["tmpl-a", "tmpl-b"]

    subsys = make_subsys()
    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        app = make_app()
        app.state._prompt_registry = registry_mock
        client = TestClient(app)
        resp = client.get("/admin/templates")
    assert resp.status_code == 200
    assert resp.json() == {"templates": ["tmpl-a", "tmpl-b"]}


# ---------------------------------------------------------------------------
# GET /admin/playbooks
# ---------------------------------------------------------------------------

def test_list_playbooks_no_runner():
    """When no _runner, returns empty list."""
    subsys = make_subsys()
    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.get("/admin/playbooks")
    assert resp.status_code == 200
    assert resp.json() == {"playbooks": []}


def test_list_playbooks_with_runner():
    runner_mock = MagicMock()
    runner_mock.list_playbooks.return_value = ["pb1.yml", "pb2.yml"]

    subsys = make_subsys()
    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        app = make_app()
        app.state._runner = runner_mock
        client = TestClient(app)
        resp = client.get("/admin/playbooks")
    assert resp.status_code == 200
    assert resp.json() == {"playbooks": ["pb1.yml", "pb2.yml"]}


# ---------------------------------------------------------------------------
# GET /admin/hooks
# ---------------------------------------------------------------------------

def test_list_hooks_empty():
    subsys = make_subsys()
    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.get("/admin/hooks")
    assert resp.status_code == 200
    assert resp.json() == {"hooks": []}


def test_list_hooks_with_entries():
    hook = MagicMock()
    hook.hook_id = "h1"
    hook.event_name = "reload"
    hook.hook_type = "webhook"
    hook.webhook_config = MagicMock(url="https://example.com/hook")
    hook.priority = 0

    subsys = make_subsys()
    subsys["hooks"].list_hooks.return_value = [hook]

    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.get("/admin/hooks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["hooks"]) == 1
    assert data["hooks"][0]["hook_id"] == "h1"
    assert data["hooks"][0]["url"] == "https://example.com/hook"


# ---------------------------------------------------------------------------
# GET /admin/workers
# ---------------------------------------------------------------------------

def test_list_workers_empty():
    subsys = make_subsys()
    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.get("/admin/workers")
    assert resp.status_code == 200
    assert resp.json() == {"workers": []}


def test_list_workers_with_entries():
    worker = MagicMock()
    worker.worker_id = "w1"
    worker.address = "127.0.0.1:9001"
    worker.last_seen = 1234567890.0

    subsys = make_subsys()
    subsys["broadcaster"].list_workers.return_value = [worker]

    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.get("/admin/workers")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["workers"]) == 1
    assert data["workers"][0]["worker_id"] == "w1"
    assert data["workers"][0]["address"] == "127.0.0.1:9001"


# ---------------------------------------------------------------------------
# POST /admin/reload
# ---------------------------------------------------------------------------

def test_admin_reload_success():
    subsys = make_subsys()
    reload_result = MagicMock()
    reload_result.success = True
    reload_result.scope = "all"
    reload_result.details = {}
    reload_result.error = None

    mock_reloader_instance = MagicMock()
    mock_reloader_instance.reload.return_value = reload_result

    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys), \
         patch("general_ludd.routers.reload.HotReloader", return_value=mock_reloader_instance):
        client = TestClient(make_app())
        resp = client.post("/admin/reload", json={"scope": "all"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["scope"] == "all"
    assert data["error"] is None


def test_admin_reload_default_scope():
    """Omitting scope defaults to 'all'."""
    subsys = make_subsys()
    reload_result = MagicMock()
    reload_result.success = True
    reload_result.scope = "all"
    reload_result.details = {}
    reload_result.error = None

    mock_reloader_instance = MagicMock()
    mock_reloader_instance.reload.return_value = reload_result

    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys), \
         patch("general_ludd.routers.reload.HotReloader", return_value=mock_reloader_instance):
        client = TestClient(make_app())
        resp = client.post("/admin/reload", json={})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /admin/hooks
# ---------------------------------------------------------------------------

def test_register_hook_ssrf_reject():
    """URLs pointing at loopback should be rejected by the pydantic validator."""
    subsys = make_subsys()
    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.post(
            "/admin/hooks",
            json={
                "event_name": "reload",
                "url": "http://127.0.0.1/evil",
            },
        )
    assert resp.status_code == 422


def test_register_hook_http_reject():
    """Plain HTTP (non-loopback) should also be rejected."""
    subsys = make_subsys()
    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.post(
            "/admin/hooks",
            json={
                "event_name": "reload",
                "url": "http://example.com/hook",
            },
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /admin/hooks/{hook_id}
# ---------------------------------------------------------------------------

def test_delete_hook():
    subsys = make_subsys()
    with patch("general_ludd.routers.reload._get_or_create_subsystems", return_value=subsys):
        client = TestClient(make_app())
        resp = client.delete("/admin/hooks/hook-abc")
    assert resp.status_code == 200
    assert resp.json() == {"removed": "hook-abc"}
    subsys["hooks"].unregister.assert_called_once_with("hook-abc")
