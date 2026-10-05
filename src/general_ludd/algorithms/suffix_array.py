"""Suffix array construction (SA-IS), LCP array (Kasai), sparse-table RMQ for O(1) LCP queries.

Appends a unique sentinel (0) to the integer sequence during construction
so the SA-IS induced sorting works correctly, then strips it from the
returned suffix array.
"""

from __future__ import annotations

import itertools


def _is_lms(t: list[bool]) -> list[int]:
    lms: list[int] = []
    for i in range(1, len(t)):
        if t[i] and not t[i - 1]:
            lms.append(i)
    return lms


def build_sa(s: str | list[int]) -> list[int]:
    """Build suffix array via SA-IS (Suffix Array Induced Sorting).

    Returns a list of starting indices sorted lexicographically.
    """
    ints = [ord(c) for c in s] if isinstance(s, str) else list(s)

    if not ints:
        return []

    # Append sentinel (smallest value) so SA-IS can seed induced sorting.
    k = max(ints) + 2  # +1 for 0-based alphabet, +1 for sentinel=0
    padded = [*ints, 0]
    sa = _sa_is(padded, k)
    # Strip sentinel index (= position n, where padded[n] == 0).
    # The sentinel entry is the first in sa (smallest suffix).
    return [i for i in sa if i < len(ints)]


def _sa_is(s: list[int], k: int) -> list[int]:
    n = len(s)

    if n == 1:
        return [0]
    if n == 2:
        return [0, 1] if s[0] <= s[1] else [1, 0]

    # Type classification (S = True, L = False).
    t = [False] * n
    t[-1] = True  # sentinel is S
    for i in range(n - 2, -1, -1):
        if s[i] < s[i + 1]:
            t[i] = True
        elif s[i] > s[i + 1]:
            t[i] = False
        else:
            t[i] = t[i + 1]

    lms = _is_lms(t)

    sa = _induced_sort(s, t, list(lms), k)

    # If only one LMS substring (plus sentinel), we're done.
    if len(lms) <= 1:
        return sa

    # ── Extract ordered LMS positions ──────────────────────────────────
    ordered = [i for i in sa if i > 0 and t[i] and not t[i - 1]]

    # ── Name the LMS substrings ────────────────────────────────────────
    # Each LMS substring spans [lms_pos .. next LMS position in TEXT order]
    # (inclusive). SA-adjacent LMS positions are NOT necessarily text-adjacent,
    # so the endpoint must come from text order, not from `ordered`.
    next_lms: dict[int, int] = {}
    for a, b in itertools.pairwise(lms):
        next_lms[a] = b
    next_lms[lms[-1]] = n - 1

    names = [-1] * n
    cur = 0
    names[ordered[0]] = cur
    prev = ordered[0]
    for p in range(1, len(ordered)):
        b = ordered[p]
        a_end = next_lms[prev]
        b_end = next_lms[b]
        same = True
        if a_end - prev != b_end - b:
            same = False
        else:
            for d in range(a_end - prev + 1):
                if s[prev + d] != s[b + d]:
                    same = False
                    break
        if not same:
            cur += 1
        names[b] = cur
        prev = b

    name_vals = [names[pos] for pos in lms]

    if cur + 1 == len(name_vals):
        # All names distinct → direct inversion.
        sub_sa = [0] * len(name_vals)
        for i, nm in enumerate(name_vals):
            sub_sa[nm] = i
    else:
        sub_sa = _sa_is(name_vals, cur + 1)

    sorted_lms = [lms[i] for i in sub_sa]
    return _induced_sort(s, t, sorted_lms, k)


def _induced_sort(s: list[int], t: list[bool], seeds: list[int], k: int) -> list[int]:
    n = len(s)
    sa = [-1] * n

    # Bucket boundaries.
    cnt = [0] * k
    for c in s:
        cnt[c] += 1
    head = [0] * k
    tail = [0] * k
    running = 0
    for i in range(k):
        head[i] = running
        running += cnt[i]
        tail[i] = running

    # Place seeds into the right end of their buckets (S-type → tail side).
    cur_tail = list(tail)
    for pos in reversed(seeds):
        c = s[pos]
        cur_tail[c] -= 1
        sa[cur_tail[c]] = pos

    # Induce L-type (scan left-to-right, place at bucket heads).
    cur_head = list(head)
    for i in range(n):
        pos = sa[i]
        if pos > 0 and not t[pos - 1]:
            c = s[pos - 1]
            sa[cur_head[c]] = pos - 1
            cur_head[c] += 1

    # Induce S-type (scan right-to-left, place at bucket tails).
    cur_tail = list(tail)
    for i in range(n - 1, -1, -1):
        pos = sa[i]
        if pos > 0 and t[pos - 1]:
            c = s[pos - 1]
            cur_tail[c] -= 1
            sa[cur_tail[c]] = pos - 1

    return sa


def build_lcp(s: str | list[int], sa: list[int]) -> list[int]:
    """Kasai's algorithm for LCP (longest common prefix) array.

    lcp[i] = LCP of suffixes at sa[i] and sa[i-1] (lcp[0] = 0).
    """
    ints = [ord(c) for c in s] if isinstance(s, str) else list(s)
    n = len(ints)
    if n == 0:
        return []

    rank = [0] * n
    for i, v in enumerate(sa):
        rank[v] = i

    lcp = [0] * n
    h = 0
    for i in range(n):
        r = rank[i]
        if r > 0:
            j = sa[r - 1]
            while i + h < n and j + h < n and ints[i + h] == ints[j + h]:
                h += 1
            lcp[r] = h
            if h > 0:
                h -= 1
    return lcp


def _suffix_cmp(text: str, pos: int, pattern: str) -> int:
    """Compare text[pos:] against pattern, only up to len(pattern) chars.

    Returns -1, 0, 1.
    """
    plen = len(pattern)
    for k in range(plen):
        if pos + k >= len(text):
            return -1
        a, b = text[pos + k], pattern[k]
        if a < b:
            return -1
        if a > b:
            return 1
    return 0  # prefix matched pattern exactly


def sa_lower_bound(sa: list[int], text: str, pattern: str) -> int:
    """Return the first index in sa where a suffix >= pattern."""
    lo, hi = 0, len(sa)
    while lo < hi:
        mid = (lo + hi) // 2
        if _suffix_cmp(text, sa[mid], pattern) < 0:
            lo = mid + 1
        else:
            hi = mid
    return lo


def sa_upper_bound(sa: list[int], text: str, pattern: str) -> int:
    """Return the first index in sa where suffix > pattern."""
    lo, hi = 0, len(sa)
    while lo < hi:
        mid = (lo + hi) // 2
        if _suffix_cmp(text, sa[mid], pattern) <= 0:
            lo = mid + 1
        else:
            hi = mid
    return lo


def sa_find_all(sa: list[int], text: str, pattern: str) -> tuple[int, int]:
    """Return (start, end) range in sa matching pattern.  (x, x) = no match."""
    lo = sa_lower_bound(sa, text, pattern)
    hi = sa_upper_bound(sa, text, pattern)
    return (lo, hi)


def sa_contains(sa: list[int], text: str, pattern: str) -> bool:
    """Return True when the pattern occurs anywhere in the text."""
    lo, _ = sa_find_all(sa, text, pattern)
    return lo < len(sa) and text[sa[lo] :].startswith(pattern)


def longest_repeated_substring(s: str | list[int], sa: list[int], lcp: list[int]) -> tuple[int, int, int]:
    """Return (length, start_a, start_b) of the longest repeated substring.

    Uses the LCP array: the maximum LCP value (and its position) identifies
    the longest shared prefix between two adjacent suffixes in sorted order.
    start_a and start_b are the two distinct suffix start positions.
    Returns (0, 0, 0) when no repeat exists (|s| < 2 or all chars distinct).
    """
    if len(lcp) < 2:
        return (0, 0, 0)
    best = 0
    best_idx = 0
    for i in range(1, len(lcp)):
        if lcp[i] > best:
            best = lcp[i]
            best_idx = i
    if best == 0:
        return (0, 0, 0)
    pos_a = sa[best_idx]
    pos_b = sa[best_idx - 1]
    if pos_a > pos_b:
        pos_a, pos_b = pos_b, pos_a
    return (best, pos_a, pos_b)


def _floor_log2(x: int) -> int:
    """Floor of log2(x) for x > 0, via bit_length."""
    return x.bit_length() - 1


def build_lcp_sparse_table(lcp: list[int]) -> list[list[int]]:
    """Build a sparse table for O(1) range-minimum queries on the LCP array.

    st[j][i] = min(LCP[i .. i + 2^j - 1])  (inclusive range).
    Returns st[0..logN] where st[0] is a copy of lcp.
    For an empty LCP array, returns [[0]].
    """
    n = len(lcp)
    if n == 0:
        return [[0]]
    k = _floor_log2(n) + 1
    st = [lcp[:]]
    for j in range(1, k):
        step = 1 << (j - 1)
        prev = st[j - 1]
        cur = [0] * n
        limit = n - (1 << j) + 1
        for i in range(limit):
            cur[i] = min(prev[i], prev[i + step])
        st.append(cur)
    return st


def lcp_query(st: list[list[int]], left: int, right_exclusive: int) -> int:
    """O(1) range-minimum query into the LCP sparse table.

    Returns min(LCP[left .. right_exclusive - 1]).
    When left >= right_exclusive, returns a large sentinel.
    """
    if left >= right_exclusive:
        return 1 << 60
    length = right_exclusive - left
    j = _floor_log2(length)
    row = st[j]
    return min(row[left], row[right_exclusive - (1 << j)])


def lcp_of_suffixes(sa: list[int], rank: list[int], st: list[list[int]], pos_a: int, pos_b: int) -> int:
    """O(1) LCP of the suffixes starting at pos_a and pos_b in the original text.

    Requires the SA, inverse rank array, and LCP sparse table already built.
    When pos_a == pos_b, returns the remaining suffix length as a sentinel
    (the caller should pass the text length).
    """
    if pos_a == pos_b:
        return 1 << 60
    r_a, r_b = rank[pos_a], rank[pos_b]
    if r_a > r_b:
        r_a, r_b = r_b, r_a
    return lcp_query(st, r_a + 1, r_b + 1)


def build_sa_lcp_rmq(
    s: str | list[int],
) -> tuple[list[int], list[int], list[list[int]]]:
    """Convenience: build SA + LCP + RMQ sparse table in one call.

    Returns (sa, lcp, st).
    """
    sa = build_sa(s)
    lcp = build_lcp(s, sa)
    st = build_lcp_sparse_table(lcp)
    return sa, lcp, st
