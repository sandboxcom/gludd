"""Degraded-mode parity for bounded message expiry admission."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import messages as messages_router

_NOW = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)


def _message(
    message_id: str,
    *,
    created_at: datetime,
    ttl_seconds: int | None,
    project_id: str = "project-a",
) -> dict[str, object]:
    return {
        "id": message_id,
        "sender": "planner",
        "recipient": "coder",
        "topic": message_id,
        "body": "",
        "priority": "normal",
        "project_id": project_id,
        "created_at": created_at,
        "read_at": None,
        "ttl_seconds": ttl_seconds,
    }


def test_degraded_inbox_filters_expiry_before_stable_hard_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(messages_router, "_utc_now", lambda: _NOW)
    live_ids = [f"MSG-LIVE-{index:03d}" for index in reversed(range(101))]
    seeded = [
        _message(
            "MSG-EXPIRED",
            created_at=_NOW - timedelta(seconds=31),
            ttl_seconds=30,
        ),
        _message(
            "MSG-BOUNDARY",
            created_at=_NOW - timedelta(seconds=30),
            ttl_seconds=30,
        ),
        _message(
            "MSG-OTHER-PROJECT",
            created_at=_NOW - timedelta(seconds=1),
            ttl_seconds=60,
            project_id="project-b",
        ),
        *[
            _message(
                message_id,
                created_at=_NOW - timedelta(seconds=1),
                ttl_seconds=60,
            )
            for message_id in live_ids
        ],
    ]
    app = FastAPI()
    state: dict[str, Any] = {"messages": seeded}
    messages_router.register(app, state)

    response = TestClient(app).get(
        "/api/messages",
        params={"recipient": "coder", "project_id": "project-a"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 100
    assert [message["id"] for message in payload["messages"]] == sorted(live_ids)[:100]

