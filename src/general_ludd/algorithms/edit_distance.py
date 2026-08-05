"""Edit-distance and sequence-alignment algorithms.

Delegates Levenshtein, Damerau-Levenshtein (OSA), Hamming, Jaro,
and Jaro-Winkler to rapidfuzz (C-optimised).  Needleman-Wunsch and
Smith-Waterman remain pure-Python for alignment with traceback.
"""

from __future__ import annotations

from rapidfuzz.distance import (
    DamerauLevenshtein as _DL,
)
from rapidfuzz.distance import (
    Hamming as _Hamming,
)
from rapidfuzz.distance import (
    Jaro as _Jaro,
)
from rapidfuzz.distance import (
    Levenshtein as _Lev,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _dp_matrix(rows: int, cols: int, init: int = 0) -> list[list[int]]:
    return [[init] * cols for _ in range(rows)]


# ── Levenshtein ──────────────────────────────────────────────────────────


def levenshtein(a: str, b: str) -> int:
    """Levenshtein edit distance (insert, delete, substitute).

    ``levenshtein("kitten", "sitting") == 3``
    """
    return _Lev.distance(a, b)


# ── Damerau-Levenshtein (Optimal String Alignment) ───────────────────────


def damerau_levenshtein(a: str, b: str) -> int:
    """Damerau-Levenshtein (OSA variant).

    Adds adjacent transposition to the Levenshtein set.
    ``damerau_levenshtein("ca", "ac") == 1`` (one transposition).
    """
    return _DL.distance(a, b)


# ── Needleman-Wunsch (global alignment) ──────────────────────────────────


def needleman_wunsch(
    a: str,
    b: str,
    match: int = 1,
    mismatch: int = -1,
    gap: int = -2,
) -> tuple[int, str, str]:
    """Global alignment with affine-gap scoring and traceback.

    Returns ``(score, aligned_a, aligned_b)`` where gaps are ``-``.
    """
    n, m = len(a), len(b)
    dp = _dp_matrix(n + 1, m + 1)
    for i in range(1, n + 1):
        dp[i][0] = i * gap
    for j in range(1, m + 1):
        dp[0][j] = j * gap
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = max(
                dp[i - 1][j] + gap,
                dp[i][j - 1] + gap,
                dp[i - 1][j - 1] + (match if a[i - 1] == b[j - 1] else mismatch),
            )
    score = dp[n][m]

    aligned_a: list[str] = []
    aligned_b: list[str] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (match if a[i - 1] == b[j - 1] else mismatch):
            aligned_a.append(a[i - 1])
            aligned_b.append(b[j - 1])
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + gap:
            aligned_a.append(a[i - 1])
            aligned_b.append("-")
            i -= 1
        else:
            aligned_a.append("-")
            aligned_b.append(b[j - 1])
            j -= 1
    aligned_a.reverse()
    aligned_b.reverse()
    return score, "".join(aligned_a), "".join(aligned_b)


# ── Smith-Waterman (local alignment) ─────────────────────────────────────


def smith_waterman(
    a: str,
    b: str,
    match: int = 2,
    mismatch: int = -1,
    gap: int = -2,
) -> tuple[int, str, str]:
    """Local alignment (affine-gap scoring) with traceback.

    Returns ``(score, aligned_a, aligned_b)`` for the highest-scoring
    local segment.
    """
    n, m = len(a), len(b)
    dp = _dp_matrix(n + 1, m + 1)
    max_score = 0
    max_pos = (0, 0)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = max(
                0,
                dp[i - 1][j] + gap,
                dp[i][j - 1] + gap,
                dp[i - 1][j - 1] + (match if a[i - 1] == b[j - 1] else mismatch),
            )
            if dp[i][j] > max_score:
                max_score = dp[i][j]
                max_pos = (i, j)

    aligned_a: list[str] = []
    aligned_b: list[str] = []
    i, j = max_pos
    while i > 0 and j > 0 and dp[i][j] > 0:
        if dp[i][j] == dp[i - 1][j - 1] + (match if a[i - 1] == b[j - 1] else mismatch):
            aligned_a.append(a[i - 1])
            aligned_b.append(b[j - 1])
            i -= 1
            j -= 1
        elif dp[i][j] == dp[i - 1][j] + gap:
            aligned_a.append(a[i - 1])
            aligned_b.append("-")
            i -= 1
        elif dp[i][j] == dp[i][j - 1] + gap:
            aligned_a.append("-")
            aligned_b.append(b[j - 1])
            j -= 1
        else:
            break
    aligned_a.reverse()
    aligned_b.reverse()
    return max_score, "".join(aligned_a), "".join(aligned_b)


# ── Hamming ──────────────────────────────────────────────────────────────


def hamming(a: str, b: str) -> int:
    """Hamming distance — substitutions only, equal-length strings.

    Raises ``ValueError`` when ``len(a) != len(b)``.
    """
    if len(a) != len(b):
        raise ValueError(f"Hamming requires equal-length strings: {len(a)} != {len(b)}")
    return _Hamming.distance(a, b)


# ── Jaro-Winkler ─────────────────────────────────────────────────────────


def jaro_similarity(a: str, b: str) -> float:
    """Jaro similarity in [0.0, 1.0].  Higher = more similar."""
    return _Jaro.similarity(a, b)


def jaro_winkler(a: str, b: str, scaling: float = 0.1, prefix_len: int = 4) -> float:
    """Jaro-Winkler similarity in [0.0, 1.0].

    Boosts the Jaro score when strings share a common prefix.
    """
    sim = _Jaro.similarity(a, b)
    if sim < 0.7:
        return sim
    prefix = 0
    for ca, cb in zip(a, b, strict=False):
        if ca == cb:
            prefix += 1
        else:
            break
        if prefix >= prefix_len:
            break
    return sim + (prefix * scaling * (1.0 - sim))
