"""Adversarial lifecycle-receipt tests for deployment events."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from general_ludd.infra.deployment_events import PostgresWakeupListener


def test_never_ready_listener_cannot_forge_a_close_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "general_ludd.infra.deployment_events._emit_wakeup_progress",
        messages.append,
    )
    listener = PostgresWakeupListener(
        database_url="postgresql+psycopg://unused/gludd",
        session_factory=Mock(),
        wake=Mock(),
        worker_id="worker-never-ready",
    )

    listener.close()
    asyncio.run(listener.aclose())
    asyncio.run(listener.aclose())

    assert not any("wake listener closed" in message for message in messages)
