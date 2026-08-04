"""Reservoir sampling, weighted reservoir, systematic sampling, shuffle.

Pure-Python, stdlib only. All samplers operate on iterables (online-capable
where noted) and return a list.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")


def reservoir_sample(stream: Iterable[T], k: int, *, rng: random.Random | None = None) -> list[T]:
    """Reservoir R — uniform sample of *k* items from an iterable in one pass.

    O(n) time, O(k) memory.  When len(stream) < k the full stream is returned.
    """
    if k <= 0:
        return []
    _rng = rng if rng is not None else random.Random()
    reservoir: list[T] = []
    it = iter(stream)
    for _ in range(k):
        try:
            reservoir.append(next(it))
        except StopIteration:
            return reservoir
    i = k
    for item in it:
        j = _rng.randint(0, i)
        if j < k:
            reservoir[j] = item
        i += 1
    return reservoir


def weighted_reservoir_sample(
    stream: Iterable[tuple[T, float]],
    k: int,
    *,
    rng: random.Random | None = None,
) -> list[T]:
    """A-Chao weighted reservoir — each item carries a weight.

    Items = (value, weight) pairs.  Sampling probability proportional to
    weight.  O(n) time, O(k) memory.
    """
    if k <= 0:
        return []
    _rng = rng if rng is not None else random.Random()
    reservoir: list[T] = []
    keys: list[float] = []
    it = iter(stream)
    for _ in range(k):
        try:
            item, weight = next(it)
            reservoir.append(item)
            keys.append(_rng.random() ** (1.0 / weight) if weight > 0 else 0.0)
        except StopIteration:
            return reservoir
    for item, weight in it:
        if weight <= 0:
            continue
        key = _rng.random() ** (1.0 / weight)
        min_idx = min(range(k), key=lambda j: keys[j])
        if key > keys[min_idx]:
            reservoir[min_idx] = item
            keys[min_idx] = key
    return reservoir


def systematic_sample(population: list[T], k: int, *, rng: random.Random | None = None) -> list[T]:
    """Systematic sampling — every N-th item after a random start.

    O(n) time, O(k) memory.  Step = len(population) / k.
    """
    n = len(population)
    if k <= 0 or n == 0:
        return []
    if k >= n:
        return list(population)
    _rng = rng if rng is not None else random.Random()
    step = n / k
    start = _rng.random() * step
    result: list[T] = []
    idx = int(start)
    while idx < n and len(result) < k:
        result.append(population[idx])
        idx += int(step)
    return result


def fisher_yates_shuffle(arr: list[T], *, rng: random.Random | None = None) -> list[T]:
    """Fisher-Yates (Knuth) shuffle — in-place, O(n). Returns the same list."""
    _rng = rng if rng is not None else random.Random()
    for i in range(len(arr) - 1, 0, -1):
        j = int(_rng.random() * (i + 1))
        arr[i], arr[j] = arr[j], arr[i]
    return arr
