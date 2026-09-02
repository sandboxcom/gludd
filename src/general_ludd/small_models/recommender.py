"""Task→model reverse lookup — rank models for a task using capability evidence and hardware fit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from general_ludd.hardware.model_fit import can_run_model
from general_ludd.hardware.survey import HardwareInventory
from general_ludd.schemas.benchmark import TaskRole
from general_ludd.small_models.cost import is_off_peak
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
from general_ludd.small_models.radar_profile import ModelRadarProfile, build_profile

_MIN_VRAM_GB = 1.0
_RECOMMENDED_VRAM_GB = 4.0
_DEFAULT_SCORE = 0.5
_VALID_PEAK_STATUSES = ("peak", "off_peak", "unknown")


@dataclass(frozen=True)
class ModelRecommendation:
    """Ranked model recommendation for a task."""

    model_profile_id: str
    task_kind: str
    role: TaskRole
    score: float
    cost_score: float
    estimated_cost_usd_per_hour: float
    evidence_count: int
    hardware_fit: str
    evidence_details: list[dict[str, Any]]
    can_run: bool
    peak_status: str
    prefer_off_peak: bool

    def __post_init__(self) -> None:
        """Validate bounded scores and recommendation metadata."""
        if not isinstance(self.score, (int, float)) or not (0.0 <= self.score <= 1.0):
            raise ValueError("score must be between 0.0 and 1.0")
        if not isinstance(self.cost_score, (int, float)) or not (0.0 <= self.cost_score <= 1.0):
            raise ValueError("cost_score must be between 0.0 and 1.0")
        if isinstance(self.evidence_count, bool) or self.evidence_count < 0:
            raise ValueError("evidence_count must be a non-negative integer")
        if self.hardware_fit not in ("fits", "marginal", "insufficient"):
            raise ValueError("hardware_fit must be 'fits', 'marginal', or 'insufficient'")
        if self.peak_status not in _VALID_PEAK_STATUSES:
            raise ValueError(f"peak_status must be one of {_VALID_PEAK_STATUSES}")


# ── natural language → task_kind keyword mapping ──────────────────

_TASK_KEYWORD_MAP: list[tuple[str, str, TaskRole]] = [
    ("context_compaction", r"\b(compact|compress|summarize|condense)\b", TaskRole.COMPACTOR),
    ("documentation_draft", r"\b(document|draft|doc|readme|writeup)\b", TaskRole.EDITOR),
    ("bounded_enumeration", r"\b(enumerate|list|enumerat|enum|itemize|catalog)\b", TaskRole.ENUMERATOR),
    (
        "failure_classification",
        r"\b(classify|failure|error|category|triage|bug.*class|root.*cause)\b",
        TaskRole.REVIEWER,
    ),
    ("format_normalization", r"\b(format|normalize|normaliz|standardi[sz]e|cleanse|scrub)\b", TaskRole.EDITOR),
    ("schema_extraction", r"\b(schema|extract|parse|field|structur)\b", TaskRole.EDITOR),
    (
        "coding",
        r"\b(add(?:ed|ing)?|bind|binding|bound|chang(?:e|ed|ing)|code|coding|debug(?:ged|ging)?|fix(?:ed|ing)?|implement|implementation|integrat(?:e|ed|ing|ion)|migrat(?:e|ed|ing|ion)|modif(?:y|ied|ying|ication)|program|python|function|class|module|refactor|remov(?:e|ed|ing|al)|replac(?:e|ed|ing|ement)|test|patch|repository|repo|wir(?:e|ed|ing))\b",
        TaskRole.CODER,
    ),
]


def map_task_to_capabilities(description: str) -> list[tuple[str, TaskRole]]:
    """Map a natural-language task description to task-kind and role pairs."""
    if not isinstance(description, str):
        raise ValueError("description must be a string")
    lowered = description.lower()
    matched: list[tuple[str, TaskRole]] = []
    for task_kind, pattern, role in _TASK_KEYWORD_MAP:
        if re.search(pattern, lowered):
            matched.append((task_kind, role))
    return matched


# Compatibility for callers that imported the mapper before it became public.
_map_task_to_capabilities = map_task_to_capabilities


# ── hardware fit ───────────────────────────────────────────────────


def _assess_hardware_fit(hardware: HardwareInventory) -> str:
    """Assess whether the hardware can run a small model."""
    if not hardware.gpus:
        return "insufficient"
    min_vram = min(g.vram_gb for g in hardware.gpus)
    if min_vram >= _RECOMMENDED_VRAM_GB:
        return "fits"
    if min_vram >= _MIN_VRAM_GB:
        return "marginal"
    return "insufficient"


# ── scoring ────────────────────────────────────────────────────────


def _compute_score(
    records: list[dict[str, Any]],
    hardware: HardwareInventory,
    model_id: str,
    radar_profile: ModelRadarProfile | None = None,
    *,
    urgent: bool = False,
) -> tuple[float, float, float]:
    """Compute composite 0.0-1.0 score from evidence quality + hardware fit + radar breadth + cost.

    When *urgent* is False and the current time is peak, the cost factor weight
    is doubled to prefer cheaper models during expensive hours.

    Returns (score, cost_score, estimated_cost_usd_per_hour).
    """
    if not records:
        return (0.0, 1.0, 0.0)

    avg_pass_rate = sum(
        cast(float, r.get("passed_cases", 0)) / max(cast(int, r.get("total_cases", 1)), 1) for r in records
    ) / len(records)

    collection_ok_rate = sum(1 for r in records if r.get("collection_ok", False)) / len(records)

    evidence_count_score = min(1.0, len(records) / 3.0)

    hw_fit = _assess_hardware_fit(hardware)
    hw_score = {"fits": 1.0, "marginal": 0.6, "insufficient": 0.2}[hw_fit]

    radar_breadth = 0.0
    if radar_profile is not None:
        scores = radar_profile.normalized()
        vec = list(scores.values())
        nonzero = sum(1 for v in vec if v > 0.0)
        avg_profile = sum(vec) / len(vec) if vec else 0.0
        radar_breadth = (nonzero / len(vec)) * 0.6 + avg_profile * 0.4

    _, cost_score, estimated_cost = _compute_cost_factors(model_id)

    currently_off_peak = is_off_peak()
    if not urgent and not currently_off_peak:
        pass_weight = 0.25
        collection_weight = 0.15
        evidence_weight = 0.12
        hw_weight = 0.10
        radar_weight = 0.13
        cost_weight = 0.25
    else:
        pass_weight = 0.30
        collection_weight = 0.20
        evidence_weight = 0.15
        hw_weight = 0.10
        radar_weight = 0.15
        cost_weight = 0.10

    return (
        float(
            pass_weight * avg_pass_rate
            + collection_weight * collection_ok_rate
            + evidence_weight * evidence_count_score
            + hw_weight * hw_score
            + radar_weight * radar_breadth
            + cost_weight * cost_score
        ),
        cost_score,
        estimated_cost,
    )


def _compute_cost_factors(model_id: str) -> tuple[float, float, float]:
    from general_ludd.small_models.cost import compute_cost_score, estimate_inference_cost

    cost_score = compute_cost_score(model_id)
    cost_info = estimate_inference_cost(model_id)
    est_raw = cost_info.get("estimated_usd_per_hour", 0.0)
    estimated_cost = float(est_raw) if isinstance(est_raw, (int, float)) else 0.0
    return (cost_score, cost_score, estimated_cost)


def _build_evidence_details(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "suite_id": r.get("suite_id", ""),
            "suite_revision": r.get("suite_revision", ""),
            "passed_cases": r.get("passed_cases", 0),
            "total_cases": r.get("total_cases", 0),
            "collection_ok": r.get("collection_ok", False),
            "local_only": r.get("local_only", False),
        }
        for r in records
    ]


# ── public API ─────────────────────────────────────────────────────


def recommend_model(
    task_description: str,
    hardware: HardwareInventory,
    store: CapabilityEvidenceStore,
    *,
    urgent: bool = False,
) -> list[ModelRecommendation]:
    """Rank models for *task_description* using capability evidence and hardware fit.

    Models that cannot run on *hardware* (per :func:`can_run_model`) are
    excluded unless the model is unknown to the hardware fitter.

    When *urgent* is ``False`` and the current time is peak, the score
    prefers models that are cheaper to run (off-peak-friendly).  When
    it is already off-peak, no adjustment is applied.

    Returns a list sorted by descending ``score``.  Empty list when no capability
    evidence matches any mapped task kind.
    """
    capabilities = map_task_to_capabilities(task_description)
    if not capabilities:
        return []

    model_evidence: dict[str, dict[str, Any]] = {}

    for task_kind, role in capabilities:
        records = store.query_by_task_kind(task_kind)
        valid = [r for r in records if r.get("collection_ok", False)]
        if not valid:
            continue

        for rec in valid:
            model_id = rec.get("model_profile_id", "")
            if not model_id:
                continue
            if model_id not in model_evidence:
                model_evidence[model_id] = {
                    "task_kind": task_kind,
                    "role": role,
                    "records": [],
                }
            model_evidence[model_id]["records"].append(rec)

    currently_off_peak = is_off_peak()

    recommendations: list[ModelRecommendation] = []
    hw_fit = _assess_hardware_fit(hardware)

    for model_id, info in model_evidence.items():
        fit_result = can_run_model(hardware, model_id)
        model_can_run = fit_result.can_run
        unknown_model = "unknown model" in fit_result.reason

        if not model_can_run and not unknown_model:
            continue

        records = info["records"]
        all_model_records = store.query_by_model(model_id)
        radar_profile = build_profile(model_id, all_model_records) if all_model_records else None
        score, cost_score, estimated_cost = _compute_score(
            records, hardware, model_id, radar_profile=radar_profile, urgent=urgent
        )

        if currently_off_peak:
            peak_status = "off_peak"
            prefer_off_peak = False
        else:
            peak_status = "peak"
            prefer_off_peak = not urgent

        recommendations.append(
            ModelRecommendation(
                model_profile_id=model_id,
                task_kind=info["task_kind"],
                role=info["role"],
                score=score,
                cost_score=cost_score,
                estimated_cost_usd_per_hour=estimated_cost,
                evidence_count=len(records),
                hardware_fit=hw_fit,
                evidence_details=_build_evidence_details(records),
                can_run=model_can_run,
                peak_status=peak_status,
                prefer_off_peak=prefer_off_peak,
            )
        )

    recommendations.sort(key=lambda r: r.score, reverse=True)
    return recommendations


def list_tasks_for_model(
    model_id: str,
    store: CapabilityEvidenceStore,
) -> list[str]:
    """Return sorted, deduplicated task kinds for which *model_id* has evidence."""
    records = store.query_by_model(model_id)
    task_kinds = sorted({r.get("task_kind", "") for r in records if r.get("task_kind")})
    return task_kinds


__all__ = [
    "ModelRecommendation",
    "list_tasks_for_model",
    "map_task_to_capabilities",
    "recommend_model",
]
