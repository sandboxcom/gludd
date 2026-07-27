"""Unit tests for abtest.compare — ABVerdict, decide(), and run_ab()."""

from __future__ import annotations

from unittest.mock import Mock, patch

from general_ludd.abtest.compare import ABVerdict, decide, run_ab
from general_ludd.abtest.runner import Result
from general_ludd.abtest.workloads import Workload, import_module_workload


def _mk_result(ok=True, crashed=False, timed_out=False, exit_code=0, duration_s=1.0, signal=None):
    return Result(
        ok=ok,
        crashed=crashed,
        timed_out=timed_out,
        exit_code=exit_code,
        output="",
        duration_s=duration_s,
        signal=signal,
    )


class TestABVerdict:
    def test_to_dict_rounds_duration(self):
        a = _mk_result(duration_s=1.234567)
        b = _mk_result(duration_s=2.345678)
        v = ABVerdict(a=a, b=b, promote=True, reason="ok")
        d = v.to_dict()
        assert d["a"]["duration_s"] == 1.2346
        assert d["b"]["duration_s"] == 2.3457

    def test_to_dict_includes_all_fields(self):
        a = _mk_result()
        b = _mk_result(ok=False, crashed=True, exit_code=-11, signal=11)
        v = ABVerdict(a=a, b=b, promote=False, reason="crash")
        d = v.to_dict()
        assert d["a"]["ok"] is True
        assert d["b"]["ok"] is False
        assert d["b"]["crashed"] is True
        assert d["b"]["exit_code"] == -11
        assert d["b"]["signal"] == 11
        assert d["promote"] is False
        assert d["reason"] == "crash"


class TestDecide:
    def test_baseline_not_ok_blocks_promotion(self):
        a = _mk_result(ok=False)
        b = _mk_result()
        promote, reason = decide(a, b)
        assert promote is False
        assert "baseline (A) did not pass" in reason

    def test_baseline_crashed_blocks_promotion(self):
        a = _mk_result(crashed=True)
        b = _mk_result()
        promote, reason = decide(a, b)
        assert promote is False
        assert "baseline (A) did not pass" in reason

    def test_candidate_crashed_blocks_promotion(self):
        a = _mk_result()
        b = _mk_result(crashed=True)
        promote, reason = decide(a, b)
        assert promote is False
        assert "candidate (B) crashed" in reason

    def test_candidate_timed_out_blocks_promotion(self):
        a = _mk_result()
        b = _mk_result(timed_out=True)
        promote, reason = decide(a, b)
        assert promote is False
        assert "candidate (B) timed out" in reason

    def test_candidate_not_ok_blocks_promotion(self):
        a = _mk_result()
        b = _mk_result(ok=False)
        promote, reason = decide(a, b)
        assert promote is False
        assert "did not report ok" in reason

    def test_candidate_too_slow_blocks_promotion(self):
        a = _mk_result(duration_s=1.0)
        b = _mk_result(duration_s=10.0)
        promote, reason = decide(a, b)
        assert promote is False
        assert "too slow" in reason
        assert "10.000" in reason

    def test_candidate_within_slack_promotes(self):
        a = _mk_result(duration_s=5.0)
        b = _mk_result(duration_s=10.0)
        promote, reason = decide(a, b)
        assert promote is True
        assert "ok and within duration slack" in reason

    def test_floor_slack_used_when_baseline_very_fast(self):
        a = _mk_result(duration_s=0.01)
        b = _mk_result(duration_s=0.4)
        promote, _reason = decide(a, b)
        assert promote is True

    def test_floor_slack_exceeded_blocks_promotion(self):
        a = _mk_result(duration_s=0.01)
        b = _mk_result(duration_s=0.6)
        promote, reason = decide(a, b)
        assert promote is False
        assert "too slow" in reason


class TestRunAB:
    def test_promotes_when_both_pass(self):
        workload: Workload = import_module_workload("os")
        mock_result = _mk_result()
        mock_run = Mock(return_value=mock_result)
        with patch(
            "general_ludd.abtest.compare.run_candidate_in_subprocess",
            new=mock_run,
        ):
            verdict = run_ab("/a/root", "/b/root", workload)
            assert verdict.promote is True
            assert verdict.a is mock_result
            assert verdict.b is mock_result
            assert mock_run.call_count == 2

    def test_rejects_when_b_crashes(self):
        workload: Workload = import_module_workload("sys")
        a_result = _mk_result()
        b_result = _mk_result(crashed=True)
        with patch(
            "general_ludd.abtest.compare.run_candidate_in_subprocess",
            new=Mock(side_effect=[a_result, b_result]),
        ):
            verdict = run_ab("/a", "/b", workload)
            assert verdict.promote is False
            assert "crashed" in verdict.reason

    def test_passes_timeout_and_mem_to_runner(self):
        workload: Workload = import_module_workload("os")
        a_result = _mk_result()
        b_result = _mk_result()
        mock_run = Mock(side_effect=[a_result, b_result])
        with patch(
            "general_ludd.abtest.compare.run_candidate_in_subprocess",
            new=mock_run,
        ):
            run_ab("/a", "/b", workload, timeout=30.0, mem_limit_mb=256)
            for call in mock_run.call_args_list:
                assert call.kwargs["timeout"] == 30.0
                assert call.kwargs["mem_limit_mb"] == 256
