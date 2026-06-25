"""Direct handler tests for the POST /admin/models/call ``system`` field.

The ``system`` field on /admin/models/call (routers/models.py admin_models_call)
prepends a system message to the message list before the user prompt. It was
added this session but only indirectly covered; these tests assert the handler's
message-building behavior directly via a FastAPI TestClient with a stubbed
gateway.

The stub replaces ``gateway.call_model`` with a capture so the exact ``messages``
argument the handler builds is asserted, without hitting any model provider.

Auth: a minimal app registered via register() has no PSK middleware, so the
handler is reachable directly. GLUDD_ALLOW_NO_AUTH is set defensively to mirror
test_models_workflow_endpoint.py's no-auth path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app_with_capture(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, dict[str, Any]]:
    """Build a minimal models-router app whose gateway captures call_model args.

    Returns the app plus a ``captured`` dict that the stubbed call_model fills
    with the positional ``profile_id``/``messages`` it was invoked with.
    """
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")

    from general_ludd.routers import models as models_mod

    captured: dict[str, Any] = {}

    def _capture_call_model(profile_id: str, messages: Any) -> Any:
        captured["profile_id"] = profile_id
        captured["messages"] = messages
        return MagicMock(content="ok", usage_metadata=None)

    app = FastAPI()

    gw = MagicMock()
    profile = MagicMock()
    profile.model_profile_id = "default"
    gw.list_profiles.return_value = [profile]
    gw.call_model.side_effect = _capture_call_model

    app.state._model_gateway = gw
    app.state._budget_guard = None
    app.state._health_tracker = None
    app.state._project_manager = None
    app.state._metrics_collector = None
    app.state._session_factory = None
    app.state._model_registry = MagicMock()
    app.state._model_registry.search.return_value = []
    app.state._model_registry.list_downloaded.return_value = []

    models_mod.register(app, {})
    return app, captured


def test_system_field_prepends_system_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``system`` field -> messages[0] is the system message, then the user."""
    app, captured = _app_with_capture(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/admin/models/call",
        json={"prompt": "write f", "system": "You are a helpful assistant."},
    )
    assert resp.status_code == 200, resp.text

    messages = captured["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a helpful assistant."
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "write f"


def test_no_system_field_single_user_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``system`` field -> just the single user message (backward-compat)."""
    app, captured = _app_with_capture(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/admin/models/call", json={"prompt": "write f"})
    assert resp.status_code == 200, resp.text

    messages = captured["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "write f"


def test_extra_unknown_fields_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extra unknown body keys -> 200, never 422 (plain-dict parse, not strict)."""
    app, captured = _app_with_capture(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/admin/models/call",
        json={
            "prompt": "write f",
            "response_format": "text",
            "options": {"temperature": 0.2, "seed": 7},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.status_code != 422

    # The unknown fields don't perturb the message list: single user message.
    messages = captured["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "write f"
