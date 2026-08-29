"""Physics-collection information-theoretic entropy metrics.

Uses scipy.stats.entropy for base Shannon-entropy computation. All functions
operate on probability dictionaries mapping outcome -> probability.  Logarithms
are base-2 by default (bits); pass `base` to use nats (base=math.e) or dits
(base=10).
"""

from __future__ import annotations

import math
from typing import TypeVar

import numpy as np
from scipy.stats import entropy as _scipy_entropy

T = TypeVar("T")


def _validate_distribution(dist: dict[T, float]) -> None:
    total = sum(dist.values())
    if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError(f"probabilities must sum to 1.0, got {total}")
    for val in dist.values():
        if val < 0.0:
            raise ValueError(f"probabilities must be non-negative, got {val}")
        if val > 1.0 + 1e-12:
            raise ValueError(f"probabilities must be <= 1.0, got {val}")


def _log(x: float, base: float) -> float:
    return math.log(x) / math.log(base)


def shannon_entropy(dist: dict[T, float], *, base: float = 2.0) -> float:
    """H(X) = -Sum p(x) log p(x)."""
    _validate_distribution(dist)
    values = np.array(list(dist.values()), dtype=np.float64)
    return float(_scipy_entropy(values, base=base))


def joint_entropy(joint: dict[tuple[T, T], float], *, base: float = 2.0) -> float:
    """H(X,Y) = -Sum p(x,y) log p(x,y)."""
    return shannon_entropy(joint, base=base)


def marginal_from_joint(joint: dict[tuple[T, T], float], *, axis: int = 0) -> dict[T, float]:
    """Recover marginal distribution p(x) or p(y) from a joint p(x,y)."""
    result: dict[T, float] = {}
    for (x, y), p in joint.items():
        key = x if axis == 0 else y
        result[key] = result.get(key, 0.0) + p
    return result


def conditional_entropy(
    joint: dict[tuple[T, T], float],
    *,
    base: float = 2.0,
) -> float:
    """H(X|Y) = H(X,Y) - H(Y) = -Sum p(x,y) log(p(x,y) / p(y))."""
    _validate_distribution(joint)
    marginal_y = marginal_from_joint(joint, axis=1)
    total = 0.0
    for (_x, y), pxy in joint.items():
        if pxy > 0.0:
            py = marginal_y[y]
            if py > 0.0:
                total -= pxy * _log(pxy / py, base)
    return total


def mutual_information(
    joint: dict[tuple[T, T], float],
    *,
    base: float = 2.0,
) -> float:
    """I(X;Y) = Sum p(x,y) log(p(x,y) / (p(x)p(y)))."""
    _validate_distribution(joint)
    px = marginal_from_joint(joint, axis=0)
    py = marginal_from_joint(joint, axis=1)
    total = 0.0
    for (x, y), pxy in joint.items():
        if pxy > 0.0:
            pxv = px.get(x, 0.0)
            pyv = py.get(y, 0.0)
            if pxv > 0.0 and pyv > 0.0:
                total += pxy * _log(pxy / (pxv * pyv), base)
    return total


def kl_divergence(
    p: dict[T, float],
    q: dict[T, float],
    *,
    base: float = 2.0,
    epsilon: float = 1e-12,
) -> float:
    """D_KL(P||Q) = Sum P(x) log(P(x) / Q(x)).

    *epsilon* replaces zero entries in *q* to avoid log(0).  The caller is
    responsible for ensuring the support of Q contains the support of P.
    """
    _validate_distribution(p)
    total = 0.0
    all_keys = set(p.keys()) | set(q.keys())
    for k in all_keys:
        pk = p.get(k, 0.0)
        if pk <= 0.0:
            continue
        qk = q.get(k, 0.0)
        if qk <= 0.0:
            qk = epsilon
        total += pk * _log(pk / qk, base)
    return total


def cross_entropy(
    p: dict[T, float],
    q: dict[T, float],
    *,
    base: float = 2.0,
    epsilon: float = 1e-12,
) -> float:
    """H(P,Q) = -Sum P(x) log Q(x) = H(P) + D_KL(P||Q)."""
    _validate_distribution(p)
    total = 0.0
    for k, pk in p.items():
        if pk <= 0.0:
            continue
        qk = q.get(k, 0.0)
        if qk <= 0.0:
            qk = epsilon
        total -= pk * _log(qk, base)
    return total


def build_joint_from_counts(
    counts: dict[tuple[T, T], int],
) -> dict[tuple[T, T], float]:
    """Normalise count dictionary to a joint probability distribution."""
    total = sum(counts.values())
    if total == 0:
        raise ValueError("total count must be > 0")
    return {k: v / total for k, v in counts.items()}


def distribution_from_counts(counts: dict[T, int]) -> dict[T, float]:
    """Normalise count dictionary to a probability distribution."""
    total = sum(counts.values())
    if total == 0:
        raise ValueError("total count must be > 0")
    return {k: v / total for k, v in counts.items()}


def entropy_from_counts(counts: dict[T, int], *, base: float = 2.0) -> float:
    """Convenience: H(X) computed directly from count data."""
    return shannon_entropy(distribution_from_counts(counts), base=base)
