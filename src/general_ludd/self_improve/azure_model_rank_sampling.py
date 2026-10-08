"""Bounded popularity-rank sampling for discovered Azure model catalogs."""

from __future__ import annotations

from collections.abc import Sequence

from general_ludd.models.model_registry import ModelSearchResult


def _spread_ranked_results(
    results: Sequence[ModelSearchResult],
    slots: int,
) -> tuple[ModelSearchResult, ...]:
    """Sample the full popularity range without exceeding a hydration budget."""
    if slots <= 0 or not results:
        return ()
    if slots >= len(results):
        return tuple(results)
    if slots == 1:
        return (results[0],)
    last = len(results) - 1
    return tuple(results[(index * last) // (slots - 1)] for index in range(slots))


def bounded_rank_sample(
    publisher_results: Sequence[Sequence[ModelSearchResult]],
    limit: int,
) -> tuple[ModelSearchResult, ...]:
    """Allocate a fixed hydration budget fairly across non-empty publishers."""
    populated = tuple(tuple(results) for results in publisher_results if results)
    if not populated:
        return ()
    base_quota, extra_slots = divmod(limit, len(populated))
    sampled: list[ModelSearchResult] = []
    sampled_ids: set[str] = set()
    for index, results in enumerate(populated):
        quota = base_quota + (1 if index < extra_slots else 0)
        for result in _spread_ranked_results(results, quota):
            if result.model_id not in sampled_ids:
                sampled.append(result)
                sampled_ids.add(result.model_id)
    if len(sampled) < limit:
        for rank in range(max(map(len, populated))):
            for results in populated:
                if rank >= len(results):
                    continue
                result = results[rank]
                if result.model_id in sampled_ids:
                    continue
                sampled.append(result)
                sampled_ids.add(result.model_id)
                if len(sampled) == limit:
                    return tuple(sampled)
    return tuple(sampled[:limit])


__all__ = ["bounded_rank_sample"]
