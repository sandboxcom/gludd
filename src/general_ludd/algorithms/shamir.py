"""Shamir secret sharing over finite fields.

Splits a secret into n shares such that any k (threshold) shares recover it,
while any k-1 shares reveal nothing. Uses Lagrange interpolation over GF(prime).

Pure-Python, stdlib only.
"""

from __future__ import annotations

import secrets as _secrets


class ShamirError(ValueError):
    """Base exception for Shamir secret sharing operations."""


DEFAULT_PRIME: int = 2**127 - 1


def _mod_inverse(a: int, prime: int) -> int:
    if a == 0:
        raise ShamirError("Cannot compute modular inverse of 0")
    if prime <= 1:
        raise ShamirError(f"Modulus must be prime, got {prime}")
    return pow(a, prime - 2, prime)


def _evaluate_polynomial(coeffs: list[int], x: int, prime: int) -> int:
    result = 0
    x_pow = 1
    for c in coeffs:
        result = (result + c * x_pow) % prime
        x_pow = (x_pow * x) % prime
    return result


def _random_polynomial(secret: int, threshold: int, prime: int) -> list[int]:
    coeffs = [secret]
    for _ in range(1, threshold):
        coeffs.append(_secrets.randbelow(prime))
    return coeffs


def _lagrange_basis(x_target: int, xs: list[int], j: int, prime: int) -> int:
    num = 1
    den = 1
    xj = xs[j]
    for m, xm in enumerate(xs):
        if m == j:
            continue
        num = (num * (x_target - xm)) % prime
        den = (den * (xj - xm)) % prime
    return (num * _mod_inverse(den, prime)) % prime


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

    coeffs = _random_polynomial(secret, threshold, prime)
    [_secrets.randbits(_max_id := max(threshold, num_shares).bit_length()) for _ in range(num_shares)]
    while True:
        used: set[int] = set()
        xs: list[int] = []
        for _ in range(num_shares):
            while True:
                x = _secrets.randbelow(prime)
                if x < 1:
                    continue
                if x not in used:
                    used.add(x)
                    xs.append(x)
                    break
        if len(xs) == num_shares:
            break

    shares: list[tuple[int, int, int]] = []
    for x in xs:
        y = _evaluate_polynomial(coeffs, x, prime)
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

    xs: list[int] = []
    ys: list[int] = []
    primes: set[int] = set()

    for x, y, p in shares:
        if x_0 is not None and x == x_0:
            return y
        if y < 0 or y >= p:
            raise ShamirError(f"Share y={y} out of range [0, {p})")
        xs.append(x)
        ys.append(y)
        primes.add(p)

    if len(primes) > 1:
        raise ShamirError(f"All shares must use the same prime, got {sorted(primes)}")
    if prime is not None and prime not in primes:
        raise ShamirError(f"Provided prime {prime} does not match shares' prime {next(iter(primes))}")

    p = prime if prime is not None else next(iter(primes))
    k = len(xs)

    if len(set(xs)) != k:
        raise ShamirError("Shares must have distinct x-coordinates")

    secret = 0
    for j in range(k):
        basis = _lagrange_basis(x_0, xs, j, p)
        secret = (secret + ys[j] * basis) % p

    return secret
