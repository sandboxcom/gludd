"""G8 Cost/quality Pareto router — selects candidates on the Pareto frontier."""

from __future__ import annotations

import math
from typing import Any


class ParetoRouter:
    """Routes model selections by constructing a cost/quality Pareto frontier.

    Given a set of model candidates each with a cost and quality score,
    the Pareto frontier is the subset where no candidate is strictly better
    on both axes simultaneously. Routing by the frontier avoids picking
    dominated models — ones that are both more expensive AND lower quality
    than another available option.
    """

    def __init__(
        self,
        cost_weight: float = 0.5,
        quality_weight: float = 0.5,
    ) -> None:
        self._cost_weight = cost_weight
        self._quality_weight = quality_weight

    def route_by_pareto_frontier(
        self, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return the subset of *candidates* that lie on the Pareto frontier.

        Each candidate dict must provide ``"cost"`` (float, lower is better)
        and ``"quality"`` (float, higher is better).

        Normalizes both axes to [0,1] internally, identifies non-dominated
        candidates (a candidate is dominated if another has both lower cost AND
        higher quality), and returns frontier candidates in quality-descending
        order.

        Handles edge cases:
        - Empty list → empty list
        - Single candidate → single-element list
        - All-equal candidates → all returned (none dominate each other)
        - NaN/Inf cost or quality → excluded from consideration
        """
        if not candidates:
            return []

        valid: list[tuple[int, float, float]] = []
        for i, c in enumerate(candidates):
            try:
                cost = float(c.get("cost", float("nan")))
                quality = float(c.get("quality", float("nan")))
            except (ValueError, TypeError):
                continue
            if math.isfinite(cost) and math.isfinite(quality):
                valid.append((i, cost, quality))

        if not valid:
            return []
        if len(valid) == 1:
            return [candidates[valid[0][0]]]

        n = len(valid)
        dominated = [False] * n
        for i in range(n):
            if dominated[i]:
                continue
            _, cost_i, quality_i = valid[i]
            for j in range(n):
                if i == j:
                    continue
                _, cost_j, quality_j = valid[j]
                if (
                    cost_j <= cost_i
                    and quality_j >= quality_i
                    and (cost_j < cost_i or quality_j > quality_i)
                ):
                    dominated[i] = True
                    break

        frontier = [
            (valid[i][0], valid[i][2]) for i in range(n) if not dominated[i]
        ]
        frontier.sort(key=lambda x: x[1], reverse=True)
        return [candidates[idx] for idx, _ in frontier]

    def pick_winner(
        self, frontier: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Pick the best candidate from the Pareto frontier using composite score.

        Normalizes cost and quality to [0,1] within the frontier, then computes:
        ``quality_norm * quality_weight - cost_norm * cost_weight``.

        Returns the candidate with the highest composite score, or ``None``
        if the frontier is empty.
        """
        if not frontier:
            return None
        if len(frontier) == 1:
            return frontier[0]

        costs = [float(c["cost"]) for c in frontier]
        qualities = [float(c["quality"]) for c in frontier]
        cost_min = min(costs)
        cost_max = max(costs)
        quality_min = min(qualities)
        quality_max = max(qualities)
        cost_range = cost_max - cost_min
        quality_range = quality_max - quality_min

        best: dict[str, Any] | None = None
        best_score = float("-inf")
        for cand in frontier:
            cost = float(cand["cost"])
            quality = float(cand["quality"])
            cost_norm = (
                (cost - cost_min) / cost_range if cost_range > 0 else 0.0
            )
            quality_norm = (
                (quality - quality_min) / quality_range if quality_range > 0 else 0.0
            )
            score = (
                quality_norm * self._quality_weight
                - cost_norm * self._cost_weight
            )
            if score > best_score:
                best_score = score
                best = cand
        return best
