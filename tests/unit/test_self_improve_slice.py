"""Tests for the self-improve config-tier slice.

Covers:
  - playbook key fix (self_improve vs self_improvement)
  - gate allow_auto_promote flag
  - harness model_gateway hook + fallback
  - harness dead_code heuristic disabled
  - _write_config_value scaffold
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


# --------------------------------------------------------------------------- #
# Bug Fix 1: playbook key                                                      #
# --------------------------------------------------------------------------- #
class TestPlaybookKeyFix:
    def test_self_improve_key_present(self):
        from general_ludd.event_loop.loop import _WORK_TYPE_PLAYBOOK_MAP
        assert "self_improve" in _WORK_TYPE_PLAYBOOK_MAP
        assert _WORK_TYPE_PLAYBOOK_MAP["self_improve"] == "self_improve_harness.yml"

    def test_self_improvement_key_absent(self):
        from general_ludd.event_loop.loop import _WORK_TYPE_PLAYBOOK_MAP
        assert "self_improvement" not in _WORK_TYPE_PLAYBOOK_MAP


# --------------------------------------------------------------------------- #
# Bug Fix 3: gate allow_auto_promote                                           #
# --------------------------------------------------------------------------- #
class TestGateAutoPromote:
    def test_no_promote_by_default(self):
        from general_ludd.schemas.todo import TodoStatus
        from general_ludd.self_improve.gate import SelfImproveGate
        gate = SelfImproveGate(max_open=10, auto_queue=False)
        decision = gate.evaluate({}, open_count=0)
        assert decision.admitted is True
        assert decision.initial_status == TodoStatus.APPROVAL_REQUIRED.value

    def test_allow_auto_promote_promotes_to_queued(self):
        from general_ludd.schemas.todo import TodoStatus
        from general_ludd.self_improve.gate import SelfImproveGate
        gate = SelfImproveGate(max_open=10, auto_queue=False, allow_auto_promote=True)
        decision = gate.evaluate({}, open_count=0)
        assert decision.admitted is True
        assert decision.initial_status == TodoStatus.QUEUED.value

    def test_auto_queue_true_gives_queued(self):
        from general_ludd.schemas.todo import TodoStatus
        from general_ludd.self_improve.gate import SelfImproveGate
        gate = SelfImproveGate(max_open=10, auto_queue=True)
        decision = gate.evaluate({}, open_count=0)
        assert decision.initial_status == TodoStatus.QUEUED.value

    def test_not_admitted_when_full(self):
        from general_ludd.self_improve.gate import SelfImproveGate
        gate = SelfImproveGate(max_open=5, allow_auto_promote=True)
        decision = gate.evaluate({}, open_count=5)
        assert decision.admitted is False


# --------------------------------------------------------------------------- #
# Feature: harness model_gateway hook                                          #
# --------------------------------------------------------------------------- #
class TestHarnessModelGateway:
    def _mock_gw(self, content: str) -> MagicMock:
        resp = MagicMock()
        resp.content = content
        gw = MagicMock()
        gw.complete.return_value = resp
        return gw

    def test_no_model_gateway_returns_list(self, tmp_path):
        from general_ludd.self_improve.harness import SelfImprovementHarness
        h = SelfImprovementHarness(repo_root=str(tmp_path))
        assert isinstance(h.run_gap_analysis(), list)

    def test_model_gateway_called_and_json_parsed(self, tmp_path):
        findings = [{"title": "Add tests for foo.py", "description": "no tests", "priority": "high", "tier": "test"}]
        gw = self._mock_gw(json.dumps(findings))
        from general_ludd.self_improve.harness import SelfImprovementHarness
        h = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gw)
        result = h.run_gap_analysis()
        gw.complete.assert_called_once()
        assert any(f.get("title") == "Add tests for foo.py" for f in result)

    def test_model_gateway_json_with_fences_parsed(self, tmp_path):
        content = '```json\n[{"title": "Fence test", "description": "x", "priority": "medium", "tier": "code"}]\n```'
        gw = self._mock_gw(content)
        from general_ludd.self_improve.harness import SelfImprovementHarness
        h = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gw)
        result = h.run_gap_analysis()
        assert any(f.get("title") == "Fence test" for f in result)

    def test_model_gateway_fallback_on_exception(self, tmp_path):
        gw = MagicMock()
        gw.complete.side_effect = RuntimeError("network error")
        from general_ludd.self_improve.harness import SelfImprovementHarness
        h = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gw)
        result = h.run_gap_analysis()
        assert isinstance(result, list)

    def test_model_gateway_fallback_on_bad_json(self, tmp_path):
        gw = self._mock_gw("not json at all")
        from general_ludd.self_improve.harness import SelfImprovementHarness
        h = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gw)
        result = h.run_gap_analysis()
        assert isinstance(result, list)


# --------------------------------------------------------------------------- #
# Feature: dead_code heuristic disabled                                        #
# --------------------------------------------------------------------------- #
class TestDeadCodeHeuristicDisabled:
    def test_completion_audit_produces_no_dead_code_findings(self, tmp_path):
        from general_ludd.self_improve.harness import SelfImprovementHarness
        h = SelfImprovementHarness(repo_root=str(tmp_path))
        findings: list = []
        h._check_completion_audit(findings)
        dead_code = [f for f in findings if f.get("type") == "dead_code"]
        assert dead_code == [], f"dead_code heuristic should be disabled, got: {dead_code}"


# --------------------------------------------------------------------------- #
# Feature: _write_config_value scaffold                                        #
# --------------------------------------------------------------------------- #
class TestWriteConfigScaffold:
    def test_write_config_value_raises_not_implemented(self, tmp_path):
        from general_ludd.self_improve.harness import SelfImprovementHarness
        cfg = tmp_path / "test.yml"
        cfg.write_text("self_improve:\n  max_open: 5\n")
        with pytest.raises(NotImplementedError) as exc_info:
            SelfImprovementHarness._write_config_value(str(cfg), "self_improve.max_open", 10)
        msg = str(exc_info.value)
        assert "SafeWriter" in msg or "HotReloader" in msg
