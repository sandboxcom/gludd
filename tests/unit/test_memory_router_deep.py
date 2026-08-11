"""Deep unit tests for the memory router — boundary validation, project_id
isolation, repo-unavailable (503), upsert TTL mutations, and cross-agent
isolation."""

from __future__ import annotations

from datetime import UTC

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio.session import async_sessionmaker
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

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

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
# 503 Service Unavailable
# ---------------------------------------------------------------------------


class TestRepoUnavailable:
    def test_create_returns_503_when_repo_not_set(self):
        app2 = FastAPI()
        memory_router.register(app2, {})
        client2 = TestClient(app2)
        resp = client2.post(
            "/api/memory",
            json={"agent_id": "a1", "key": "k1", "value": "v1"},
        )
        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"].lower()

    def test_list_returns_503_when_repo_not_set(self):
        app2 = FastAPI()
        memory_router.register(app2, {})
        client2 = TestClient(app2)
        resp = client2.get("/api/memory/a1")
        assert resp.status_code == 503

    def test_delete_returns_503_when_repo_not_set(self):
        app2 = FastAPI()
        memory_router.register(app2, {})
        client2 = TestClient(app2)
        resp = client2.delete("/api/memory/a1/k1")
        assert resp.status_code == 503

    def test_repo_wrong_type_returns_503(self):
        app2 = FastAPI()
        app2.state._memory_repo = "not_a_repo"
        memory_router.register(app2, {})
        client2 = TestClient(app2)
        resp = client2.get("/api/memory/a1")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Boundary validation (422 from Pydantic)
# ---------------------------------------------------------------------------


class TestBoundaryValidation:
    POST = "/api/memory"

    def test_empty_agent_id_returns_422(self, client):
        resp = client.post(self.POST, json={"agent_id": "", "key": "k1"})
        assert resp.status_code == 422

    def test_agent_id_max_length_128(self, client):
        resp = client.post(self.POST, json={"agent_id": "a" * 128, "key": "k1"})
        assert resp.status_code == 201
        resp2 = client.post(self.POST, json={"agent_id": "a" * 129, "key": "k1"})
        assert resp2.status_code == 422

    def test_empty_key_returns_422(self, client):
        resp = client.post(self.POST, json={"agent_id": "a1", "key": ""})
        assert resp.status_code == 422

    def test_key_max_length_256(self, client):
        resp = client.post(self.POST, json={"agent_id": "a1", "key": "k" * 256})
        assert resp.status_code == 201
        resp2 = client.post(self.POST, json={"agent_id": "a1", "key": "k" * 257})
        assert resp2.status_code == 422

    def test_missing_key_returns_422(self, client):
        resp = client.post(self.POST, json={"agent_id": "a1"})
        assert resp.status_code == 422

    def test_negative_ttl_returns_422(self, client):
        resp = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "ttl_seconds": -1},
        )
        assert resp.status_code == 422

    def test_ttl_zero_is_valid(self, client):
        resp = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "ttl_seconds": 0},
        )
        assert resp.status_code == 201

    def test_namespace_empty_defaults_to_default(self, client):
        resp = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "namespace": ""},
        )
        assert resp.status_code == 201
        assert resp.json()["namespace"] == ""

    def test_namespace_max_length_128(self, client):
        resp = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "namespace": "n" * 128},
        )
        assert resp.status_code == 201
        resp2 = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k2", "namespace": "n" * 129},
        )
        assert resp2.status_code == 422

    def test_project_id_max_length_256(self, client):
        resp = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "project_id": "p" * 256},
        )
        assert resp.status_code == 201
        resp2 = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k2", "project_id": "p" * 257},
        )
        assert resp2.status_code == 422

    def test_extra_fields_ignored(self, client):
        resp = client.post(
            self.POST,
            json={
                "agent_id": "a1",
                "key": "k1",
                "value": "v1",
                "extra_garbage": 12345,
            },
        )
        assert resp.status_code == 201

    def test_zero_length_value_is_valid(self, client):
        resp = client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": ""})
        assert resp.status_code == 201
        assert resp.json()["value"] == ""


# ---------------------------------------------------------------------------
# Project-id isolation
# ---------------------------------------------------------------------------


class TestProjectIdIsolation:
    POST = "/api/memory"
    LIST = "/api/memory/{agent_id}"
    DELETE = "/api/memory/{agent_id}/{key}"

    def test_create_with_project_id(self, client):
        resp = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "project_id": "proj-abc"},
        )
        assert resp.status_code == 201
        assert resp.json()["project_id"] == "proj-abc"

    def test_list_filters_by_project_id(self, client):
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "project_id": "p1"},
        )
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k2", "project_id": "p2"},
        )
        resp = client.get(f"{self.LIST.format(agent_id='a1')}?namespace=*&project_id=p1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["key"] == "k1"
        assert data[0]["project_id"] == "p1"

    def test_delete_filters_by_project_id(self, client):
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "project_id": "p1"},
        )
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "project_id": "p2"},
        )
        resp = client.delete(f"{self.DELETE.format(agent_id='a1', key='k1')}?namespace=default&project_id=p1")
        assert resp.status_code == 204
        # p2 record still exists
        resp2 = client.get(f"{self.LIST.format(agent_id='a1')}?namespace=*&project_id=p2")
        assert len(resp2.json()) == 1

    def test_same_key_different_project_ids(self, client):
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "shared", "value": "vp1", "project_id": "p1"},
        )
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "shared", "value": "vp2", "project_id": "p2"},
        )
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "shared", "value": "vnull"},
        )
        resp = client.get(f"{self.LIST.format(agent_id='a1')}?namespace=*")
        assert len(resp.json()) == 3

    def test_project_id_none_on_create(self, client):
        resp = client.post(self.POST, json={"agent_id": "a1", "key": "k1"})
        assert resp.status_code == 201
        assert resp.json()["project_id"] is None


# ---------------------------------------------------------------------------
# Cross-agent isolation
# ---------------------------------------------------------------------------


class TestCrossAgentIsolation:
    POST = "/api/memory"
    LIST = "/api/memory/{agent_id}"

    def test_different_agents_dont_leak(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        client.post(self.POST, json={"agent_id": "a2", "key": "k2", "value": "v2"})
        resp_a1 = client.get(self.LIST.format(agent_id="a1"))
        resp_a2 = client.get(self.LIST.format(agent_id="a2"))
        assert len(resp_a1.json()) == 1
        assert len(resp_a2.json()) == 1
        assert resp_a1.json()[0]["agent_id"] == "a1"
        assert resp_a2.json()[0]["agent_id"] == "a2"

    def test_same_key_different_agents_no_collision(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "shared", "value": "v1"})
        client.post(self.POST, json={"agent_id": "a2", "key": "shared", "value": "v2"})
        resp_a1 = client.get(self.LIST.format(agent_id="a1"))
        resp_a2 = client.get(self.LIST.format(agent_id="a2"))
        values = {resp_a1.json()[0]["value"], resp_a2.json()[0]["value"]}
        assert values == {"v1", "v2"}


# ---------------------------------------------------------------------------
# Upsert with TTL mutation
# ---------------------------------------------------------------------------


class TestUpsertTtlMutation:
    POST = "/api/memory"

    def test_upsert_changes_value(self, client):
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "value": "old"},
        )
        resp = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "value": "new"},
        )
        assert resp.status_code == 201
        assert resp.json()["value"] == "new"

    def test_upsert_adds_ttl(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        resp = client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "ttl_seconds": 3600},
        )
        assert resp.status_code == 201
        assert resp.json()["ttl_seconds"] == 3600
        assert resp.json()["value"] == "v1"

    def test_upsert_removes_ttl(self, client):
        client.post(
            self.POST,
            json={"agent_id": "a1", "key": "k1", "ttl_seconds": 3600},
        )
        resp = client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        assert resp.status_code == 201
        assert resp.json()["ttl_seconds"] is None

    def test_upsert_same_id_preserved(self, client):
        r1 = client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        id1 = r1.json()["id"]
        r2 = client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v2"})
        assert r2.json()["id"] == id1


# ---------------------------------------------------------------------------
# from_model serialization
# ---------------------------------------------------------------------------


class TestFromModelSerialization:
    def test_from_model_with_none_dates_handles_default(self):
        from datetime import datetime as dt

        from general_ludd.db.models import MemoryRecordModel

        row = MemoryRecordModel(
            id="test-id",
            agent_id="a1",
            key="k1",
            value="v1",
            namespace="default",
            created_at=dt(2024, 1, 1, 0, 0, 0),
            updated_at=dt(2024, 1, 1, 0, 0, 0),
        )
        resp = memory_router.MemoryRecordResponse.from_model(row)
        assert resp.id == "test-id"
        assert resp.created_at == "2024-01-01T00:00:00"
        assert resp.updated_at == "2024-01-01T00:00:00"

    def test_from_model_includes_all_fields(self):
        from datetime import datetime as dt

        from general_ludd.db.models import MemoryRecordModel

        row = MemoryRecordModel(
            id="full-id",
            agent_id="agent-x",
            key="key-y",
            value="val-z",
            namespace="ns-custom",
            project_id="proj-1",
            ttl_seconds=7200,
            created_at=dt(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
            updated_at=dt(2025, 6, 15, 12, 30, 0, tzinfo=UTC),
        )
        resp = memory_router.MemoryRecordResponse.from_model(row)
        assert resp.id == "full-id"
        assert resp.agent_id == "agent-x"
        assert resp.key == "key-y"
        assert resp.value == "val-z"
        assert resp.namespace == "ns-custom"
        assert resp.project_id == "proj-1"
        assert resp.ttl_seconds == 7200
        assert "2025" in resp.created_at
        assert "2025" in resp.updated_at


# ---------------------------------------------------------------------------
# Delete edge cases
# ---------------------------------------------------------------------------


class TestDeleteEdgeCases:
    POST = "/api/memory"
    DELETE = "/api/memory/{agent_id}/{key}"

    def test_delete_nonexistent_with_namespace(self, client):
        resp = client.delete(f"{self.DELETE.format(agent_id='a1', key='nk')}?namespace=default")
        assert resp.status_code == 404

    def test_delete_from_empty_repo(self, client):
        resp = client.delete(self.DELETE.format(agent_id="a1", key="k1"))
        assert resp.status_code == 404

    def test_delete_returns_204_empty_body(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        resp = client.delete(self.DELETE.format(agent_id="a1", key="k1"))
        assert resp.status_code == 204
        assert resp.content == b""


# ---------------------------------------------------------------------------
# List edge cases
# ---------------------------------------------------------------------------


class TestListEdgeCases:
    POST = "/api/memory"
    LIST = "/api/memory/{agent_id}"

    def test_list_response_is_json_array(self, client):
        resp = client.get(self.LIST.format(agent_id="a1"))
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_all_fields_present(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        resp = client.get(self.LIST.format(agent_id="a1"))
        record = resp.json()[0]
        for field in (
            "id",
            "agent_id",
            "key",
            "value",
            "namespace",
            "project_id",
            "ttl_seconds",
            "created_at",
            "updated_at",
        ):
            assert field in record, f"missing field {field}"

    def test_list_sorted_by_key(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "zzz", "value": "last"})
        client.post(self.POST, json={"agent_id": "a1", "key": "aaa", "value": "first"})
        client.post(self.POST, json={"agent_id": "a1", "key": "mmm", "value": "middle"})
        resp = client.get(self.LIST.format(agent_id="a1"))
        keys = [r["key"] for r in resp.json()]
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# Create response shape
# ---------------------------------------------------------------------------


class TestCreateResponseShape:
    POST = "/api/memory"

    def test_create_response_has_all_fields(self, client):
        resp = client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        assert resp.status_code == 201
        data = resp.json()
        for field in (
            "id",
            "agent_id",
            "key",
            "value",
            "namespace",
            "project_id",
            "ttl_seconds",
            "created_at",
            "updated_at",
        ):
            assert field in data, f"missing field {field} in create response"

    def test_create_id_is_string_nonempty(self, client):
        resp = client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        rid = resp.json()["id"]
        assert isinstance(rid, str)
        assert len(rid) > 0

    def test_create_dates_are_isoformat_strings(self, client):
        resp = client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        data = resp.json()
        assert "T" in data["created_at"]
        assert "T" in data["updated_at"]


# ---------------------------------------------------------------------------
# Namespace query parameter edge cases
# ---------------------------------------------------------------------------


class TestNamespaceQueryEdgeCases:
    POST = "/api/memory"
    LIST = "/api/memory/{agent_id}"
    DELETE = "/api/memory/{agent_id}/{key}"

    def test_wildcard_list_includes_all_namespaces(self, client):
        for ns in ("ns1", "ns2", "ns3"):
            client.post(
                self.POST,
                json={
                    "agent_id": "a1",
                    "key": f"k-{ns}",
                    "value": "v",
                    "namespace": ns,
                },
            )
        resp = client.get(f"{self.LIST.format(agent_id='a1')}?namespace=*")
        assert len(resp.json()) == 3

    def test_list_default_namespace_implicit(self, client):
        client.post(self.POST, json={"agent_id": "a1", "key": "k1", "value": "v1"})
        client.post(
            self.POST,
            json={
                "agent_id": "a1",
                "key": "k2",
                "value": "v2",
                "namespace": "custom",
            },
        )
        resp = client.get(self.LIST.format(agent_id="a1"))
        data = resp.json()
        assert len(data) == 1
        assert data[0]["namespace"] == "default"
        assert data[0]["key"] == "k1"

    def test_list_explicit_namespace(self, client):
        client.post(
            self.POST,
            json={
                "agent_id": "a1",
                "key": "k1",
                "value": "v1",
                "namespace": "custom",
            },
        )
        resp = client.get(f"{self.LIST.format(agent_id='a1')}?namespace=custom")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["namespace"] == "custom"
