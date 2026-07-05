"""Unit tests for the memory router (G1 persistent agent memory API)."""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import MemoryRepository
from general_ludd.routers import memory as memory_router


@pytest_asyncio.fixture
async def app():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    _app = FastAPI()
    _app.state._memory_repo = MemoryRepository(session_factory=session_factory)
    memory_router.register(_app, {})
    yield _app
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMemoryCreate:
    POST = "/api/memory"

    def test_post_creates_and_returns_201(self, client):
        resp = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "value": "v1"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["agent_id"] == "a1"
        assert data["key"] == "k1"
        assert data["value"] == "v1"
        assert data["namespace"] == "default"
        assert "id" in data

    def test_post_with_custom_namespace(self, client):
        resp = client.post(
            self.POST,
            json={
                "agent_id": "a1",
                "key": "k1",
                "value": "v1",
                "namespace": "custom",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["namespace"] == "custom"

    def test_post_with_ttl(self, client):
        resp = client.post(
            self.POST,
            json={
                "agent_id": "a1",
                "key": "k1",
                "value": "v1",
                "ttl_seconds": 3600,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["ttl_seconds"] == 3600

    def test_post_missing_agent_id_returns_422(self, client):
        resp = client.post(self.POST, json={"key": "k1", "value": "v1"})
        assert resp.status_code == 422

    def test_post_upsert_overwrites(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        resp = client.post(
            self.POST, json={"agent_id": "a1", "key": "k1", "value": "v2"}
        )
        assert resp.status_code == 201
        assert resp.json()["value"] == "v2"


class TestMemoryList:
    LIST = "/api/memory/{agent_id}"
    POST = "/api/memory"

    def test_list_returns_records(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        client.post(self.POST, json={"agent_id": "a1", "key": "k2", "value": "v2"})
        resp = client.get(self.LIST.format(agent_id="a1"))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        keys = {r["key"] for r in data}
        assert keys == {"k1", "k2"}

    def test_list_filters_by_namespace(self, client):
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "value": "v1", "namespace": "ns1"},
        )
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k2", "value": "v2", "namespace": "ns2"},
        )
        resp = client.get(f"{self.LIST.format(agent_id='a1')}?namespace=ns1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["key"] == "k1"
        assert data[0]["namespace"] == "ns1"

    def test_list_wildcard_all_namespaces(self, client):
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "value": "v1", "namespace": "ns1"},
        )
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k2", "value": "v2", "namespace": "ns2"},
        )
        resp = client.get(f"{self.LIST.format(agent_id='a1')}?namespace=*")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_list_empty_agent(self, client):
        resp = client.get(self.LIST.format(agent_id="nonexistent"))
        assert resp.status_code == 200
        assert resp.json() == []


class TestMemoryDelete:
    DELETE = "/api/memory/{agent_id}/{key}"
    POST = "/api/memory"

    def test_delete_existing_returns_204(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        resp = client.delete(self.DELETE.format(agent_id="a1", key="k1"))
        assert resp.status_code == 204

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete(self.DELETE.format(agent_id="a1", key="nonexistent"))
        assert resp.status_code == 404

    def test_delete_is_gone_after(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        client.delete(self.DELETE.format(agent_id="a1", key="k1"))
        resp = client.get("/api/memory/a1")
        assert resp.json() == []

    def test_delete_with_custom_namespace(self, client):
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "value": "v1", "namespace": "ns1"},
        )
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "value": "v1", "namespace": "ns2"},
        )
        resp = client.delete(
            f"{self.DELETE.format(agent_id='a1', key='k1')}?namespace=ns1"
        )
        assert resp.status_code == 204
        # ns2 record still exists
        list_resp = client.get("/api/memory/a1?namespace=*")
        data = list_resp.json()
        assert len(data) == 1
        assert data[0]["namespace"] == "ns2"


class TestMemoryTtl:
    POST = "/api/memory"

    def test_ttl_expiry_hides_expired_record(self, client):
        # Set with TTL of 1 second, then wait for expiry
        client.post(
            self.POST,
            json={
                "agent_id": "a1",
                "key": "temp",
                "value": "data",
                "ttl_seconds": 1,
            },
        )
        # Verify it exists immediately
        resp = client.get("/api/memory/a1")
        assert len(resp.json()) == 1

        # Wait for TTL to expire
        time.sleep(1.1)

        # After TTL expiry the record should be hidden
        resp = client.get("/api/memory/a1")
        assert resp.json() == []
