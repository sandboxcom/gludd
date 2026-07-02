"""Tests for the self-improve config-tier slice.

Covers: playbook key fix, gate auto-promote flag, harness model-gateway hook,
dead-code heuristic disabled, atomic config write+reload.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from general_ludd.event_loop.loop import _WORK_TYPE_PLAYBOOK_MAP
from general_ludd.reload.hot_reloader import ReloadScope
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.gate import SelfImproveGate
from general_ludd.self_improve.harness import SelfImprovementHarness

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeModelResponse:
    content: str


class _FakeGateway:
    """Fake model gateway that returns a canned JSON response."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[str] = []

    def complete(self, prompt: str) -> _FakeModelResponse:
        self.calls.append(prompt)
        return _FakeModelResponse(content=self._response_text)


class _ErrorGateway:
    """Fake gateway that always raises on complete()."""

    def complete(self, prompt: str) -> _FakeModelResponse:
        raise RuntimeError("gateway exploded")


class _RealShapeGateway:
    """Fake matching the real ModelGateway.call_model(profile_id, messages) shape."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def call_model(
        self, profile_id: str, messages: list[dict[str, str]], **kwargs: Any
    ) -> _FakeModelResponse:
        self.calls.append((profile_id, messages))
        return _FakeModelResponse(content=self._response_text)


class _FakeReloader:
    """Records reload() calls."""

    def __init__(self) -> None:
        self.calls: list[ReloadScope] = []

    def reload(self, scope: ReloadScope) -> None:
        self.calls.append(scope)


def _make_tmp_repo(tmp_path: Any) -> str:
    """Create minimal src/general_ludd + tests dirs so the harness walks them."""
    src = tmp_path / "src" / "general_ludd"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "dummy_module.py").write_text("class Dummy:\n    pass\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    return str(tmp_path)


# ---------------------------------------------------------------------------
# 1. Playbook key
# ---------------------------------------------------------------------------


class TestPlaybookKey:
    def test_self_improve_key_present(self) -> None:
        assert "self_improve" in _WORK_TYPE_PLAYBOOK_MAP
        assert _WORK_TYPE_PLAYBOOK_MAP["self_improve"] == "self_improve_harness.yml"

    def test_self_improvement_key_absent(self) -> None:
        assert "self_improvement" not in _WORK_TYPE_PLAYBOOK_MAP


# ---------------------------------------------------------------------------
# 2-5. Gate
# ---------------------------------------------------------------------------


class TestSelfImproveGate:
    def test_default_parks_for_approval(self) -> None:
        # Security: auto_queue must default to False so self-authored
        # code/test todos land in APPROVAL_REQUIRED behind a human review gate
        # rather than QUEUED (immediate execution). Auto-queueing self-authored
        # work with no human review is a self-modification approval bypass.
        # The release path (SelfImproveApprovalManager, wired to the
        # `gludd self-improve approve/reject` CLI + daemon
        # /self-improve/approvals routes) promotes APPROVAL_REQUIRED -> QUEUED,
        # so nothing strands.
        decision = SelfImproveGate().evaluate({}, open_count=0)
        assert decision.initial_status == TodoStatus.APPROVAL_REQUIRED.value
        assert decision.admitted is True

    def test_auto_queue(self) -> None:
        # Config opt-in: self_improve.auto_queue: true still yields QUEUED for
        # deployments where self-modification without review is acceptable.
        decision = SelfImproveGate(auto_queue=True).evaluate({}, open_count=0)
        assert decision.initial_status == TodoStatus.QUEUED.value

    def test_auto_promote_off_keeps_approval(self) -> None:
        # allow_auto_promote is only meaningful when auto_queue=False (the
        # promote branch runs inside the APPROVAL_REQUIRED path), so set the
        # precondition explicitly rather than relying on a default.
        decision = SelfImproveGate(auto_queue=False, allow_auto_promote=False).evaluate({}, open_count=0)
        assert decision.initial_status == TodoStatus.APPROVAL_REQUIRED.value

    def test_auto_promote_on_promotes_to_queued(self) -> None:
        decision = SelfImproveGate(auto_queue=False, allow_auto_promote=True).evaluate({}, open_count=0)
        assert decision.initial_status == TodoStatus.QUEUED.value

    def test_full_capacity_rejects(self) -> None:
        decision = SelfImproveGate(max_open=2).evaluate({}, open_count=2)
        assert decision.admitted is False


# ---------------------------------------------------------------------------
# 6-11. Harness — model gateway
# ---------------------------------------------------------------------------


class TestHarnessNoGateway:
    def test_static_analysis_returns_list(self, tmp_path: Any) -> None:
        repo_root = _make_tmp_repo(tmp_path)
        harness = SelfImprovementHarness(repo_root=repo_root)
        result = harness.run_gap_analysis()
        assert isinstance(result, list)


class TestHarnessModelGateway:
    _VALID_JSON = '[{"title": "t", "description": "d", "priority": "high", "tier": "test"}]'

    def test_gateway_called_and_parsed(self, tmp_path: Any) -> None:
        repo_root = _make_tmp_repo(tmp_path)
        gw = _FakeGateway(self._VALID_JSON)
        harness = SelfImprovementHarness(repo_root=repo_root, model_gateway=gw)
        result = harness.run_gap_analysis()
        assert len(gw.calls) == 1
        assert result == [{"title": "t", "description": "d", "priority": "high", "tier": "test"}]

    def test_real_call_model_shape_used(self, tmp_path: Any) -> None:
        """A gateway exposing call_model (the real ModelGateway shape) is used."""
        repo_root = _make_tmp_repo(tmp_path)
        gw = _RealShapeGateway(self._VALID_JSON)
        harness = SelfImprovementHarness(
            repo_root=repo_root, model_gateway=gw, model_profile_id="analysis"
        )
        result = harness.run_gap_analysis()
        assert len(gw.calls) == 1
        profile_id, messages = gw.calls[0]
        assert profile_id == "analysis"
        assert messages[0]["role"] == "user"
        assert result == [{"title": "t", "description": "d", "priority": "high", "tier": "test"}]

    def test_fence_tolerant(self, tmp_path: Any) -> None:
        repo_root = _make_tmp_repo(tmp_path)
        fenced = f"```json\n{self._VALID_JSON}\n```"
        gw = _FakeGateway(fenced)
        harness = SelfImprovementHarness(repo_root=repo_root, model_gateway=gw)
        result = harness.run_gap_analysis()
        assert result == [{"title": "t", "description": "d", "priority": "high", "tier": "test"}]

    def test_fallback_on_error(self, tmp_path: Any) -> None:
        repo_root = _make_tmp_repo(tmp_path)
        harness = SelfImprovementHarness(repo_root=repo_root, model_gateway=_ErrorGateway())
        result = harness.run_gap_analysis()
        # Must fall back to static list, not raise
        assert isinstance(result, list)

    def test_fallback_on_bad_json(self, tmp_path: Any) -> None:
        repo_root = _make_tmp_repo(tmp_path)
        gw = _FakeGateway("not json{{{")
        harness = SelfImprovementHarness(repo_root=repo_root, model_gateway=gw)
        result = harness.run_gap_analysis()
        assert isinstance(result, list)

    def test_fallback_on_non_list_json(self, tmp_path: Any) -> None:
        repo_root = _make_tmp_repo(tmp_path)
        gw = _FakeGateway('{"key": "value"}')
        harness = SelfImprovementHarness(repo_root=repo_root, model_gateway=gw)
        result = harness.run_gap_analysis()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 12. Dead-code heuristic disabled
# ---------------------------------------------------------------------------


class TestDeadCodeDisabled:
    def test_no_dead_code_findings(self, tmp_path: Any) -> None:
        """_check_completion_audit must not append any dead_code findings."""
        repo_root = _make_tmp_repo(tmp_path)
        harness = SelfImprovementHarness(repo_root=repo_root)
        result = harness.run_gap_analysis()
        dead_code = [f for f in result if f.get("type") == "dead_code"]
        assert dead_code == [], f"Expected no dead_code findings, got: {dead_code}"


# ---------------------------------------------------------------------------
# 13-14. write_config_value
# ---------------------------------------------------------------------------


class TestWriteConfigValue:
    def test_e2e_atomic_write_and_reload(self, tmp_path: Any) -> None:
        cfg = tmp_path / "self_improve.yml"
        cfg.write_text(yaml.safe_dump({"self_improve": {"max_open": 5, "other": "keep"}}))

        reloader = _FakeReloader()
        harness = SelfImprovementHarness()
        content = harness.write_config_value(
            str(cfg), "self_improve.max_open", 9, reloader=reloader
        )

        # File updated
        data = yaml.safe_load(cfg.read_text())
        assert data["self_improve"]["max_open"] == 9
        assert data["self_improve"]["other"] == "keep"

        # Reloader called once with CONFIG scope
        assert len(reloader.calls) == 1
        assert reloader.calls[0] == ReloadScope.CONFIG

        # Return value is the new YAML content string
        assert isinstance(content, str)
        assert "max_open: 9" in content

    def test_reloader_optional(self, tmp_path: Any) -> None:
        cfg = tmp_path / "cfg.yml"
        cfg.write_text(yaml.safe_dump({"x": 1}))

        harness = SelfImprovementHarness()
        harness.write_config_value(str(cfg), "x", 42)  # reloader=None

        data = yaml.safe_load(cfg.read_text())
        assert data["x"] == 42


# ---------------------------------------------------------------------------
# 15. EventLoop -> harness gateway wiring (Phase 1)
# ---------------------------------------------------------------------------


class TestEventLoopGatewayWiring:
    """Phase 1: _phase_self_improve must forward EventLoop._model_gateway
    into the SelfImprovementHarness constructor so the model-driven gap
    analysis activates instead of always falling back to static analysis."""

    async def test_gateway_forwarded_to_harness(self) -> None:
        from unittest.mock import patch

        from general_ludd.event_loop.loop import EventLoop

        sentinel_gateway = object()
        loop = EventLoop(
            self_improve_interval=1,
            daemon_state={},
            model_gateway=sentinel_gateway,
        )
        loop._total_ticks = 1

        with patch(
            "general_ludd.event_loop.loop.SelfImprovementHarness"
        ) as MockHarness:
            instance = MockHarness.return_value
            instance.run_gap_analysis.return_value = []
            await loop._phase_self_improve()

        # The harness now ALSO receives repo_root (resolved from the tick's
        # project) so gap analysis targets the external project checkout rather
        # than gludd's own repo; the gateway must still be forwarded.
        MockHarness.assert_called_once()
        assert MockHarness.call_args.kwargs["model_gateway"] is sentinel_gateway
        assert "repo_root" in MockHarness.call_args.kwargs

    async def test_no_gateway_passes_none_to_harness(self) -> None:
        from unittest.mock import patch

        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(self_improve_interval=1, daemon_state={})
        assert loop._model_gateway is None
        loop._total_ticks = 1

        with patch(
            "general_ludd.event_loop.loop.SelfImprovementHarness"
        ) as MockHarness:
            instance = MockHarness.return_value
            instance.run_gap_analysis.return_value = []
            await loop._phase_self_improve()

        MockHarness.assert_called_once()
        assert MockHarness.call_args.kwargs["model_gateway"] is None
        assert "repo_root" in MockHarness.call_args.kwargs
