"""Integer-partition algorithms: counting, listing, restricted, conjugate,
generating-function coefficients.  Pure-Python, stdlib only.
"""

from __future__ import annotations


def partition_count(n: int) -> int:
    """Number of integer partitions of *n* via Euler's pentagonal-number
    recurrence.  O(n^{1.5}) time, O(n) space.
    """
    if n < 0:
        return 0
    p = [0] * (n + 1)
    p[0] = 1
    for i in range(1, n + 1):
        total = 0
        k = 1
        while True:
            g_k = k * (3 * k - 1) // 2
            if g_k > i:
                break
            sign = 1 if k % 2 == 1 else -1
            total += sign * p[i - g_k]
            g_neg = k * (3 * k + 1) // 2
            if g_neg <= i:
                total += sign * p[i - g_neg]
            k += 1
        p[i] = total
    return p[n]


def partition_count_mod(n: int, mod: int) -> int:
    """p(n) modulo *mod* (for large n where exact integer overflows)."""
    if n < 0:
        return 0
    p = [0] * (n + 1)
    p[0] = 1
    for i in range(1, n + 1):
        total = 0
        k = 1
        while True:
            g_k = k * (3 * k - 1) // 2
            if g_k > i:
                break
            sign = 1 if k % 2 == 1 else -1
            total = (total + sign * p[i - g_k]) % mod
            g_neg = k * (3 * k + 1) // 2
            if g_neg <= i:
                total = (total + sign * p[i - g_neg]) % mod
            k += 1
        p[i] = total % mod
    return p[n]


def partition_all_counts(limit: int) -> list[int]:
    """Return p(0) … p(limit) in one pass.  Useful for OEIS cross-checks."""
    p = [0] * (limit + 1)
    p[0] = 1
    for i in range(1, limit + 1):
        total = 0
        k = 1
        while True:
            g_k = k * (3 * k - 1) // 2
            if g_k > i:
                break
            sign = 1 if k % 2 == 1 else -1
            total += sign * p[i - g_k]
            g_neg = k * (3 * k + 1) // 2
            if g_neg <= i:
                total += sign * p[i - g_neg]
            k += 1
        p[i] = total
    return p


def partition_restricted_count(n: int, max_part: int) -> int:
    """Number of partitions of *n* using parts ≤ max_part.
    O(n * max_part) time, O(n) space (1D DP).
    """
    if n < 0:
        return 0
    dp = [0] * (n + 1)
    dp[0] = 1
    for part in range(1, max_part + 1):
        for s in range(part, n + 1):
            dp[s] += dp[s - part]
    return dp[n]


def partition_into_k_parts(n: int, k: int) -> int:
    """Number of partitions of *n* into exactly *k* positive integer parts.
    DP recurrence: p(n,k) = p(n-k,k) + p(n-1,k-1).
    O(n*k) time, O(n*k) space.
    """
    if k == 0:
        return 1 if n == 0 else 0
    if n <= 0:
        return 0
    dp = [[0] * (k + 1) for _ in range(n + 1)]
    dp[0][0] = 1
    for i in range(1, n + 1):
        dp[i][0] = 0
        for j in range(1, min(i, k) + 1):
            dp[i][j] = dp[i - 1][j - 1] + (dp[i - j][j] if i >= j else 0)
    return dp[n][k]


def partition_into_distinct_parts(n: int) -> int:
    """Number of partitions of *n* into distinct (non-repeating) parts.
    DP: ∏(1 + x^i) — standard coin-change variant with each part used
    at most once (0/1 knapsack on 1…n).  O(n²) time, O(n) space.
    Equivalently by Euler's identity, equals partitions into odd parts.
    """
    if n < 0:
        return 0
    dp = [0] * (n + 1)
    dp[0] = 1
    for part in range(1, n + 1):
        for s in range(n, part - 1, -1):
            dp[s] += dp[s - part]
    return dp[n]


def partition_conjugate(parts: tuple[int, ...]) -> tuple[int, ...]:
    """Return the conjugate partition (transpose of Ferrers diagram).
    O(sum(parts)) time.
    """
    if not parts:
        return ()
    max_part = parts[0]
    conj: list[int] = []
    for col in range(1, max_part + 1):
        count = 0
        for p in parts:
            if p >= col:
                count += 1
            else:
                break
        if count == 0:
            break
        conj.append(count)
    return tuple(conj)


def partition_generating_coeffs(n: int) -> int:
    """p(n) computed via series expansion of the generating function.
    Product_{i=1..n} 1/(1 - x^i), truncated to degree *n*.
    O(n²) time, O(n) space.  Slower than Euler but pedagogic.
    """
    if n < 0:
        return 0
    coeffs = [0] * (n + 1)
    coeffs[0] = 1
    for i in range(1, n + 1):
        for j in range(i, n + 1):
            coeffs[j] += coeffs[j - i]
    return coeffs[n]


def partition_list(n: int) -> list[tuple[int, ...]]:
    """Enumerate all integer partitions of *n* (lexicographic descending).
    Generator-friendly but materialised here for testability.
    """
    result: list[tuple[int, ...]] = []

    def _recurse(remaining: int, max_val: int, tail: list[int]) -> None:
        if remaining == 0:
            result.append(tuple(tail))
            return
        for p in range(min(max_val, remaining), 0, -1):
            tail.append(p)
            _recurse(remaining - p, p, tail)
            tail.pop()

    if n == 0:
        result.append(())
    else:
        _recurse(n, n, [])
    return result
