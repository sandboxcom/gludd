"""C.19 acceptance: cross-tenant trace isolation for /api/traces.

Verifies that the focused traces endpoint never leaks traces across
project-tenant boundaries and requires a project_id (no default-to-all).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.observability.recorder import AutoBenchmarkRecorder
from general_ludd.observability.trace_store import RecentTracesBuffer
from general_ludd.observability.tracer import ExecutionTrace
from tests.e2e.conftest import _find_free_port

PSK = "c19-e2e-psk"
AUTH = {"Authorization": f"Bearer {PSK}"}


def _build_trace(
    todo_id: str = "C19-TODO",
    work_type: str = "code",
    project_id: str | None = None,
) -> ExecutionTrace:
    trace = ExecutionTrace(todo_id=todo_id, work_type=work_type, project_id=project_id)
    plan = trace.start_span(name="plan", phase="plan")
    plan.complete(
        status="success",
        input_tokens=120,
        output_tokens=80,
        cost_usd=0.0005,
        model_profile_id="glm-5.1",
    )
    gen = trace.start_span(name="generate", phase="generate")
    gen.complete(
        status="success",
        input_tokens=300,
        output_tokens=150,
        cost_usd=0.0021,
        model_profile_id="glm-5.1",
    )
    return trace


async def _make_app(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=999.0)
    app.state._session_factory = factory
    app.state._recent_traces = RecentTracesBuffer()

    port = _find_free_port()
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url=f"http://127.0.0.1:{port}")
    return engine, factory, client, app


class TestC19CrossTenantTraces:
    @pytest.mark.asyncio
    async def test_no_project_id_returns_error(self, monkeypatch):
        """C.19: /api/traces without a project_id returns 400 (not all traces)."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-A", project_id="proj-a"), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-B", project_id="proj-b"), success=True
            )

            resp = await client.get("/api/traces", headers=AUTH)
            assert resp.status_code == 400
            data = resp.json()
            assert "detail" in data
            assert "project_id" in data["detail"]
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_project_a_traces_isolated_from_project_b(self, monkeypatch):
        """C.19: /api/traces?project_id=A returns only project A traces."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-A1", project_id="proj-a"), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-A2", project_id="proj-a"), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-B1", project_id="proj-b"), success=True
            )

            resp = await client.get(
                "/api/traces", params={"project_id": "proj-a"}, headers=AUTH
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 2
            for trace in data["recent"]:
                assert trace["project_id"] == "proj-a"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_project_b_traces_isolated_from_project_a(self, monkeypatch):
        """C.19: /api/traces?project_id=B returns only project B traces."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-A1", project_id="proj-a"), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-B1", project_id="proj-b"), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-B2", project_id="proj-b"), success=True
            )

            resp = await client.get(
                "/api/traces", params={"project_id": "proj-b"}, headers=AUTH
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 2
            for trace in data["recent"]:
                assert trace["project_id"] == "proj-b"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_both_projects_queried_independently_no_leak(self, monkeypatch):
        """C.19: Independent queries for A and B confirm complete isolation."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-A1", project_id="proj-a"), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-B1", project_id="proj-b"), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-A2", project_id="proj-a"), success=True
            )

            resp_a = await client.get(
                "/api/traces", params={"project_id": "proj-a"}, headers=AUTH
            )
            resp_b = await client.get(
                "/api/traces", params={"project_id": "proj-b"}, headers=AUTH
            )

            data_a = resp_a.json()
            data_b = resp_b.json()
            assert data_a["count"] == 2
            assert data_b["count"] == 1

            a_todo_ids = {t["todo_id"] for t in data_a["recent"]}
            b_todo_ids = {t["todo_id"] for t in data_b["recent"]}
            assert a_todo_ids == {"T-A1", "T-A2"}
            assert b_todo_ids == {"T-B1"}
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_unattributed_traces_excluded_from_scoped_query(self, monkeypatch):
        """C.19: Legacy None-project_id traces do not leak to a scoped query."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-LEGACY", project_id=None), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-A1", project_id="proj-a"), success=True
            )

            resp = await client.get(
                "/api/traces", params={"project_id": "proj-a"}, headers=AUTH
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["recent"][0]["project_id"] == "proj-a"
        finally:
            await client.aclose()
            await engine.dispose()
