"""Task→model reverse lookup — rank models for a task using capability evidence and hardware fit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from general_ludd.hardware.survey import HardwareInventory
from general_ludd.schemas.benchmark import TaskRole
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
from general_ludd.small_models.radar import RadarProfile, build_profile

_MIN_VRAM_GB = 1.0
_RECOMMENDED_VRAM_GB = 4.0
_DEFAULT_SCORE = 0.5


@dataclass(frozen=True)
class ModelRecommendation:
    """Ranked model recommendation for a task."""

    model_profile_id: str
    task_kind: str
    role: TaskRole
    score: float
    evidence_count: int
    hardware_fit: str
    evidence_details: list[dict[str, Any]]

    def __post_init__(self) -> None:
        if not isinstance(self.score, (int, float)) or not (0.0 <= self.score <= 1.0):
            raise ValueError("score must be between 0.0 and 1.0")
        if isinstance(self.evidence_count, bool) or self.evidence_count < 0:
            raise ValueError("evidence_count must be a non-negative integer")
        if self.hardware_fit not in ("fits", "marginal", "insufficient"):
            raise ValueError("hardware_fit must be 'fits', 'marginal', or 'insufficient'")


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
]


def _map_task_to_capabilities(description: str) -> list[tuple[str, TaskRole]]:
    """Map a natural-language task description to (task_kind, TaskRole) pairs."""
    lowered = description.lower()
    matched: list[tuple[str, TaskRole]] = []
    for task_kind, pattern, role in _TASK_KEYWORD_MAP:
        if re.search(pattern, lowered):
            matched.append((task_kind, role))
    return matched


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
    radar_profile: RadarProfile | None = None,
) -> float:
    """Compute composite 0.0-1.0 score from evidence quality + hardware fit + radar breadth."""
    if not records:
        return 0.0

    avg_pass_rate = sum(
        cast(float, r.get("passed_cases", 0)) / max(cast(int, r.get("total_cases", 1)), 1) for r in records
    ) / len(records)

    collection_ok_rate = sum(1 for r in records if r.get("collection_ok", False)) / len(records)

    evidence_count_score = min(1.0, len(records) / 3.0)

    hw_fit = _assess_hardware_fit(hardware)
    hw_score = {"fits": 1.0, "marginal": 0.6, "insufficient": 0.2}[hw_fit]

    radar_breadth = 0.0
    if radar_profile is not None:
        vec = radar_profile.vector()
        nonzero = sum(1 for v in vec if v > 0.0)
        avg_profile = sum(vec) / len(vec) if vec else 0.0
        radar_breadth = (nonzero / len(vec)) * 0.6 + avg_profile * 0.4

    return float(
        0.35 * avg_pass_rate
        + 0.25 * collection_ok_rate
        + 0.15 * evidence_count_score
        + 0.10 * hw_score
        + 0.15 * radar_breadth
    )


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
) -> list[ModelRecommendation]:
    """Rank models for *task_description* using capability evidence and hardware fit.

    Returns a list sorted by descending ``score``.  Empty list when no capability
    evidence matches any mapped task kind.
    """
    capabilities = _map_task_to_capabilities(task_description)
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

    recommendations: list[ModelRecommendation] = []
    hw_fit = _assess_hardware_fit(hardware)

    for model_id, info in model_evidence.items():
        records = info["records"]
        all_model_records = store.query_by_model(model_id)
        radar_profile = build_profile(model_id, all_model_records) if all_model_records else None
        score = _compute_score(records, hardware, radar_profile=radar_profile)
        recommendations.append(
            ModelRecommendation(
                model_profile_id=model_id,
                task_kind=info["task_kind"],
                role=info["role"],
                score=score,
                evidence_count=len(records),
                hardware_fit=hw_fit,
                evidence_details=_build_evidence_details(records),
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
