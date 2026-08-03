"""MT-Bench 8-axis radar/spider chart capability profiling for small models.

Generates per-model radar profiles from CapabilityEvidence, renders
SVG spider charts, and provides model-comparison utilities.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from general_ludd.routing_roles.small_model_policy import (
    CapabilityEvidence,
)

_MT_BENCH_AXES: tuple[str, ...] = (
    "writing",
    "roleplay",
    "extraction",
    "reasoning",
    "math",
    "coding",
    "stem",
    "humanities",
)

_AXIS_LABELS: dict[str, str] = {
    "writing": "Writing",
    "roleplay": "Roleplay",
    "extraction": "Extraction",
    "reasoning": "Reasoning",
    "math": "Math",
    "coding": "Coding",
    "stem": "STEM",
    "humanities": "Humanities",
}

_TASK_TO_AXIS: dict[str, str] = {
    "documentation_draft": "writing",
    "format_normalization": "extraction",
    "schema_extraction": "extraction",
    "context_compaction": "extraction",
    "bounded_enumeration": "reasoning",
    "failure_classification": "reasoning",
    "coding": "coding",
    "math": "math",
    "reasoning": "reasoning",
    "stem": "stem",
    "humanities": "humanities",
    "writing": "writing",
    "roleplay": "roleplay",
}


def _map_task_kind_to_axis(task_kind: str) -> str | None:
    """Map a task_kind string to an MT-Bench axis, or None if unknown."""
    if not task_kind or not isinstance(task_kind, str):
        return None
    if task_kind in _TASK_TO_AXIS:
        return _TASK_TO_AXIS[task_kind]
    lowered = task_kind.lower()
    for axis in _MT_BENCH_AXES:
        if axis in lowered:
            return axis
    return None


class _ScoresDict(dict):
    """Dict that clamps values to [0.0, 1.0] on setitem."""

    def __setitem__(self, key: str, value: float) -> None:
        if key not in _MT_BENCH_AXES:
            raise KeyError(key)
        super().__setitem__(key, max(0.0, min(1.0, float(value))))


@dataclass
class ModelRadarProfile:
    """Per-model radar chart data across the 8 MT-Bench capability axes.

    Scores range 0.0-1.0 and are clamped on assignment via __setitem__
    on the scores dict-like interface through dataclass field validation.
    """

    model_profile_id: str
    _scores: dict[str, float] = field(default_factory=dict, repr=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.model_profile_id, str) or not self.model_profile_id.strip():
            raise ValueError("model_profile_id must be a non-empty string")
        self._scores = _ScoresDict({axis: 0.0 for axis in _MT_BENCH_AXES})

    @property
    def scores(self) -> dict[str, float]:
        """Axis→score mapping.  Setting a value clamps it to [0.0, 1.0]."""
        return self._scores

    @scores.setter
    def scores(self, value: dict[str, float]) -> None:
        valid = {axis: max(0.0, min(1.0, float(value.get(axis, 0.0)))) for axis in _MT_BENCH_AXES}
        object.__setattr__(self, "_scores", valid)

    def normalized(self) -> dict[str, float]:
        """Return a normalized 0.0-1.0 copy of scores."""
        return {axis: max(0.0, min(1.0, self._scores.get(axis, 0.0))) for axis in _MT_BENCH_AXES}

    def active_axes(self) -> list[str]:
        """Return axis names with non-zero scores."""
        return [axis for axis in _MT_BENCH_AXES if self._scores.get(axis, 0.0) > 0.0]


def generate_radar(evidences: list[CapabilityEvidence]) -> ModelRadarProfile:
    """Generate a radar profile by aggregating CapabilityEvidence across MT-Bench axes.

    Per-axis scores are the mean of passed_cases/total_cases ratios
    for all evidence items mapping to that axis.
    """
    if not evidences:
        return ModelRadarProfile(model_profile_id="unknown")

    model_id = str(evidences[0].model_profile_id)
    axis_sums: dict[str, float] = {a: 0.0 for a in _MT_BENCH_AXES}
    axis_counts: dict[str, int] = {a: 0 for a in _MT_BENCH_AXES}

    for ev in evidences:
        axis = _map_task_kind_to_axis(ev.task_kind)
        if axis is None or ev.total_cases <= 0:
            continue
        ratio = ev.passed_cases / ev.total_cases
        clamped = max(0.0, min(1.0, ratio))
        axis_sums[axis] += clamped
        axis_counts[axis] += 1

    profile = ModelRadarProfile(model_profile_id=model_id)
    scores: dict[str, float] = {}
    for axis in _MT_BENCH_AXES:
        if axis_counts[axis] > 0:
            scores[axis] = axis_sums[axis] / axis_counts[axis]
        else:
            scores[axis] = 0.0
    profile.scores = scores
    return profile


def render_radar_svg(
    profile: ModelRadarProfile,
    width: int = 400,
    height: int = 400,
) -> str:
    """Render a spider/radar chart as an SVG string.  Pure Python, zero dependencies."""

    scores = profile.normalized()
    cx = width / 2.0
    cy = height / 2.0
    radius = min(width, height) / 2.0 - 50.0
    n = len(_MT_BENCH_AXES)
    angle_step = 2.0 * math.pi / n

    parts: list[str] = []

    parts.append(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
    )
    parts.append(f'<rect width="{width}" height="{height}" fill="#1a1a2e"/>')

    grid_angles = [angle_step * i - math.pi / 2.0 for i in range(n)]
    grid_colors = ["#2d2d44", "#3d3d54", "#4d4d64"]

    for level_idx in range(1, 4):
        r = radius * level_idx / 3.0
        pts: list[str] = []
        for angle in grid_angles:
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            pts.append(f"{x:.1f},{y:.1f}")
        color = grid_colors[level_idx - 1]
        parts.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1"/>')

    for angle in grid_angles:
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        parts.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="#4d4d64" stroke-width="1"/>')

    data_pts: list[str] = []
    for i, axis in enumerate(_MT_BENCH_AXES):
        angle = angle_step * i - math.pi / 2.0
        val = scores.get(axis, 0.0)
        r = radius * val
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        data_pts.append(f"{x:.1f},{y:.1f}")

    parts.append(
        f'<polygon points="{" ".join(data_pts)}" fill="#e94560" fill-opacity="0.35" stroke="#e94560" stroke-width="2"/>'
    )

    for i, axis in enumerate(_MT_BENCH_AXES):
        angle = angle_step * i - math.pi / 2.0
        val = scores.get(axis, 0.0)
        r = radius * val
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#ff6b6b" stroke="#fff" stroke-width="1"/>')

    for i, axis in enumerate(_MT_BENCH_AXES):
        angle = angle_step * i - math.pi / 2.0
        label_r = radius + 22.0
        lx = cx + label_r * math.cos(angle)
        ly = cy + label_r * math.sin(angle) + 4.0
        label = _AXIS_LABELS.get(axis, axis)
        score = scores.get(axis, 0.0)
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#a0a0b0" '
            f'font-family="monospace" font-size="10" text-anchor="middle">{label} ({score:.2f})</text>'
        )

    parts.append(
        f'<text x="{cx:.1f}" y="{height - 8:.1f}" fill="#606070" '
        f'font-family="monospace" font-size="9" text-anchor="middle">'
        f"{profile.model_profile_id}</text>"
    )

    parts.append("</svg>")
    return "\n".join(parts)


def compare_models(profiles: list[ModelRadarProfile]) -> dict[str, Any]:
    """Compare multiple models, returning normalized comparison data.

    Returns a dict with keys:
    - profiles: model_id → scores dict
    - mean: axis → mean score across all models
    - ranking: model_ids sorted by descending mean score
    - winner: top-ranked model_id or None
    """
    if not profiles:
        return {
            "profiles": {},
            "mean": {},
            "ranking": [],
            "winner": None,
        }

    profile_scores: dict[str, dict[str, float]] = {}
    counts = len(profiles)

    mean_scores: dict[str, float] = {axis: 0.0 for axis in _MT_BENCH_AXES}

    for p in profiles:
        profile_scores[p.model_profile_id] = p.normalized()
        for axis in _MT_BENCH_AXES:
            mean_scores[axis] += profile_scores[p.model_profile_id][axis] / counts

    ranked = sorted(
        profiles,
        key=lambda p: sum(p.normalized().values()),
        reverse=True,
    )
    ranking = [p.model_profile_id for p in ranked]

    return {
        "profiles": profile_scores,
        "mean": mean_scores,
        "ranking": ranking,
        "winner": ranking[0] if ranking else None,
    }


def best_for_task(
    profiles: list[ModelRadarProfile],
    task_category: str,
) -> ModelRadarProfile | None:
    """Return the profile with the highest score for *task_category*.

    *task_category* is case-insensitive and must be one of the 8 MT-Bench axes.
    """
    if not profiles:
        return None

    lowered = task_category.lower().strip()
    if lowered not in _MT_BENCH_AXES:
        raise ValueError(f"Unknown task category {task_category!r}. Must be one of: {', '.join(_MT_BENCH_AXES)}")

    best = None
    best_score = -1.0
    for p in profiles:
        score = p.scores.get(lowered, 0.0)
        if score > best_score:
            best_score = score
            best = p
    return best


__all__ = [
    "_MT_BENCH_AXES",
    "ModelRadarProfile",
    "best_for_task",
    "compare_models",
    "generate_radar",
    "render_radar_svg",
]
