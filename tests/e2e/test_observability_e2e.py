"""End-to-end tests for the observability subsystem.

Exercises the REAL daemon observability pipeline through HTTP:
  tracer.py          -> ExecutionTrace / ExecutionSpan construction
  recorder.py        -> AutoBenchmarkRecorder.record_from_trace (the production
                        event-loop path that appends a completed trace to the
                        recent-traces buffer)
  trace_store.py     -> RecentTracesBuffer (the bounded ring /api/facts and
                        /api/traces read from)
  otel_bridge.py     -> OTelBridge.is_available() honest status reporting
  token_cost.py      -> TokenCostTracker populated via the same
                        default_token_tracker().record() call the ModelGateway
                        makes on every billed call (gateway.py:885)
  metrics_exporter.py / langsmith_tracer.py / dashboard_data.py -> covered
                        transitively via the daemon boot + facts aggregation.

The daemon is booted in-process via create_daemon_app() over an ASGITransport
(reliable, no subprocess flakiness) using the ephemeral-port helper from the
e2e conftest for the client base_url.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.observability.recorder import AutoBenchmarkRecorder
from general_ludd.observability.token_cost import default_token_tracker
from general_ludd.observability.trace_store import RecentTracesBuffer
from general_ludd.observability.tracer import ExecutionTrace
from tests.e2e.conftest import _find_free_port

PSK = "observability-e2e-psk"
AUTH = {"Authorization": f"Bearer {PSK}"}


def _build_trace(
    todo_id: str = "TODO-OBS-1",
    work_type: str = "code",
    project_id: str | None = None,
) -> ExecutionTrace:
    """Build a realistic multi-span trace (plan + generate) via the real tracer."""
    trace = ExecutionTrace(todo_id=todo_id, work_type=work_type, project_id=project_id)
    plan = trace.start_span(name="plan", phase="plan")
    plan.complete(
        status="success",
        input_tokens=120,
        output_tokens=40,
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
    """Boot the REAL daemon in-process (no lifespan) + wire observability state.

    Mirrors tests/integration/test_facts_live_seam.py: create_daemon_app builds
    the full router stack (including /api/facts + /api/traces); we attach a fresh
    RecentTracesBuffer (the same object the lifespan would create at
    daemon.py:2099-2101) so recorder appends are observable through HTTP.
    """
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

    # Long tick interval so the background event loop never fires during the test.
    app = create_daemon_app(tick_interval=999.0)
    app.state._session_factory = factory
    app.state._recent_traces = RecentTracesBuffer()

    port = _find_free_port()
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url=f"http://127.0.0.1:{port}")
    return engine, factory, client, app


class TestObservabilityE2E:
    """Full daemon boot -> HTTP -> observability assertions."""

    @pytest.mark.asyncio
    async def test_recorder_produced_trace_reaches_facts(self, monkeypatch):
        """A trace recorded via the production AutoBenchmarkRecorder path
        (the same call the event loop makes on job completion) surfaces in the
        /api/facts traces facet."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(_build_trace(), success=True)

            resp = await client.get("/api/facts", headers=AUTH)
            assert resp.status_code == 200, resp.text
            traces = resp.json()["traces"]
            assert traces["count"] == 1
            assert traces["total_recorded"] == 1
            rec = traces["recent"][0]
            assert rec["todo_id"] == "TODO-OBS-1"
            assert rec["span_count"] == 2
            # by-phase aggregate over genuinely-captured spans.
            assert traces["by_phase"]["generate"]["span_count"] == 1
            assert traces["by_phase"]["generate"]["total_tokens"] == 150
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_traces_endpoint_returns_recent_traces(self, monkeypatch):
        """GET /api/traces returns the recently-recorded trace + phase aggregate."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(
                _build_trace(project_id="proj-default"), success=True
            )

            resp = await client.get(
                "/api/traces", params={"project_id": "proj-default"}, headers=AUTH
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["count"] == 1
            assert data["total_recorded"] == 1
            rec = data["recent"][0]
            assert rec["trace_id"].startswith("trace-")
            assert rec["work_type"] == "code"
            assert rec["total_cost_usd"] == pytest.approx(0.0026)
            assert rec["success_rate"] == 1.0
            assert {ph for ph in data["by_phase"]} == {"plan", "generate"}
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_traces_endpoint_todo_filter(self, monkeypatch):
        """GET /api/traces?todo_id= scopes to the requested todo."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="TODO-A", project_id="proj-default"), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="TODO-B", project_id="proj-default"), success=True
            )

            resp = await client.get(
                "/api/traces",
                params={"todo_id": "TODO-A", "project_id": "proj-default"},
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["count"] == 1
            assert data["recent"][0]["todo_id"] == "TODO-A"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_otel_exporter_status_honestly_disabled(self, monkeypatch):
        """When no OTLP collector is wired, the exporter status is reported as
        'disabled' (never fabricated as 'available') in both /api/facts and
        /api/traces."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            # No app.state._otel_bridge set -> _traces_facet must report disabled.
            assert getattr(app.state, "_otel_bridge", None) is None

            facts = await client.get("/api/facts", headers=AUTH)
            assert facts.status_code == 200
            assert facts.json()["traces"]["otel_exporter_status"] == "disabled"

            traces = await client.get(
                "/api/traces", params={"project_id": "proj-default"}, headers=AUTH
            )
            assert traces.status_code == 200
            assert traces.json()["otel_exporter_status"] == "disabled"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_otel_bridge_unavailable_reports_disabled(self, monkeypatch):
        """An OTelBridge instance whose is_available() is False (no OTLP packages
        installed in the test env) still reports 'disabled' honestly."""
        from general_ludd.observability.otel_bridge import OTelBridge

        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            bridge = OTelBridge(endpoint="http://127.0.0.1:4317")
            app.state._otel_bridge = bridge
            # In the test env opentelemetry is not installed -> unavailable.
            assert bridge.is_available() is False

            resp = await client.get(
                "/api/traces", params={"project_id": "proj-default"}, headers=AUTH
            )
            assert resp.status_code == 200
            assert resp.json()["otel_exporter_status"] == "disabled"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_empty_traces_facet_is_well_formed(self, monkeypatch):
        """Before any trace is recorded, the traces facet is structurally valid
        (honest zero-count, not an error)."""
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get(
                "/api/traces", params={"project_id": "proj-default"}, headers=AUTH
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 0
            assert data["total_recorded"] == 0
            assert data["recent"] == []
            assert data["otel_exporter_status"] == "disabled"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_token_cost_populated_after_model_call(self, monkeypatch):
        """The ModelGateway records every billed call via
        default_token_tracker().record(work_type, in, out) (gateway.py:885).
        After such calls the tracker must reflect learned per-key token weight."""
        import general_ludd.observability.token_cost as tc_module

        # Reset the process-wide singleton so this test is isolated from any
        # prior call (the gateway path uses the shared tracker).
        monkeypatch.setattr(tc_module, "_shared_tracker", None)
        tracker = default_token_tracker()

        # Simulate three billed model calls for the same work_type (the min-samples
        # gate is 3 before a baseline is trusted).
        for _ in range(3):
            tracker.record("code", input_tokens=400, output_tokens=200)

        weight = tracker.weight("code")
        assert weight is not None, "token weight not populated after model calls"
        assert weight.key == "code"
        assert weight.samples == 3
        assert weight.median_input == 400
        assert weight.median_output == 200
        assert weight.median_total == 600
        assert tracker.baseline_total("code") == 600
        assert any(w.key == "code" for w in tracker.heaviest())

    @pytest.mark.asyncio
    async def test_token_cost_classifies_heavy_vs_light(self, monkeypatch):
        """Two work_types with divergent token costs classify as heavy/light."""
        import general_ludd.observability.token_cost as tc_module

        monkeypatch.setattr(tc_module, "_shared_tracker", None)
        tracker = default_token_tracker()

        # 'code' is token-heavy, 'docs' is token-light.
        for _ in range(4):
            tracker.record("code", input_tokens=2000, output_tokens=1000)
        for _ in range(4):
            tracker.record("docs", input_tokens=50, output_tokens=20)

        heavy = tracker.classify("code")
        light = tracker.classify("docs")
        assert heavy == "heavy"
        assert light == "light"

    @pytest.mark.asyncio
    async def test_recorder_records_multiple_traces_in_order(self, monkeypatch):
        """The recent-traces buffer returns newest-first; total_recorded counts
        every trace ever seen (including ones beyond the window)."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(_build_trace(todo_id="T1", project_id="proj-default"), success=True)
            await recorder.record_from_trace(_build_trace(todo_id="T2", project_id="proj-default"), success=True)
            await recorder.record_from_trace(_build_trace(todo_id="T3", project_id="proj-default"), success=True)

            resp = await client.get(
                "/api/traces", params={"project_id": "proj-default"}, headers=AUTH
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 3
            assert data["total_recorded"] == 3
            # newest-first ordering
            todo_ids = [r["todo_id"] for r in data["recent"]]
            assert todo_ids == ["T3", "T2", "T1"]
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_failed_span_error_surfaces_in_trace(self, monkeypatch):
        """A span that completes with status='error' carries its error_message
        through the recorder into the HTTP trace payload."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            trace = ExecutionTrace(todo_id="TODO-ERR", work_type="code", project_id="proj-default")
            span = trace.start_span(name="generate", phase="generate")
            span.complete(
                status="error",
                input_tokens=10,
                output_tokens=0,
                cost_usd=0.0001,
                error_message="model timeout",
            )
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(trace, success=False)

            resp = await client.get(
                "/api/traces", params={"project_id": "proj-default"}, headers=AUTH
            )
            assert resp.status_code == 200
            rec = resp.json()["recent"][0]
            assert rec["todo_id"] == "TODO-ERR"
            assert rec["success_rate"] == 0.0
            err_span = rec["spans"][0]
            assert err_span["status"] == "error"
            assert err_span["error_message"] == "model timeout"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_metrics_facet_present_in_facts(self, monkeypatch):
        """The metrics facet (sourced from MetricsCollector / metrics_exporter)
        is structurally present in /api/facts even when no agent is registered."""
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/facts", headers=AUTH)
            assert resp.status_code == 200
            metrics = resp.json()["metrics"]
            for key in (
                "agents",
                "total_agents",
                "running_agents",
                "global_model_usage",
                "cost_by_project",
                "benchmark_rankings",
            ):
                assert key in metrics, f"missing metrics facet key: {key}"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_project_scoped_traces_exclude_other_tenants(self, monkeypatch):
        """A project-scoped /api/traces?project_id=A query returns only that
        project's traces and excludes unattributed (None-project) traces."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-PROJ-A", project_id="proj-a"), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-PROJ-B", project_id="proj-b"), success=True
            )
            # Legacy unattributed trace (project_id=None) must NOT leak to a scoped caller.
            await recorder.record_from_trace(
                _build_trace(todo_id="T-LEGACY", project_id=None), success=True
            )

            resp = await client.get(
                "/api/traces", params={"project_id": "proj-a"}, headers=AUTH
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["recent"][0]["todo_id"] == "T-PROJ-A"
            assert data["recent"][0]["project_id"] == "proj-a"
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_facts_trace_block_matches_traces_endpoint(self, monkeypatch):
        """The /api/facts `traces` block and the focused /api/traces endpoint
        read the SAME buffer and agree on count + total_recorded."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(
                _build_trace(project_id="proj-default"), success=True
            )

            facts_resp = await client.get("/api/facts", headers=AUTH)
            traces_resp = await client.get(
                "/api/traces", params={"project_id": "proj-default"}, headers=AUTH
            )
            f_traces = facts_resp.json()["traces"]
            t_traces = traces_resp.json()
            assert f_traces["count"] == t_traces["count"]
            assert f_traces["total_recorded"] == t_traces["total_recorded"]
            assert f_traces["recent"][0]["trace_id"] == t_traces["recent"][0]["trace_id"]
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_c19_project_scoped_token_cannot_read_other_tenant(self, monkeypatch):
        """C19 acceptance: a project-scoped bearer token (proj-a:psk) MUST
        NOT be able to read another project's traces. The auth scope overrides
        any caller-supplied ?project_id= query param."""
        engine, _factory, client, app = await _make_app(monkeypatch)
        try:
            recorder = AutoBenchmarkRecorder(
                benchmark_repo=None,
                trace_buffer=app.state._recent_traces,
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-PROJ-A", project_id="proj-a"), success=True
            )
            await recorder.record_from_trace(
                _build_trace(todo_id="T-PROJ-B", project_id="proj-b"), success=True
            )

            # Project-scoped token: "proj-a:psk". The middleware parses
            # this and stamps request.state.project_id = "proj-a".
            scoped_auth = {"Authorization": f"Bearer proj-a:{PSK}"}
            resp = await client.get(
                "/api/traces",
                params={"project_id": "proj-b"},
                headers=scoped_auth,
            )
            assert resp.status_code == 200
            data = resp.json()
            # Auth-derived scope "proj-a" wins — only proj-a traces returned,
            # even though the caller requested proj-b.
            assert data["count"] == 1
            assert data["recent"][0]["todo_id"] == "T-PROJ-A"
            assert data["recent"][0]["project_id"] == "proj-a"
        finally:
            await client.aclose()
            await engine.dispose()
