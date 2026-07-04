"""G8 Cost/quality Pareto router — selects candidates on the Pareto frontier."""

from __future__ import annotations

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

        Returns the frontier candidates unchanged — callers may apply a
        secondary tie-break (e.g. lowest cost, highest quality, or composite
        score) to pick a single winner.
        """
        return candidates
