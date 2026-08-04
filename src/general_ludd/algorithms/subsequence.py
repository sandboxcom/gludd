"""Dynamic-programming subsequence algorithms: LCS, LIS (patience sorting),
LRS, shortest common supersequence, and Needleman-Wunsch sequence alignment.
Pure-Python, stdlib only.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T", bound=Sequence[object])


def longest_common_subsequence(a: Sequence[object], b: Sequence[object]) -> list[object]:
    """Return one longest common subsequence of sequences *a* and *b*.

    O(|a|·|b|) time and space via classic DP. Ties are broken by
    preferring the subsequence that keeps earlier elements from *a*.
    """
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        ai = a[i - 1]
        row = dp[i]
        prev = dp[i - 1]
        for j in range(1, n + 1):
            if ai == b[j - 1]:
                row[j] = prev[j - 1] + 1
            else:
                row[j] = max(prev[j], row[j - 1])
    result: list[object] = []
    i, j = m, n
    while i > 0 and j > 0:
        if a[i - 1] == b[j - 1]:
            result.append(a[i - 1])
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    result.reverse()
    return result


def lcs_length(a: Sequence[object], b: Sequence[object]) -> int:
    """Return the length of the longest common subsequence. O(|a|·|b|)."""
    return len(longest_common_subsequence(a, b))


def longest_increasing_subsequence(seq: list[int]) -> list[int]:
    """Return one longest strictly-increasing subsequence via patience sorting.

    O(n log n) time, O(n) space. For non-strictly-increasing, set
    the comparison to ``<=`` inside bisect_left.
    """
    if not seq:
        return []
    tails: list[int] = []
    pred: list[int | None] = [None] * len(seq)
    tail_indices: list[int] = []
    for i, x in enumerate(seq):
        pos = bisect.bisect_left(tails, x)
        if pos == len(tails):
            tails.append(x)
            tail_indices.append(i)
        else:
            tails[pos] = x
            tail_indices[pos] = i
        pred[i] = tail_indices[pos - 1] if pos > 0 else None
    lis: list[int] = []
    idx = tail_indices[-1] if tail_indices else None
    while idx is not None:
        lis.append(seq[idx])
        idx = pred[idx]
    lis.reverse()
    return lis


def lis_length(seq: list[int]) -> int:
    """Return the length of the LIS. O(n log n)."""
    return len(longest_increasing_subsequence(seq))


def longest_repeated_subsequence(s: str) -> str:
    """Return one longest subsequence that appears at least twice in *s*
    (non-overlapping occurrences, DP construction).

    O(n²) time and space.
    """
    n = len(s)
    dp = [[0] * (n + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        si = s[i - 1]
        row = dp[i]
        prev = dp[i - 1]
        for j in range(1, n + 1):
            if si == s[j - 1] and i != j:
                row[j] = prev[j - 1] + 1
            else:
                row[j] = max(prev[j], row[j - 1])
    result_chars: list[str] = []
    i, j = n, n
    while i > 0 and j > 0:
        if s[i - 1] == s[j - 1] and i != j and dp[i][j] == dp[i - 1][j - 1] + 1:
            result_chars.append(s[i - 1])
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    result_chars.reverse()
    return "".join(result_chars)


def lrs_length(s: str) -> int:
    """Return the length of the longest repeated subsequence. O(n²)."""
    return len(longest_repeated_subsequence(s))


def shortest_common_supersequence(a: Sequence[object], b: Sequence[object]) -> list[object]:
    """Return one shortest common supersequence (SCS) that contains both
    *a* and *b* as subsequences. O(|a|·|b|).

    Constructed by merging the LCS into a single traversal.
    """
    m, n = len(a), len(b)
    lcs_seq = longest_common_subsequence(a, b)
    result: list[object] = []
    i = j = 0
    for c in lcs_seq:
        while i < m and a[i] != c:
            result.append(a[i])
            i += 1
        while j < n and b[j] != c:
            result.append(b[j])
            j += 1
        result.append(c)
        i += 1
        j += 1
    result.extend(a[i:])
    result.extend(b[j:])
    return result


def scs_length(a: Sequence[object], b: Sequence[object]) -> int:
    """Return the length of the SCS: |a|+|b|-|LCS(a,b)|."""
    return len(a) + len(b) - lcs_length(a, b)


def needleman_wunsch(
    a: Sequence[object], b: Sequence[object], match: int = 1, mismatch: int = -1, gap: int = -1
) -> tuple[int, str, str]:
    """Needleman-Wunsch global sequence alignment.

    Returns ``(score, aligned_a, aligned_b)`` where gaps are represented
    by ``'-'``.

    O(|a|·|b|) time and space.
    """
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        dp[i][0] = i * gap
    for j in range(1, n + 1):
        dp[0][j] = j * gap
    for i in range(1, m + 1):
        ai = a[i - 1]
        row = dp[i]
        prev = dp[i - 1]
        for j in range(1, n + 1):
            diag = prev[j - 1] + (match if ai == b[j - 1] else mismatch)
            row[j] = max(diag, prev[j] + gap, dp[i][j - 1] + gap)
    score = dp[m][n]
    aligned_a: list[str] = []
    aligned_b: list[str] = []
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (match if a[i - 1] == b[j - 1] else mismatch):
            aligned_a.append(str(a[i - 1]))
            aligned_b.append(str(b[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + gap:
            aligned_a.append(str(a[i - 1]))
            aligned_b.append("-")
            i -= 1
        elif j > 0 and dp[i][j] == dp[i][j - 1] + gap:
            aligned_a.append("-")
            aligned_b.append(str(b[j - 1]))
            j -= 1
    aligned_a.reverse()
    aligned_b.reverse()
    return score, "".join(aligned_a), "".join(aligned_b)


def alignment_score(a: Sequence[object], b: Sequence[object], match: int = 1, mismatch: int = -1, gap: int = -1) -> int:
    """Return the Needleman-Wunsch score. Convenience wrapper."""
    score, _, _ = needleman_wunsch(a, b, match, mismatch, gap)
    return score


__all__ = [
    "alignment_score",
    "lcs_length",
    "lis_length",
    "longest_common_subsequence",
    "longest_increasing_subsequence",
    "longest_repeated_subsequence",
    "lrs_length",
    "needleman_wunsch",
    "scs_length",
    "shortest_common_supersequence",
]
