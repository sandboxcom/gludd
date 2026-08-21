"""Durable Terraform lifecycle event bridge tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import AuditEventModel, Base
from general_ludd.events import CustomEvent, EventBus
from general_ludd.infra.deployment_events import (
    PostgresWakeupListener,
    TerraformEventBridge,
    WakeupListener,
)


def test_wakeup_listener_protocol_supports_bounded_runtime_validation() -> None:
    class CompleteListener:
        def start(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def aclose(self) -> None:
            return None

    class MissingAsyncClose:
        def start(self) -> None:
            return None

        def close(self) -> None:
            return None

    assert isinstance(CompleteListener(), WakeupListener)
    assert not isinstance(MissingAsyncClose(), WakeupListener)


@pytest.mark.asyncio
async def test_listener_and_bridge_cleanup_are_idempotent_at_lifecycle_edges() -> None:
    listener = PostgresWakeupListener(
        database_url="postgresql+psycopg://unused/gludd",
        session_factory=Mock(),
        wake=Mock(),
        worker_id="worker-not-started",
    )

    listener.close()
    await listener.aclose()

    assert listener._task is None
    assert not listener.ready

    class LifecycleListener:
        def __init__(self) -> None:
            self.started = 0
            self.closed = 0
            self.async_closed = 0

        def start(self) -> None:
            self.started += 1

        def close(self) -> None:
            self.closed += 1

        async def aclose(self) -> None:
            self.async_closed += 1

    lifecycle = LifecycleListener()
    bridge = TerraformEventBridge(
        event_bus=EventBus(),
        session_factory=Mock(),
        listener=lifecycle,
    )
    bridge.close()
    await bridge.aclose()
    bridge.start()
    bridge.start()
    bridge.close()
    bridge.close()
    await bridge.aclose()

    assert lifecycle.started == 1
    assert lifecycle.closed == 3
    assert lifecycle.async_closed == 2


@pytest.mark.asyncio
async def test_bridge_persists_progress_and_wakes_on_resource_terminal_state() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    bus = EventBus(history_size=20)
    wake = Mock()
    bridge = TerraformEventBridge(
        event_bus=bus,
        session_factory=sessions,
        wake=wake,
        worker_id="worker-a",
    )
    bridge.start()

    bus.publish(
        CustomEvent(
            name="terraform_output",
            payload={"deployment_id": "d-1", "message": "creating resource"},
            source="terraform_deployment",
        )
    )
    bus.publish(
        CustomEvent(
            name="terraform_deploy_completed",
            payload={"deployment_id": "d-1", "instance_id": "instance-1"},
            source="terraform_deployment",
        )
    )
    await bus.drain()

    async with sessions() as session:
        rows = list(
            (
                await session.execute(
                    select(AuditEventModel)
                    .where(AuditEventModel.entity_type == "terraform_deployment")
                    .order_by(AuditEventModel.id)
                )
            )
            .scalars()
            .all()
        )
    assert [row.event_type for row in rows] == [
        "terraform_output",
        "terraform_deploy_completed",
    ]
    assert rows[0].actor == "worker-a"
    assert json.loads(rows[1].details)["instance_id"] == "instance-1"
    wake.assert_called_once_with()

    bridge.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_listener_deduplicates_notifications_and_catches_up_missed_rows() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    wake = Mock()
    listener = PostgresWakeupListener(
        database_url="postgresql+psycopg://unused/gludd",
        session_factory=sessions,
        wake=wake,
        worker_id="worker-listener",
    )

    async with sessions() as session:
        session.add(
            AuditEventModel(
                event_type="terraform_deploy_completed",
                actor="worker-a",
                entity_type="terraform_deployment",
                entity_id="event-missed",
                details="{}",
            )
        )
        await session.commit()
    caught_up = await listener.catch_up()
    assert caught_up == 1
    assert wake.call_count == 1
    listener.handle_notification('{"audit_event_id": 1}')
    listener.handle_notification('{"audit_event_id": 1}')
    assert wake.call_count == 1

    async with sessions() as session:
        session.add(
            AuditEventModel(
                event_type="terraform_deploy_failed",
                actor="worker-a",
                entity_type="terraform_deployment",
                entity_id="event-missed-2",
                details="{}",
            )
        )
        await session.commit()
    assert await listener.catch_up() == 1
    assert wake.call_count == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_bridge_starts_and_gracefully_closes_listener() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    listener = Mock()
    listener.start = Mock()
    listener.aclose = AsyncMock()
    bridge = TerraformEventBridge(
        event_bus=EventBus(),
        session_factory=sessions,
        listener=listener,
    )
    bridge.start()
    listener.start.assert_called_once_with()
    await bridge.aclose()
    listener.aclose.assert_awaited_once_with()
    await engine.dispose()


@pytest.mark.asyncio
async def test_listener_start_wait_and_close_cancel_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    listener = PostgresWakeupListener(
        database_url="postgresql+psycopg://unused/gludd",
        session_factory=sessions,
        wake=Mock(),
        worker_id="worker-cancel",
    )

    async def block_until_cancelled() -> None:
        listener._ready.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(listener, "_listen_once", AsyncMock(side_effect=block_until_cancelled))
    listener.start()
    first_task = listener._task
    listener.start()
    assert listener._task is first_task
    await listener.wait_ready(timeout=1)
    assert listener.ready
    await listener.aclose()
    assert listener._task is None
    assert not listener.ready
    await engine.dispose()


@pytest.mark.asyncio
async def test_listener_retries_after_disconnect_before_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    listener = PostgresWakeupListener(
        database_url="postgresql+psycopg://unused/gludd",
        session_factory=sessions,
        wake=Mock(),
        worker_id="worker-retry",
        reconnect_min_seconds=0,
        reconnect_max_seconds=0,
    )
    listen_once = AsyncMock(side_effect=ConnectionError("forced disconnect"))
    monkeypatch.setattr(listener, "_listen_once", listen_once)
    listener.start()
    for _ in range(100):
        if listen_once.await_count >= 2:
            break
        await asyncio.sleep(0)
    assert listen_once.await_count >= 2
    await listener.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_listener_uses_dedicated_autocommit_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg

    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    wake = Mock()
    listener = PostgresWakeupListener(
        database_url="postgresql+psycopg://user:secret@db/gludd",
        session_factory=sessions,
        wake=wake,
        worker_id="worker-connect",
    )

    class FakeConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def __aenter__(self) -> FakeConnection:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def execute(self, statement: str) -> None:
            self.statements.append(statement)

        async def notifies(self) -> AsyncIterator[SimpleNamespace]:
            yield SimpleNamespace(payload='{"audit_event_id": 9}')

    connection = FakeConnection()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(listener, "catch_up", AsyncMock(return_value=0))

    await listener._listen_once()

    connect.assert_awaited_once()
    await_args = connect.await_args
    assert await_args is not None
    assert await_args.args[0] == "postgresql://user:secret@db/gludd"
    assert await_args.kwargs["autocommit"] is True
    assert connection.statements == ["LISTEN gludd_terraform_ready"]
    wake.assert_called_once_with()
    await engine.dispose()


def test_listener_malformed_notification_still_wakes_for_durable_catchup() -> None:
    listener = PostgresWakeupListener(
        database_url="postgresql+psycopg://unused/gludd",
        session_factory=Mock(),
        wake=(wake := Mock()),
        worker_id="worker-malformed",
    )
    listener.handle_notification("not-json")
    wake.assert_called_once_with()


@pytest.mark.asyncio
async def test_bridge_synchronous_close_stops_listener() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    listener = Mock()
    bridge = TerraformEventBridge(
        event_bus=EventBus(),
        session_factory=sessions,
        listener=listener,
    )
    bridge.start()
    bridge.close()
    listener.close.assert_called_once_with()
    await engine.dispose()


@pytest.mark.asyncio
async def test_bridge_ignores_non_terraform_custom_events_after_close() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    bus = EventBus()
    bridge = TerraformEventBridge(
        event_bus=bus,
        session_factory=sessions,
        worker_id="worker-b",
    )
    bridge.start()
    bus.publish(CustomEvent(name="unrelated", payload={"value": 1}))
    bridge.close()
    bus.publish(CustomEvent(name="terraform_deploy_failed", payload={"deployment_id": "d-2"}))
    await bus.drain()

    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(AuditEventModel))
    assert count == 0
    await engine.dispose()
