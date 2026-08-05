"""RSA cryptosystem backed by the `cryptography` library.

Public API: RSAKey, RSAError, DecryptionError, generate_keypair, encrypt,
decrypt, decrypt_crt, pkcs1_v15_encode, pkcs1_v15_decode.
"""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric import rsa as crypto_rsa
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateNumbers,
    RSAPublicNumbers,
)


@dataclass(slots=True)
class RSAKey:
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
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate) and math.gcd(candidate - 1, e) == 1:
            return candidate


def _mod_inverse(a: int, m: int) -> int:
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
    return x.to_bytes(length, byteorder="big")


def _os2ip(octets: bytes) -> int:
    return int.from_bytes(octets, byteorder="big")


def pkcs1_v15_encode(message: bytes, k: int) -> bytes:
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
    if len(encoded) != k:
        raise DecryptionError(f"decrypted data length {len(encoded)} != expected {k}")
    if encoded[:2] != b"\x00\x02":
        raise DecryptionError("bad PKCS#1 v1.5 leading bytes")
    sep = encoded.find(b"\x00", 2)
    if sep < 10:
        raise DecryptionError(f"bad PKCS#1 v1.5 padding separator at index {sep}")
    return encoded[sep + 1 :]


def _crypto_key_to_rsa_key(private_key: crypto_rsa.RSAPrivateKey) -> RSAKey:
    numbers = private_key.private_numbers()
    return RSAKey(
        n=numbers.public_numbers.n,
        e=numbers.public_numbers.e,
        d=numbers.d,
        p=numbers.p,
        q=numbers.q,
    )


def _build_private_key(key: RSAKey) -> crypto_rsa.RSAPrivateKey:
    if key.d is None:
        raise RSAError("private key requires d")
    if key.p is None or key.q is None:
        raise RSAError("private key requires p and q")
    dmp1 = key.d % (key.p - 1)
    dmq1 = key.d % (key.q - 1)
    iqmp = _mod_inverse(key.q, key.p)
    private_numbers = RSAPrivateNumbers(
        p=key.p,
        q=key.q,
        d=key.d,
        dmp1=dmp1,
        dmq1=dmq1,
        iqmp=iqmp,
        public_numbers=RSAPublicNumbers(e=key.e, n=key.n),
    )
    return private_numbers.private_key()


def generate_keypair(bits: int = 2048, e: int = 65537) -> RSAKey:
    if bits < 512:
        raise RSAError(f"key size must be >= 512 bits, got {bits}")
    if bits % 2 != 0:
        raise RSAError(f"key size must be even, got {bits}")
    if e % 2 == 0:
        raise RSAError(f"public exponent must be odd, got {e}")

    private_key = crypto_rsa.generate_private_key(
        public_exponent=e,
        key_size=bits,
    )
    return _crypto_key_to_rsa_key(private_key)


def encrypt(key: RSAKey, plaintext: bytes) -> bytes:
    public_numbers = RSAPublicNumbers(e=key.e, n=key.n)
    public_key = public_numbers.public_key()
    return public_key.encrypt(plaintext, padding.PKCS1v15())


def decrypt(key: RSAKey, ciphertext: bytes) -> bytes:
    private_key = _build_private_key(key)
    try:
        return private_key.decrypt(ciphertext, padding.PKCS1v15())
    except ValueError as exc:
        raise DecryptionError(str(exc)) from exc


def decrypt_crt(key: RSAKey, ciphertext: bytes) -> bytes:
    if key.p is None or key.q is None or key.d is None:
        raise RSAError("decrypt_crt requires p, q, and d on the key")
    return decrypt(key, ciphertext)
