"""Pure cost/quality routing-score metric (the W$ scoring formula).

Extracted from ``AdaptiveRouter._cost_adjusted_rank`` (C-05) so the ranking
formula lives in one pure, side-effect-free, easily testable place. The router
delegates here; the formula is not duplicated.

The metric is a per-task-role reward: a quality term rewarded by the role's
``quality`` weight, minus a normalized-cost penalty scaled by the role's
``cost`` weight. It is an INTERNAL RANKING key only — it never mutates a
candidate's ``composite_score``.
"""

from __future__ import annotations

import math

from general_ludd.routing_roles.weights import RoleWeights


def normalize_cost(cost: float, max_cost: float) -> float:
    """Cost normalized into [0, 1] against the candidate-set ``max_cost``.

    A non-finite ``cost`` (NaN/inf) or a non-positive ``max_cost`` yields 0.0 so
    the cost penalty never poisons the rank with a non-finite value — matching
    the original inline behavior.
    """
    if max_cost > 0 and math.isfinite(cost):
        return cost / max_cost
    return 0.0


def score(cost: float, quality: float, weights: RoleWeights, max_cost: float) -> float:
    """The W$ routing score: ``weights.quality * quality - weights.cost * cost_norm``.

    Pure function. ``cost`` is normalized against ``max_cost`` (the largest cost
    in the candidate set) before the penalty is applied. Higher is better.
    """
    cost_norm = normalize_cost(cost, max_cost)
    return weights.quality * quality - weights.cost * cost_norm
