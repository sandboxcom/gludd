"""Catalan numbers, binomial coefficients, Stirling numbers (1st & 2nd kind),
Bell numbers, and integer partition count via Euler's pentagonal theorem.

Pure-Python, stdlib only.  All functions return arbitrary-precision integers.
"""

from __future__ import annotations


def binomial(n: int, k: int) -> int:
    """Binomial coefficient C(n, k) = n! / (k! * (n-k)!)."""
    if k < 0 or k > n:
        return 0
    k = min(k, n - k)
    result = 1
    for i in range(1, k + 1):
        result = result * (n - k + i) // i
    return result


def catalan(n: int) -> int:
    """n-th Catalan number: C_n = binomial(2n, n) / (n + 1)."""
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    return binomial(2 * n, n) // (n + 1)


def catalan_numbers(limit: int) -> list[int]:
    """First `limit` Catalan numbers (C_0 through C_{limit-1})."""
    return [catalan(i) for i in range(limit)]


# ── Stirling numbers of the first kind (unsigned) ────────────────────


def stirling1(n: int, k: int) -> int:
    """Unsigned Stirling numbers of the first kind c(n, k).

    Count permutations of n elements with exactly k cycles.
    Recurrence: c(n,k) = c(n-1,k-1) + (n-1) * c(n-1,k)
    """
    if n < 0 or k < 0 or k > n:
        return 0
    if k == 0:
        return 1 if n == 0 else 0
    if k == n:
        return 1
    row = [0] * (k + 1)
    row[0] = 0
    row[1] = 1
    for i in range(2, n + 1):
        new = [0] * (k + 1)
        new[0] = 0
        limit_j = min(i, k)
        for j in range(1, limit_j + 1):
            new[j] = row[j - 1] + (i - 1) * row[j]
        row = new
    return row[k]


def stirling2(n: int, k: int) -> int:
    """Stirling numbers of the second kind S(n, k).

    Count ways to partition a set of n elements into k non-empty subsets.
    Recurrence: S(n,k) = S(n-1,k-1) + k * S(n-1,k)
    """
    if n < 0 or k < 0 or k > n:
        return 0
    if k == 0:
        return 1 if n == 0 else 0
    if k == n:
        return 1
    row = [0] * (k + 1)
    row[0] = 0
    row[1] = 1
    for i in range(2, n + 1):
        new = [0] * (k + 1)
        new[0] = 0
        limit_j = min(i, k)
        for j in range(1, limit_j + 1):
            new[j] = row[j - 1] + j * row[j]
        row = new
    return row[k]


# ── Bell numbers ─────────────────────────────────────────────────────


def bell_number(n: int) -> int:
    """n-th Bell number: number of set partitions of an n-element set.

    Uses the Bell triangle (Aitken's array) for O(n^2) time.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    if n == 0:
        return 1
    row: list[int] = [1]
    for _ in range(n):
        next_row = [row[-1]]
        for val in row:
            next_row.append(next_row[-1] + val)
        row = next_row
    return row[0]


def bell_triangle(limit: int) -> list[int]:
    """First `limit` Bell numbers."""
    return [bell_number(i) for i in range(limit)]


# ── Integer partition count ──────────────────────────────────────────


def count_partitions(n: int) -> int:
    """Partition function p(n) via Euler's pentagonal-number theorem.

    Number of ways to write n as a sum of positive integers (order irrelevant).
    """
    if n < 0:
        return 0
    p = [0] * (n + 1)
    p[0] = 1
    for i in range(1, n + 1):
        total = 0
        k = 1
        while True:
            pent1 = k * (3 * k - 1) // 2
            if pent1 > i:
                break
            sign = 1 if k % 2 == 1 else -1
            total += sign * p[i - pent1]
            pent2 = k * (3 * k + 1) // 2
            if pent2 <= i:
                total += sign * p[i - pent2]
            k += 1
        p[i] = total
    return p[n]
