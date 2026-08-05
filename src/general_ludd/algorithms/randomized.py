"""Randomized algorithms: Fisher-Yates shuffle, random permutation,
random subset (reservoir sampling), bogosort check.
"""

from __future__ import annotations

import random


def fisher_yates_shuffle(items: list[int], rng: random.Random | None = None) -> list[int]:
    result = list(items)
    rand = rng if rng is not None else random.Random()
    for i in range(len(result) - 1, 0, -1):
        j = rand.randint(0, i)
        result[i], result[j] = result[j], result[i]
    return result


def random_permutation(n: int, rng: random.Random | None = None) -> list[int]:
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    return fisher_yates_shuffle(list(range(n)), rng=rng)


def random_subset(population: list[int], k: int, rng: random.Random | None = None) -> list[int]:
    if k <= 0 or not population:
        return []
    if k >= len(population):
        return list(population)
    rand = rng if rng is not None else random.Random()
    reservoir = list(population[:k])
    for i in range(k, len(population)):
        j = rand.randint(0, i)
        if j < k:
            reservoir[j] = population[i]
    return reservoir


def bogosort_check(items: list[int]) -> bool:
    return all(items[i] <= items[i + 1] for i in range(len(items) - 1))
