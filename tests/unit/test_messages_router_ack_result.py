"""Regression coverage for boolean acknowledgement compatibility results."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from general_ludd.db.repository import AgentMessageRepository
from general_ludd.routers.messages import register


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def commit(self) -> None:
        return None


class _FakeSessionFactory:
    def __call__(self) -> _FakeSession:
        return _FakeSession()


@pytest.mark.asyncio
async def test_ack_route_accepts_boolean_repository_result(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    register(app, {})
    app.state._session_factory = _FakeSessionFactory()

    async def _ack(_self: AgentMessageRepository, _message_id: str, project_id: str | None = None) -> bool:
        del project_id
        return True

    monkeypatch.setattr(AgentMessageRepository, "ack", _ack)
    endpoint = next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == "/api/messages/{message_id}/ack"
    )

    result = await endpoint("MSG-BOOL")

    assert result == {"acked": True, "id": "MSG-BOOL", "read_at": None}
