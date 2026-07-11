"""Tests for C25: Remediation endpoint idempotency guard.

Covers:
  - Same (action, target) within window -> deduped
  - Different action_kind on same target -> allowed
  - X-Idempotency-Key header respected
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, RemediationActionModel, TodoModel
from general_ludd.remediation.blocker_detector import RemediationConfig
from general_ludd.routers.remediation import register
from general_ludd.schemas.todo import TodoStatus


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed_chronic_todo(
    session: AsyncSession,
    *,
    todo_id: str = "TODO-CHRONIC-IDEM",
    run_count: int = 5,
) -> TodoModel:
    todo = TodoModel(
        todo_id=todo_id,
        title="chronically re-queued task",
        status=TodoStatus.QUEUED.value,
        work_type="code",
        queue="core",
        project_id=None,
        run_count=run_count,
    )
    session.add(todo)
    await session.flush()
    return todo


@pytest.fixture
def app_with_idem_db(async_engine) -> FastAPI:
    app = FastAPI()
    app.state._session_factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    cfg = RemediationConfig(retry_delay_hours=100000)
    register(app, {"remediation_config": cfg})
    return app


@pytest.fixture
def client_idem(app_with_idem_db: FastAPI) -> TestClient:
    return TestClient(app_with_idem_db)


class TestSameActionTargetWindowDeduped:
    """C25: Duplicate (blocked_todo_id, action_kind) within window is skipped."""

    async def test_duplicate_remediate_call_deduped(
        self,
        app_with_idem_db: FastAPI,
        client_idem: TestClient,
    ) -> None:
        factory = app_with_idem_db.state._session_factory
        async with factory() as session:
            await _seed_chronic_todo(session, todo_id="TODO-IDEM-A")
            await session.commit()

        resp1 = client_idem.post("/admin/remediation/remediate")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["scanned"] >= 1
        a1 = next(
            a for a in data1["actions"] if a["blocked_todo_id"] == "TODO-IDEM-A"
        )
        assert a1["kind"] == "dispatch_agent"
        assert a1["ok"] is True

        resp2 = client_idem.post("/admin/remediation/remediate")
        assert resp2.status_code == 200
        data2 = resp2.json()
        skipped = [
            a
            for a in data2["actions"]
            if a["blocked_todo_id"] == "TODO-IDEM-A"
            and a.get("skipped_reason")
        ]
        assert len(skipped) > 0
        assert "duplicate" in skipped[0]["skipped_reason"]

        async with factory() as session:
            result = await session.execute(
                select(RemediationActionModel).where(
                    RemediationActionModel.blocked_todo_id == "TODO-IDEM-A",
                    RemediationActionModel.action_kind == "dispatch_agent",
                )
            )
            rows = result.scalars().all()
            assert len(rows) == 1


class TestDifferentActionAllowed:
    """C25: Different action_kind on same target is NOT deduped."""

    async def test_different_action_kind_not_deduped(
        self,
        app_with_idem_db: FastAPI,
        client_idem: TestClient,
    ) -> None:
        factory = app_with_idem_db.state._session_factory
        async with factory() as session:
            await _seed_chronic_todo(session, todo_id="TODO-IDEM-B")
            action = RemediationActionModel(
                blocked_todo_id="TODO-IDEM-B",
                action_kind="schedule_retry",
                blocker_kind="resource_contention",
                summary="prior different action",
                created_at=datetime.now(UTC),
            )
            session.add(action)
            await session.commit()

        resp = client_idem.post("/admin/remediation/remediate")
        assert resp.status_code == 200
        data = resp.json()
        a = next(
            a for a in data["actions"] if a["blocked_todo_id"] == "TODO-IDEM-B"
        )
        assert a["kind"] == "dispatch_agent"
        assert a["ok"] is True


class TestIdempotencyKeyHeader:
    """C25: X-Idempotency-Key header: first call processes, second returns replay."""

    async def test_idempotency_key_first_and_replay(
        self,
        app_with_idem_db: FastAPI,
        client_idem: TestClient,
    ) -> None:
        factory = app_with_idem_db.state._session_factory
        async with factory() as session:
            await _seed_chronic_todo(session, todo_id="TODO-IDEM-C")
            await session.commit()

        idem_key = "test-key-c25-001"

        resp1 = client_idem.post(
            "/admin/remediation/remediate",
            headers={"X-Idempotency-Key": idem_key},
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1.get("idempotent_replay") is not True
        assert data1["scanned"] >= 1

        resp2 = client_idem.post(
            "/admin/remediation/remediate",
            headers={"X-Idempotency-Key": idem_key},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2.get("idempotent_replay") is True
        assert len(data2["actions"]) > 0

    async def test_different_idempotency_keys_are_independent(
        self,
        app_with_idem_db: FastAPI,
        client_idem: TestClient,
    ) -> None:
        factory = app_with_idem_db.state._session_factory
        async with factory() as session:
            await _seed_chronic_todo(session, todo_id="TODO-IDEM-D")
            await session.commit()

        resp_a = client_idem.post(
            "/admin/remediation/remediate",
            headers={"X-Idempotency-Key": "key-alpha"},
        )
        assert resp_a.status_code == 200

        resp_b = client_idem.post(
            "/admin/remediation/remediate",
            headers={"X-Idempotency-Key": "key-beta"},
        )
        assert resp_b.status_code == 200
        assert resp_b.json().get("idempotent_replay") is not True
