"""Kyber ML-KEM (FIPS 203): post-quantum key encapsulation mechanism.

Implements Kyber-512, Kyber-768, Kyber-1024 over the polynomial ring
Z_q[X]/(X^256 + 1) with q = 3329.  Uses NTT for fast polynomial
multiplication and the Fujisaki-Okamoto transform for IND-CCA2 security.

Pure-Python, stdlib only.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

# ── Polynomial ring constants ──────────────────────────────────────────

_N = 256
_Q = 3329

_ZETAS: tuple[int, ...] = (
    1,
    1729,
    2580,
    3289,
    2642,
    630,
    1897,
    848,
    1062,
    1919,
    193,
    797,
    2786,
    3260,
    569,
    1746,
    296,
    2447,
    1339,
    1476,
    3046,
    56,
    2240,
    1333,
    1426,
    2094,
    535,
    2882,
    2393,
    2879,
    1974,
    821,
    289,
    331,
    3253,
    1756,
    1197,
    2304,
    2277,
    2055,
    650,
    1977,
    2513,
    632,
    2865,
    33,
    1320,
    1915,
    2319,
    1435,
    807,
    452,
    1438,
    2868,
    1534,
    2402,
    2647,
    2617,
    1481,
    648,
    2474,
    3110,
    1227,
    910,
    17,
    2761,
    583,
    2649,
    1637,
    723,
    2288,
    1100,
    1409,
    2662,
    3281,
    233,
    756,
    2156,
    3015,
    3050,
    1703,
    1651,
    2789,
    1789,
    1847,
    952,
    1461,
    2687,
    939,
    2308,
    2437,
    2388,
    733,
    2337,
    268,
    641,
    1584,
    2298,
    2037,
    3220,
    375,
    2549,
    2090,
    1645,
    1063,
    319,
    2773,
    757,
    2099,
    561,
    2466,
    2594,
    2804,
    1092,
    403,
    1026,
    1143,
    2150,
    2775,
    886,
    1722,
    1212,
    1874,
    1029,
    2110,
    2935,
    885,
    2154,
)


_Q_INV_I16: int = -3327  # q^{-1} mod 2^{16} as signed int16


def _mont_reduce(a: int) -> int:
    """Montgomery reduction: returns a * 2^{-16} mod q, emulating C int32."""
    u = (a & 0xFFFF) * _Q_INV_I16
    u &= 0xFFFFFFFF
    if u >= 0x80000000:
        u -= 0x100000000
    t = u * _Q
    t &= 0xFFFFFFFF
    if t >= 0x80000000:
        t -= 0x100000000
    return (a - t) >> 16


def _barrett_reduce(a: int) -> int:
    """Barrett reduction: returns a mod q for a in [-q*2^16, q*2^16]."""
    v = ((1 << 26) + _Q // 2) // _Q
    t = (v * a + (1 << 25)) >> 26
    t *= _Q
    return a - t


def _csubq(a: int) -> int:
    """Conditionally subtract q if a >= q."""
    a -= _Q
    a += (a >> 31) & _Q
    return a


# ── NTT / InvNTT ──────────────────────────────────────────────────────


def ntt(r: tuple[int, ...]) -> tuple[int, ...]:
    """Forward NTT of a length-256 polynomial."""
    assert len(r) == _N
    a = list(r)
    k = 0
    for length in (128, 64, 32, 16, 8, 4, 2):
        for start in range(0, 256, 2 * length):
            zeta = _ZETAS[k]
            k += 1
            for j in range(start, start + length):
                t = _mont_reduce(zeta * a[j + length])
                a[j + length] = _barrett_reduce(a[j] - t)
                a[j] = _barrett_reduce(a[j] + t)
    return tuple(a)


def inv_ntt(r: tuple[int, ...]) -> tuple[int, ...]:
    """Inverse NTT of a length-256 polynomial."""
    assert len(r) == _N
    a = list(r)
    k = 0
    for length in (2, 4, 8, 16, 32, 64, 128):
        for start in range(0, 256, 2 * length):
            zeta = _Q - _ZETAS[127 - k]
            k += 1
            for j in range(start, start + length):
                t = a[j]
                a[j] = _barrett_reduce(t + a[j + length])
                a[j + length] = _barrett_reduce(t - a[j + length])
                a[j + length] = _mont_reduce(zeta * a[j + length])
    f = 3303
    for j in range(256):
        a[j] = _mont_reduce(a[j] * f)
    return tuple(a)


def poly_add(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    return tuple((x + y) % _Q for x, y in zip(a, b, strict=False))


def poly_sub(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    return tuple((x - y) % _Q for x, y in zip(a, b, strict=False))


def poly_mul(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    a_hat = ntt(a)
    b_hat = ntt(b)
    c_hat = tuple(_mont_reduce(x * y) for x, y in zip(a_hat, b_hat, strict=False))
    return inv_ntt(c_hat)


def poly_mul_mont(a_hat: tuple[int, ...], b_hat: tuple[int, ...]) -> tuple[int, ...]:
    c_hat = tuple(_mont_reduce(x * y) for x, y in zip(a_hat, b_hat, strict=False))
    return inv_ntt(c_hat)


# ── Serialize / deserialize polynomials ───────────────────────────────


def poly_to_bytes(poly: tuple[int, ...]) -> bytes:
    out = bytearray(384)
    for i in range(256):
        coeff = poly[i] & 0xFFF
        out[i * 3] = coeff & 0xFF
        out[i * 3 + 1] = (coeff >> 8) & 0x0F
        if i < 255:
            out[i * 3 + 1] |= (poly[i + 1] & 0x0F) << 4
    return bytes(out)


def poly_from_bytes(data: bytes) -> tuple[int, ...]:
    coeffs: list[int] = [0] * 256
    for i in range(256):
        b0 = data[i * 3] if i * 3 < len(data) else 0
        b1 = data[i * 3 + 1] if i * 3 + 1 < len(data) else 0
        b2 = data[i * 3 + 2] if i * 3 + 2 < len(data) else 0
        coeffs[i] = (b0 | ((b1 & 0x0F) << 8) | (b2 << 12)) & 0xFFF
    return tuple(coeffs)


def poly_to_msg(poly: tuple[int, ...]) -> bytes:
    bits = bytearray(32)
    for i in range(256):
        bit = 1 if poly[i] > _Q // 2 else 0
        bits[i >> 3] |= bit << (i & 7)
    return bytes(bits)


def poly_from_msg(data: bytes) -> tuple[int, ...]:
    coeffs = list((_Q + 1) // 2 if ((data[i >> 3] >> (i & 7)) & 1) else 0 for i in range(256))
    return tuple(coeffs)


# ── XOF / PRF ─────────────────────────────────────────────────────────


def _prf(seed: bytes, length: int, domain: int) -> bytes:
    return hashlib.shake_256(seed + bytes([domain])).digest(length)


def _hash_h(msg: bytes) -> bytes:
    return hashlib.sha3_256(msg).digest()


def _hash_g(msg: bytes) -> bytes:
    return hashlib.sha3_512(msg).digest()


# ── CBD sampling ──────────────────────────────────────────────────────


def _cbd(noise: bytes, eta: int) -> tuple[int, ...]:
    bits: list[int] = []
    for byte in noise:
        for s in range(7, -1, -1):
            bits.append((byte >> s) & 1)
    coeffs: list[int] = [0] * 256
    for i in range(256):
        a = 0
        b = 0
        base = 2 * i * eta
        for j in range(eta):
            if base + j < len(bits):
                a += bits[base + j]
            if base + eta + j < len(bits):
                b += bits[base + eta + j]
        coeffs[i] = a - b
    return tuple(coeffs)


# ── Compression ───────────────────────────────────────────────────────


def compress(poly: tuple[int, ...], d: int) -> tuple[int, ...]:
    divisor = 1 << d
    result: list[int] = []
    for x in poly:
        x = x % _Q
        val = (x * divisor + _Q // 2) // _Q
        result.append(val % divisor)
    return tuple(result)


def decompress(poly: tuple[int, ...], d: int) -> tuple[int, ...]:
    divisor = 1 << d
    result: list[int] = []
    for x in poly:
        val = (x * _Q + divisor // 2) // divisor
        result.append(val % _Q)
    return tuple(result)


# ── Matrix A generation ───────────────────────────────────────────────


def _gen_matrix(seed: bytes, k: int, transposed: bool = False) -> list[list[tuple[int, ...]]]:
    mat: list[list[tuple[int, ...]]] = []
    for i in range(k):
        row: list[tuple[int, ...]] = []
        for j in range(k):
            ii, jj = (j, i) if transposed else (i, j)
            buf = hashlib.shake_128(seed + bytes([ii, jj])).digest(3 * 256)
            row.append(poly_from_bytes(buf))
        mat.append(row)
    return mat


# ── Vector helpers ────────────────────────────────────────────────────


def _vec_add(a: list[tuple[int, ...]], b: list[tuple[int, ...]]) -> list[tuple[int, ...]]:
    return [poly_add(x, y) for x, y in zip(a, b, strict=False)]


def _vec_ntt(v: list[tuple[int, ...]]) -> list[tuple[int, ...]]:
    return [ntt(p) for p in v]


def _mat_vec_mul(mat: list[list[tuple[int, ...]]], vec: list[tuple[int, ...]]) -> list[tuple[int, ...]]:
    k = len(vec)
    vec_hat = _vec_ntt(vec)
    result: list[tuple[int, ...]] = []
    for i in range(k):
        acc = [0] * _N
        for j in range(k):
            m_hat = ntt(mat[i][j])
            for idx in range(_N):
                prod = _mont_reduce(m_hat[idx] * vec_hat[j][idx])
                acc[idx] = (acc[idx] + prod) % _Q
        result.append(inv_ntt(tuple(acc)))
    return result


def _vec_to_bytes(v: list[tuple[int, ...]]) -> bytes:
    return b"".join(poly_to_bytes(p) for p in v)


def _vec_from_bytes(data: bytes, k: int) -> list[tuple[int, ...]]:
    stride = 384
    vec: list[tuple[int, ...]] = []
    for i in range(k):
        chunk = data[i * stride : (i + 1) * stride]
        vec.append(poly_from_bytes(chunk))
    return vec


# ── Parameters ────────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class KyberParams:
    k: int
    eta1: int
    eta2: int
    du: int
    dv: int


PARAMS_512 = KyberParams(k=2, eta1=3, eta2=2, du=10, dv=4)
PARAMS_768 = KyberParams(k=3, eta1=2, eta2=2, du=10, dv=4)
PARAMS_1024 = KyberParams(k=4, eta1=2, eta2=2, du=11, dv=5)


# ── KyberError ────────────────────────────────────────────────────────


class KyberError(Exception):
    """Base exception for Kyber KEM operations."""


# ── K-PKE layer ──────────────────────────────────────────────────────


def _sample_vec(noise: bytes, k: int, eta: int) -> list[tuple[int, ...]]:
    """Sample k polynomials from noise bytes using CBD with parameter eta."""
    eta_bytes = 64 * eta
    vec: list[tuple[int, ...]] = []
    for i in range(k):
        chunk = noise[i * eta_bytes : (i + 1) * eta_bytes]
        vec.append(_cbd(chunk, eta))
    return vec


def _vec_mul_norm(a: list[tuple[int, ...]], b: list[tuple[int, ...]]) -> tuple[int, ...]:
    """Dot product of two vectors in NTT domain, returned in normal domain."""
    assert len(a) == len(b)
    acc = [0] * _N
    for i in range(len(a)):
        for idx in range(_N):
            prod = _mont_reduce(a[i][idx] * b[i][idx])
            acc[idx] = (acc[idx] + prod) % _Q
    return inv_ntt(tuple(acc))


def _pke_keygen(params: KyberParams) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]], bytes]:
    d = os.urandom(32)
    g = _hash_g(d)
    seed = g[:32]
    mat_a = _gen_matrix(seed, params.k, transposed=True)
    noise = _prf(seed, params.k * 64 * params.eta1, domain=0)
    noise2 = _prf(seed, params.k * 64 * params.eta1, domain=1)
    s = _sample_vec(noise, params.k, params.eta1)
    e = _sample_vec(noise2, params.k, params.eta1)
    t = _vec_add(_mat_vec_mul(mat_a, s), e)
    t_hat = _vec_ntt(t)
    s_hat = _vec_ntt(s)
    pk_bytes = _vec_to_bytes(t_hat) + seed
    return t_hat, s_hat, pk_bytes


def _pke_encrypt(pk_t_hat: list[tuple[int, ...]], seed_a: bytes, msg: bytes, r: bytes, params: KyberParams) -> bytes:
    mat_a = _gen_matrix(seed_a, params.k, transposed=True)
    m_poly = poly_from_msg(msg)
    eta1_bytes = 64 * params.eta1
    y = _sample_vec(r[: params.k * eta1_bytes], params.k, params.eta1)
    e1_offset = params.k * eta1_bytes
    e1 = _sample_vec(r[e1_offset : e1_offset + params.k * 64 * params.eta2], params.k, params.eta2)
    e2_offset = e1_offset + params.k * 64 * params.eta2
    e2 = _cbd(r[e2_offset : e2_offset + 64 * params.eta2], params.eta2)
    mat_y = _mat_vec_mul(mat_a, y)
    u = [poly_add(mat_y[i], e1[i]) for i in range(params.k)]
    u_comp = [compress(p, params.du) for p in u]
    v_normal = _vec_mul_norm(pk_t_hat, _vec_ntt(y))
    v = poly_add(poly_add(v_normal, m_poly), e2)
    v_comp = compress(v, params.dv)
    ct = _ct_from_components(u_comp, v_comp, params.du)
    return ct


def _ct_from_components(u_comp: list[tuple[int, ...]], v_comp: tuple[int, ...], du: int) -> bytes:
    ct = bytearray()
    for ui in u_comp:
        coeffs = list(ui)
        buf = 0
        for i, c in enumerate(coeffs):
            buf |= int(c) << (du * (i % (8 // du)))
            if i % (8 // du) == (8 // du) - 1 or i == len(coeffs) - 1:
                ct.append(buf & 0xFF)
                if du > 4:
                    ct.append((buf >> 8) & 0xFF)
                buf = 0
    buf = 0
    for i, c in enumerate(list(v_comp)[:256]):
        buf |= int(c) << (4 * (i % 2))
        if i % 2 == 1:
            ct.append(buf & 0xFF)
            buf = 0
    return bytes(ct)


def _pke_decrypt(s_hat: list[tuple[int, ...]], ct: bytes, params: KyberParams) -> bytes:
    u = _u_from_ct(ct, params)
    v = _v_from_ct(ct, params)
    u_hat = _vec_ntt(u)
    m_val = _vec_mul_norm(s_hat, u_hat)
    m = tuple(((int(v[i]) - int(m_val[i])) % _Q) for i in range(_N))
    return poly_to_msg(tuple(m))


def _u_from_ct(ct: bytes, params: KyberParams) -> list[tuple[int, ...]]:
    du = params.du
    stride = 32 * du
    u: list[tuple[int, ...]] = []
    for i in range(params.k):
        chunk = ct[i * stride : (i + 1) * stride]
        coeffs: list[int] = [0] * 256
        mask = (1 << du) - 1
        for j in range(256):
            byte_idx = (j * du) // 8
            bit_off = (j * du) % 8
            val = 0
            for b in range(du):
                bb = byte_idx + (bit_off + b) // 8
                bo = (bit_off + b) % 8
                if bb < len(chunk):
                    val |= ((chunk[bb] >> bo) & 1) << b
            coeffs[j] = val & mask
        u.append(decompress(tuple(coeffs), du))
    return u


def _v_from_ct(ct: bytes, params: KyberParams) -> tuple[int, ...]:
    du = params.du
    dv = params.dv
    v_start = params.k * 32 * du
    coeffs: list[int] = [0] * 256
    mask = (1 << dv) - 1
    for j in range(256):
        bit_off = j * dv
        val = 0
        for b in range(dv):
            bb = v_start + (bit_off + b) // 8
            bo = (bit_off + b) % 8
            if bb < len(ct):
                val |= ((ct[bb] >> bo) & 1) << b
        coeffs[j] = val & mask
    return decompress(tuple(coeffs), dv)


# ── KEM layer ─────────────────────────────────────────────────────────


def keygen(params: KyberParams = PARAMS_512) -> tuple[bytes, bytes]:
    _t_hat, s_hat, pk_bytes = _pke_keygen(params)
    hpk = _hash_h(pk_bytes)
    z = os.urandom(32)
    sk_bytes = _vec_to_bytes(s_hat) + pk_bytes + hpk + z
    return pk_bytes, sk_bytes


def encapsulate(pk_bytes: bytes, params: KyberParams = PARAMS_512) -> tuple[bytes, bytes]:
    t_hat = _vec_from_bytes(pk_bytes[: params.k * 384], params.k)
    seed_a = pk_bytes[params.k * 384 : params.k * 384 + 32]
    t_hat_ntt = _vec_ntt(t_hat)
    m = os.urandom(32)
    m_hash = _hash_h(m)
    kr = _hash_g(m_hash + _hash_h(pk_bytes))
    K, r = kr[:32], kr[32:]
    ct = _pke_encrypt(t_hat_ntt, seed_a, m_hash, r, params)
    return ct, K


def decapsulate(ct: bytes, sk_bytes: bytes, params: KyberParams = PARAMS_512) -> bytes:
    s_hat_len = params.k * 384
    pk_len = params.k * 384 + 32
    s_hat = _vec_from_bytes(sk_bytes[:s_hat_len], params.k)
    s_hat_ntt = _vec_ntt(s_hat)
    pk_bytes = sk_bytes[s_hat_len : s_hat_len + pk_len]
    hpk = sk_bytes[s_hat_len + pk_len : s_hat_len + pk_len + 32]
    z = sk_bytes[s_hat_len + pk_len + 32 : s_hat_len + pk_len + 64]
    m_prime = _pke_decrypt(s_hat_ntt, ct, params)
    kr = _hash_g(m_prime + hpk)
    K_prime, r_prime = kr[:32], kr[32:]
    t_hat = _vec_from_bytes(pk_bytes[: params.k * 384], params.k)
    seed_a = pk_bytes[params.k * 384 :]
    t_hat_ntt_check = _vec_ntt(t_hat)
    ct_check = _pke_encrypt(t_hat_ntt_check, seed_a, m_prime, r_prime, params)
    if ct_check != ct:
        return _hash_h(z + ct)
    return K_prime


# ── Convenience functions ─────────────────────────────────────────────


def keygen_512() -> tuple[bytes, bytes]:
    return keygen(PARAMS_512)


def keygen_768() -> tuple[bytes, bytes]:
    return keygen(PARAMS_768)


def keygen_1024() -> tuple[bytes, bytes]:
    return keygen(PARAMS_1024)


def encapsulate_512(pk: bytes) -> tuple[bytes, bytes]:
    return encapsulate(pk, PARAMS_512)


def encapsulate_768(pk: bytes) -> tuple[bytes, bytes]:
    return encapsulate(pk, PARAMS_768)


def encapsulate_1024(pk: bytes) -> tuple[bytes, bytes]:
    return encapsulate(pk, PARAMS_1024)


def decapsulate_512(ct: bytes, sk: bytes) -> bytes:
    return decapsulate(ct, sk, PARAMS_512)


def decapsulate_768(ct: bytes, sk: bytes) -> bytes:
    return decapsulate(ct, sk, PARAMS_768)


def decapsulate_1024(ct: bytes, sk: bytes) -> bytes:
    return decapsulate(ct, sk, PARAMS_1024)
