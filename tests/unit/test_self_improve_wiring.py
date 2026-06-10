from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.agents.behavior import AgentBehavior, BehaviorRenderer
from general_ludd.daemon import create_daemon_app
from general_ludd.event_loop.loop import EventLoop


@pytest.fixture
def app(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return create_daemon_app(config_dir=str(config_dir))


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestSelfImproveAnalyzeEndpoint:
    @pytest.mark.asyncio
    async def test_analyze_returns_findings(self, transport):
        with patch(
            "general_ludd.routers.self_improve.SelfImprovementHarness"
        ) as MockHarness:
            instance = MockHarness.return_value
            instance.run_gap_analysis.return_value = [
                {"type": "dead_code", "file": "src/mod.py", "severity": "medium",
                 "message": "Foo has no callers"},
            ]
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/self-improve/analyze")
                assert resp.status_code == 200
                data = resp.json()
                assert data["findings_count"] == 1
                assert data["findings"][0]["type"] == "dead_code"


class TestSelfImproveRunEndpoint:
    @pytest.mark.asyncio
    async def test_run_full_cycle(self, transport):
        with patch(
            "general_ludd.routers.self_improve.SelfImprovementHarness"
        ) as MockHarness:
            instance = MockHarness.return_value
            instance.run_full_cycle.return_value = {
                "findings_count": 2,
                "todos_generated": 2,
                "todos_enqueued": 2,
                "findings": [
                    {"type": "missing_tests", "file": "a.py", "severity": "high",
                     "message": "no tests"},
                ],
                "todos": [
                    {"title": "Add tests for a.py", "work_type": "test",
                     "priority": "high"},
                ],
            }
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/self-improve/run")
                assert resp.status_code == 200
                data = resp.json()
                assert data["findings_count"] == 2
                assert data["todos_enqueued"] == 2


class TestSelfImproveStatusEndpoint:
    @pytest.mark.asyncio
    async def test_status_never_run(self, transport):
        from general_ludd.daemon import _daemon_state
        _daemon_state.pop("self_improve_last_analysis", None)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/self-improve/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "never_run"

    @pytest.mark.asyncio
    async def test_status_after_analyze(self, transport):
        with patch(
            "general_ludd.routers.self_improve.SelfImprovementHarness"
        ) as MockHarness:
            instance = MockHarness.return_value
            instance.run_gap_analysis.return_value = [
                {"type": "dead_code", "file": "x.py", "severity": "medium",
                 "message": "unused class"},
            ]
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post("/admin/self-improve/analyze")
                resp = await client.get("/admin/self-improve/status")
                data = resp.json()
                assert data["status"] == "completed"
                assert data["findings_count"] == 1


class TestAgentBehaviorSelfImproveInterval:
    def test_default_interval_is_zero(self):
        behavior = AgentBehavior()
        assert behavior.self_improve_interval == 0

    def test_set_interval(self):
        behavior = AgentBehavior(self_improve_interval=10)
        assert behavior.self_improve_interval == 10

    def test_negative_interval_raises(self):
        with pytest.raises(ValueError, match="self_improve_interval"):
            AgentBehavior(self_improve_interval=-1)

    def test_to_dict_includes_interval(self):
        behavior = AgentBehavior(self_improve_interval=5)
        d = behavior.to_dict()
        assert d["self_improve_interval"] == 5

    def test_from_dict_with_interval(self):
        behavior = AgentBehavior.from_dict({"self_improve_interval": 7})
        assert behavior.self_improve_interval == 7


class TestBehaviorRendererSelfImprove:
    def test_render_disabled_when_zero(self):
        behavior = AgentBehavior(self_improve_interval=0)
        renderer = BehaviorRenderer()
        result = renderer.render(behavior)
        assert "Self-Improvement" not in result

    def test_render_enabled_shows_interval(self):
        behavior = AgentBehavior(self_improve_interval=10)
        renderer = BehaviorRenderer()
        result = renderer.render(behavior)
        assert "Self-Improvement Cycle" in result
        assert "10 ticks" in result

    def test_render_as_prompt_includes_self_improve(self):
        behavior = AgentBehavior(self_improve_interval=5)
        renderer = BehaviorRenderer()
        result = renderer.render_as_prompt(behavior, "build", "fix bugs")
        assert "Self-Improvement Cycle" in result
        assert "5 ticks" in result


class TestEventLoopSelfImprovePhase:
    @pytest.mark.asyncio
    async def test_phase_skipped_when_interval_zero(self):
        loop = EventLoop(self_improve_interval=0)
        loop._total_ticks = 5
        await loop._phase_self_improve()
        assert loop._tick_metrics.get("self_improve_gaps") is None

    @pytest.mark.asyncio
    async def test_phase_skipped_when_not_multiple(self):
        loop = EventLoop(self_improve_interval=10)
        loop._total_ticks = 5
        await loop._phase_self_improve()
        assert loop._tick_metrics.get("self_improve_gaps") is None

    @pytest.mark.asyncio
    async def test_phase_runs_on_interval(self):
        loop = EventLoop(self_improve_interval=10, daemon_state={})
        loop._total_ticks = 20
        from general_ludd.self_improve.harness import SelfImprovementHarness
        with patch.object(SelfImprovementHarness, "run_gap_analysis", return_value=[
            {"type": "dead_code", "file": "src/mod.py", "severity": "medium",
             "message": "unused"},
        ]), patch.object(SelfImprovementHarness, "generate_fix_todos", return_value=[
            {"title": "Wire X", "work_type": "code", "priority": "medium"},
        ]), patch.object(SelfImprovementHarness, "enqueue_todos", return_value=1):
            await loop._phase_self_improve()
            assert loop._tick_metrics["self_improve_gaps"] == 1

    @pytest.mark.asyncio
    async def test_phase_sets_high_priority_and_self_improve_type(self):
        loop = EventLoop(self_improve_interval=1, daemon_state={})
        loop._total_ticks = 1
        from general_ludd.self_improve.harness import SelfImprovementHarness
        captured_todos: list = []
        with patch.object(SelfImprovementHarness, "run_gap_analysis", return_value=[
            {"type": "missing_tests", "file": "a.py", "severity": "high",
             "message": "no tests"},
        ]), patch.object(SelfImprovementHarness, "generate_fix_todos", return_value=[
            {"title": "Add tests", "work_type": "test", "priority": "high"},
        ]):
            def capture_enqueue(todos):
                captured_todos.extend(todos)
                return len(todos)
            with patch.object(SelfImprovementHarness, "enqueue_todos", side_effect=capture_enqueue):
                await loop._phase_self_improve()
                assert len(captured_todos) == 1
                assert captured_todos[0]["priority"] == "high"
                assert captured_todos[0]["work_type"] == "self_improve"

    @pytest.mark.asyncio
    async def test_phase_no_findings_no_crash(self):
        loop = EventLoop(self_improve_interval=1, daemon_state={})
        loop._total_ticks = 1
        from general_ludd.self_improve.harness import SelfImprovementHarness
        with patch.object(SelfImprovementHarness, "run_gap_analysis", return_value=[]):
            await loop._phase_self_improve()
            assert loop._tick_metrics["self_improve_gaps"] == 0
