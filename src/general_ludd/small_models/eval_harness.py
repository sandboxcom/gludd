"""EleutherAI LM eval harness integration for small model capability evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from general_ludd.routing_roles.small_model_policy import CapabilityEvidence
from general_ludd.schemas.benchmark import TaskRole


class EvalTask(StrEnum):
    HELLASWAG = "hellaswag"
    MMLU = "mmlu"
    ARC_EASY = "arc_easy"
    ARC_CHALLENGE = "arc_challenge"
    TRUTHFULQA = "truthfulqa_mc2"


STANDARD_TASKS: tuple[EvalTask, ...] = (
    EvalTask.HELLASWAG,
    EvalTask.MMLU,
    EvalTask.ARC_EASY,
    EvalTask.ARC_CHALLENGE,
    EvalTask.TRUTHFULQA,
)

_DEFAULT_THRESHOLDS: dict[str, float] = {
    "hellaswag": 0.35,
    "mmlu": 0.30,
    "arc_easy": 0.35,
    "arc_challenge": 0.30,
    "truthfulqa_mc2": 0.35,
}

_DEFAULT_THRESHOLD = 0.35


@dataclass(frozen=True)
class ParsedResult:
    task_name: str
    score: float
    raw_metrics: dict[str, float]


@dataclass
class HarnessConfig:
    model: str = "hf"
    model_args: str = ""
    batch_size: str = "auto"
    device: str | None = None
    limit: int | None = None

    def to_cli_args(self) -> list[str]:
        args: list[str] = [
            "--model",
            self.model,
            "--model_args",
            self.model_args,
            "--batch_size",
            self.batch_size,
        ]
        if self.device is not None:
            args.extend(["--device", self.device])
        if self.limit is not None:
            args.extend(["--limit", str(self.limit)])
        return args


class EleutherAIHarness:
    def __init__(self, config: HarnessConfig | None = None) -> None:
        self.config = config or HarnessConfig()

    def _build_command(self, tasks: list[EvalTask]) -> list[str]:
        task_str = ",".join(t.value for t in tasks)
        return ["lm_eval", *self.config.to_cli_args(), "--tasks", task_str]

    def parse_and_evidence(
        self,
        output: dict[str, Any],
        model_profile_id: str,
        model_identity_digest: str,
        task_kind: str,
        role: TaskRole,
        collection: str = "general_ludd.agent",
        suite_id: str = "lm-eval-harness",
        suite_revision: str = "v0.4.5",
    ) -> list[CapabilityEvidence]:
        results = parse_lm_eval_output(output)
        return [
            result_to_evidence(
                result=r,
                model_profile_id=model_profile_id,
                model_identity_digest=model_identity_digest,
                task_kind=task_kind,
                role=role,
                collection=collection,
                suite_id=suite_id,
                suite_revision=suite_revision,
            )
            for r in results
        ]


def parse_lm_eval_output(output: dict[str, Any]) -> list[ParsedResult]:
    raw_results = output.get("results", {})
    if not isinstance(raw_results, dict):
        return []

    parsed: list[ParsedResult] = []
    for task_name, metrics in raw_results.items():
        if not isinstance(metrics, dict):
            continue
        score = _extract_primary_score(metrics)
        if score is None:
            continue
        cleaned = _clean_metrics(metrics)
        parsed.append(ParsedResult(task_name=task_name, score=score, raw_metrics=cleaned))

    return parsed


def _extract_primary_score(metrics: dict[str, Any]) -> float | None:
    priority_keys = ["acc,none", "acc_norm,none", "f1,none"]
    for key in priority_keys:
        if key in metrics and isinstance(metrics[key], (int, float)):
            return float(metrics[key])

    for _key, value in metrics.items():
        if isinstance(value, (int, float)):
            return float(value)

    return None


def _clean_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for key, value in metrics.items():
        if not isinstance(value, (int, float)):
            continue
        cleaned_key = key.split(",")[0]
        cleaned[cleaned_key] = float(value)
    return cleaned


def score_passing(task_name: str, score: float, *, threshold: float | None = None) -> bool:
    if threshold is not None:
        return score >= threshold
    t = _DEFAULT_THRESHOLDS.get(task_name, _DEFAULT_THRESHOLD)
    return score >= t


def result_to_evidence(
    result: ParsedResult,
    model_profile_id: str,
    model_identity_digest: str,
    task_kind: str,
    role: TaskRole,
    collection: str,
    suite_id: str,
    suite_revision: str,
) -> CapabilityEvidence:
    passed = score_passing(result.task_name, result.score)
    evidence_data = (
        f"{model_profile_id}:{result.task_name}:{result.score}:{model_identity_digest}:{suite_id}:{suite_revision}"
    )
    evidence_digest = hashlib.sha256(evidence_data.encode()).hexdigest()

    return CapabilityEvidence(
        model_profile_id=model_profile_id,
        model_identity_digest=model_identity_digest,
        task_kind=task_kind,
        role=role,
        collection=collection,
        suite_id=suite_id,
        suite_revision=suite_revision,
        acceptance_contract_digest=hashlib.sha256(
            json.dumps({"task": result.task_name, "score": result.score, "passed": passed}).encode()
        ).hexdigest(),
        passed_cases=1 if passed else 0,
        total_cases=1,
        collection_ok=True,
        local_only=True,
        evidence_digest=evidence_digest,
    )
