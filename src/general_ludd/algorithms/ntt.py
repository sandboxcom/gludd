"""Number Theoretic Transform: NTT, INTT, convolution under modulus.

Pure-Python, stdlib only. Works with NTT-friendly primes (p = k * 2^n + 1).
"""

from __future__ import annotations

MOD = 998244353
"""Default NTT-friendly prime: 119 * 2^23 + 1."""


def _mod_pow(base: int, exp: int, mod: int) -> int:
    result = 1
    base %= mod
    while exp:
        if exp & 1:
            result = (result * base) % mod
        base = (base * base) % mod
        exp >>= 1
    return result


def primitive_root(mod: int) -> int:
    """Find a primitive root modulo *mod* (mod must be prime)."""
    phi = mod - 1
    factors: list[int] = []
    m = phi
    p = 2
    while p * p <= m:
        if m % p == 0:
            factors.append(p)
            while m % p == 0:
                m //= p
        p += 1 if p == 2 else 2
    if m > 1:
        factors.append(m)
    for g in range(2, mod):
        ok = True
        for q in factors:
            if _mod_pow(g, phi // q, mod) == 1:
                ok = False
                break
        if ok:
            return g
    raise RuntimeError("no primitive root found")


def _bit_reverse(x: int, bits: int) -> int:
    result = 0
    for _ in range(bits):
        result = (result << 1) | (x & 1)
        x >>= 1
    return result


def _next_power_of_two(n: int) -> int:
    if n == 0:
        return 1
    return 1 << (n - 1).bit_length()


def ntt(a: list[int], mod: int = MOD, invert: bool = False) -> list[int]:
    """Cooley-Tukey NTT.  Length must be a power of 2.

    When invert=True computes the inverse NTT (divided by n).
    """
    n = len(a)
    a = a[:]
    bits = n.bit_length() - 1
    rev = [_bit_reverse(i, bits) for i in range(n)]
    for i in range(n):
        if i < rev[i]:
            a[i], a[rev[i]] = a[rev[i]], a[i]
    g = primitive_root(mod)
    length = 2
    while length <= n:
        wlen = _mod_pow(g, (mod - 1) // length, mod)
        if invert:
            wlen = _mod_pow(wlen, mod - 2, mod)
        for i in range(0, n, length):
            w = 1
            half = length // 2
            for j in range(half):
                u = a[i + j]
                v = (a[i + j + half] * w) % mod
                a[i + j] = (u + v) % mod
                a[i + j + half] = (u - v) % mod
                w = (w * wlen) % mod
        length <<= 1
    if invert:
        inv_n = _mod_pow(n, mod - 2, mod)
        for i in range(n):
            a[i] = (a[i] * inv_n) % mod
    return a


def intt(a: list[int], n: int, mod: int = MOD) -> list[int]:
    """Inverse NTT — pads a to length n (must be power of 2), then inverts."""
    padded = a[:]
    if len(padded) < n:
        padded += [0] * (n - len(padded))
    return ntt(padded, mod, invert=True)


def ntt_convolve(a: list[int], b: list[int], mod: int = MOD) -> list[int]:
    """Convolve two integer sequences via NTT: intt(ntt(a_padded) * ntt(b_padded))."""
    result_len = len(a) + len(b) - 1
    n = _next_power_of_two(result_len)
    fa = ntt(a + [0] * (n - len(a)), mod)
    fb = ntt(b + [0] * (n - len(b)), mod)
    fc = [(fa[i] * fb[i]) % mod for i in range(n)]
    result = intt(fc, n, mod)
    return result[:result_len]


def ntt_multiply(p: list[int], q: list[int], mod: int = MOD) -> list[int]:
    """Multiply two polynomials (coefficient lists, ascending powers) via NTT."""
    return ntt_convolve(p, q, mod)
