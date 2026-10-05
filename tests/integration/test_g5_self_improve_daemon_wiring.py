"""Integration tests for G5 outcome-driven self-improvement daemon wiring.

Proves the self_improve router is registered (GET /admin/self-improve/status),
OutcomeAnalyzer works with stub data, and the self-improve apply pipeline
(analyze → generate → enqueue) integrates correctly.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.self_improve.outcomes import OutcomeAnalyzer

PSK = "test-self-improve-psk"
AUTH = {"Authorization": f"Bearer {PSK}"}


async def _make_app(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, factory, client, app


class TestSelfImproveRouterWired:
    @pytest.mark.asyncio
    async def test_self_improve_status_endpoint_returns_200(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.get("/admin/self-improve/status", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "status" in data
            assert data["status"] in ("never_run", "completed")
            assert "findings_count" in data
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_self_improve_analyze_endpoint_returns_findings(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post("/admin/self-improve/analyze", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "findings" in data
            assert isinstance(data["findings"], list)
            assert "findings_count" in data
            assert isinstance(data["findings_count"], int)
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_self_improve_run_endpoint_with_db(self, monkeypatch):
        engine, _factory, client, _app = await _make_app(monkeypatch)
        try:
            resp = await client.post("/admin/self-improve/run", headers=AUTH)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "findings_count" in data
            assert "todos_enqueued" in data
            assert isinstance(data["todos_enqueued"], int)
        finally:
            await client.aclose()
            await engine.dispose()


class TestOutcomeAnalyzer:
    def test_analyzer_with_no_data_returns_empty(self):
        analyzer = OutcomeAnalyzer()
        result = analyzer.analyze()
        assert result["status"] == "no_data"
        assert result["suggestions"] == []

    def test_analyzer_with_passing_outcomes(self):
        analyzer = OutcomeAnalyzer(min_samples=1)
        outcomes = [
            {"task_type": "code", "model": "sonnet", "passed": True, "tokens_used": 100, "duration_ms": 500},
            {"task_type": "code", "model": "sonnet", "passed": True, "tokens_used": 120, "duration_ms": 600},
            {"task_type": "code", "model": "sonnet", "passed": True, "tokens_used": 90, "duration_ms": 450},
        ]
        result = analyzer.analyze(outcomes, threshold=0.5)
        assert result["status"] == "analyzed"
        assert isinstance(result["suggestions"], list)
        # With 100% pass rate and threshold 0.5, no suggestions expected
        assert len(result["suggestions"]) == 0

    def test_analyzer_with_failing_outcomes(self):
        analyzer = OutcomeAnalyzer(min_samples=1)
        outcomes = [
            {"task_type": "code", "model": "opus", "passed": False, "tokens_used": 200, "duration_ms": 1000},
            {"task_type": "code", "model": "opus", "passed": False, "tokens_used": 250, "duration_ms": 1200},
            {"task_type": "code", "model": "opus", "passed": True, "tokens_used": 180, "duration_ms": 800},
        ]
        result = analyzer.analyze(outcomes, threshold=0.5)
        assert result["status"] == "analyzed"
        assert len(result["suggestions"]) >= 1

        suggestion = result["suggestions"][0]
        assert suggestion["task_type"] == "code"
        assert suggestion["model"] == "opus"
        assert suggestion["pass_rate"] < 0.5
        assert "sample_count" in suggestion
        assert suggestion["sample_count"] == 3
        assert "suggestion" in suggestion

    def test_analyzer_accumulates_outcomes_across_calls(self):
        analyzer = OutcomeAnalyzer(min_samples=1)
        analyzer.analyze([
            {"task_type": "test", "model": "sonnet", "passed": False,
             "tokens_used": 50, "duration_ms": 300},
        ])
        result = analyzer.analyze(
            [{"task_type": "test", "model": "sonnet", "passed": False, "tokens_used": 60, "duration_ms": 400}],
            threshold=0.5,
        )
        assert result["status"] == "analyzed"
        assert len(result["suggestions"]) >= 1
        assert result["suggestions"][0]["sample_count"] >= 2

    def test_analyzer_respects_threshold(self):
        analyzer = OutcomeAnalyzer(min_samples=1)
        outcomes = [
            {"task_type": "code", "model": "sonnet", "passed": True, "tokens_used": 100, "duration_ms": 500},
            {"task_type": "code", "model": "sonnet", "passed": False, "tokens_used": 100, "duration_ms": 500},
        ]
        # Threshold 0.5: 50% pass rate => not below threshold => no suggestions
        result = analyzer.analyze(outcomes, threshold=0.5)
        assert len(result["suggestions"]) == 0

        # Threshold 0.6: 50% < 0.6 => suggestions appear
        result2 = analyzer.analyze([], threshold=0.6)
        assert len(result2["suggestions"]) == 1
        assert result2["suggestions"][0]["pass_rate"] == 0.5

    def test_analyzer_multiple_task_types(self):
        analyzer = OutcomeAnalyzer(min_samples=1)
        outcomes = [
            {"task_type": "code", "model": "sonnet", "passed": False, "tokens_used": 100, "duration_ms": 500},
            {"task_type": "code", "model": "sonnet", "passed": False, "tokens_used": 120, "duration_ms": 600},
            {"task_type": "test", "model": "haiku", "passed": False, "tokens_used": 50, "duration_ms": 300},
            {"task_type": "test", "model": "haiku", "passed": False, "tokens_used": 60, "duration_ms": 350},
        ]
        result = analyzer.analyze(outcomes, threshold=0.5)
        assert result["status"] == "analyzed"
        task_types = {s["task_type"] for s in result["suggestions"]}
        assert "code" in task_types
        assert "test" in task_types


class TestSelfImprovePipeline:
    def test_analyze_generate_enqueue_cycle(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        harness = SelfImprovementHarness()
        findings = harness.run_gap_analysis()
        assert isinstance(findings, list)

        todos = harness.generate_fix_todos(findings)
        assert isinstance(todos, list)

        enqueued = harness.enqueue_todos(todos)
        assert enqueued == len(todos)

    def test_run_full_cycle_returns_expected_structure(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        harness = SelfImprovementHarness()
        result = harness.run_full_cycle()

        assert "findings_count" in result
        assert "todos_generated" in result
        assert "todos_enqueued" in result
        assert "findings" in result
        assert "todos" in result

    def test_harness_recurring_failure_smoke(self):
        from general_ludd.self_improve.harness import SelfImprovementHarness

        harness = SelfImprovementHarness()
        findings = harness.run_gap_analysis(
            recurring_failures=[{
                "task_type": "code",
                "blocker_kind": "permission_escalation",
                "incident_count": 5,
                "recent_todo_ids": ["T-1", "T-2"],
            }]
        )
        recurring = [f for f in findings if f.get("type") == "recurring_failure"]
        assert len(recurring) >= 1
        assert recurring[0]["blocker_kind"] == "permission_escalation"
        assert recurring[0]["incident_count"] == 5
