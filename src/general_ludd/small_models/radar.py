"""Radar profile — visual fingerprint and comparison for small-model capability evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, cast

from general_ludd.routing_roles.small_model_policy import DEFAULT_TASK_CONTRACTS

_TASK_KINDS = sorted(DEFAULT_TASK_CONTRACTS.keys())
_SVG_NS = "http://www.w3.org/2000/svg"
_DIMENSION_COUNT = len(_TASK_KINDS)
_DIMENSION_LABELS: dict[str, str] = {
    "context_compaction": "Compaction",
    "documentation_draft": "Document",
    "bounded_enumeration": "Enumerate",
    "failure_classification": "Failure Cls",
    "format_normalization": "Format",
    "schema_extraction": "Schema",
}
_DEFAULT_COLORS = [
    "#3b82f6",
    "#ef4444",
    "#10b981",
    "#f59e0b",
    "#8b5cf6",
    "#ec4899",
    "#06b6d4",
    "#84cc16",
]


@dataclass(frozen=True)
class RadarProfile:
    """Capability fingerprint — one score per task kind from 0.0 to 1.0."""

    model_id: str
    scores: dict[str, float]
    evidence_counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in _TASK_KINDS:
            if key not in self.scores:
                object.__setattr__(self, "scores", {**self.scores, key: 0.0})
            score = self.scores[key]
            if not isinstance(score, (int, float)) or not (0.0 <= score <= 1.0):
                raise ValueError(f"score for {key!r} must be 0.0-1.0, got {score!r}")
        for task_kind in self.evidence_counts:
            if task_kind not in _TASK_KINDS:
                raise ValueError(f"unknown task_kind {task_kind!r} in evidence_counts")
            cnt = self.evidence_counts[task_kind]
            if isinstance(cnt, bool) or (isinstance(cnt, (int, float)) and cnt < 0):
                raise ValueError(f"evidence_count for {task_kind!r} must be non-negative integer")

    @property
    def dimensions(self) -> list[str]:
        """Return the ordered task-kind dimensions."""
        return list(_TASK_KINDS)

    def vector(self) -> list[float]:
        """Return scores as a list in dimension order."""
        return [self.scores[k] for k in _TASK_KINDS]


def build_profile(
    model_id: str,
    evidence_list: list[dict[str, Any]],
) -> RadarProfile:
    """Aggregate capability evidence into a single radar profile.

    For each task kind, the score is the best pass rate across evidence
    records (taking the highest passed/total ratio).
    """
    task_scores: dict[str, float] = {}
    task_counts: dict[str, int] = {}

    for rec in evidence_list:
        task_kind = str(rec.get("task_kind", ""))
        if task_kind not in _TASK_KINDS:
            continue
        _tv = rec.get("total_cases", 0)
        _pv = rec.get("passed_cases", 0)
        total = int(_tv) if isinstance(_tv, (int, float, str)) else 0
        passed = int(_pv) if isinstance(_pv, (int, float, str)) else 0
        ratio = (passed / total) if total > 0 else 0.0

        task_counts[task_kind] = task_counts.get(task_kind, 0) + 1

        current = task_scores.get(task_kind, -1.0)
        if ratio > current:
            task_scores[task_kind] = ratio

    for key in _TASK_KINDS:
        task_scores.setdefault(key, 0.0)
        task_counts.setdefault(key, 0)

    return RadarProfile(model_id=model_id, scores=task_scores, evidence_counts=task_counts)


def profile_similarity(a: RadarProfile, b: RadarProfile) -> float:
    """Cosine similarity between two radar profiles (0.0 = orthogonal, 1.0 = identical)."""
    av = a.vector()
    bv = b.vector()
    dot = sum(x * y for x, y in zip(av, bv, strict=True))
    na = math.sqrt(sum(x * x for x in av))
    nb = math.sqrt(sum(x * x for x in bv))
    if na == 0.0 and nb == 0.0:
        return 1.0
    if na == 0.0 or nb == 0.0:
        return 0.0
    return round(dot / (na * nb), 4)


def profile_distance(a: RadarProfile, b: RadarProfile) -> float:
    """Normalized Euclidean distance (0.0 = identical, 1.0 = maximally different)."""
    av = a.vector()
    bv = b.vector()
    max_dist = math.sqrt(len(av))
    if max_dist == 0.0:
        return 0.0
    sq_dist = sum((x - y) ** 2 for x, y in zip(av, bv, strict=True))
    return round(math.sqrt(sq_dist) / max_dist, 4)


def radar_svg(
    profiles: list[RadarProfile],
    size: int = 600,
    level_count: int = 5,
) -> str:
    """Generate an SVG radar chart for one or more profiles.

    Returns a valid SVG string suitable for embedding or serving directly.
    """
    if not profiles:
        return _empty_svg(size)

    center = size / 2.0
    radius = size * 0.38
    n = _DIMENSION_COUNT
    angle_step = (2.0 * math.pi) / n
    start_angle = -math.pi / 2.0

    levels = [i / level_count for i in range(1, level_count + 1)]
    circles: list[str] = []
    grid_lines: list[str] = []
    for lvl in levels:
        r = radius * lvl
        points: list[tuple[float, float]] = []
        for i in range(n):
            angle = start_angle + i * angle_step
            px = center + r * math.cos(angle)
            py = center + r * math.sin(angle)
            points.append((round(px, 2), round(py, 2)))
        if lvl == 1.0:
            circles.append(
                f'<polygon points="{" ".join(f"{x},{y}" for x, y in points)}" '
                f'fill="none" stroke="#cbd5e1" stroke-width="1.5"/>'
            )
        else:
            circles.append(
                f'<polygon points="{" ".join(f"{x},{y}" for x, y in points)}" '
                f'fill="none" stroke="#e2e8f0" stroke-width="1"/>'
            )
        for x, y in points:
            grid_lines.append(
                f'<line x1="{center}" y1="{center}" x2="{x}" y2="{y}" stroke="#e2e8f0" stroke-width="0.5"/>'
            )

    axes: list[str] = []
    labels: list[str] = []
    max_label_r = radius * 1.18
    for i in range(n):
        angle = start_angle + i * angle_step
        tip_x = center + radius * math.cos(angle)
        tip_y = center + radius * math.sin(angle)
        axes.append(
            f'<line x1="{center}" y1="{center}" x2="{round(tip_x, 1)}" y2="{round(tip_y, 1)}" '
            f'stroke="#94a3b8" stroke-width="1"/>'
        )
        lx = center + max_label_r * math.cos(angle)
        ly = center + max_label_r * math.sin(angle)
        name = _DIMENSION_LABELS.get(_TASK_KINDS[i], _TASK_KINDS[i])
        anchor = "middle"
        if lx < center - 5:
            anchor = "end"
        elif lx > center + 5:
            anchor = "start"
        labels.append(
            f'<text x="{round(lx, 1)}" y="{round(ly + 3, 1)}" text-anchor="{anchor}" '
            f'fill="#475569" font-size="11" font-family="system-ui, sans-serif">{name}</text>'
        )

    polygons: list[str] = []
    for pi, profile in enumerate(profiles):
        color = _DEFAULT_COLORS[pi % len(_DEFAULT_COLORS)]
        vec = profile.vector()
        poly_points: list[str] = []
        for i, score in enumerate(vec):
            angle = start_angle + i * angle_step
            r = radius * score
            px = center + r * math.cos(angle)
            py = center + r * math.sin(angle)
            poly_points.append(f"{round(px, 1)},{round(py, 1)}")
        ps = " ".join(poly_points)
        polygons.append(
            f'<polygon points="{ps}" fill="{color}" fill-opacity="0.25" stroke="{color}" stroke-width="2"/>'
        )

    legend: list[str] = []
    if len(profiles) > 1:
        legend_x = 16
        legend_y = 16
        for pi, profile in enumerate(profiles):
            color = _DEFAULT_COLORS[pi % len(_DEFAULT_COLORS)]
            ly = legend_y + pi * 22
            legend.append(
                f'<rect x="{legend_x}" y="{ly}" width="14" height="14" fill="{color}" '
                f'fill-opacity="0.35" stroke="{color}" stroke-width="1.5"/>'
            )
            legend.append(
                f'<text x="{legend_x + 20}" y="{ly + 11}" fill="#475569" '
                f'font-size="11" font-family="system-ui, sans-serif">{profile.model_id}</text>'
            )

    parts: list[str] = [
        f'<svg xmlns="{_SVG_NS}" viewBox="0 0 {size} {size}" width="{size}" height="{size}">',
        "<style>.radar-bg{fill:#f8fafc}.radar-label{fill:#475569;font-size:11px;font-family:system-ui,sans-serif}</style>",
        f'<rect width="{size}" height="{size}" class="radar-bg"/>',
        *grid_lines,
        *circles,
        *axes,
        *polygons,
        *labels,
        *legend,
        "</svg>",
    ]
    return "\n".join(parts)


def _empty_svg(size: int) -> str:
    return (
        f'<svg xmlns="{_SVG_NS}" viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
        '<rect width="100%" height="100%" fill="#f8fafc"/>'
        f'<text x="{size / 2}" y="{size / 2}" text-anchor="middle" fill="#94a3b8" '
        f'font-size="14" font-family="system-ui, sans-serif">No profile data</text>'
        "</svg>"
    )


def compare_models(
    model_ids: list[str],
    all_evidence: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build profiles for multiple models and return a ranked comparison.

    Returns a dict with ``profiles``, ``pairwise_similarities``, and
    ``ranking`` (best to worst by average score).
    """
    profiles: dict[str, RadarProfile] = {}
    for mid in model_ids:
        evidence = all_evidence.get(mid, [])
        profiles[mid] = build_profile(mid, evidence)

    pairwise: list[dict[str, Any]] = []
    profile_list = list(profiles.values())
    for i in range(len(profile_list)):
        for j in range(i + 1, len(profile_list)):
            a = profile_list[i]
            b = profile_list[j]
            pairwise.append(
                {
                    "model_a": a.model_id,
                    "model_b": b.model_id,
                    "similarity": profile_similarity(a, b),
                    "distance": profile_distance(a, b),
                }
            )

    ranking = sorted(
        [{"model_id": p.model_id, "average_score": round(sum(p.vector()) / len(p.vector()), 4)} for p in profile_list],
        key=lambda x: cast(float, x["average_score"]),
        reverse=True,
    )

    return {
        "profiles": {mid: {"scores": p.scores, "evidence_counts": p.evidence_counts} for mid, p in profiles.items()},
        "pairwise_similarities": sorted(pairwise, key=lambda x: x["similarity"], reverse=True),
        "ranking": ranking,
    }


__all__ = [
    "RadarProfile",
    "build_profile",
    "compare_models",
    "profile_distance",
    "profile_similarity",
    "radar_svg",
]
