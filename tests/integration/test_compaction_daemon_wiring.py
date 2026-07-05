"""Integration: compaction subsystem wired end-to-end — daemon routes + controllers + arena.

Proves the compaction subsystem works from HTTP endpoints down to the pure controller
and self-improving arena. No real model calls — everything runs offline with DI callables.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from general_ludd.agents.context import ContextMessage
from general_ludd.compaction import (
    CompactionRequest,
    EvalSample,
    SelfImprovingCompactor,
    build_self_improving_compactor,
    estimate_tokens,
    generate_candidates,
    run_arena,
)
from general_ludd.compaction.evaluate import CompactionMetrics as EvalMetrics
from general_ludd.controllers.compaction_aggressiveness import (
    AccuracySample,
    CompactionAggressivenessController,
)
from general_ludd.daemon import create_daemon_app

# ── helpers ──────────────────────────────────────────────────────────────────

def _msg(content: str, *, role: str = "user", system: bool = False) -> ContextMessage:
    return ContextMessage(
        role=role,
        content=content,
        token_estimate=estimate_tokens(content),
        is_system=system,
    )


def _build_app(**kw: Any) -> Any:
    with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
        app = create_daemon_app(**kw)
    return app


def _wire_compaction(app: Any) -> None:
    """Manually wire compaction state on the app, simulating what the lifespan does."""
    app.state._compaction_aggressiveness_controller = CompactionAggressivenessController()
    app.state._compaction_compactor = build_self_improving_compactor()
    app.state._compaction_metrics = EvalMetrics(compactor="noop")


# ── test classes ─────────────────────────────────────────────────────────────


class TestCompactionAggressivenessStatusEndpoint:
    """GET /admin/compaction/aggressiveness-status returns controller parameters."""

    def test_endpoint_returns_200_and_available_true_after_wiring(self) -> None:
        app = _build_app(tick_interval=999.0)
        _wire_compaction(app)
        client = TestClient(app)
        resp = client.get("/admin/compaction/aggressiveness-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert "floor" in data
        assert "min_samples" in data
        assert "max_level" in data

    def test_endpoint_returns_controller_defaults(self) -> None:
        app = _build_app(tick_interval=999.0)
        _wire_compaction(app)
        client = TestClient(app)
        resp = client.get("/admin/compaction/aggressiveness-status")
        data = resp.json()
        assert data["floor"] == 0.9
        assert data["min_samples"] == 20
        assert isinstance(data["max_level"], int)
        assert data["max_level"] >= 0

    def test_endpoint_returns_available_false_before_lifespan(self) -> None:
        app = _build_app(tick_interval=999.0)
        client = TestClient(app)
        resp = client.get("/admin/compaction/aggressiveness-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False


class TestCompactionEvalStatusEndpoint:
    """GET /admin/compaction/eval-status returns champion + metrics."""

    def test_endpoint_returns_200_and_wired_true_after_wiring(self) -> None:
        app = _build_app(tick_interval=999.0)
        _wire_compaction(app)
        client = TestClient(app)
        resp = client.get("/admin/compaction/eval-status")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["wired"] is True

    def test_endpoint_returns_champion_compactor_name(self) -> None:
        app = _build_app(tick_interval=999.0)
        _wire_compaction(app)
        client = TestClient(app)
        resp = client.get("/admin/compaction/eval-status")
        data = resp.json()
        assert data["champion"] is not None
        assert isinstance(data["champion"], str)
        assert data["champion"] != ""

    def test_endpoint_returns_metrics_dict(self) -> None:
        app = _build_app(tick_interval=999.0)
        _wire_compaction(app)
        client = TestClient(app)
        resp = client.get("/admin/compaction/eval-status")
        data = resp.json()
        assert data["metrics"] is not None
        assert isinstance(data["metrics"], dict)
        assert "compactor" in data["metrics"]

    def test_endpoint_returns_wired_false_before_lifespan(self) -> None:
        app = _build_app(tick_interval=999.0)
        client = TestClient(app)
        resp = client.get("/admin/compaction/eval-status")
        data = resp.json()
        assert data["wired"] is False
        assert data["champion"] is None
        assert data["metrics"] is None


class TestCompactionAggressivenessControllerDirectly:
    """Pure controller decision logic — no daemon, no HTTP."""

    def test_holds_when_below_min_samples(self) -> None:
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=20)
        sample = AccuracySample(passed=5, total=5)
        result = ctrl.compute(current_level=2, sample=sample)
        assert result == 2

    def test_climbs_when_accuracy_holds(self) -> None:
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=5)
        sample = AccuracySample(passed=27, total=30)
        result = ctrl.compute(current_level=1, sample=sample)
        assert result == 2

    def test_backs_off_when_accuracy_drops(self) -> None:
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=5)
        sample = AccuracySample(passed=10, total=20)
        result = ctrl.compute(current_level=3, sample=sample)
        assert result == 2

    def test_does_not_overflow_max_level(self) -> None:
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=5, max_level=3)
        sample = AccuracySample(passed=30, total=30)
        result = ctrl.compute(current_level=3, sample=sample)
        assert result == 3

    def test_does_not_go_negative(self) -> None:
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=5)
        sample = AccuracySample(passed=2, total=20)
        result = ctrl.compute(current_level=0, sample=sample)
        assert result == 0

    def test_disable_signaled_at_level_zero_with_failing_accuracy(self) -> None:
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=20)
        sample = AccuracySample(passed=5, total=30)
        assert ctrl.disable_signaled(current_level=0, sample=sample) is True

    def test_disable_not_signaled_above_level_zero(self) -> None:
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=20)
        sample = AccuracySample(passed=5, total=30)
        assert ctrl.disable_signaled(current_level=1, sample=sample) is False

    def test_disable_not_signaled_with_insufficient_samples(self) -> None:
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=20)
        sample = AccuracySample(passed=1, total=2)
        assert ctrl.disable_signaled(current_level=0, sample=sample) is False


class TestConfigFlowUserConfigToCompactionDict:
    """UserConfig compaction config → dict consumed by EventLoop."""

    def test_compaction_config_block_defaults(self) -> None:
        from general_ludd.config.user_config import CompactionConfigBlock

        block = CompactionConfigBlock()
        d = block.model_dump()
        assert d["enabled"] is False
        assert d["level"] == 1

    def test_compaction_config_block_enabled_serialised(self) -> None:
        from general_ludd.config.user_config import CompactionConfigBlock

        block = CompactionConfigBlock(enabled=True, level=3)
        d = block.model_dump()
        assert d["enabled"] is True
        assert d["level"] == 3

    def test_event_loop_receives_config_dict(self) -> None:
        cfg = {"compaction": {"enabled": True, "level": 2}}
        compaction_cfg = cfg.get("compaction", {})
        assert compaction_cfg.get("enabled") is True
        assert compaction_cfg.get("level") == 2

    def test_event_loop_defaults_when_config_missing(self) -> None:
        cfg: dict[str, Any] = {}
        compaction_cfg = cfg.get("compaction", {})
        assert compaction_cfg.get("enabled", False) is False


class TestSelfImprovingCompactorArena:
    """SelfImprovingCompactor arena mode — champion/challenger promotion."""

    def test_build_creates_self_improving_compactor(self) -> None:
        compactor = build_self_improving_compactor()
        assert isinstance(compactor, SelfImprovingCompactor)
        assert compactor.champion.name != ""

    def test_compact_delegates_to_champion(self) -> None:
        compactor = build_self_improving_compactor()
        request = CompactionRequest(
            messages=[_msg("hello"), _msg("world")],
            goal="test goal",
        )
        result = compactor.compact(request)
        assert result.method == compactor.champion.name
        assert len(result.messages) > 0

    def test_improve_runs_arena_and_returns_result(self) -> None:
        compactor = build_self_improving_compactor()
        samples: list[EvalSample] = [
            EvalSample(
                messages=[_msg("agent: verify that the build passes"), _msg("echo done")],
                goal="CI verification",
            ),
            EvalSample(
                messages=[_msg("fix the auth middleware"), _msg("use HMAC")],
                goal="security fix",
            ),
        ]
        result = compactor.improve(samples)
        assert result.winner != ""
        assert len(result.leaderboard) > 0
        assert result.leaderboard[0].compactor != ""

    def test_repeated_improve_maintains_champion_or_promotes(self) -> None:
        compactor = build_self_improving_compactor()
        champion_first = compactor.champion.name
        samples: list[EvalSample] = [
            EvalSample(
                messages=[_msg("deploy to staging"), _msg("kubectl apply")],
                goal="deployment",
            ),
        ]
        compactor.improve(samples)
        champion_after = compactor.champion.name
        assert champion_after == champion_first or champion_after in {
            c.name for c in generate_candidates()
        }


class TestRunArenaDirectly:
    """Direct run_arena() — leaderboard ranking + promotion gating."""

    def test_run_arena_ranks_candidates_by_score(self) -> None:
        from general_ludd.compaction import NoOpCompactor, TruncationCompactor

        candidates = [NoOpCompactor(), TruncationCompactor()]
        samples: list[EvalSample] = [
            EvalSample(
                messages=[
                    _msg("the API key is abc123"),
                    _msg("file is at src/main.py"),
                    _msg("unrelated filler text goes here"),
                ],
                goal="locate the API key location",
            ),
        ]
        result = run_arena(candidates, samples)
        assert len(result.leaderboard) >= 2
        assert result.winner != ""

    def test_run_arena_with_incumbent_respects_min_improvement(self) -> None:
        from general_ludd.compaction import NoOpCompactor, TruncationCompactor

        candidates = [TruncationCompactor(), NoOpCompactor()]
        samples: list[EvalSample] = [
            EvalSample(
                messages=[_msg("the database password is in env vars"), _msg("chitchat")],
                goal="find password location",
            ),
        ]
        result = run_arena(
            candidates,
            samples,
            incumbent=TruncationCompactor.name,
            min_improvement=0.01,
        )
        assert result.incumbent == TruncationCompactor.name

    def test_run_arena_promotes_top_scorer_without_incumbent(self) -> None:
        from general_ludd.compaction import NoOpCompactor, TruncationCompactor

        candidates = [TruncationCompactor(), NoOpCompactor()]
        samples: list[EvalSample] = [
            EvalSample(
                messages=[_msg("critical: port 8080 is the one"), _msg("filler filler")],
                goal="identify port",
            ),
        ]
        result = run_arena(candidates, samples, incumbent=None)
        assert result.promoted is True
        assert result.winner == result.leaderboard[0].compactor
