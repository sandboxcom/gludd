"""Boyer-Moore, Boyer-Moore-Horspool, and BMH-Sunday string search.

Pure-Python, stdlib-only implementations with precomputed bad-character
and good-suffix shift tables.
"""

from __future__ import annotations

from collections.abc import Callable


def build_bad_char_table(pattern: str) -> dict[str, int]:
    """Build the bad-character shift table.

    For each character *ch* in *pattern*, stores the rightmost index.
    Characters not in the table shift by *len(pattern)*.
    """
    table: dict[str, int] = {}
    for i, ch in enumerate(pattern):
        table[ch] = i
    return table


def build_good_suffix_table(pattern: str) -> list[int]:
    """Build the strong good-suffix shift table.

    Returns a list *gs* where *gs[j]* is the safe shift when a mismatch
    occurs at position *j* (0-indexed from the left).  The shift is
    based on the widest border of the suffix that has been matched.
    """
    m = len(pattern)
    if m == 0:
        return []
    gs = [m] * m
    suff = _build_suffixes(pattern)
    j = 0
    for i in range(m - 1, -1, -1):
        if suff[i] == i + 1:
            while j < m - 1 - i:
                if gs[j] == m:
                    gs[j] = m - 1 - i
                j += 1
    for i in range(m - 1):
        gs[m - 1 - suff[i]] = m - 1 - i
    return gs


def _build_suffixes(pattern: str) -> list[int]:
    """Compute the suffix array for good-suffix preprocessing.

    *suff[i]* = length of the longest suffix of *pattern*
    that ends at position *i* and is also a suffix of the whole pattern.
    """
    m = len(pattern)
    suff = [0] * m
    suff[m - 1] = m
    g = m - 1
    f = 0
    for i in range(m - 2, -1, -1):
        if i > g and suff[i + m - 1 - f] < i - g:
            suff[i] = suff[i + m - 1 - f]
        else:
            if i < g:
                g = i
            f = i
            while g >= 0 and pattern[g] == pattern[g + m - 1 - f]:
                g -= 1
            suff[i] = f - g
    return suff


def boyer_moore_search(text: str, pattern: str) -> list[int]:
    """Return all start indices of *pattern* in *text* using full
    Boyer-Moore (bad-character + good-suffix).
    """
    if not pattern:
        return list(range(len(text) + 1))
    n, m = len(text), len(pattern)
    if m > n:
        return []
    bad_char = build_bad_char_table(pattern)
    gs = build_good_suffix_table(pattern)
    result: list[int] = []
    s = 0
    while s <= n - m:
        j = m - 1
        while j >= 0 and pattern[j] == text[s + j]:
            j -= 1
        if j < 0:
            result.append(s)
            s += gs[0] if gs[0] > 0 else 1
        else:
            bc_shift = j - bad_char.get(text[s + j], -1)
            s += max(gs[j], bc_shift, 1)
    return result


def boyer_moore_horspool_search(text: str, pattern: str) -> list[int]:
    """Return all start indices using Boyer-Moore-Horspool.

    Uses only the bad-character rule keyed on the character aligned
    with the END of the pattern — simpler and often faster than full BM.
    """
    if not pattern:
        return list(range(len(text) + 1))
    n, m = len(text), len(pattern)
    if m > n:
        return []
    shift: dict[str, int] = {}
    for i, ch in enumerate(pattern[:-1]):
        shift[ch] = m - 1 - i
    result: list[int] = []
    s = 0
    while s <= n - m:
        j = m - 1
        while j >= 0 and pattern[j] == text[s + j]:
            j -= 1
        if j < 0:
            result.append(s)
            s += shift.get(pattern[-1], m) if m > 1 else 1
        else:
            s += shift.get(text[s + m - 1], m)
    return result


def boyer_moore_horspool_sunday_search(text: str, pattern: str) -> list[int]:
    """Return all start indices using the BMH-Sunday (Quick Search) variant.

    Instead of looking at the matched text character, Sunday's rule
    looks at the character *one past* the current alignment window.
    """
    if not pattern:
        return list(range(len(text) + 1))
    n, m = len(text), len(pattern)
    if m > n:
        return []
    shift: dict[str, int] = {}
    for i, ch in enumerate(pattern):
        shift[ch] = m - i
    result: list[int] = []
    s = 0
    while s <= n - m:
        j = 0
        while j < m and pattern[j] == text[s + j]:
            j += 1
        if j == m:
            result.append(s)
        if s + m >= n:
            break
        next_ch = text[s + m]
        s += shift.get(next_ch, m + 1)
    return result


def boyer_moore_search_with_table(
    text: str,
    bad_char: dict[str, int],
    pattern: str,
) -> list[int]:
    """Search using a precomputed bad-character table, avoiding
    the per-call table build.
    """
    if not pattern:
        return list(range(len(text) + 1))
    n, m = len(text), len(pattern)
    if m > n:
        return []
    result: list[int] = []
    s = 0
    while s <= n - m:
        j = m - 1
        while j >= 0 and pattern[j] == text[s + j]:
            j -= 1
        if j < 0:
            result.append(s)
            bc_shift = m - bad_char.get(text[s + m], -1) if s + m < n else 1
            s += max(1, bc_shift)
        else:
            bc = bad_char.get(text[s + j], -1)
            s += max(1, j - bc)
    return result


ENGINES: dict[str, Callable[[str, str], list[int]]] = {
    "boyer_moore": boyer_moore_search,
    "horspool": boyer_moore_horspool_search,
    "sunday": boyer_moore_horspool_sunday_search,
}
