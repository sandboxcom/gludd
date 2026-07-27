"""Return reviewer that uses model gateway to evaluate task returns."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from general_ludd.models.gateway import ModelGateway
from general_ludd.models.router import ModelRouter
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.review.conversation import Conversation
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn

if TYPE_CHECKING:
    from general_ludd.review.estimation_tracker import EstimationTracker
    from general_ludd.security.adversarial_detector import AdversarialCodeDetector

logger = logging.getLogger(__name__)


class ReturnReviewer:
    def __init__(
        self,
        gateway: ModelGateway,
        prompt_registry: PromptRegistry,
        model_profile_id: str = "default",
        router: ModelRouter | None = None,
        conversations: dict[str, Conversation] | None = None,
        budget_guard: Any = None,
        adversarial_detector: AdversarialCodeDetector | None = None,
        estimation_tracker: EstimationTracker | None = None,
    ) -> None:
        self._gateway = gateway
        self._registry = prompt_registry
        self._model_profile_id = model_profile_id
        self._router = router
        self._budget_guard = budget_guard
        self._adversarial_detector = adversarial_detector
        self._estimation_tracker = estimation_tracker
        self._conversations: dict[str, Conversation] = conversations if conversations is not None else {}

    def get_conversations(self) -> dict[str, Conversation]:
        return dict(self._conversations)

    def review_return(
        self,
        task_return: TaskReturn,
        candidate_todos: list[dict[str, Any]],
        artifacts: list[str],
    ) -> TaskDecision:
        todo_id = task_return.todo_id or ""
        conv = self._conversations.get(todo_id)
        if conv is None:
            conv = Conversation(todo_id=todo_id, return_id=task_return.return_id)
            self._conversations[todo_id] = conv

        adversarial_findings: list[dict[str, object]] = []
        if self._adversarial_detector is not None:
            scan_result = self._adversarial_detector.scan_task_return(task_return)
            adversarial_findings = [
                {
                    "pattern_id": f.pattern_id,
                    "category": f.category.value if hasattr(f.category, "value") else str(f.category),
                    "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
                    "description": f.description,
                    "match_text": f.match_text,
                    "confidence": f.confidence,
                }
                for f in scan_result.findings
            ]
            if scan_result.high_confidence:
                return TaskDecision(
                    return_id=task_return.return_id,
                    matched_todo_id=task_return.todo_id,
                    decision="blocked",
                    confidence=1.0,
                    audit_notes=[
                        f"Blocked by adversarial scan: {len(adversarial_findings)} high-confidence finding(s)",
                        *[
                            f"{f['category']}/{f['pattern_id']}: {str(f.get('match_text', ''))[:120]}"
                            for f in adversarial_findings
                        ],
                    ],
                    adversarial_findings=adversarial_findings,
                )

        prior_context = ""
        if conv.messages:
            prior_context = "\n\n".join(f"[{m.role}]: {m.content}" for m in conv.get_context())
        review_prompt_text = f"Review return {task_return.return_id} for todo {todo_id}"

        if adversarial_findings:
            non_critical = "\n".join(
                f"  - [{f['category']}] {f['description']}: {str(f.get('match_text', ''))[:100]}"
                for f in adversarial_findings
            )
            review_prompt_text += f"\n\nADVERSARIAL SCAN FINDINGS (non-critical, review with scrutiny):\n{non_critical}"

        conv.add_message("user", review_prompt_text)
        prompt = self._registry.render(
            "return_review.md.j2",
            task_return=task_return.model_dump(mode="json"),
            candidate_todos=candidate_todos,
            artifacts=artifacts,
            conversation_context=prior_context,
        )
        raw_output, error_msg = self._call_model(prompt)
        if raw_output is None:
            audit = ["Model call failed"]
            if error_msg:
                audit.append(f"Error: {error_msg}")
            decision = TaskDecision(
                return_id=task_return.return_id,
                matched_todo_id=task_return.todo_id,
                decision="failed",
                confidence=0.0,
                audit_notes=audit,
                adversarial_findings=adversarial_findings,
            )
            conv.add_message("assistant", json.dumps(decision.model_dump(mode="json")))
            return decision
        parsed = self._parse_model_output(raw_output, task_return)
        if parsed is not None:
            parsed = parsed.model_copy(update={"adversarial_findings": adversarial_findings})
            evidence_notes = self._audit_evidence(parsed, artifacts)
            if evidence_notes:
                parsed = parsed.model_copy(update={"audit_notes": [*parsed.audit_notes, *evidence_notes]})

            if self._estimation_tracker is not None:
                from general_ludd.review.estimation_tracker import TaskActual

                # S11: extract real actuals from the task_return when available.
                # Falls back to zeros when cost/time/LOC are unpopulated —
                # record_estimate() must be called on dispatch for variance
                # detection to work.
                actual_cost = getattr(task_return, "cost_estimate", 0.0) or 0.0
                actual = TaskActual(
                    todo_id=task_return.todo_id or task_return.return_id,
                    actual_cost_usd=float(actual_cost),
                    actual_time_minutes=float(getattr(task_return, "duration_seconds", 0.0) or 0.0) / 60.0,
                    actual_loc=0,
                    exit_code=task_return.exit_code,
                )
                variance = self._estimation_tracker.record_completion(actual)
                if variance.is_suspect:
                    parsed = parsed.model_copy(
                        update={
                            "estimation_suspect": True,
                            "audit_notes": [
                                *parsed.audit_notes,
                                f"ESTIMATION_SUSPECT: {', '.join(variance.suspect_reasons)}",
                            ],
                        }
                    )

            conv.add_message("assistant", json.dumps(parsed.model_dump(mode="json")))
            return parsed
        logger.warning(
            "Invalid model output for return %s, falling back to failed",
            task_return.return_id,
        )
        fallback = TaskDecision(
            return_id=task_return.return_id,
            matched_todo_id=task_return.todo_id,
            decision="failed",
            confidence=0.0,
            audit_notes=["Model output was not valid JSON or did not match TaskDecision schema"],
            adversarial_findings=adversarial_findings,
        )
        conv.add_message("assistant", json.dumps(fallback.model_dump(mode="json")))
        return fallback

    def _audit_evidence(self, decision: TaskDecision, artifacts: list[str]) -> list[str]:
        """Flag unsupported factual claims in the model's audit notes.

        Uses EvidenceChecker to scan the review's own audit_notes for factual
        claims with no backing source (file:line / artifact), so a confidently
        wrong review leaves a trail rather than passing silently.
        """
        from general_ludd.review.evidence_checker import EvidenceChecker

        checker = EvidenceChecker()
        notes: list[str] = []
        for claim_text in decision.audit_notes:
            results = checker.audit_response(claim_text, artifacts)
            for res in results:
                if not res.supported:
                    notes.append(f"evidence: unsupported claim in review — {res.claim[:120]}")
        return notes

    def _call_model(self, prompt: str) -> tuple[str | None, str | None]:
        if self._router is not None:
            profile_id = self._router.resolve_role("return_review")
            if profile_id is not None:
                self._model_profile_id = profile_id
        from general_ludd.budget_guard_check import budget_pre_check

        denial = budget_pre_check(self._budget_guard)
        if denial is not None:
            logger.warning("Budget pre-check denied in reviewer: %s", denial)
            return None, f"Budget denied: {denial}"
        try:
            response = self._gateway.call_model(
                self._model_profile_id,
                messages=[{"role": "user", "content": prompt}],
                work_type="review",
            )
            return response.content, None
        except Exception as exc:
            logger.warning(
                "Model call failed for profile %s: %s",
                self._model_profile_id,
                exc,
            )
            return None, str(exc)

    def _parse_model_output(self, raw: Any, task_return: TaskReturn) -> TaskDecision | None:
        if isinstance(raw, TaskDecision):
            return raw
        if not isinstance(raw, str):
            return None
        cleaned = self._extract_json_from_output(raw)
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("return_id", task_return.return_id)
        data.setdefault("matched_todo_id", task_return.todo_id)
        try:
            return TaskDecision(**data)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_json_from_output(text: str) -> str:
        """Strip markdown code fences and extract the first JSON object.

        Handles ```json\\n{...}\\n``` and ```\\n{...}\\n``` fences, leading/trailing
        prose, and plain JSON (passthrough).  Uses json.JSONDecoder.raw_decode so
        that brace characters inside string values (e.g. "reason": "loop }{ ok") are
        handled correctly and trailing prose after the closing brace is ignored.
        """
        import re

        # Strip an outer ```json ... ``` or ``` ... ``` fence if present, then fall
        # through to the raw_decode path so trailing prose inside the fence is also
        # handled correctly.
        fence_match = re.search(r"```(?:json)?\s*(\{.*?)\s*```", text, re.DOTALL)
        candidate = fence_match.group(1).strip() if fence_match else text

        start = candidate.find("{")
        if start == -1:
            return candidate

        try:
            _obj, _end = json.JSONDecoder().raw_decode(candidate, start)
            return candidate[start:_end]
        except json.JSONDecodeError:
            return candidate
