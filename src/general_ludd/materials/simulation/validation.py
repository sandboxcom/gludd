"""Simulation validation and uncertainty quantification (spec MATE-001 §6,
MATE-AT-008).

Implements the three validation primitives required by phase MATE-P4 / §6:

  - :func:`validate_against_experiment` — compare simulation outputs against an
    experimental dataset, compute per-point prediction error, and surface
    outliers WITHOUT hiding them (MATE-AT-008: "prediction error and
    uncertainty bounds SHALL be reported without hiding outliers").
  - :func:`uncertainty_bounds` — propagate a list of input uncertainties into a
    single output bound via worst-case (linear sum) or RSS (statistical)
    stacking.
  - :func:`sensitivity_analysis` — vary a decision-driving input across its
    declared uncertainty range and check whether candidate rank changes
    deterministically (MATE-AT-008: "Changing a decision-driving input across
    its uncertainty range SHALL update candidate rank and prescribed tests
    deterministically").

Every result is a verdict dict mirroring :mod:`general_ludd.materials.strength`
and :mod:`general_ludd.materials.tolerance`: ``state`` + ``equation_id`` +
``inputs`` (with units) + ``assumptions`` so downstream code can satisfy
MATE-DEC-004 (calculation traceability) and MATE-SAFE-003 (no fabricated
precision). Missing/degenerate inputs return ``state = "fail_closed"`` rather
than a silently-wrong number (MATE-SAFE-006: fail closed).
"""

from __future__ import annotations

import math
import statistics
from typing import Any, TypedDict

STATE_OK = "ok"
STATE_FAIL_CLOSED = "fail_closed"
STATE_OUTLIERS = "outliers_detected"
STATE_RANK_CHANGED = "rank_changed"
STATE_RANK_STABLE = "rank_stable"

_EQ_PREDICTION = "prediction_error: e_i = sim_i - exp_i; rms = sqrt(mean(e_i^2))"
_EQ_RSS = "uncertainty_rss: band = sqrt(sum(u_i^2))"
_EQ_WORST = "uncertainty_worst_case: band = sum(|u_i|)"
_EQ_SENSITIVITY = "sensitivity: sample input across uncertainty_range, re-rank candidates, compare to nominal ranking"


class _ScoredCandidate(TypedDict):
    id: str
    nominal_score: float
    uncertainty: float


def _modified_z_scores(values: list[float]) -> list[float]:
    """Robust modified z-scores based on median absolute deviation (MAD).

    Uses the Iglewicz-Hoaglin formula ``0.6745 * (x - median) / MAD`` so a
    single large outlier cannot mask itself by inflating the sample standard
    deviation (the failure mode of plain z-scores that MATE-AT-008 forbids:
    "without hiding outliers"). Falls back to 0.0 when MAD == 0 (e.g. all
    values identical or sample too small).
    """
    n = len(values)
    if n == 0:
        return []
    med = statistics.median(values)
    abs_dev = [abs(v - med) for v in values]
    mad = statistics.median(abs_dev)
    if mad == 0.0:
        return [0.0 for _ in values]
    return [0.6745 * (v - med) / mad for v in values]


def validate_against_experiment(
    simulation: list[float],
    experiment: list[float],
    *,
    unit: str,
    tolerance: float | None = None,
    outlier_z_threshold: float = 2.5,
) -> dict[str, Any]:
    """Compare a simulation output series against an experimental dataset.

    Computes per-point prediction error ``e_i = sim_i - exp_i`` plus aggregate
    statistics (mean, RMS, max-abs). Outliers are surfaced as a list of dicts
    (never hidden) — a point is an outlier if either its absolute z-score
    exceeds ``outlier_z_threshold`` OR its absolute error exceeds ``tolerance``
    when ``tolerance`` is provided.

    Returns a verdict dict. ``state`` is:

      - ``"fail_closed"`` — length mismatch, empty inputs, or non-numeric values.
      - ``"outliers_detected"`` — at least one outlier (z-score OR tolerance).
      - ``"ok"`` — all points within tolerance and below the z-threshold.

    Satisfies MATE-AT-008 (prediction error + outliers reported).
    """
    if not unit or not str(unit).strip():
        raise ValueError("unit must be a non-empty string")
    if len(simulation) != len(experiment):
        return {
            "state": STATE_FAIL_CLOSED,
            "n_points": 0,
            "unit": unit,
            "mean_error": 0.0,
            "rms_error": 0.0,
            "max_abs_error": 0.0,
            "per_point_errors": [],
            "outliers": [],
            "equation_id": _EQ_PREDICTION,
            "inputs": {"n_sim": len(simulation), "n_exp": len(experiment), "nominal_tolerance": tolerance},
            "assumptions": ["length mismatch — datasets cannot be paired"],
        }
    if not simulation:
        return {
            "state": STATE_FAIL_CLOSED,
            "n_points": 0,
            "unit": unit,
            "mean_error": 0.0,
            "rms_error": 0.0,
            "max_abs_error": 0.0,
            "per_point_errors": [],
            "outliers": [],
            "equation_id": _EQ_PREDICTION,
            "inputs": {"n_sim": 0, "n_exp": 0, "nominal_tolerance": tolerance},
            "assumptions": ["empty datasets — no points to validate"],
        }

    try:
        sim_f = [float(v) for v in simulation]
        exp_f = [float(v) for v in experiment]
    except (TypeError, ValueError):
        return {
            "state": STATE_FAIL_CLOSED,
            "n_points": 0,
            "unit": unit,
            "mean_error": 0.0,
            "rms_error": 0.0,
            "max_abs_error": 0.0,
            "per_point_errors": [],
            "outliers": [],
            "equation_id": _EQ_PREDICTION,
            "inputs": {"nominal_tolerance": tolerance},
            "assumptions": ["non-numeric input — cannot compute prediction error"],
        }

    errors = [s - e for s, e in zip(sim_f, exp_f, strict=True)]
    abs_errors = [abs(e) for e in errors]
    z = _modified_z_scores(errors)
    n = len(errors)

    outliers: list[dict[str, Any]] = []
    for i, (e, ae, zi) in enumerate(zip(errors, abs_errors, z, strict=True)):
        over_z = abs(zi) > outlier_z_threshold
        over_tol = tolerance is not None and ae > tolerance
        if over_z or over_tol:
            outliers.append(
                {
                    "index": i,
                    "sim": sim_f[i],
                    "exp": exp_f[i],
                    "error": e,
                    "abs_error": ae,
                    "z_score": zi,
                    "reason": "z_threshold"
                    if over_z and not over_tol
                    else ("tolerance" if over_tol and not over_z else "both"),
                }
            )

    state = STATE_OUTLIERS if outliers else STATE_OK
    return {
        "state": state,
        "n_points": n,
        "unit": unit,
        "mean_error": statistics.fmean(errors),
        "rms_error": math.sqrt(statistics.fmean(e * e for e in errors)),
        "max_abs_error": max(abs_errors),
        "per_point_errors": errors,
        "outliers": outliers,
        "equation_id": _EQ_PREDICTION,
        "inputs": {
            "n_sim": n,
            "n_exp": n,
            "nominal_tolerance": tolerance,
            "outlier_z_threshold": outlier_z_threshold,
        },
        "assumptions": [
            "errors are paired sim minus exp at the same condition",
            "outlier z-score is the Iglewicz-Hoaglin modified z (MAD-based) so a "
            "single outlier cannot mask itself by inflating the std",
        ],
    }


def uncertainty_bounds(
    nominal: float,
    uncertainties: list[float],
    *,
    method: str = "rss",
    unit: str = "",
) -> dict[str, Any]:
    """Propagate a list of input uncertainties to a single output bound.

    ``method`` selects the stacking rule:

      - ``"rss"`` (default): ``band = sqrt(sum(u_i^2))`` — 1-sigma statistical,
        assumes independent inputs.
      - ``"worst_case"``: ``band = sum(|u_i|)`` — linear, conservative.

    Returns ``state = "fail_closed"`` if any uncertainty is negative or
    non-finite, or if ``method`` is unrecognized. The band collapses to 0 in
    that case so downstream code never sees a silently-widened interval.

    Satisfies MATE-SAFE-003 (no fabricated precision) — propagation method is
    explicit in ``equation_id`` and ``assumptions``.
    """
    if not unit:
        unit = ""
    if method not in ("rss", "worst_case"):
        return {
            "state": STATE_FAIL_CLOSED,
            "nominal": float(nominal),
            "upper": float(nominal),
            "lower": float(nominal),
            "band": 0.0,
            "unit": unit,
            "method": method,
            "equation_id": _EQ_RSS if method == "rss" else _EQ_WORST,
            "inputs": {"uncertainties": list(uncertainties)},
            "assumptions": [f"unknown method '{method}' — fail closed"],
        }
    try:
        u_list = [float(u) for u in uncertainties]
    except (TypeError, ValueError):
        return {
            "state": STATE_FAIL_CLOSED,
            "nominal": float(nominal),
            "upper": float(nominal),
            "lower": float(nominal),
            "band": 0.0,
            "unit": unit,
            "method": method,
            "equation_id": _EQ_RSS if method == "rss" else _EQ_WORST,
            "inputs": {"uncertainties": list(uncertainties)},
            "assumptions": ["non-numeric uncertainty — fail closed"],
        }
    if any((u < 0.0 or not math.isfinite(u)) for u in u_list):
        return {
            "state": STATE_FAIL_CLOSED,
            "nominal": float(nominal),
            "upper": float(nominal),
            "lower": float(nominal),
            "band": 0.0,
            "unit": unit,
            "method": method,
            "equation_id": _EQ_RSS if method == "rss" else _EQ_WORST,
            "inputs": {"uncertainties": u_list},
            "assumptions": ["negative or non-finite uncertainty — fail closed"],
        }

    if method == "rss":
        band = math.sqrt(sum(u * u for u in u_list))
        eq = _EQ_RSS
        assumptions = [
            "inputs independent",
            "1-sigma statistical coverage (extend with k-factor for higher confidence)",
        ]
    else:
        band = sum(abs(u) for u in u_list)
        eq = _EQ_WORST
        assumptions = ["inputs may be correlated / worst-case linear stack"]

    nominal_f = float(nominal)
    return {
        "state": STATE_OK,
        "nominal": nominal_f,
        "upper": nominal_f + band,
        "lower": nominal_f - band,
        "band": band,
        "unit": unit,
        "method": method,
        "equation_id": eq,
        "inputs": {"uncertainties": u_list},
        "assumptions": assumptions,
    }


def sensitivity_analysis(
    candidates: list[dict[str, Any]],
    *,
    varying_input: str,
    uncertainty_range: tuple[float, float],
    n_samples: int = 5,
) -> dict[str, Any]:
    """Vary a decision-driving input across its uncertainty range and check
    whether candidate rank changes deterministically.

    Each ``candidate`` is a dict with ``id``, ``nominal_score`` (higher is
    better), and ``uncertainty`` (symmetric +/- on the score).

    Samples ``n_samples`` points across ``uncertainty_range`` (a fraction of
    each candidate's uncertainty, ``0.0 = nominal``, ``1.0 = full
    ±uncertainty``) and re-ranks candidates at each sample using the
    conservative lower bound ``nominal - fraction * uncertainty``.

    Returns ``state = "rank_changed"`` if any sampled ranking differs from the
    nominal ranking, else ``"rank_stable"``. Rankings are lists of candidate
    ids in descending-score order.

    Determinism: the sampling grid is uniform and order-independent; calling
    the function twice with the same arguments returns identical output
    (MATE-AT-008: "deterministically").

    Satisfies MATE-DEC-002 §4 ("rank surviving candidates under at least
    nominal, conservative, and sensitivity cases") and MATE-AT-008.
    """
    lo, hi = float(uncertainty_range[0]), float(uncertainty_range[1])
    if n_samples < 1:
        n_samples = 1
    if hi < lo:
        lo, hi = hi, lo

    # Defensive copy so caller dicts are not mutated.
    cands: list[_ScoredCandidate] = [
        {
            "id": str(c["id"]),
            "nominal_score": float(c["nominal_score"]),
            "uncertainty": float(c.get("uncertainty", 0.0)),
        }
        for c in candidates
    ]

    def rank_at(fraction: float) -> list[str]:
        # Conservative sensitivity sweep: at fraction f, each candidate is
        # evaluated at its LOWER bound (nominal - f * uncertainty). This is the
        # MATE-DEC-002 §4 "conservative case" — if the rank changes when every
        # candidate sags to its low end, the nominal leader is fragile to
        # uncertainty and the decision must be flagged.
        scored = []
        for c in cands:
            score = c["nominal_score"] - fraction * c["uncertainty"]
            scored.append((c["id"], score))
        # Stable descending sort: ties broken by original order (deterministic).
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return [cid for cid, _ in scored]

    nominal_ranking = sorted(
        [(c["id"], c["nominal_score"]) for c in cands],
        key=lambda kv: kv[1],
        reverse=True,
    )
    nominal_ranking_ids = [cid for cid, _ in nominal_ranking]

    if n_samples == 1:
        fractions = [(lo + hi) / 2.0]
    else:
        step = (hi - lo) / (n_samples - 1)
        fractions = [lo + step * k for k in range(n_samples)]

    sampled_rankings = [rank_at(f) for f in fractions]
    rank_changed = any(r != nominal_ranking_ids for r in sampled_rankings)

    return {
        "state": STATE_RANK_CHANGED if rank_changed else STATE_RANK_STABLE,
        "nominal_ranking": nominal_ranking_ids,
        "sampled_rankings": sampled_rankings,
        "n_samples": len(sampled_rankings),
        "varying_input": varying_input,
        "uncertainty_range": (lo, hi),
        "equation_id": _EQ_SENSITIVITY,
        "inputs": {
            "candidates": cands,
            "n_samples": n_samples,
        },
        "assumptions": [
            "higher nominal_score is better",
            "uncertainty is symmetric on nominal_score",
            "sampling grid is uniform across uncertainty_range",
            "at fraction f each candidate is scored at nominal - f*uncertainty "
            "(conservative lower-bound sweep per MATE-DEC-002 §4)",
        ],
    }
