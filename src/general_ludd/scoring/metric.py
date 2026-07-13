"""W$ cost-adjusted scoring metric.

Formula (D.20): ``W$ = W / log10(1 + median_$/Mtok)``

Where ``W`` is the raw composite score and ``median_$/Mtok`` is the provider's
median cost per million tokens.  The logarithmic denominator deflates the raw
score for expensive providers while preserving it for cheap ones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class MetricConfig:
    """Parameters for the W$ formula.

    Attributes:
        log_base: Base of the logarithm used to penalize cost (default 10).
        offset: Added to cost before taking the log, so zero-cost providers
            are not penalised (``log10(1+0) == 0`` would make the denominator
            zero — the offset avoids that division).
        score_floor: Minimum allowed score (clamped from below).
        score_ceiling: Maximum allowed score (clamped from above).
    """

    log_base: float = 10.0
    offset: float = 1.0
    score_floor: float = 0.0
    score_ceiling: float = float("inf")


def compute_w_dollar(
    composite_score: float,
    median_dollars_per_mtok: float,
    *,
    config: MetricConfig | None = None,
) -> float:
    """Return the cost-adjusted score ``W$``.

    ``W$ = W / log_base(offset + median_$/Mtok)``

    Args:
        composite_score: Raw composite score ``W``, expected in [0.0, 1.0].
        median_dollars_per_mtok: Provider's median cost per million tokens.
            Must be ≥ 0.  A value of 0 means the denominator is
            ``log_base(offset)`` (with default offset=1, ``log10(1)=0``, so
            the raw score is returned unchanged).
        config: Tuning parameters.  Defaults to ``MetricConfig()``.

    Returns:
        Cost-adjusted score in ``[score_floor, score_ceiling]``.

    Raises:
        ValueError: If *composite_score* is outside ``[0.0, 1.0]`` or
            *median_dollars_per_mtok* is negative.
    """
    if composite_score < 0.0 or composite_score > 1.0:
        raise ValueError(
            f"composite_score must be in [0.0, 1.0], got {composite_score}"
        )
    if median_dollars_per_mtok < 0.0:
        raise ValueError(
            f"median_dollars_per_mtok must be >= 0, got {median_dollars_per_mtok}"
        )

    cfg = config or MetricConfig()

    argument = cfg.offset + median_dollars_per_mtok
    denom = (
        math.log10(argument)
        if cfg.log_base == 10.0
        else math.log(argument) / math.log(cfg.log_base)
    )

    if denom <= 0.0:
        return composite_score

    w_dollar = composite_score / denom
    return max(cfg.score_floor, min(cfg.score_ceiling, w_dollar))
