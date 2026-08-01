"""Durable Terraform lifecycle event bridge tests."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import AuditEventModel, Base
from general_ludd.events import CustomEvent, EventBus
from general_ludd.infra.deployment_events import TerraformEventBridge


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
