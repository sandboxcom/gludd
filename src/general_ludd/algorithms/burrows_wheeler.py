"""Burrows-Wheeler transform (BWT).

Forward transform: produces the last column of sorted rotations and the
index of the original string. Suffix sentinel NUL (\\x00).

Inverse transform: recovers the original string from the BWT output
using LF-mapping.

Pure-Python, stdlib only.
"""

from __future__ import annotations

SENTINEL = "\x00"


def bwt_encode(s: str) -> tuple[str, int]:
    """Encode string *s* to its Burrows-Wheeler transform.

    Returns ``(encoded, idx)`` where *encoded* is the last column of
    the sorted rotation matrix and *idx* is the row containing the
    original string (with sentinel appended).
    """
    t = s + SENTINEL
    n = len(t)
    rotations = [(t[i:] + t[:i], i) for i in range(n)]
    rotations.sort()

    encoded = "".join(r[-1] for r, _ in rotations)

    for row, (_, start) in enumerate(rotations):
        if start == 0:
            return encoded, row

    return encoded, 0


def bwt_decode(encoded: str, idx: int) -> str:
    """Decode a Burrows-Wheeler transform back to the original string.

    Args:
        encoded: BWT last-column string (includes sentinel).
        idx: Row index of the original string in the sorted matrix.

    Returns:
        The original string (without sentinel).
    """
    if not encoded:
        return ""

    L = encoded
    n = len(L)
    F = sorted(L)

    first: dict[str, int] = {}
    for i, c in enumerate(F):
        if c not in first:
            first[c] = i

    counts: dict[str, int] = {}
    T: list[int] = [0] * n
    for i, c in enumerate(L):
        rank = counts.get(c, 0)
        counts[c] = rank + 1
        T[i] = first[c] + rank

    chars: list[str] = []
    row = T[idx]
    for _ in range(n - 1):
        chars.append(L[row])
        row = T[row]

    return "".join(reversed(chars))
