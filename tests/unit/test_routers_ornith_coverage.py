"""Typed fail-closed and proxy coverage for the Ornith router."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.db.models import OrnithTrainingPairModel
from general_ludd.routers import ornith


def _client() -> tuple[FastAPI, TestClient]:
    """Build the router without daemon database wiring."""
    app = FastAPI()
    ornith.register(app, {})
    return app, TestClient(app)


def test_pair_serialization_sanitizes_corrupt_json() -> None:
    """Replace corrupt stored JSON and absent timestamps with safe values."""
    row = SimpleNamespace(
        id="ORN-1",
        invoked_at=None,
        task_description="task",
        target_files="bad-json",
        scaffold_kind="patch",
        scaffold_content="content",
        scaffold_hash="digest",
        iterations_used=1,
        tokens_consumed=2,
        model_sha="sha",
        outcome_status="succeeded",
        outcome_details="bad-json",
        outcome_set_at=None,
        project_id=None,
        agent_id="agent",
    )
    result = ornith._pair_to_dict(cast(OrnithTrainingPairModel, row))
    assert result["target_files"] == []
    assert result["outcome_details"] == {}
    assert result["invoked_at"] is None
    assert result["outcome_set_at"] is None


def test_database_endpoints_fail_closed_or_return_empty_without_factory() -> None:
    """Keep read-only empty responses distinct from write/export failures."""
    _app, client = _client()
    record = {
        "task_description": "task",
        "scaffold_kind": "patch",
        "scaffold_content": "content",
        "agent_id": "agent",
    }
    with client:
        assert client.post("/admin/ornith/record", json=record).status_code == 503
        assert client.patch(
            "/admin/ornith/ORN-1/outcome", json={"status": "succeeded"}
        ).status_code == 503
        assert client.get("/admin/ornith/pending").json() == {"pending": [], "count": 0}
        assert client.get("/admin/ornith/export").status_code == 503
        assert client.get("/admin/ornith/stats").json()["total"] == 0
        assert client.get("/admin/ornith/pairs", params={"status": "reverted"}).json() == {
            "pairs": [],
            "count": 0,
        }


class _SessionContext(AbstractAsyncContextManager[object]):
    """Return one opaque session for patched repository calls."""

    async def __aenter__(self) -> object:
        """Return the session marker."""
        return object()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Close the context without suppressing exceptions."""
        return None


class _FailingRepo:
    """Raise each repository validation error at the router boundary."""

    def __init__(self, _session: object) -> None:
        pass

    async def record_pair(self, _invocation: object) -> object:
        """Reject the record."""
        raise ValueError("record rejected")

    async def set_outcome(self, *_args: object) -> object:
        """Reject the outcome."""
        raise ValueError("outcome rejected")

    async def list_pairs_by_statuses(self, **_kwargs: object) -> list[object]:
        """Reject the filter."""
        raise ValueError("filter rejected")


def test_repository_value_errors_map_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Map repository validation failures to stable HTTP responses."""
    app, client = _client()
    app.state._session_factory = _SessionContext
    monkeypatch.setattr(ornith, "OrnithTrainingRepo", _FailingRepo)
    record = {
        "task_description": "task",
        "scaffold_kind": "patch",
        "scaffold_content": "content",
        "agent_id": "agent",
    }
    with client:
        assert client.post("/admin/ornith/record", json=record).status_code == 422
        assert client.patch(
            "/admin/ornith/ORN-1/outcome", json={"status": "succeeded"}
        ).status_code == 422
        assert client.get("/admin/ornith/pairs", params={"status": "reverted"}).status_code == 422
        assert client.get("/admin/ornith/pairs", params={"status": ",,"}).json() == {
            "pairs": [],
            "count": 0,
        }


class _AsyncClient:
    """Model success, HTTP error, or transport exception responses."""

    response: object = SimpleNamespace(status_code=200, text="ok", json=lambda: {"ok": True})

    async def __aenter__(self) -> _AsyncClient:
        """Enter the owned HTTP client."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Close the owned HTTP client."""
        return None

    async def post(self, *_args: object, **_kwargs: object) -> Any:
        """Return or raise the configured response."""
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


@pytest.mark.parametrize("mode", ["success", "http_error", "transport_error"])
def test_self_improve_proxy_records_bounded_history(mode: str) -> None:
    """Record successful and failed proxy calls in bounded runtime history."""
    app, client = _client()
    app.state._host = "127.0.0.1"
    app.state._port = 8123
    if mode == "success":
        _AsyncClient.response = SimpleNamespace(status_code=200, text="ok", json=lambda: {"ok": True})
    elif mode == "http_error":
        _AsyncClient.response = SimpleNamespace(status_code=503, text="busy", json=lambda: {})
    else:
        _AsyncClient.response = RuntimeError("offline")

    with patch.object(httpx, "AsyncClient", _AsyncClient), client:
        response = client.post("/admin/ornith/self-improve")

    assert response.status_code == 200
    result = response.json()["cycle"]["result"]
    if mode == "success":
        assert result == {"ok": True}
    elif mode == "http_error":
        assert result == {"error": "busy", "status_code": 503}
    else:
        assert result == {"error": "offline"}
    assert len(app.state._ornith_history) == 1
