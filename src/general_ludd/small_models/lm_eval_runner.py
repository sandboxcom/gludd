"""Lightweight lm_eval runner for standard benchmarks.

Wraps lm_eval's simple_evaluate API for common benchmarks (MMLU, GSM8K,
HellaSwag, ARC, TruthfulQA) and converts results to CapabilityEvidence.
Minimal dependencies: graceful skip if lm_eval is not installed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from typing import Any

from general_ludd.routing_roles.small_model_policy import CapabilityEvidence
from general_ludd.schemas.benchmark import TaskRole

_DEFAULT_TASKS: tuple[str, ...] = (
    "mmlu",
    "gsm8k",
    "hellaswag",
    "arc_easy",
    "arc_challenge",
    "truthfulqa_mc2",
)

_DEFAULT_THRESHOLDS: dict[str, float] = {
    "mmlu": 0.30,
    "gsm8k": 0.25,
    "hellaswag": 0.35,
    "arc_easy": 0.35,
    "arc_challenge": 0.30,
    "truthfulqa_mc2": 0.35,
}
_DEFAULT_THRESHOLD = 0.35

_SUITE_REVISION = "v0.4.5"


def _try_import_lm_eval() -> bool:
    return importlib.util.find_spec("lm_eval") is not None


def _extract_score_from_results(results: dict[str, Any], tasks: list[str]) -> dict[str, float]:
    raw = results.get("results", {})
    if not isinstance(raw, dict):
        return {}

    scores: dict[str, float] = {}
    requested = set(tasks)
    for task_name, metrics in raw.items():
        if task_name not in requested:
            continue
        if not isinstance(metrics, dict):
            continue
        score = _extract_primary_score(metrics)
        if score is not None:
            scores[task_name] = score
    return scores


def _extract_primary_score(metrics: dict[str, Any]) -> float | None:
    priority_keys = ["acc,none", "acc_norm,none", "f1,none", "exact_match,flexible-extract"]
    for key in priority_keys:
        if key in metrics and isinstance(metrics[key], (int, float)):
            return float(metrics[key])

    for _key, value in metrics.items():
        if isinstance(value, (int, float)):
            return float(value)

    return None


class LMEvalRunner:
    def __init__(
        self,
        model_id: str = "hf",
        batch_size: str = "auto",
        device: str | None = None,
        limit: int | None = None,
    ) -> None:
        self.model_id = model_id
        self.batch_size = batch_size
        self.device = device
        self.limit = limit
        self._available = _try_import_lm_eval()

    @property
    def default_tasks(self) -> tuple[str, ...]:
        return _DEFAULT_TASKS

    @property
    def available(self) -> bool:
        return self._available

    def _build_command(self, tasks: list[str]) -> list[str]:
        task_str = ",".join(tasks)
        cmd: list[str] = [
            "lm_eval",
            "--model",
            "hf",
            "--model_args",
            f"pretrained={self.model_id}",
            "--batch_size",
            self.batch_size,
            "--tasks",
            task_str,
        ]
        if self.device is not None:
            cmd.extend(["--device", self.device])
        if self.limit is not None:
            cmd.extend(["--limit", str(self.limit)])
        return cmd

    def run_benchmark(self, tasks: list[str]) -> dict[str, float]:
        return run_benchmark(
            self.model_id,
            tasks,
            batch_size=self.batch_size,
            device=self.device,
            limit=self.limit,
        )


def run_benchmark(
    model_id: str,
    tasks: list[str],
    batch_size: str = "auto",
    device: str | None = None,
    limit: int | None = None,
) -> dict[str, float]:
    if not _try_import_lm_eval():
        return {}

    try:
        import lm_eval

        eval_kwargs: dict[str, Any] = {
            "model": "hf",
            "model_args": f"pretrained={model_id}",
            "batch_size": batch_size,
            "tasks": tasks,
            "log_samples": False,
        }
        if device is not None:
            eval_kwargs["device"] = device
        if limit is not None:
            eval_kwargs["limit"] = limit

        results = lm_eval.simple_evaluate(**eval_kwargs)
        return _extract_score_from_results(results, tasks)
    except ImportError:
        return {}


def to_capability_evidence(
    results: dict[str, float],
    model_profile_id: str,
    model_identity_digest: str | None = None,
    thresholds: dict[str, float] | None = None,
) -> list[CapabilityEvidence]:
    if model_identity_digest is None:
        model_identity_digest = hashlib.sha256(f"{model_profile_id}:unnamed".encode()).hexdigest()

    if thresholds is None:
        thresholds = {}

    evidence_list: list[CapabilityEvidence] = []
    for task_name, score in results.items():
        threshold = thresholds.get(task_name, _DEFAULT_THRESHOLDS.get(task_name, _DEFAULT_THRESHOLD))
        passed = score >= threshold

        evidence_data = (
            f"{model_profile_id}:{task_name}:{score}:{model_identity_digest}:lm-eval-runner:{_SUITE_REVISION}"
        )
        evidence_digest = hashlib.sha256(evidence_data.encode()).hexdigest()

        acceptance_digest = hashlib.sha256(
            json.dumps({"task": task_name, "score": score, "passed": passed}).encode()
        ).hexdigest()

        evidence_list.append(
            CapabilityEvidence(
                model_profile_id=model_profile_id,
                model_identity_digest=model_identity_digest,
                task_kind=task_name,
                role=TaskRole.ENUMERATOR,
                collection="general_ludd.agent",
                suite_id="lm-eval-runner",
                suite_revision=_SUITE_REVISION,
                acceptance_contract_digest=acceptance_digest,
                passed_cases=1 if passed else 0,
                total_cases=1,
                collection_ok=True,
                local_only=True,
                evidence_digest=evidence_digest,
            )
        )

    return evidence_list
