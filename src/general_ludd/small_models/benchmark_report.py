"""Benchmark reporting dashboard — aggregate per-model scores, radar comparison, and cost analysis.

Provides ``generate_report()`` which loads capability evidence from a store, builds radar
profiles, computes per-axis winners, and assembles a ``BenchmarkReport`` with optional SVG renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from general_ludd.small_models.cost import (
    estimate_download_cost,
    estimate_inference_cost,
)
from general_ludd.small_models.radar_profile import (
    _MT_BENCH_AXES,
    ModelRadarProfile,
    compare_models,
    generate_radar,
    render_radar_svg,
)


@dataclass(frozen=True)
class BenchmarkReport:
    """Aggregate benchmark report for one or more models.

    Attributes:
        models: Ordered list of model IDs included in the report.
        per_model_scores: ``model_id → {axis: score}`` mapping.
        radar_comparison: Output of ``compare_models()`` (profiles, mean, ranking, winner).
        cost_analysis: ``model_id → dict`` with inference/download cost breakdowns.
        best_per_axis: Per-axis best-performing model ID (or None).
        overall_winner: Top-ranked model by sum of axis scores, or None.
        radar_svgs: ``model_id → SVG`` strings (populated when ``include_svg=True``).
    """

    models: list[str]
    per_model_scores: dict[str, dict[str, float]]
    radar_comparison: dict[str, object]
    cost_analysis: dict[str, dict[str, object]]
    best_per_axis: dict[str, str | None]
    overall_winner: str | None
    radar_svgs: dict[str, str] = field(default_factory=dict)


def generate_report(
    model_ids: list[str],
    store: object,
    *,
    include_svg: bool = False,
) -> BenchmarkReport:
    """Generate a benchmark report for *model_ids* using evidence from *store*.

    *store* may be a ``CapabilityEvidenceStore`` or a plain ``dict[str, list[dict]]``
    keyed by ``cap:<model_id>`` (the daemon's in-memory store format).

    Args:
        model_ids: Non-empty list of model profile IDs to include.
        store: Evidence source — either a ``CapabilityEvidenceStore`` or a dict.
        include_svg: When True, populate ``radar_svgs`` with per-model SVG radar charts.

    Returns:
        A populated ``BenchmarkReport``.

    Raises:
        ValueError: If *model_ids* is empty.
    """
    if not model_ids:
        raise ValueError("model_ids must be a non-empty list")

    cleaned: list[str] = [str(m).strip() for m in model_ids]

    profiles: list[ModelRadarProfile] = []
    per_model_scores: dict[str, dict[str, float]] = {}
    cost_analysis: dict[str, dict[str, object]] = {}
    radar_svgs: dict[str, str] = {}

    for model_id in cleaned:
        evidence_dicts = _query_evidence(store, model_id)
        profile = _build_profile_from_evidence(model_id, evidence_dicts)
        profiles.append(profile)
        normalized = dict(profile.normalized())
        per_model_scores[model_id] = normalized if any(v > 0.0 for v in normalized.values()) else {}
        cost_analysis[model_id] = _build_cost_analysis(model_id)
        if include_svg:
            radar_svgs[model_id] = render_radar_svg(profile)

    comparison = compare_models(profiles) if profiles else _empty_comparison()

    best_per_axis = _compute_best_per_axis(profiles, cleaned)

    ranking = comparison.get("ranking", [])
    overall_winner: str | None = None
    if isinstance(ranking, list) and ranking:
        candidate = str(ranking[0])
        candidate_scores = per_model_scores.get(candidate, {})
        if candidate_scores and any(v > 0.0 for v in candidate_scores.values()):
            overall_winner = candidate

    return BenchmarkReport(
        models=cleaned,
        per_model_scores=per_model_scores,
        radar_comparison=comparison,
        cost_analysis=cost_analysis,
        best_per_axis=best_per_axis,
        overall_winner=overall_winner,
        radar_svgs=radar_svgs,
    )


def render_report(report: BenchmarkReport) -> dict[str, object]:
    """Serialize a ``BenchmarkReport`` to a JSON-safe dict.

    This is the API response shape used by the daemon endpoint and CLI.
    """
    return {
        "models": report.models,
        "per_model_scores": report.per_model_scores,
        "radar_comparison": report.radar_comparison,
        "cost_analysis": report.cost_analysis,
        "best_per_axis": report.best_per_axis,
        "overall_winner": report.overall_winner,
        "radar_svgs": report.radar_svgs if report.radar_svgs else None,
    }


# ── helpers ────────────────────────────────────────────────────────


def _query_evidence(store: object, model_id: str) -> list[dict[str, Any]]:
    """Query evidence for *model_id* from a store or dict."""
    if hasattr(store, "query_by_model"):
        from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

        if isinstance(store, CapabilityEvidenceStore):
            return store.query_by_model(model_id)
    if isinstance(store, dict):
        return [dict(r) for r in store.get(f"cap:{model_id}", [])]
    return []


def _build_profile_from_evidence(
    model_id: str,
    evidence_dicts: list[dict[str, Any]],
) -> ModelRadarProfile:
    """Build a ``ModelRadarProfile`` from raw evidence dictionaries."""
    from general_ludd.routing_roles.small_model_policy import CapabilityEvidence

    if not evidence_dicts:
        return ModelRadarProfile(model_profile_id=model_id)

    evidence_objects: list[CapabilityEvidence] = []
    for ed in evidence_dicts:
        try:
            converted = dict(ed)
            converted.pop("registered_at", None)
            role_val = converted.get("role")
            if isinstance(role_val, str):
                from general_ludd.schemas.benchmark import TaskRole

                converted["role"] = TaskRole(role_val)
            evidence_objects.append(CapabilityEvidence(**converted))
        except (TypeError, ValueError):
            continue

    if not evidence_objects:
        return ModelRadarProfile(model_profile_id=model_id)

    return generate_radar(evidence_objects)


def _build_cost_analysis(model_id: str) -> dict[str, object]:
    """Build a cost-analysis dict for a model."""
    inference = estimate_inference_cost(model_id)
    download = estimate_download_cost(model_id)

    return {
        "model_id": model_id,
        "tier": inference.get("tier", "small_local"),
        "inference": {
            "input_usd_per_1m_tokens": inference.get("input_usd_per_1m_tokens", 0.0),
            "output_usd_per_1m_tokens": inference.get("output_usd_per_1m_tokens", 0.0),
            "estimated_usd_per_hour": inference.get("estimated_usd_per_hour", 0.0),
            "estimated_tokens_per_hour": inference.get("estimated_tokens_per_hour", 0),
        },
        "download": {
            "size_gb": download.get("size_gb", 0.0),
            "data_transfer_usd": download.get("data_transfer_usd", 0.0),
            "estimated_storage_usd_per_month": download.get("estimated_storage_usd_per_month", 0.0),
        },
        "estimated_usd_per_hour": inference.get("estimated_usd_per_hour", 0.0),
    }


def _compute_best_per_axis(
    profiles: list[ModelRadarProfile],
    model_ids: list[str],
) -> dict[str, str | None]:
    """Compute the best model per axis based on normalized scores."""
    result: dict[str, str | None] = {}

    for axis in _MT_BENCH_AXES:
        best_model: str | None = None
        best_score: float = -1.0
        for profile in profiles:
            score = profile.normalized().get(axis, 0.0)
            if score > best_score:
                best_score = score
                best_model = profile.model_profile_id
        result[axis] = best_model if best_score > 0.0 else None

    return result


def _empty_comparison() -> dict[str, object]:
    return {
        "profiles": {},
        "mean": {},
        "ranking": [],
        "winner": None,
    }


__all__ = [
    "BenchmarkReport",
    "generate_report",
    "render_report",
]
