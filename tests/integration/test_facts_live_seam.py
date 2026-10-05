"""LIVE end-to-end facts seam test (W12 observability facts).

Proves that genuinely-captured LIVE stats — todos/returns/messages AND
metrics/traces — flow through the REAL daemon ``/api/facts`` endpoint, through
the REAL ``gludd_facts`` module's snapshot transform, into the injected
``ansible_facts.gludd`` dynamic vars. No canned mock values: every asserted
number is seeded into a real repository / collector / trace buffer in this test.

This closes the facts coverage seam: previously the molecule scenario proved
the module transform against a *mock* daemon, and the API test proved the
endpoint against a real DB, but nothing proved the live daemon stats reach the
Ansible fact dict end to end — including the new metrics + traces blocks.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import (
    AgentMessageRepository,
    TaskReturnRepository,
    TodoRepository,
)
from general_ludd.metrics.collector import MetricsCollector
from general_ludd.observability.trace_store import RecentTracesBuffer
from general_ludd.observability.tracer import ExecutionTrace

ROOT = Path(__file__).parent.parent.parent
GLUDD_FACTS_MODULE = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
    / "plugins" / "modules" / "gludd_facts.py"
)

PSK = "live-seam-psk"
AUTH = {"Authorization": f"Bearer {PSK}"}


def _gludd_facts_transform(resp: dict) -> dict:
    """The EXACT transform the real gludd_facts module applies to the /api/facts
    response: strip ``_``-prefixed transport keys and wrap under ``gludd``.

    We assert below that the real module source contains this transform line so
    this helper cannot drift from the shipped module.
    """
    snapshot = {k: v for k, v in resp.items() if not k.startswith("_")}
    return {"gludd": snapshot}


def _seed_metrics(collector: MetricsCollector) -> None:
    """Seed REAL model usage through the collector (not canned)."""
    collector.register_agent("agent-1", agent_name="coder", project="alpha")
    # 3 calls, 2 success / 1 failure -> success_rate 2/3.
    collector.record_model_call(
        "agent-1", "glm-5.1", input_tokens=100, output_tokens=50, success=True,
        cost_per_input_token=0.000001, cost_per_output_token=0.000002,
    )
    collector.record_model_call(
        "agent-1", "glm-5.1", input_tokens=200, output_tokens=80, success=True,
        cost_per_input_token=0.000001, cost_per_output_token=0.000002,
    )
    collector.record_model_call(
        "agent-1", "glm-5.1", input_tokens=50, output_tokens=10, success=False,
        cost_per_input_token=0.000001, cost_per_output_token=0.000002,
    )


def _seed_traces(buffer: RecentTracesBuffer) -> None:
    """Seed a REAL execution trace into the recent-traces buffer."""
    trace = ExecutionTrace(todo_id="TODO-LIVE-1", work_type="code")
    plan = trace.start_span(name="plan", phase="plan")
    plan.complete(status="success", input_tokens=120, output_tokens=40, cost_usd=0.0005)
    gen = trace.start_span(name="generate", phase="generate")
    gen.complete(status="success", input_tokens=300, output_tokens=150, cost_usd=0.0021)
    buffer.record(trace)


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

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory
    # Seed the live observability subsystems on app.state (the lifespan is not
    # run here, so we wire the same objects the daemon would).
    collector = MetricsCollector()
    _seed_metrics(collector)
    app.state._metrics_collector = collector
    buffer = RecentTracesBuffer()
    _seed_traces(buffer)
    app.state._recent_traces = buffer

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


class TestFactsLiveSeam:
    def test_module_transform_line_is_shipped(self):
        """Guard: the helper transform must match the real module source so the
        end-to-end assertion below exercises the genuine transform shape."""
        src = GLUDD_FACTS_MODULE.read_text()
        assert "snapshot = {k: v for k, v in resp.items() if not k.startswith(\"_\")}" in src
        assert '"ansible_facts": {"gludd": snapshot}' in src

    def test_gludd_facts_module_importable(self):
        spec = importlib.util.spec_from_file_location("gludd_facts", str(GLUDD_FACTS_MODULE))
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main")

    @pytest.mark.asyncio
    async def test_live_stats_reach_ansible_facts(self, monkeypatch):
        engine, factory, client, _app = await _make_app(monkeypatch)
        try:
            # --- Seed real DB-backed work/todos/returns/messages ---
            async with factory() as session:
                todo_repo = TodoRepository(session)
                await todo_repo.create(
                    {"title": "t1", "queue": "core", "work_type": "code", "status": "queued"}
                )
                tr_repo = TaskReturnRepository(session)
                await tr_repo.create(
                    {
                        "return_id": "R1", "job_id": "J1", "playbook": "noop.yml",
                        "queue": "core", "work_type": "code", "status": "created",
                        "exit_code": 0,
                    }
                )
                msg_repo = AgentMessageRepository(session)
                await msg_repo.send({"sender": "a", "recipient": "coder", "topic": "x"})
                await session.commit()

            resp = await client.get("/api/facts", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            # --- Apply the REAL module transform -> ansible_facts.gludd ---
            ansible_facts = _gludd_facts_transform(data)
            gludd = ansible_facts["gludd"]

            # DB-backed live stats present end to end.
            assert gludd["todos"]["total"] == 1
            assert gludd["messages"]["total_unread"] == 1

            # --- metrics block: seeded model usage reflected, not canned ---
            metrics = gludd["metrics"]
            assert metrics["total_agents"] == 1
            usage = metrics["global_model_usage"]["glm-5.1"]
            assert usage["total_calls"] == 3
            assert abs(usage["success_rate"] - (2 / 3)) < 1e-9
            assert metrics["cost_by_project"].get("alpha") is not None
            assert metrics["cost_by_project"]["alpha"] > 0

            # Agent-level summary reflects the seeded agent.
            agent_ids = {a["agent_id"] for a in metrics["agents"]}
            assert "agent-1" in agent_ids

            # --- traces block: seeded trace + phase aggregate reflected ---
            traces = gludd["traces"]
            assert traces["count"] == 1
            assert traces["total_recorded"] == 1
            rec = traces["recent"][0]
            assert rec["todo_id"] == "TODO-LIVE-1"
            assert rec["span_count"] == 2
            # by-phase aggregate over genuinely-captured spans.
            assert traces["by_phase"]["generate"]["span_count"] == 1
            assert traces["by_phase"]["generate"]["total_tokens"] == 150
            # otel exporter honestly disabled (no OTLP collector wired).
            assert traces["otel_exporter_status"] == "disabled"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_models_usage_numbers_reflect_seeded_usage(self, monkeypatch):
        """Deepened model assertions: models.usage numbers (calls/success_rate/
        cost) reflect the SEEDED model usage, not merely that routing exists."""
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/facts", headers=AUTH)
            assert resp.status_code == 200, resp.text
            models = resp.json()["models"]

            assert "routing" in models
            usage = models["usage"]["glm-5.1"]
            assert usage["total_calls"] == 3
            assert usage["successful_calls"] == 2
            assert usage["failed_calls"] == 1
            assert abs(usage["success_rate"] - (2 / 3)) < 1e-9
            # cost = (100+200+50)*1e-6 + (50+80+10)*2e-6 = 0.00035 + 0.00028.
            assert abs(usage["total_cost_usd"] - 0.00063) < 1e-9
        finally:
            await client.aclose()
            await engine.dispose()
