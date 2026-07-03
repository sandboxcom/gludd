"""Tests for ToolCallAuditor — useless tool call detection."""
from general_ludd.execution.tool_auditor import (
    ErrorLoopDetector,
    IrrelevanceDetector,
    RedundancyDetector,
    ToolCallAuditor,
)


class TestRedundancyDetector:
    """Detects repeated calls to the same tool with the same args."""

    def test_first_call_allowed(self):
        det = RedundancyDetector(max_repeats=2)
        verdict = det.check("read_file", {"path": "/foo"})
        assert verdict.allowed is True
        assert verdict.reason == ""

    def test_second_call_allowed_within_limit(self):
        det = RedundancyDetector(max_repeats=2)
        det.check("read_file", {"path": "/foo"})
        verdict = det.check("read_file", {"path": "/foo"})
        assert verdict.allowed is True

    def test_third_call_blocked_exceeds_limit(self):
        det = RedundancyDetector(max_repeats=2)
        det.check("read_file", {"path": "/foo"})
        det.check("read_file", {"path": "/foo"})
        verdict = det.check("read_file", {"path": "/foo"})
        assert verdict.allowed is False
        assert "repeated" in verdict.reason.lower()
        assert verdict.classification == "redundant"

    def test_different_args_not_redundant(self):
        det = RedundancyDetector(max_repeats=2)
        det.check("read_file", {"path": "/foo"})
        det.check("read_file", {"path": "/bar"})
        verdict = det.check("read_file", {"path": "/foo"})  # same as first, but not consecutive
        assert verdict.allowed is True  # only consecutive repeats count

    def test_different_tool_not_redundant(self):
        det = RedundancyDetector(max_repeats=2)
        det.check("read_file", {"path": "/foo"})
        det.check("write_file", {"path": "/foo"})
        verdict = det.check("read_file", {"path": "/foo"})
        assert verdict.allowed is True

    def test_reset_clears_history(self):
        det = RedundancyDetector(max_repeats=2)
        det.check("read_file", {"path": "/foo"})
        det.check("read_file", {"path": "/foo"})
        det.reset()
        verdict = det.check("read_file", {"path": "/foo"})
        assert verdict.allowed is True


class TestErrorLoopDetector:
    """Detects when the model retries a tool after it already errored."""

    def test_first_call_allowed(self):
        det = ErrorLoopDetector(max_error_retries=2)
        verdict = det.check("read_file", {"path": "/nonexistent"})
        assert verdict.allowed is True

    def test_record_error_then_first_retry_allowed(self):
        det = ErrorLoopDetector(max_error_retries=2)
        det.check("read_file", {"path": "/nonexistent"})
        det.record_error("read_file", {"path": "/nonexistent"}, "File not found")
        verdict = det.check("read_file", {"path": "/nonexistent"})
        assert verdict.allowed is True  # first retry allowed

    def test_record_error_then_third_retry_blocked(self):
        det = ErrorLoopDetector(max_error_retries=2)
        for _ in range(2):
            det.check("read_file", {"path": "/nonexistent"})
            det.record_error("read_file", {"path": "/nonexistent"}, "File not found")
        verdict = det.check("read_file", {"path": "/nonexistent"})
        assert verdict.allowed is False
        assert verdict.classification == "error_loop"
        assert "error" in verdict.reason.lower()

    def test_successful_call_resets_error_count(self):
        det = ErrorLoopDetector(max_error_retries=2)
        det.check("read_file", {"path": "/nonexistent"})
        det.record_error("read_file", {"path": "/nonexistent"}, "File not found")
        det.record_success("read_file", {"path": "/foo"})  # successful call
        verdict = det.check("read_file", {"path": "/nonexistent"})
        assert verdict.allowed is True  # counter reset


class TestIrrelevanceDetector:
    """Detects tool calls that don't match the task context."""

    def test_allowed_when_no_keywords_configured(self):
        det = IrrelevanceDetector(relevant_keywords=None)
        verdict = det.check("unknown_tool", {}, "write code")
        assert verdict.allowed is True  # no keywords = allow all

    def test_allowed_when_tool_matches_context(self):
        det = IrrelevanceDetector(
            relevant_keywords={"read_file": ["file", "read", "code", "source"]}
        )
        verdict = det.check("read_file", {"path": "foo.py"}, "read the source code")
        assert verdict.allowed is True

    def test_blocked_when_tool_irrelevant_to_context(self):
        det = IrrelevanceDetector(
            relevant_keywords={"read_file": ["file", "read", "code", "source"]}
        )
        verdict = det.check("read_file", {"path": "foo.py"}, "deploy to kubernetes cluster")
        assert verdict.allowed is False
        assert verdict.classification == "irrelevant"

    def test_tool_not_in_keyword_map_allowed(self):
        det = IrrelevanceDetector(
            relevant_keywords={"read_file": ["file", "read"]}
        )
        verdict = det.check("write_file", {"path": "foo.py"}, "deploy to k8s")
        assert verdict.allowed is True  # not in map = no restriction

    def test_empty_context_allowed(self):
        det = IrrelevanceDetector(
            relevant_keywords={"read_file": ["file", "read"]}
        )
        verdict = det.check("read_file", {"path": "foo.py"}, "")
        assert verdict.allowed is True  # empty context = allow


class TestToolCallAuditor:
    """Integration: auditor combines all detectors."""

    def test_all_detectors_pass_call_allowed(self):
        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(max_repeats=3),
            error_loop_detector=ErrorLoopDetector(max_error_retries=3),
            irrelevance_detector=IrrelevanceDetector(),
        )
        verdict = auditor.audit("read_file", {"path": "/foo"}, task_context="read source")
        assert verdict.allowed is True

    def test_first_block_wins_redundancy_before_irrelevance(self):
        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(max_repeats=1),
            error_loop_detector=ErrorLoopDetector(),
            irrelevance_detector=IrrelevanceDetector(
                relevant_keywords={"read_file": ["deploy"]}
            ),
        )
        # First call
        auditor.audit("read_file", {"path": "/foo"}, task_context="read source")
        # Second call — redundant (hits max_repeats=1), should block before irrelevance check
        verdict = auditor.audit("read_file", {"path": "/foo"}, task_context="read source")
        assert verdict.allowed is False
        assert verdict.classification == "redundant"  # redundancy wins

    def test_record_call_error_flow(self):
        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(),
            error_loop_detector=ErrorLoopDetector(max_error_retries=1),
        )
        auditor.audit("read_file", {"path": "/nonexistent"})
        auditor.record_error("read_file", {"path": "/nonexistent"}, "not found")
        verdict = auditor.audit("read_file", {"path": "/nonexistent"})
        assert verdict.allowed is False
        assert verdict.classification == "error_loop"

    def test_record_call_success_flow(self):
        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(),
            error_loop_detector=ErrorLoopDetector(max_error_retries=1),
        )
        auditor.audit("read_file", {"path": "/nonexistent"})
        auditor.record_success("read_file", {"path": "/nonexistent"}, "content here")
        # Recorded in history
        assert len(auditor.call_history) == 1

    def test_call_history_capped(self):
        auditor = ToolCallAuditor(max_history=5)
        for i in range(10):
            auditor.audit(f"tool_{i}", {"n": i})
        assert len(auditor.call_history) == 5
        # Should have the most recent 5
        assert auditor.call_history[0]["tool_name"] == "tool_5"
        assert auditor.call_history[-1]["tool_name"] == "tool_9"

    def test_create_situation_on_blocked_call(self):
        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(max_repeats=1),
        )
        auditor.audit("read_file", {"path": "/foo"}, task_context="read code")
        situation = auditor.audit(
            "read_file", {"path": "/foo"}, task_context="read code",
            capture_situation=True,
        )
        # Should create a situation
        assert situation is not None
        assert situation.tool_name == "read_file"
        assert situation.classification == "redundant"
        assert situation.task_excerpt == "read code"
        assert len(situation.recent_calls) > 0
