"""Polynomial rolling hash, double hash, and Rabin-Karp matcher.

Pure-Python, stdlib-only implementations of rolling hashes for efficient
sliding-window string matching with O(1) per-character hash updates.
"""

from __future__ import annotations

from collections.abc import Callable


class PolynomialRollingHash:
    """Polynomial rolling hash over a character sequence.

    Hash(s[i:j]) = sum_{k=i}^{j-1} ord(s[k]) * base^(j-1-k)  mod mod.
    Supports O(1) slide: add one character on the right, drop one on the left.
    """

    _base: int
    _mod: int
    _hash: int
    _window: list[int]
    _max_pow: int

    def __init__(self, base: int = 911, mod: int = 1_000_000_007, data: str = "") -> None:
        self._base = base
        self._mod = mod
        self._hash = 0
        self._window: list[int] = []
        self._max_pow = 1
        for ch in data:
            self.push(ch)

    @property
    def value(self) -> int:
        return self._hash

    @property
    def window(self) -> str:
        return "".join(chr(v) for v in self._window)

    def __len__(self) -> int:
        return len(self._window)

    def push(self, ch: str) -> None:
        v = ord(ch)
        self._hash = (self._hash * self._base + v) % self._mod
        self._window.append(v)
        if len(self._window) > 1:
            self._max_pow = (self._max_pow * self._base) % self._mod

    def slide(self, drop: str, add: str) -> None:
        d = ord(drop)
        a = ord(add)
        n = len(self._window)
        if n == 0:
            self.push(add)
            return
        dropped = (d * self._max_pow) % self._mod
        self._hash = ((self._hash - dropped) * self._base + a) % self._mod
        self._window.pop(0)
        self._window.append(a)

    def set_window(self, s: str) -> None:
        self._hash = 0
        self._window.clear()
        self._max_pow = 1
        for ch in s:
            self.push(ch)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PolynomialRollingHash):
            return NotImplemented
        return self._hash == other._hash and self._window == other._window

    def __repr__(self) -> str:
        return f"PolynomialRollingHash(base={self._base}, mod={self._mod}, value={self._hash})"


def _pow_mod(base: int, exp: int, mod: int) -> int:
    result = 1
    b = base % mod
    e = exp
    while e > 0:
        if e & 1:
            result = (result * b) % mod
        b = (b * b) % mod
        e >>= 1
    return result


class DoubleHash:
    """Two independent polynomial hashes for collision avoidance.

    Uses two (base, mod) pairs so the probability of a false positive is
    inversely proportional to the product of the two moduli.
    """

    _h1: PolynomialRollingHash
    _h2: PolynomialRollingHash

    _BASES = (911, 1597)
    _MODS = (1_000_000_007, 1_000_000_009)

    def __init__(
        self,
        bases: tuple[int, int] | None = None,
        mods: tuple[int, int] | None = None,
    ) -> None:
        b1, b2 = bases if bases is not None else self._BASES
        m1, m2 = mods if mods is not None else self._MODS
        self._h1 = PolynomialRollingHash(base=b1, mod=m1)
        self._h2 = PolynomialRollingHash(base=b2, mod=m2)

    @property
    def value(self) -> tuple[int, int]:
        return (self._h1.value, self._h2.value)

    def push(self, ch: str) -> None:
        self._h1.push(ch)
        self._h2.push(ch)

    def slide(self, drop: str, add: str) -> None:
        self._h1.slide(drop, add)
        self._h2.slide(drop, add)

    def set_window(self, s: str) -> None:
        self._h1.set_window(s)
        self._h2.set_window(s)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DoubleHash):
            return NotImplemented
        return self.value == other.value

    def __repr__(self) -> str:
        return f"DoubleHash(value={self.value})"


def rabin_karp_search(
    text: str,
    pattern: str,
    *,
    double_hash: bool = False,
) -> list[int]:
    """Return all start indices of *pattern* in *text* using Rabin-Karp.

    O(|text| + |pattern|) expected time.  When *double_hash* is True uses
    two independent hashes to virtually eliminate false positives.
    """
    if not pattern:
        return list(range(len(text) + 1))
    n, m = len(text), len(pattern)
    if m > n:
        return []
    if m == 0:
        return list(range(n + 1))

    if double_hash:
        return _rabin_karp_double(text, pattern, m, n)
    return _rabin_karp_single(text, pattern, m, n)


def _rabin_karp_single(text: str, pattern: str, m: int, n: int) -> list[int]:
    base = 911
    mod = 1_000_000_007
    h = PolynomialRollingHash(base, mod)
    p = PolynomialRollingHash(base, mod)
    p.set_window(pattern)
    h.set_window(text[:m])
    target = p.value

    result: list[int] = []
    if h.value == target and text[:m] == pattern:
        result.append(0)
    for i in range(1, n - m + 1):
        h.slide(text[i - 1], text[i + m - 1])
        if h.value == target and text[i : i + m] == pattern:
            result.append(i)
    return result


def _rabin_karp_double(text: str, pattern: str, m: int, n: int) -> list[int]:
    h = DoubleHash()
    p = DoubleHash()
    p.set_window(pattern)
    h.set_window(text[:m])
    target = p.value

    result: list[int] = []
    if h.value == target and text[:m] == pattern:
        result.append(0)
    for i in range(1, n - m + 1):
        h.slide(text[i - 1], text[i + m - 1])
        if h.value == target and text[i : i + m] == pattern:
            result.append(i)
    return result


def build_rolling_hash(pattern: str, base: int = 911, mod: int = 1_000_000_007) -> int:
    """Compute the non-rolling polynomial hash of *pattern*."""
    h = PolynomialRollingHash(base, mod)
    h.set_window(pattern)
    return h.value


ENGINES: dict[str, Callable[[str, str], list[int]]] = {
    "rabin_karp": rabin_karp_search,
    "rabin_karp_double": lambda t, p: rabin_karp_search(t, p, double_hash=True),
}
