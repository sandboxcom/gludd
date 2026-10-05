"""Unit tests for return reviewer."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from general_ludd.models.gateway import ModelGateway
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.review.reviewer import ReturnReviewer
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn


def _make_task_return() -> TaskReturn:
    return TaskReturn(
        return_id="RET-001",
        todo_id="TODO-001",
        job_id="JOB-001",
        playbook="test_playbook",
        queue="core",
        result_summary="All tests passed",
        exit_code=0,
        artifacts=["logs.txt"],
    )


def _make_decision() -> TaskDecision:
    return TaskDecision(
        return_id="RET-001",
        matched_todo_id="TODO-001",
        decision="complete",
        confidence=0.95,
        evidence_refs=["coverage.xml"],
    )


class TestReturnReviewer:
    def test_high_confidence_adversarial_finding_blocks_before_model_call(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        registry = MagicMock(spec=PromptRegistry)
        detector = MagicMock()
        detector.scan_task_return.return_value = SimpleNamespace(
            findings=[
                SimpleNamespace(
                    pattern_id="ADV-001",
                    category="prompt_injection",
                    severity="high",
                    description="attempted instruction override",
                    match_text="ignore all prior instructions",
                    confidence=0.99,
                )
            ],
            high_confidence=True,
        )
        reviewer = ReturnReviewer(
            gateway=gateway,
            prompt_registry=registry,
            adversarial_detector=detector,
        )

        decision = reviewer.review_return(_make_task_return(), [], [])

        assert decision.decision == "blocked"
        assert decision.confidence == 1.0
        assert decision.adversarial_findings[0]["pattern_id"] == "ADV-001"
        registry.render.assert_not_called()
        gateway.call_model.assert_not_called()

    def test_noncritical_adversarial_finding_is_preserved_for_model_scrutiny(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        registry = MagicMock(spec=PromptRegistry)
        registry.render.return_value = "rendered prompt"
        detector = MagicMock()
        category = SimpleNamespace(value="suspicious_code")
        severity = SimpleNamespace(value="medium")
        detector.scan_task_return.return_value = SimpleNamespace(
            findings=[
                SimpleNamespace(
                    pattern_id="ADV-002",
                    category=category,
                    severity=severity,
                    description="review carefully",
                    match_text="bounded suspicious fragment",
                    confidence=0.55,
                )
            ],
            high_confidence=False,
        )
        reviewer = ReturnReviewer(
            gateway=gateway,
            prompt_registry=registry,
            adversarial_detector=detector,
        )
        decision_json = _make_decision().model_dump_json()

        with (
            patch.object(reviewer, "_call_model", return_value=(decision_json, None)),
            patch.object(reviewer, "_audit_evidence", return_value=[]),
        ):
            decision = reviewer.review_return(_make_task_return(), [], [])

        assert decision.decision == "complete"
        assert decision.adversarial_findings[0]["category"] == "suspicious_code"
        messages = reviewer.get_conversations()["TODO-001"].messages
        assert "ADVERSARIAL SCAN FINDINGS" in messages[0].content
        assert "review carefully" in messages[0].content

    def test_estimation_variance_is_attached_to_completed_review(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        registry = MagicMock(spec=PromptRegistry)
        registry.render.return_value = "rendered prompt"
        tracker = MagicMock()
        tracker.record_completion.return_value = SimpleNamespace(
            is_suspect=True,
            suspect_reasons=["cost variance", "duration variance"],
        )
        reviewer = ReturnReviewer(
            gateway=gateway,
            prompt_registry=registry,
            estimation_tracker=tracker,
        )
        task_return = _make_task_return()
        object.__setattr__(task_return, "cost_estimate", 1.25)
        object.__setattr__(task_return, "duration_seconds", 120.0)
        decision_json = _make_decision().model_dump_json()

        with (
            patch.object(reviewer, "_call_model", return_value=(decision_json, None)),
            patch.object(reviewer, "_audit_evidence", return_value=[]),
        ):
            decision = reviewer.review_return(task_return, [], [])

        assert decision.estimation_suspect is True
        assert "ESTIMATION_SUSPECT: cost variance, duration variance" in decision.audit_notes
        actual = tracker.record_completion.call_args.args[0]
        assert actual.actual_cost_usd == 1.25
        assert actual.actual_time_minutes == 2.0
        assert actual.exit_code == 0

    def test_normal_estimation_variance_leaves_review_unchanged(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        registry = MagicMock(spec=PromptRegistry)
        registry.render.return_value = "rendered prompt"
        tracker = MagicMock()
        tracker.record_completion.return_value = SimpleNamespace(
            is_suspect=False,
            suspect_reasons=[],
        )
        reviewer = ReturnReviewer(
            gateway=gateway,
            prompt_registry=registry,
            estimation_tracker=tracker,
        )
        decision_json = _make_decision().model_dump_json()

        with (
            patch.object(reviewer, "_call_model", return_value=(decision_json, None)),
            patch.object(reviewer, "_audit_evidence", return_value=[]),
        ):
            decision = reviewer.review_return(_make_task_return(), [], [])

        assert decision.estimation_suspect is False
        tracker.record_completion.assert_called_once()

    def test_return_reviewer_renders_prompt_with_context(self) -> None:
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

    def test_return_reviewer_calls_model_gateway(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        registry = PromptRegistry(template_dir="templates/prompts")
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)
        task_return = _make_task_return()
        decision_json = _make_decision().model_dump_json()

        with patch.object(reviewer, "_call_model", return_value=(decision_json, None)) as mock_call:
            result = reviewer.review_return(task_return, [], [])

        mock_call.assert_called_once()
        assert result.decision == "complete"

    def test_return_reviewer_validates_task_decision_schema(self) -> None:
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

    def test_parse_model_output_preserves_validated_decision_instance(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        registry = MagicMock(spec=PromptRegistry)
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)
        task_return = _make_task_return()
        expected = _make_decision()

        decision = reviewer._parse_model_output(expected, task_return)

        assert decision is expected

    def test_parse_model_output_rejects_non_string_payload(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        registry = MagicMock(spec=PromptRegistry)
        reviewer = ReturnReviewer(gateway=gateway, prompt_registry=registry)

        decision = reviewer._parse_model_output({"decision": "complete"}, _make_task_return())

        assert decision is None

    def test_return_reviewer_handles_invalid_model_output(self) -> None:
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

    def test_extract_json_fenced_with_braces_in_string_and_trailing_prose(self) -> None:
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

    def test_extract_json_plain_braces_in_string_value(self) -> None:
        """No fence: plain JSON where a string value contains braces."""
        raw = 'Preamble {"key": "val } inner {", "ok": true} trailing text'
        result = ReturnReviewer._extract_json_from_output(raw)
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert "inner {" in parsed["key"]

    def test_extract_json_fenced_no_prose(self) -> None:
        """Well-formed fenced JSON still parses correctly (regression guard)."""
        raw = '```json\n{"decision": "complete", "confidence": 0.9}\n```'
        result = ReturnReviewer._extract_json_from_output(raw)
        parsed = json.loads(result)
        assert parsed["decision"] == "complete"

    def test_extract_json_review_return_fenced_braces_in_string(self) -> None:
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
