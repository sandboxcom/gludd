"""Wildcard / glob pattern matching with ``*`` and ``?``.

Pure-Python, stdlib only. Provides three implementations and a convenience
function that picks the best fit.

- ``*`` matches any sequence of characters (including empty).
- ``?`` matches exactly one character.

Functions
---------
match_backtrack  : recursive with pruning
match_dp         : bottom-up DP, O(m·n)
match_regex      : compile wildcard to regex, delegate to re
match            : convenience — uses DP by default
"""

from __future__ import annotations

import re


def match_backtrack(pattern: str, text: str) -> bool:
    """Recursive match with backtracking and start-end pruning.

    Parameters
    ----------
    pattern : str
        Wildcard pattern containing ``*`` and ``?``.
    text : str
        String to match against.

    Returns
    -------
    bool
    """
    p_len = len(pattern)
    t_len = len(text)

    p_idx = 0
    t_idx = 0
    star_idx = -1
    match_idx = 0

    while t_idx < t_len:
        if p_idx < p_len and (pattern[p_idx] == "?" or pattern[p_idx] == text[t_idx]):
            p_idx += 1
            t_idx += 1
        elif p_idx < p_len and pattern[p_idx] == "*":
            if star_idx < 0 or star_idx != p_idx - 1:
                star_idx = p_idx
                match_idx = t_idx
            p_idx += 1
        elif star_idx != -1:
            p_idx = star_idx + 1
            match_idx += 1
            t_idx = match_idx
        else:
            return False

    while p_idx < p_len and pattern[p_idx] == "*":
        p_idx += 1

    return p_idx == p_len


def _to_regex(pattern: str) -> str:
    """Translate a wildcard pattern to an anchored regex."""
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
        i += 1
    return "^{}$".format("".join(parts))


def match_regex(pattern: str, text: str) -> bool:
    """Compile the wildcard pattern to an anchored regex and delegate to ``re``.

    Parameters
    ----------
    pattern : str
        Wildcard pattern containing ``*`` and ``?``.
    text : str
        String to match against.

    Returns
    -------
    bool
    """
    return bool(re.fullmatch(_to_regex(pattern), text))


def match_dp(pattern: str, text: str) -> bool:
    """Bottom-up dynamic-programming matcher, O(m·n) time and O(m·n) space.

    Parameters
    ----------
    pattern : str
        Wildcard pattern containing ``*`` and ``?``.
    text : str
        String to match against.

    Returns
    -------
    bool
    """
    m, n = len(pattern), len(text)
    dp = [[False] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = True

    for i in range(1, m + 1):
        if pattern[i - 1] == "*":
            dp[i][0] = dp[i - 1][0]
        else:
            break

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            p_ch = pattern[i - 1]
            if p_ch == "*":
                dp[i][j] = dp[i - 1][j] or dp[i][j - 1]
            elif p_ch == "?" or p_ch == text[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]

    return dp[m][n]


def match(pattern: str, text: str) -> bool:
    """Convenience — delegates to ``match_dp``.

    Parameters
    ----------
    pattern : str
    text : str

    Returns
    -------
    bool
    """
    return match_dp(pattern, text)
