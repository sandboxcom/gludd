"""Tests for model health daemon endpoint and router health-aware routing."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutEvent,
    TimeoutKind,
)


class TestDaemonModelHealthEndpoint:
    def test_get_models_health_empty(self) -> None:
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        client = TestClient(app)
        resp = client.get("/admin/models/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "health" in data

    def test_get_models_health_with_profiles(self) -> None:
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        client = TestClient(app)
        client.post("/admin/models", json={
            "model_id": "test-health-1",
            "provider": "openai",
            "model": "gpt-4",
            "enabled": True,
        })
        resp = client.get("/admin/models/health")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["health"]) >= 1
        health = data["health"][0]
        assert health["model_id"] == "test-health-1"
        assert health["healthy"] is True

    def test_get_models_health_unhealthy_model(self) -> None:
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        client = TestClient(app)
        client.post("/admin/models", json={
            "model_id": "sick-model",
            "provider": "openai",
            "model": "gpt-4",
            "enabled": True,
        })
        tracker = app.state._health_tracker
        for _ in range(3):
            tracker.record_event(TimeoutEvent(
                model_id="sick-model",
                kind=TimeoutKind.READ_TIMEOUT,
                timestamp=time.monotonic(),
                duration_s=30.0,
            ))
        resp = client.get("/admin/models/health")
        assert resp.status_code == 200
        data = resp.json()
        sick = next(h for h in data["health"] if h["model_id"] == "sick-model")
        assert sick["healthy"] is False
        assert sick["consecutive_failures"] == 3


class TestRouterHealthAwareRouting:
    @pytest.mark.asyncio
    async def test_unhealthy_model_skipped_in_routing(self) -> None:
        from general_ludd.schemas.benchmark import TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        tracker = ModelHealthTracker(failure_threshold=2)
        for _ in range(3):
            tracker.record_event(TimeoutEvent(
                model_id="bad-model",
                kind=TimeoutKind.PROVIDER_ERROR,
                timestamp=time.monotonic(),
                duration_s=5.0,
            ))

        repo = MagicMock()

        async def _return_rows(**kwargs: object) -> list[dict]:
            return [
                {
                    "model_profile_id": "bad-model",
                    "prompt_profile_id": "prompt-1",
                    "composite_score": 0.95,
                    "avg_cost": 0.01,
                    "sample_count": 5,
                    "task_type": "bug_fix",
                },
                {
                    "model_profile_id": "good-model",
                    "prompt_profile_id": "prompt-1",
                    "composite_score": 0.85,
                    "avg_cost": 0.005,
                    "sample_count": 5,
                    "task_type": "bug_fix",
                },
            ]

        repo.get_aggregate_scores = _return_rows

        router = AdaptiveRouter(benchmark_repo=repo, health_tracker=tracker)
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.selected_model_profile_id == "good-model"

    @pytest.mark.asyncio
    async def test_all_unhealthy_returns_fallback(self) -> None:
        from general_ludd.schemas.benchmark import TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        tracker = ModelHealthTracker(failure_threshold=2)
        for model in ("m1", "m2"):
            for _ in range(3):
                tracker.record_event(TimeoutEvent(
                    model_id=model,
                    kind=TimeoutKind.READ_TIMEOUT,
                    timestamp=time.monotonic(),
                    duration_s=30.0,
                ))

        async def _return_rows(**kwargs: object) -> list[dict]:
            return [
                {
                    "model_profile_id": "m1",
                    "prompt_profile_id": "p1",
                    "composite_score": 0.95,
                    "avg_cost": 0.01,
                    "sample_count": 5,
                    "task_type": "bug_fix",
                },
                {
                    "model_profile_id": "m2",
                    "prompt_profile_id": "p1",
                    "composite_score": 0.85,
                    "avg_cost": 0.005,
                    "sample_count": 5,
                    "task_type": "bug_fix",
                },
            ]

        repo = MagicMock()
        repo.get_aggregate_scores = _return_rows

        router = AdaptiveRouter(benchmark_repo=repo, health_tracker=tracker)
        decision = await router.route(TaskType.BUG_FIX)
        assert decision.fallback is True

    @pytest.mark.asyncio
    async def test_cheapest_skips_unhealthy(self) -> None:
        from general_ludd.schemas.benchmark import TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        tracker = ModelHealthTracker(failure_threshold=2)
        for _ in range(3):
            tracker.record_event(TimeoutEvent(
                model_id="cheap-but-dead",
                kind=TimeoutKind.CONNECTION_TIMEOUT,
                timestamp=time.monotonic(),
                duration_s=10.0,
            ))

        async def _return_rows(**kwargs: object) -> list[dict]:
            return [
                {
                    "model_profile_id": "expensive-ok",
                    "prompt_profile_id": "p1",
                    "composite_score": 0.9,
                    "avg_cost": 0.08,
                    "sample_count": 5,
                    "task_type": "bug_fix",
                },
                {
                    "model_profile_id": "cheap-but-dead",
                    "prompt_profile_id": "p1",
                    "composite_score": 0.88,
                    "avg_cost": 0.02,
                    "sample_count": 5,
                    "task_type": "bug_fix",
                },
            ]

        repo = MagicMock()
        repo.get_aggregate_scores = _return_rows

        router = AdaptiveRouter(benchmark_repo=repo, health_tracker=tracker)
        decision = await router.route(TaskType.BUG_FIX, max_cost_usd=0.05)
        assert decision.selected_model_profile_id == "expensive-ok"
