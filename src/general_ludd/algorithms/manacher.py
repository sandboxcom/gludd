"""Manacher's algorithm for linear-time palindrome detection.

Finds all palindromic substrings, longest palindromic substring,
and palindrome count in O(n) time and O(n) space.
Pure-Python, stdlib only.
"""

from __future__ import annotations


def manacher_odd(s: str) -> list[int]:
    """Return odd-length palindrome radii centred at each position.

    ``radii[i]`` = maximal integer *r* such that
    ``s[i-r : i+r+1]`` is a palindrome (radius in characters).
    The palindrome length at *i* is ``2*radii[i] + 1``.

    O(|s|) time, O(|s|) space.
    """
    n = len(s)
    if n == 0:
        return []
    d1 = [0] * n
    left, right = 0, -1
    for i in range(n):
        k = 1 if i > right else min(d1[left + right - i], right - i + 1)
        while i - k >= 0 and i + k < n and s[i - k] == s[i + k]:
            k += 1
        d1[i] = k - 1
        if i + d1[i] > right:
            left = i - d1[i]
            right = i + d1[i]
    return d1


def manacher_even(s: str) -> list[int]:
    """Return even-length palindrome radii centred between ``s[i]`` and ``s[i+1]``.

    ``radii[i]`` = maximal integer *r* such that
    ``s[i-r+1 : i+r+1]`` is a palindrome (radius in characters).
    The palindrome length at *i* is ``2*radii[i]``; ``radii[-1]`` is always 0.

    O(|s|) time, O(|s|) space.
    """
    n = len(s)
    if n == 0:
        return []
    d2 = [0] * n
    left, right = 0, -1
    for i in range(n):
        k = 0 if i > right else min(d2[left + right - i + 1], right - i + 1)
        while i - k - 1 >= 0 and i + k < n and s[i - k - 1] == s[i + k]:
            k += 1
        d2[i] = k
        if i + d2[i] - 1 > right:
            left = i - d2[i]
            right = i + d2[i] - 1
    return [*d2[1:], 0]


def longest_palindrome(s: str) -> str:
    """Return the longest palindromic substring of *s*.

    If multiple longest exist the first (leftmost) is returned.
    Returns empty string for empty input.

    O(|s|) time, O(|s|) space.
    """
    if not s:
        return ""
    d1 = manacher_odd(s)
    d2 = manacher_even(s)
    best_len = 1
    best_start = 0
    for i, r in enumerate(d1):
        length = 2 * r + 1
        if length > best_len:
            best_len = length
            best_start = i - r
    for i, r in enumerate(d2):
        length = 2 * r
        if length > best_len:
            best_len = length
            best_start = i - r + 1
    return s[best_start : best_start + best_len]


def count_palindromes(s: str) -> int:
    """Return the total number of distinct palindromic substrings.

    Counts each (start, length) pair. Uses the array of maximal
    radii from Manacher and converts to a count of all palindromes.

    O(|s|) time, O(|s|) space.
    """
    if not s:
        return 0
    d1 = manacher_odd(s)
    d2 = manacher_even(s)
    total = 0
    for r in d1:
        total += r + 1
    for r in d2:
        total += r
    return total


def is_palindrome(s: str, i: int, j: int) -> bool:
    """Return True if s[i:j] is a palindrome.

    Uses Manacher precomputation for O(1) queries after O(n) preprocessing.
    Accepts Python-slice style [i, j) with i <= j.
    """
    len(s)
    if i >= j:
        return True
    length = j - i
    if length % 2 == 1:
        center = i + length // 2
        radius = manacher_odd(s)[center]
        return radius * 2 + 1 >= length
    else:
        gap = i + length // 2 - 1
        radius = manacher_even(s)[gap]
        return radius * 2 >= length


def _manacher_unified(s: str) -> tuple[list[int], list[int]]:
    """Return (odd_radii, even_radii) in a single pass.

    Lower-level building block that computes both arrays simultaneously
    for callers that need both. O(|s|) time, O(|s|) space.
    """
    n = len(s)
    if n == 0:
        return [], []
    d1, d2 = [0] * n, [0] * n
    l1, r1 = 0, -1
    l2, r2 = 0, -1
    for i in range(n):
        # odd
        k_odd = 1 if i > r1 else min(d1[l1 + r1 - i], r1 - i + 1)
        while i - k_odd >= 0 and i + k_odd < n and s[i - k_odd] == s[i + k_odd]:
            k_odd += 1
        d1[i] = k_odd - 1
        if i + d1[i] > r1:
            l1, r1 = i - d1[i], i + d1[i]
        # even
        k_even = 0 if i > r2 else min(d2[l2 + r2 - i + 1], r2 - i + 1)
        while i - k_even - 1 >= 0 and i + k_even < n and s[i - k_even - 1] == s[i + k_even]:
            k_even += 1
        d2[i] = k_even
        if i + d2[i] - 1 > r2:
            l2, r2 = i - d2[i], i + d2[i] - 1
    return d1, [*d2[1:], 0]
