"""Knuth-Morris-Pratt (KMP) prefix function, string matching,
Z-algorithm (Z-array), and Z-based pattern matching.
Pure-Python, stdlib only.
"""

from __future__ import annotations


def prefix_function(s: str) -> list[int]:
    """Compute the KMP prefix function (pi array) for string *s*.

    ``pi[i]`` is the length of the longest proper prefix of ``s[:i+1]``
    that is also a suffix of ``s[:i+1]``.

    O(|s|) time, O(|s|) space.
    """
    n = len(s)
    pi = [0] * n
    for i in range(1, n):
        j = pi[i - 1]
        while j > 0 and s[i] != s[j]:
            j = pi[j - 1]
        if s[i] == s[j]:
            j += 1
        pi[i] = j
    return pi


def kmp_search(text: str, pattern: str) -> list[int]:
    """Return all starting indices where *pattern* occurs in *text*.

    Uses the KMP prefix function to achieve O(|text|+|pattern|) time.
    Overlapping matches are reported.
    """
    if not pattern:
        return []
    n, m = len(text), len(pattern)
    if m > n:
        return []
    pi = prefix_function(pattern)
    result: list[int] = []
    j = 0
    for i in range(n):
        while j > 0 and text[i] != pattern[j]:
            j = pi[j - 1]
        if text[i] == pattern[j]:
            j += 1
        if j == m:
            result.append(i - m + 1)
            j = pi[j - 1]
    return result


def z_array(s: str) -> list[int]:
    """Compute the Z-array for string *s*.

    ``z[i]`` is the length of the longest substring starting at ``s[i]``
    that is also a prefix of *s*. By convention ``z[0] = 0``.

    O(|s|) time via the Z-box algorithm (Gusfield).
    """
    n = len(s)
    if n == 0:
        return []
    z = [0] * n
    left = right = 0
    for i in range(1, n):
        if i <= right:
            z[i] = min(right - i + 1, z[i - left])
        while i + z[i] < n and s[z[i]] == s[i + z[i]]:
            z[i] += 1
        if i + z[i] - 1 > right:
            left, right = i, i + z[i] - 1
    return z


def z_search(text: str, pattern: str) -> list[int]:
    """Return all starting indices where *pattern* occurs in *text*.

    Constructs the concatenation ``pattern + sentinel + text``, computes
    its Z-array, and reports positions where ``z[i] == |pattern|``.

    The sentinel character ``\x00`` is chosen because it never appears
    in the pattern (guaranteed for gludd's text inputs). O(|text|+|pattern|)
    time.
    """
    if not pattern:
        return []
    m = len(pattern)
    n = len(text)
    if m > n:
        return []
    sentinel = "\x00"
    combined = pattern + sentinel + text
    z = z_array(combined)
    result: list[int] = []
    offset = m + 1
    for i in range(offset, len(combined)):
        if z[i] == m:
            result.append(i - offset)
    return result
