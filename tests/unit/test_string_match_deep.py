"""Deep tests for string matching algorithms: KMP, Boyer-Moore, Rabin-Karp,
Aho-Corasick, and wildcard matching.
"""

from __future__ import annotations

from collections import deque

# ---------------------------------------------------------------------------
# Algorithm implementations
# ---------------------------------------------------------------------------


def kmp_search(text: str, pattern: str) -> list[int]:
    """Return all start indices of *pattern* in *text* using KMP."""
    if not pattern:
        return list(range(len(text) + 1))
    lps = _kmp_lps(pattern)
    result: list[int] = []
    j = 0
    for i, ch in enumerate(text):
        while j > 0 and ch != pattern[j]:
            j = lps[j - 1]
        if ch == pattern[j]:
            j += 1
        if j == len(pattern):
            result.append(i - j + 1)
            j = lps[j - 1]
    return result


def _kmp_lps(pattern: str) -> list[int]:
    lps = [0] * len(pattern)
    length = 0
    i = 1
    while i < len(pattern):
        if pattern[i] == pattern[length]:
            length += 1
            lps[i] = length
            i += 1
        elif length != 0:
            length = lps[length - 1]
        else:
            lps[i] = 0
            i += 1
    return lps


def boyer_moore_search(text: str, pattern: str) -> list[int]:
    """Return all start indices of *pattern* in *text* using Boyer-Moore
    (bad-character rule).
    """
    if not pattern:
        return list(range(len(text) + 1))
    n, m = len(text), len(pattern)
    if m > n:
        return []
    bad_char: dict[str, int] = {}
    for i, ch in enumerate(pattern):
        bad_char[ch] = i
    result: list[int] = []
    s = 0
    while s <= n - m:
        j = m - 1
        while j >= 0 and pattern[j] == text[s + j]:
            j -= 1
        if j < 0:
            result.append(s)
            s += m - bad_char.get(text[s + m], -1) if s + m < n else 1
        else:
            bc = bad_char.get(text[s + j], -1)
            s += max(1, j - bc)
    return result


def rabin_karp_search(text: str, pattern: str, base: int = 256, mod: int = 10**9 + 7) -> list[int]:
    """Return all start indices of *pattern* in *text* using Rabin-Karp."""
    if not pattern:
        return list(range(len(text) + 1))
    n, m = len(text), len(pattern)
    if m > n:
        return []
    result: list[int] = []
    h = pow(base, m - 1, mod)
    p_hash = 0
    t_hash = 0
    for i in range(m):
        p_hash = (p_hash * base + ord(pattern[i])) % mod
        t_hash = (t_hash * base + ord(text[i])) % mod
    for s in range(n - m + 1):
        if p_hash == t_hash and text[s : s + m] == pattern:
            result.append(s)
        if s < n - m:
            t_hash = (t_hash - ord(text[s]) * h) % mod
            t_hash = (t_hash * base + ord(text[s + m])) % mod
    return result


class AhoCorasick:
    """Aho-Corasick multi-pattern matcher."""

    def __init__(self) -> None:
        self._go: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._output: list[list[int]] = [[]]
        self._pat_count = 0

    def add_pattern(self, pattern: str) -> int:
        node = 0
        for ch in pattern:
            if ch not in self._go[node]:
                self._go[node][ch] = len(self._go)
                self._go.append({})
                self._fail.append(0)
                self._output.append([])
            node = self._go[node][ch]
        pat_id = self._pat_count
        self._pat_count += 1
        self._output[node].append(pat_id)
        return pat_id

    def build(self) -> None:
        q: deque[int] = deque()
        for _ch, nxt in self._go[0].items():
            self._fail[nxt] = 0
            q.append(nxt)
        while q:
            r = q.popleft()
            for ch, nxt in self._go[r].items():
                q.append(nxt)
                f = self._fail[r]
                while f > 0 and ch not in self._go[f]:
                    f = self._fail[f]
                self._fail[nxt] = self._go[f].get(ch, 0)
                self._output[nxt].extend(self._output[self._fail[nxt]])

    def search(self, text: str) -> list[tuple[int, int]]:
        """Return (end_index, pattern_index) for every match."""
        result: list[tuple[int, int]] = []
        node = 0
        for i, ch in enumerate(text):
            while node > 0 and ch not in self._go[node]:
                node = self._fail[node]
            node = self._go[node].get(ch, 0)
            for pat_id in self._output[node]:
                result.append((i, pat_id))
        return result


def wildcard_match(text: str, pattern: str) -> bool:
    """Return True if *pattern* matches *text* with ``?`` (any one char) and
    ``*`` (any sequence, including empty)."""
    ti = pi = 0
    star_idx = -1
    match_idx = 0
    while ti < len(text):
        if pi < len(pattern) and (pattern[pi] == "?" or pattern[pi] == text[ti]):
            ti += 1
            pi += 1
        elif pi < len(pattern) and pattern[pi] == "*":
            star_idx = pi
            match_idx = ti
            pi += 1
        elif star_idx != -1:
            pi = star_idx + 1
            match_idx += 1
            ti = match_idx
        else:
            return False
    while pi < len(pattern) and pattern[pi] == "*":
        pi += 1
    return pi == len(pattern)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKMPSearch:
    def test_no_match(self) -> None:
        assert kmp_search("abc", "xyz") == []

    def test_single_match_basic(self) -> None:
        assert kmp_search("hello world hello", "world") == [6]

    def test_multiple_overlapping(self) -> None:
        assert kmp_search("aaaaa", "aa") == [0, 1, 2, 3]

    def test_pattern_at_start_end(self) -> None:
        assert kmp_search("ababcababc", "ab") == [0, 2, 5, 7]

    def test_empty_pattern(self) -> None:
        assert kmp_search("abc", "") == [0, 1, 2, 3]

    def test_lps_table_long_prefix(self) -> None:
        assert _kmp_lps("abcab") == [0, 0, 0, 1, 2]


class TestBoyerMoore:
    def test_no_match(self) -> None:
        assert boyer_moore_search("abcdef", "xyz") == []

    def test_single_match(self) -> None:
        assert boyer_moore_search("the quick brown fox", "brown") == [10]

    def test_multiple_matches(self) -> None:
        assert boyer_moore_search("ababa", "aba") == [0, 2]

    def test_pattern_longer_than_text(self) -> None:
        assert boyer_moore_search("abc", "abcdefg") == []

    def test_empty_pattern(self) -> None:
        assert boyer_moore_search("abc", "") == [0, 1, 2, 3]


class TestRabinKarp:
    def test_no_match(self) -> None:
        assert rabin_karp_search("abcdef", "xyz") == []

    def test_single_match(self) -> None:
        assert rabin_karp_search("hello world", "world") == [6]

    def test_multiple_matches(self) -> None:
        assert rabin_karp_search("aaaa", "aa") == [0, 1, 2]

    def test_collision_resistance(self) -> None:
        assert rabin_karp_search("abc", "ab") == [0]

    def test_empty_pattern(self) -> None:
        assert rabin_karp_search("abc", "") == [0, 1, 2, 3]


class TestAhoCorasick:
    def test_empty_text(self) -> None:
        ac = AhoCorasick()
        ac.add_pattern("cat")
        ac.build()
        assert ac.search("") == []

    def test_single_pattern_match(self) -> None:
        ac = AhoCorasick()
        ac.add_pattern("cat")
        ac.build()
        matches = ac.search("the cat sat")
        assert len(matches) == 1
        assert matches[0][0] == 6

    def test_multiple_patterns(self) -> None:
        ac = AhoCorasick()
        ac.add_pattern("he")
        ac.add_pattern("she")
        ac.add_pattern("his")
        ac.add_pattern("hers")
        ac.build()
        matches = ac.search("ushers")
        end_positions = {m[0] for m in matches}
        assert 3 in end_positions
        assert 5 in end_positions
        assert len(matches) >= 3


class TestWildcardMatching:
    def test_exact_match(self) -> None:
        assert wildcard_match("abc", "abc") is True

    def test_question_mark(self) -> None:
        assert wildcard_match("abc", "a?c") is True
        assert wildcard_match("abc", "a?b") is False

    def test_star_any_sequence(self) -> None:
        assert wildcard_match("abczzz", "abc*") is True

    def test_star_empty_sequence(self) -> None:
        assert wildcard_match("abc", "abc*") is True

    def test_star_mid(self) -> None:
        assert wildcard_match("abcxyz123", "abc*123") is True
        assert wildcard_match("abc123", "abc*45") is False

    def test_only_star(self) -> None:
        assert wildcard_match("anything", "*") is True

    def test_multiple_stars(self) -> None:
        assert wildcard_match("cd", "*a*b*c*d*") is False
        assert wildcard_match("", "***") is True

    def test_mixed_wildcards(self) -> None:
        assert wildcard_match("abcde", "a*c?e") is True
        assert wildcard_match("abcd", "a*c?e") is False
