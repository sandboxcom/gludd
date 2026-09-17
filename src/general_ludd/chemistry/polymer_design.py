"""Constrained polymer-design adapter for the universal task executor."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from general_ludd.ai_ml.policy import PolicyEngine
from general_ludd.ai_ml.schemas import (
    Constraints,
    DataClassification,
    ExpertRequest,
    ExpertTask,
)
from general_ludd.chemistry.provenance import build_chain, verify_chain
from general_ludd.chemistry.safety import classify_risk
from general_ludd.chemistry.schemas import ValueRecord
from general_ludd.chemistry.validation import validate_result
from general_ludd.execution.universal_task import (
    AdapterDecision,
    CandidateAssessment,
    ExecutionTarget,
    ToolRunnerProtocol,
    UniversalTaskRequest,
)


class PolymerCandidate(BaseModel):
    """Strict model-produced polymer candidate; prose/scaffolds cannot pass."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    monomers: list[str] = Field(min_length=1)
    repeat_unit: str = Field(min_length=1)
    properties: list[ValueRecord] = Field(min_length=1)
    synthesis_scale: Literal["lab", "pilot", "industrial"] = "lab"
    facility_controls: list[str] = Field(default_factory=list)
    validation: dict[str, object]
    provenance: dict[str, object]


class PolymerDesignAdapter:
    """Translate polymer design into a policy- and evidence-gated task."""

    capability = "polymer_design"

    def __init__(
        self,
        *,
        policy_engine: PolicyEngine,
        required_tool: str | None = None,
    ) -> None:
        """Bind policy enforcement and an optional external validator."""
        self._policy_engine = policy_engine
        self._required_tool = required_tool

    def build_messages(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> list[dict[str, str]]:
        """Request one strict JSON candidate from the selected profile."""
        schema = {
            "candidate_id": "non-empty string",
            "monomers": ["hazard-registry chemical name"],
            "repeat_unit": "non-empty structural description",
            "properties": [
                {
                    "name": "property name",
                    "value": "number",
                    "unit": "required unit",
                    "uncertainty": "non-negative number",
                    "method_id": "method identifier",
                }
            ],
            "synthesis_scale": "lab|pilot|industrial",
            "facility_controls": ["declared controls"],
            "validation": {
                "checks": ["unit_consistency", "convergence"],
                "converged": True,
                "iterations": "positive integer",
            },
            "provenance": {
                "source": {"locator": "source locator"},
                "method": "method identifier",
                "conditions": {"named": "conditions"},
                "code": {"repository": "repository", "commit": "revision"},
                "raw_artifact": {"uri": "artifact URI", "digest": "sha256"},
            },
        }
        return [
            {
                "role": "system",
                "content": (
                    "Return exactly one JSON object matching this schema. "
                    "Do not return prose, markdown, TODOs, or placeholders: "
                    + json.dumps(schema, sort_keys=True)
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instruction": request.instruction,
                        "requested": dict(request.metadata),
                        "provider": target.provider,
                    },
                    sort_keys=True,
                    default=str,
                ),
            },
        ]

    def preflight(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> AdapterDecision:
        """Reuse the AI/ML policy engine before any model invocation."""
        deadline_s = request.metadata.get("deadline_s", 300)
        if (
            not isinstance(deadline_s, int)
            or isinstance(deadline_s, bool)
            or deadline_s <= 0
        ):
            return AdapterDecision(
                accepted=False,
                reasons=("policy_refused:deadline_s must be a positive integer",),
                evidence={
                    "policy": {
                        "allowed": False,
                        "reason": "invalid_deadline_s",
                        "target_offline": target.offline,
                    }
                },
            )
        policy_request = ExpertRequest(
            request_id=request.task_id,
            tenant_id=str(request.metadata.get("tenant_id", "universal-task")),
            task=ExpertTask.SIMULATE,
            query=request.instruction,
            constraints=Constraints(
                deadline_s=deadline_s,
                budget_usd=request.budget_usd,
                data_classification=DataClassification(request.data_classification),
                offline=target.offline,
                allowed_tools=tuple(sorted(request.allowed_tools)),
            ),
            requested_outputs=("structured_polymer_candidate",),
        )
        decision = self._policy_engine.check_request(policy_request)
        return AdapterDecision(
            accepted=decision.allowed,
            reasons=tuple(f"policy_refused:{reason}" for reason in decision.refusal_reasons),
            evidence={
                "policy": {
                    "allowed": decision.allowed,
                    "decision_id": decision.decision_id,
                    "ruleset_sha256": decision.ruleset_sha256,
                    "target_offline": target.offline,
                }
            },
        )

    def parse_candidate(self, content: str) -> PolymerCandidate:
        """Parse one strict JSON object; model prose and scaffolds are errors."""
        try:
            payload = json.loads(content)
            if not isinstance(payload, dict):
                raise ValueError("candidate must be a JSON object")
            return PolymerCandidate.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError("structured polymer candidate required") from exc

    def assess_candidate(
        self,
        request: UniversalTaskRequest,
        candidate: object,
        tool_runner: ToolRunnerProtocol | None,
    ) -> CandidateAssessment:
        """Require safety, validation, provenance, and configured tool proof."""
        if not isinstance(candidate, PolymerCandidate):
            return CandidateAssessment(False, ("structured_candidate_required",))

        safety = classify_risk(
            candidate.monomers,
            scale=candidate.synthesis_scale,
            facility_controls=candidate.facility_controls,
        )
        safety_evidence = safety.to_dict()

        validation_payload: dict[str, Any] = dict(candidate.validation)
        validation_payload["values"] = [
            value.model_dump(mode="json") for value in candidate.properties
        ]
        validation_evidence = validate_result(validation_payload)

        chain = build_chain(candidate.provenance)
        provenance_evidence = verify_chain(chain)
        evidence: dict[str, object] = {
            "safety": safety_evidence,
            "validation": validation_evidence,
            "provenance": provenance_evidence,
        }
        reasons: list[str] = []
        if safety.refused_reason is not None:
            reasons.append("safety_gate_refused")
        if not bool(validation_evidence.get("supports_execution", False)):
            reasons.append("validation_gate_refused")
        if not bool(provenance_evidence.get("complete", False)):
            reasons.append("provenance_gate_refused")

        if self._required_tool is not None:
            if self._required_tool not in request.allowed_tools:
                reasons.append("required_tool_not_allowed")
            elif tool_runner is None:
                reasons.append("required_tool_unavailable")
            else:
                try:
                    tool_evidence = tool_runner.run(
                        self._required_tool,
                        candidate.model_dump(mode="json"),
                    )
                except Exception as exc:
                    reasons.append(f"tool_check_failed:{type(exc).__name__}")
                else:
                    evidence["tool"] = dict(tool_evidence)
                    if tool_evidence.get("passed") is not True:
                        reasons.append("tool_check_refused")

        return CandidateAssessment(
            accepted=not reasons,
            reasons=tuple(reasons),
            evidence=evidence,
        )


__all__ = ["PolymerCandidate", "PolymerDesignAdapter"]
