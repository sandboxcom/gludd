"""RSA cryptosystem: key generation, PKCS#1 v1.5 padding, encryption,
decryption, and CRT-accelerated decryption.

Pure-Python, stdlib only. Uses Miller-Rabin primality testing and
square-and-multiply modular exponentiation.
"""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass


@dataclass(slots=True)
class RSAKey:
    """An RSA key pair (public or private)."""

    n: int
    e: int
    d: int | None = None
    p: int | None = None
    q: int | None = None

    @property
    def is_private(self) -> bool:
        return self.d is not None

    @property
    def public_key(self) -> RSAKey:
        return RSAKey(n=self.n, e=self.e)


class RSAError(Exception):
    """Base exception for RSA operations."""


class DecryptionError(RSAError):
    """Raised when decryption fails (e.g. bad padding)."""


_SMALL_PRIMES = frozenset(
    {
        2,
        3,
        5,
        7,
        11,
        13,
        17,
        19,
        23,
        29,
        31,
        37,
        41,
        43,
        47,
        53,
        59,
        61,
        67,
        71,
        73,
        79,
        83,
        89,
        97,
        101,
        103,
        107,
        109,
        113,
        127,
        131,
        137,
        139,
        149,
        151,
        157,
        163,
        167,
        173,
        179,
        181,
        191,
        193,
        197,
        199,
        211,
        223,
        227,
        229,
        233,
        239,
        241,
        251,
        257,
        263,
        269,
        271,
        277,
        281,
        283,
        293,
        307,
        311,
        313,
        317,
        331,
        337,
        347,
        349,
        353,
        359,
        367,
        373,
        379,
        383,
        389,
        397,
        401,
        409,
        419,
        421,
        431,
        433,
        439,
        443,
        449,
        457,
        461,
        463,
        467,
        479,
        487,
        491,
        499,
        503,
        509,
        521,
        523,
        541,
    }
)


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    """Miller-Rabin probabilistic primality test."""
    if n < 2:
        return False
    if n in _SMALL_PRIMES:
        return True
    if n % 2 == 0:
        return False

    for small in _SMALL_PRIMES:
        if n % small == 0:
            return False

    d = n - 1
    s = 0
    while d % 2 == 0:
        d //= 2
        s += 1

    n.bit_length()
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _generate_prime(bits: int, e: int = 65537) -> int:
    """Generate a prime of exactly *bits* length, coprime to *e*."""
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate) and math.gcd(candidate - 1, e) == 1:
            return candidate


def _mod_inverse(a: int, m: int) -> int:
    """Extended Euclidean algorithm — returns x such that (a * x) % m == 1."""
    t, newt = 0, 1
    r, newr = m, a
    while newr != 0:
        quotient = r // newr
        t, newt = newt, t - quotient * newt
        r, newr = newr, r - quotient * newr
    if r > 1:
        raise RSAError(f"no modular inverse: gcd({a}, {m}) = {r} != 1")
    if t < 0:
        t += m
    return t


def _i2osp(x: int, length: int) -> bytes:
    """Integer-to-Octet-String primitive (RFC 8017 §4.1)."""
    return x.to_bytes(length, byteorder="big")


def _os2ip(octets: bytes) -> int:
    """Octet-String-to-Integer primitive (RFC 8017 §4.2)."""
    return int.from_bytes(octets, byteorder="big")


def generate_keypair(bits: int = 2048, e: int = 65537) -> RSAKey:
    """Generate a new RSA key pair with the specified modulus size.

    Args:
        bits: Modulus size in bits (must be >= 512 and even).
        e: Public exponent (default F4 = 65537).

    Returns:
        An RSAKey with n, e, d, p, q populated.
    """
    if bits < 512:
        raise RSAError(f"key size must be >= 512 bits, got {bits}")
    if bits % 2 != 0:
        raise RSAError(f"key size must be even, got {bits}")
    if e % 2 == 0:
        raise RSAError(f"public exponent must be odd, got {e}")

    half = bits // 2
    while True:
        p = _generate_prime(half, e)
        q = _generate_prime(half, e)
        if p != q:
            break

    n = p * q
    phi = (p - 1) * (q - 1)
    d = _mod_inverse(e, phi)

    return RSAKey(n=n, e=e, d=d, p=p, q=q)


# ── PKCS#1 v1.5 padding (RFC 8017 §7.2) ──────────────────────────────


def pkcs1_v15_encode(message: bytes, k: int) -> bytes:
    """EME-PKCS1-v1_5-ENCODE. Returns *k*-length padded message.

    Args:
        message: Plaintext to pad.
        k: Length of the RSA modulus in bytes.

    Returns:
        Padded message of exactly *k* bytes.
    """
    if len(message) > k - 11:
        raise RSAError(f"message too long: {len(message)} > {k - 11}")
    ps_len = k - 3 - len(message)
    ps = b""
    while ps_len > 0:
        b = secrets.randbits(8).to_bytes(1, "big")
        if b[0] != 0:
            ps += b
            ps_len -= 1
    return b"\x00\x02" + ps + b"\x00" + message


def pkcs1_v15_decode(encoded: bytes, k: int) -> bytes:
    """EME-PKCS1-v1_5-DECODE. Strips padding, returns plaintext.

    Args:
        encoded: *k*-length padded message.
        k: Length of the RSA modulus in bytes.

    Returns:
        Plaintext with padding stripped.

    Raises:
        DecryptionError: If the padding is malformed.
    """
    if len(encoded) != k:
        raise DecryptionError(f"decrypted data length {len(encoded)} != expected {k}")
    if encoded[:2] != b"\x00\x02":
        raise DecryptionError("bad PKCS#1 v1.5 leading bytes")
    sep = encoded.find(b"\x00", 2)
    if sep < 10:
        raise DecryptionError(f"bad PKCS#1 v1.5 padding separator at index {sep}")
    return encoded[sep + 1 :]


# ── RSA core operations ───────────────────────────────────────────────


def encrypt(key: RSAKey, plaintext: bytes) -> bytes:
    """RSAES-PKCS1-v1_5-ENCRYPT.

    Args:
        key: Public key (n, e).
        plaintext: Data to encrypt.

    Returns:
        Ciphertext as bytes (same length as modulus in bytes).
    """
    k = (key.n.bit_length() + 7) // 8
    padded = pkcs1_v15_encode(plaintext, k)
    m = _os2ip(padded)
    c = pow(m, key.e, key.n)
    return _i2osp(c, k)


def decrypt(key: RSAKey, ciphertext: bytes) -> bytes:
    """RSAES-PKCS1-v1_5-DECRYPT (basic, non-CRT).

    Args:
        key: Private key (n, e, d).
        ciphertext: Data to decrypt.

    Returns:
        Plaintext bytes.

    Raises:
        DecryptionError: If padding is invalid.
    """
    if key.d is None:
        raise RSAError("decrypt requires a private key with d")
    k = (key.n.bit_length() + 7) // 8
    c = _os2ip(ciphertext)
    m = pow(c, key.d, key.n)
    encoded = _i2osp(m, k)
    return pkcs1_v15_decode(encoded, k)


def decrypt_crt(key: RSAKey, ciphertext: bytes) -> bytes:
    """RSA decryption using the Chinese Remainder Theorem (approx 4x faster).

    Requires *p* and *q* on the key.  Fails with RSAError if either
    is missing.

    Args:
        key: Private key (n, e, d, p, q).
        ciphertext: Data to decrypt.

    Returns:
        Plaintext bytes.

    Raises:
        RSAError: If p or q is missing from the key.
    """
    p = key.p
    q = key.q
    d = key.d
    if p is None or q is None or d is None:
        raise RSAError("decrypt_crt requires p, q, and d on the key")

    k = (key.n.bit_length() + 7) // 8
    c = _os2ip(ciphertext)

    dp = d % (p - 1)
    dq = d % (q - 1)
    qinv = _mod_inverse(q, p)

    m1 = pow(c, dp, p)
    m2 = pow(c, dq, q)
    h = (qinv * (m1 - m2)) % p
    m = m2 + h * q

    encoded = _i2osp(m, k)
    return pkcs1_v15_decode(encoded, k)
