"""Edit-distance and sequence-alignment algorithms.

Pure-Python, stdlib only. Provides six classic measures:

- Levenshtein (insert, delete, substitute)
- Damerau-Levenshtein (OSA variant: + adjacent transposition)
- Needleman-Wunsch (global alignment with traceback)
- Smith-Waterman (local alignment with traceback)
- Hamming (substitution only, equal-length)
- Jaro-Winkler (prefix-biased similarity)
"""

from __future__ import annotations

# ── helpers ──────────────────────────────────────────────────────────────


def _dp_matrix(rows: int, cols: int, init: int = 0) -> list[list[int]]:
    return [[init] * cols for _ in range(rows)]


# ── Levenshtein ──────────────────────────────────────────────────────────


def levenshtein(a: str, b: str) -> int:
    """Levenshtein edit distance (insert, delete, substitute).

    ``levenshtein("kitten", "sitting") == 3``
    """
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    curr = [0] * (m + 1)
    for i in range(1, n + 1):
        curr[0] = i
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + cost,
            )
        prev, curr = curr, prev
    return prev[m]


# ── Damerau-Levenshtein (Optimal String Alignment) ───────────────────────


def damerau_levenshtein(a: str, b: str) -> int:
    """Damerau-Levenshtein (OSA variant).

    Adds adjacent transposition to the Levenshtein set.
    ``damerau_levenshtein("ca", "ac") == 1`` (one transposition).
    """
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    d = _dp_matrix(n + 1, m + 1)
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + cost)
    return d[n][m]


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
    return sum(1 for ca, cb in zip(a, b, strict=False) if ca != cb)


# ── Jaro-Winkler ─────────────────────────────────────────────────────────


def jaro_similarity(a: str, b: str) -> float:
    """Jaro similarity in [0.0, 1.0].  Higher = more similar."""
    la, lb = len(a), len(b)
    if la == 0 and lb == 0:
        return 1.0
    if la == 0 or lb == 0:
        return 0.0

    match_distance = max(0, max(la, lb) // 2 - 1)
    a_matched = [False] * la
    b_matched = [False] * lb
    matches = 0

    for i in range(la):
        start = max(0, i - match_distance)
        end = min(lb, i + match_distance + 1)
        for j in range(start, end):
            if not b_matched[j] and a[i] == b[j]:
                a_matched[i] = b_matched[j] = True
                matches += 1
                break

    if matches == 0:
        return 0.0

    transpositions = 0
    k = 0
    for i in range(la):
        if a_matched[i]:
            while not b_matched[k]:
                k += 1
            if a[i] != b[k]:
                transpositions += 1
            k += 1
    transpositions //= 2

    return (matches / la + matches / lb + (matches - transpositions) / matches) / 3.0


def jaro_winkler(a: str, b: str, scaling: float = 0.1, prefix_len: int = 4) -> float:
    """Jaro-Winkler similarity in [0.0, 1.0].

    Boosts the Jaro score when strings share a common prefix.
    """
    sim = jaro_similarity(a, b)
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
