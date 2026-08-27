"""Security-collection Shamir secret-sharing adapter.

Wraps the ``shamir`` PyPI package for core GF(prime) arithmetic.
Splits a secret into n shares such that any k (threshold) shares recover it,
while any k-1 shares reveal nothing.
"""

from __future__ import annotations

from typing import cast

from shamir import _PRIME as _LIB_PRIME
from shamir import _eval_at as _lib_eval_at
from shamir import _lagrange_interpolate as _lib_lagrange
from shamir import _rint as _lib_rint
from shamir import recover_secret as _lib_recover_secret


class ShamirError(ValueError):
    """Base exception for Shamir secret sharing operations."""


DEFAULT_PRIME: int = _LIB_PRIME


def split(
    secret: int,
    *,
    threshold: int,
    num_shares: int,
    prime: int = DEFAULT_PRIME,
) -> list[tuple[int, int, int]]:
    if threshold < 1 or threshold > num_shares:
        raise ShamirError(f"threshold ({threshold}) must be >= 1 and <= num_shares ({num_shares})")
    if secret < 0 or secret >= prime:
        raise ShamirError(f"secret must be in [0, {prime})")

    coeffs = [secret] + [_lib_rint(prime) for _ in range(1, threshold)]

    used: set[int] = set()
    xs: list[int] = []
    for _ in range(num_shares):
        while True:
            x = _lib_rint(prime)
            if x < 1:
                continue
            if x not in used:
                used.add(x)
                xs.append(x)
                break

    shares: list[tuple[int, int, int]] = []
    for x in xs:
        y = _lib_eval_at(tuple(coeffs), x, prime)
        shares.append((x, y, prime))
    return shares


def combine(
    shares: list[tuple[int, int, int]],
    prime: int | None = None,
    *,
    x_0: int = 0,
) -> int:
    if not shares:
        raise ShamirError("Need at least 1 share to combine")

    primes: set[int] = set()
    points: list[tuple[int, int]] = []

    for x, y, p in shares:
        if x_0 is not None and x == x_0:
            return y
        if y < 0 or y >= p:
            raise ShamirError(f"Share y={y} out of range [0, {p})")
        points.append((x, y))
        primes.add(p)

    if len(primes) > 1:
        raise ShamirError(f"All shares must use the same prime, got {sorted(primes)}")
    if prime is not None and prime not in primes:
        raise ShamirError(f"Provided prime {prime} does not match shares' prime {next(iter(primes))}")

    p = prime if prime is not None else next(iter(primes))

    xs = [pt[0] for pt in points]
    if len(set(xs)) != len(xs):
        raise ShamirError("Shares must have distinct x-coordinates")

    if len(points) == 1:
        return points[0][1]

    ys = [pt[1] for pt in points]
    if x_0 == 0:
        return cast(int, _lib_recover_secret(points, prime=p))

    return cast(int, _lib_lagrange(x_0, xs, ys, p))
