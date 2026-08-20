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

    def _capture_call_model(profile_id: str, messages: Any, **kwargs: Any) -> Any:
        captured["profile_id"] = profile_id
        captured["messages"] = messages
        captured["kwargs"] = kwargs
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


def test_response_schema_adds_deterministic_json_nudge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JSON schema is serialized deterministically into the system prompt."""
    app, captured = _app_with_capture(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/admin/models/call",
        json={
            "prompt": "return an answer",
            "system": "Keep it concise.",
            "response_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
            },
        },
    )

    assert response.status_code == 200, response.text
    system_message = captured["messages"][0]
    assert system_message["role"] == "system"
    assert system_message["content"].startswith("Keep it concise.\n\nRespond ONLY")
    assert (
        '{"properties": {"answer": {"type": "string"}}, "type": "object"}'
        in system_message["content"]
    )


def test_json_response_format_adds_nudge_without_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON response format alone creates a system nudge before the user prompt."""
    app, captured = _app_with_capture(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/admin/models/call",
        json={"prompt": "return an answer", "response_format": "JSON"},
    )

    assert response.status_code == 200, response.text
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("Respond ONLY")
    assert messages[1] == {"role": "user", "content": "return an answer"}


@pytest.mark.parametrize("max_tokens", ["not-an-integer", 0])
def test_invalid_output_cap_falls_back_to_profile_limit(
    monkeypatch: pytest.MonkeyPatch,
    max_tokens: object,
) -> None:
    """Malformed and non-positive caps cannot under-report budgeted output."""
    app, captured = _app_with_capture(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/admin/models/call",
        json={"prompt": "write f", "max_tokens": max_tokens},
    )

    assert response.status_code == 200, response.text
    assert captured["kwargs"]["requested_max_output_tokens"] is None


def test_missing_budget_guard_fails_closed_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Degraded startup rejects calls when fail-closed budgeting is enabled."""
    app, _captured = _app_with_capture(monkeypatch)
    del app.state._budget_guard
    monkeypatch.setenv("GLUDD_BUDGET_FAIL_CLOSED_DEGRADED", "1")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/admin/models/call", json={"prompt": "write f"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "budget guard unavailable (degraded startup); "
            "GLUDD_BUDGET_FAIL_CLOSED_DEGRADED=1"
        )
    }


def test_budget_guard_exception_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Budget-provider errors remain visible as a deterministic 503 boundary."""
    app, _captured = _app_with_capture(monkeypatch)
    guard = MagicMock()
    guard.check_all_limits.side_effect = RuntimeError("provider unavailable")
    app.state._budget_guard = guard
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/admin/models/call", json={"prompt": "write f"})

    assert response.status_code == 503
    assert response.json() == {"detail": "budget check failed"}


def test_budget_guard_allows_call_after_positive_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit positive verdict reaches the model gateway exactly once."""
    app, captured = _app_with_capture(monkeypatch)
    guard = MagicMock()
    guard.check_all_limits.return_value = {"allowed": True}
    app.state._budget_guard = guard
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/admin/models/call", json={"prompt": "write f"})

    assert response.status_code == 200, response.text
    assert captured["messages"] == [{"role": "user", "content": "write f"}]
    guard.check_all_limits.assert_called_once_with(estimated_cost=0.0)


@pytest.mark.parametrize(
    ("verdict", "detail"),
    [
        ({"allowed": False, "reason": "daily limit"}, "budget exhausted: daily limit"),
        ("invalid", "budget exhausted: non-dict"),
    ],
)
def test_budget_guard_denial_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    verdict: object,
    detail: str,
) -> None:
    """Denied and malformed guard verdicts both fail closed without ambiguity."""
    app, _captured = _app_with_capture(monkeypatch)
    guard = MagicMock()
    guard.check_all_limits.return_value = verdict
    app.state._budget_guard = guard
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/admin/models/call", json={"prompt": "write f"})

    assert response.status_code == 429
    assert response.json() == {"detail": detail}
