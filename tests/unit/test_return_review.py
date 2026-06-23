"""Unit tests for return reviewer."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from general_ludd.models.gateway import ModelGateway
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.review.reviewer import ReturnReviewer
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn


def _make_task_return(**overrides: object) -> TaskReturn:
    defaults = {
        "return_id": "RET-001", "todo_id": "TODO-001",
        "job_id": "JOB-001", "playbook": "test_playbook",
        "queue": "core", "result_summary": "All tests passed",
        "exit_code": 0, "artifacts": ["logs.txt"],
    }
    defaults.update(overrides)
    return TaskReturn(**defaults)


def _make_decision(**overrides: object) -> TaskDecision:
    defaults = {
        "return_id": "RET-001", "matched_todo_id": "TODO-001",
        "decision": "complete", "confidence": 0.95,
        "evidence_refs": ["coverage.xml"],
    }
    defaults.update(overrides)
    return TaskDecision(**defaults)


class TestReturnReviewer:
    def test_return_reviewer_renders_prompt_with_context(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)
        task_return = _make_task_return()
        decision_json = _make_decision().model_dump_json()

        render_calls: list[str] = []
        original_render = registry.render

        def capture_render(name: str, **kwargs: object) -> str:
            render_calls.append(name)
            return original_render(name, **kwargs)

        with (
            patch.object(registry, "render", side_effect=capture_render),
            patch.object(reviewer, "_call_model", return_value=(decision_json, None)),
        ):
            reviewer.review_return(task_return, [{"todo_id": "TODO-001", "title": "Fix bug"}], ["coverage.xml"])

        assert "return_review.md.j2" in render_calls

    def test_return_reviewer_calls_model_gateway(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)
        task_return = _make_task_return()
        decision_json = _make_decision().model_dump_json()

        with patch.object(reviewer, "_call_model", return_value=(decision_json, None)) as mock_call:
            result = reviewer.review_return(task_return, [], [])

        mock_call.assert_called_once()
        assert result.decision == "complete"

    def test_return_reviewer_validates_task_decision_schema(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)
        task_return = _make_task_return()
        valid_json = json.dumps({
            "return_id": "RET-001", "matched_todo_id": "TODO-001",
            "decision": "complete", "confidence": 0.95,
        })
        decision = reviewer._parse_model_output(valid_json, task_return)
        assert decision is not None
        assert decision.decision == "complete"

    def test_return_reviewer_handles_invalid_model_output(self):
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)
        task_return = _make_task_return()

        with patch.object(reviewer, "_call_model", return_value=("not valid json", None)):
            result = reviewer.review_return(task_return, [], [])
        assert result.decision == "failed"

    # ------------------------------------------------------------------
    # Fence extractor correctness tests
    # ------------------------------------------------------------------

    def test_extract_json_fenced_with_braces_in_string_and_trailing_prose(self):
        """JSON inside a ```json fence with a } inside a string value and trailing prose.

        This is the exact brittle-parse failure mode: the old brace scanner would
        miscount depth on the embedded } and return a truncated string, causing
        json.loads to raise and the review to collapse to the "failed" fallback.
        """
        raw = (
            "```json\n"
            '{"reason": "loop }{ ok", "status": "complete"}\n'
            "```\n"
            "Some trailing prose the model appended after the fence."
        )
        result = ReturnReviewer._extract_json_from_output(raw)
        parsed = json.loads(result)
        assert parsed["status"] == "complete"
        assert parsed["reason"] == "loop }{ ok"

    def test_extract_json_plain_braces_in_string_value(self):
        """No fence: plain JSON where a string value contains braces."""
        raw = 'Preamble {"key": "val } inner {", "ok": true} trailing text'
        result = ReturnReviewer._extract_json_from_output(raw)
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert "inner {" in parsed["key"]

    def test_extract_json_fenced_no_prose(self):
        """Well-formed fenced JSON still parses correctly (regression guard)."""
        raw = '```json\n{"decision": "complete", "confidence": 0.9}\n```'
        result = ReturnReviewer._extract_json_from_output(raw)
        parsed = json.loads(result)
        assert parsed["decision"] == "complete"

    def test_extract_json_review_return_fenced_braces_in_string(self):
        """End-to-end: review_return parses fenced model output with brace-in-string."""
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)
        task_return = _make_task_return()

        model_output = (
            "```json\n"
            '{"return_id": "RET-001", "matched_todo_id": "TODO-001", '
            '"decision": "complete", "confidence": 0.9, '
            '"audit_notes": ["passed loop }{ ok test"]}\n'
            "```\n"
            "The model added this prose after the fence."
        )
        with (
            patch.object(reviewer, "_call_model", return_value=(model_output, None)),
            patch.object(reviewer, "_audit_evidence", return_value=[]),
        ):
            result = reviewer.review_return(task_return, [], [])

        assert result.decision == "complete", (
            f"Expected 'complete' but got '{result.decision}' — brittle-parse bug still present"
        )
