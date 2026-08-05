"""Deep tests for HKDF, HMAC-KB KDF, and PBKDF2 key derivation."""

from __future__ import annotations

import hashlib
import hmac as std_hmac

import pytest

from general_ludd.algorithms.hkdf import (
    HASHLEN,
    HMAC_BLOCK_SIZE,
    HKDFError,
    _hmac_digest,
    _xor_bytes,
    hkdf,
    hkdf_expand,
    hkdf_extract,
    hmac_kb_kdf,
    pbkdf2,
)

# ── Test vectors (RFC 5869 Appendix A) ───────────────────────────────────

_RFC5869_IKM = bytes.fromhex("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b")
_RFC5869_SALT = bytes.fromhex("000102030405060708090a0b0c")
_RFC5869_INFO = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
_RFC5869_L = 42
_RFC5869_PRK = bytes.fromhex("077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5")
_RFC5869_OKM = bytes.fromhex("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865")

_RFC5869_IKM2 = bytes.fromhex(
    "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
    "202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f"
    "404142434445464748495051525354555758595a5b5c5d5e5f6061626364656667"
    "68696a6b6c6d6e6f707172737475767778797a7b7c7d7e7f8081828384858687"
    "88898a8b8c8d8e8f909192939495969798999a9b9c9d9e9fa0a1a2a3a4a5a6a7"
    "a8a9aaabacadaeafb0b1b2b3b4b5b6b7b8b9babbbcbdbebfc0c1c2c3c4c5c6c7"
    "c8c9cacbcccdcecfd0d1d2d3d4d5d6d7d8d9dadbdcdddedfe0e1e2e3e4e5e6e7"
    "e8e9eaebecedeeeff0f1f2f3f4f5f6f7f8f9fafbfcfdfeff"
)
_RFC5869_SALT2 = bytes.fromhex(
    "606162636465666768696a6b6c6d6e6f707172737475767778797a7b7c7d7e7f"
    "808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f"
    "a0a1a2a3a4a5a6a7a8a9aaabacadaeaf"
)
_RFC5869_INFO2 = bytes.fromhex(
    "b0b1b2b3b4b5b6b7b8b9babbbcbdbebfc0c1c2c3c4c5c6c7c8c9cacbcccdcecf"
    "d0d1d2d3d4d5d6d7d8d9dadbdcdddedfe0e1e2e3e4e5e6e7e8e9eaebecedeeef"
    "f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff"
)
_RFC5869_L2 = 82
_RFC5869_PRK2 = bytes.fromhex("06a6b88c5853361a06104c9ceb35b45cef760014904671014a193f40c15fc244")
_RFC5869_OKM2 = bytes.fromhex(
    "b11e398dc80327a1c8e7f78c596a49344f012eda2d4efad8a050cc4c19afa97c"
    "59045a99cac7827271cb41c65e590e09da3275600c2f09b8367793a9aca3db71"
    "cc30c58179ec3e87c14c01d5c1f3434f1d87"
)

_RFC5869_IKM_ZSALT = bytes.fromhex("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b")
_RFC5869_ZSALT_PRK = bytes.fromhex("19ef24a32c717b167f33a91d6f648bdf96596776afdb6377ac434c1c293ccb04")
_RFC5869_ZSALT_OKM = bytes.fromhex(
    "8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d9d201395faa4b61a96c8"
)


# ── _xor_bytes ──────────────────────────────────────────────────────────


def test_xor_empty():
    assert _xor_bytes(b"", b"") == b""


def test_xor_identity():
    assert _xor_bytes(b"abc", b"\x00\x00\x00") == b"abc"


def test_xor_symmetric():
    a = b"\x01\x02\x03"
    b = b"\x04\x05\x06"
    assert _xor_bytes(_xor_bytes(a, b), b) == a


# ── _hmac_digest matches stdlib hmac ────────────────────────────────────


def test_hmac_digest_matches_stdlib_sha256():
    key = b"secret"
    msg = b"hello"
    expected = std_hmac.new(key, msg, hashlib.sha256).digest()
    assert _hmac_digest(key, msg, "sha256") == expected


def test_hmac_digest_matches_stdlib_sha512():
    key = b"secret"
    msg = b"hello"
    expected = std_hmac.new(key, msg, hashlib.sha512).digest()
    assert _hmac_digest(key, msg, "sha512") == expected


def test_hmac_digest_key_longer_than_block():
    key = b"x" * 128
    msg = b"data"
    assert _hmac_digest(key, msg, "sha256") == std_hmac.new(key, msg, hashlib.sha256).digest()


def test_hmac_digest_same_key_same_msg():
    k, m = b"key", b"message"
    assert _hmac_digest(k, m, "sha256") == _hmac_digest(k, m, "sha256")


def test_hmac_digest_differs_on_key():
    h1 = _hmac_digest(b"key1", b"msg", "sha256")
    h2 = _hmac_digest(b"key2", b"msg", "sha256")
    assert h1 != h2


def test_hmac_digest_differs_on_msg():
    h1 = _hmac_digest(b"key", b"msg1", "sha256")
    h2 = _hmac_digest(b"key", b"msg2", "sha256")
    assert h1 != h2


# ── HKDF-Extract RFC 5869 test vectors ──────────────────────────────────


def test_hkdf_extract_test_vector():
    prk = hkdf_extract(_RFC5869_SALT, _RFC5869_IKM)
    assert prk == _RFC5869_PRK


def test_hkdf_extract_zero_salt():
    prk = hkdf_extract(b"", _RFC5869_IKM_ZSALT)
    assert prk == _RFC5869_ZSALT_PRK


def test_hkdf_extract_hashes_to_expected_length():
    prk = hkdf_extract(b"\x01" * 32, b"ikm", "sha256")
    assert len(prk) == 32
    prk512 = hkdf_extract(b"\x01" * 64, b"ikm", "sha512")
    assert len(prk512) == 64


def test_hkdf_extract_rejects_unsupported_hash():
    with pytest.raises(HKDFError, match="Unsupported"):
        hkdf_extract(b"salt", b"ikm", "md5")


# ── HKDF-Expand RFC 5869 test vectors ───────────────────────────────────


def test_hkdf_expand_test_vector():
    okm = hkdf_expand(_RFC5869_PRK, _RFC5869_INFO, _RFC5869_L)
    assert okm == _RFC5869_OKM


def test_hkdf_expand_long_ikm():
    okm = hkdf_expand(_RFC5869_PRK2, _RFC5869_INFO2, _RFC5869_L2)
    assert okm == _RFC5869_OKM2


def test_hkdf_expand_exact_block():
    okm = hkdf_expand(b"\x01" * 32, b"", 32)
    assert len(okm) == 32


def test_hkdf_expand_cross_block():
    okm = hkdf_expand(b"\x01" * 32, b"info", 63)
    assert len(okm) == 63


def test_hkdf_expand_multi_block():
    okm = hkdf_expand(b"\x01" * 32, b"info", 100)
    assert len(okm) == 100


def test_hkdf_expand_rejects_short_prk():
    with pytest.raises(HKDFError, match="PRK too short"):
        hkdf_expand(b"short", b"info", 32)


def test_hkdf_expand_rejects_excessive_length():
    with pytest.raises(HKDFError, match="exceeds max"):
        hkdf_expand(b"\x01" * 32, b"", 255 * 32 + 1)


def test_hkdf_expand_deterministic():
    a = hkdf_expand(b"\x02" * 32, b"ctx", 44)
    b_ = hkdf_expand(b"\x02" * 32, b"ctx", 44)
    assert a == b_


def test_hkdf_expand_sensitive_to_info():
    a = hkdf_expand(b"\x03" * 32, b"info1", 32)
    b_ = hkdf_expand(b"\x03" * 32, b"info2", 32)
    assert a != b_


# ── Combined HKDF ───────────────────────────────────────────────────────


def test_hkdf_combined_test_vector():
    okm = hkdf(_RFC5869_IKM, _RFC5869_L, _RFC5869_SALT, _RFC5869_INFO)
    assert okm == _RFC5869_OKM


def test_hkdf_combined_matches_extract_then_expand():
    prk = hkdf_extract(_RFC5869_SALT, _RFC5869_IKM)
    okm_direct = hkdf_expand(prk, _RFC5869_INFO, _RFC5869_L)
    okm_combined = hkdf(_RFC5869_IKM, _RFC5869_L, _RFC5869_SALT, _RFC5869_INFO)
    assert okm_combined == okm_direct


def test_hkdf_default_args_produce_output():
    okm = hkdf(b"ikm", 16)
    assert len(okm) == 16


def test_hkdf_sha512_output_length():
    okm = hkdf(b"ikm", 64, b"salt", b"info", "sha512")
    assert len(okm) == 64


def test_hkdf_sha256_vs_sha512_differ():
    h256 = hkdf(b"key", 32, b"s", b"i", "sha256")
    h512 = hkdf(b"key", 32, b"s", b"i", "sha512")
    assert h256 != h512


def test_hkdf_output_depends_on_ikm():
    k1 = hkdf(b"key1", 32, b"salt", b"info")
    k2 = hkdf(b"key2", 32, b"salt", b"info")
    assert k1 != k2


def test_hkdf_output_depends_on_salt():
    k1 = hkdf(b"key", 32, b"salt1", b"info")
    k2 = hkdf(b"key", 32, b"salt2", b"info")
    assert k1 != k2


def test_hkdf_output_depends_on_info():
    k1 = hkdf(b"key", 32, b"salt", b"info1")
    k2 = hkdf(b"key", 32, b"salt", b"info2")
    assert k1 != k2


# ── HMAC-KB KDF (NIST SP 800-108) ──────────────────────────────────────


def test_hmac_kb_kdf_produces_correct_length():
    dk = hmac_kb_kdf(b"secret", b"label", b"context", 48)
    assert len(dk) == 48


def test_hmac_kb_kdf_zero_length():
    assert hmac_kb_kdf(b"key", b"L", b"C", 0) == b""


def test_hmac_kb_kdf_deterministic():
    a = hmac_kb_kdf(b"key", b"label", b"ctx", 64)
    b_ = hmac_kb_kdf(b"key", b"label", b"ctx", 64)
    assert a == b_


def test_hmac_kb_kdf_sensitive_to_key():
    a = hmac_kb_kdf(b"key1", b"L", b"C", 32)
    b_ = hmac_kb_kdf(b"key2", b"L", b"C", 32)
    assert a != b_


def test_hmac_kb_kdf_sensitive_to_label():
    a = hmac_kb_kdf(b"key", b"L1", b"C", 32)
    b_ = hmac_kb_kdf(b"key", b"L2", b"C", 32)
    assert a != b_


def test_hmac_kb_kdf_sensitive_to_context():
    a = hmac_kb_kdf(b"key", b"L", b"C1", 32)
    b_ = hmac_kb_kdf(b"key", b"L", b"C2", 32)
    assert a != b_


def test_hmac_kb_kdf_multi_block():
    dk = hmac_kb_kdf(b"key", b"label", b"ctx", 100)
    assert len(dk) == 100


def test_hmac_kb_kdf_counter_width_2():
    dk = hmac_kb_kdf(b"key", b"L", b"C", 40, counter_width=2)
    assert len(dk) == 40


def test_hmac_kb_kdf_sha512():
    dk = hmac_kb_kdf(b"key", b"L", b"C", 72, hash_name="sha512")
    assert len(dk) == 72


# ── PBKDF2 ──────────────────────────────────────────────────────────────


def test_pbkdf2_empty_derives_empty():
    assert pbkdf2(b"pw", b"salt", 1, 0) == b""


def test_pbkdf2_one_iteration_produces_correct_length():
    dk = pbkdf2(b"pw", b"salt", 1, 32)
    assert len(dk) == 32


def test_pbkdf2_deterministic():
    a = pbkdf2(b"password", b"NaCl", 4096, 32)
    b_ = pbkdf2(b"password", b"NaCl", 4096, 32)
    assert a == b_


def test_pbkdf2_iterations_matter():
    dk1 = pbkdf2(b"pw", b"salt", 1, 32)
    dk2 = pbkdf2(b"pw", b"salt", 2, 32)
    assert dk1 != dk2


def test_pbkdf2_salt_matters():
    dk1 = pbkdf2(b"pw", b"a", 10, 32)
    dk2 = pbkdf2(b"pw", b"b", 10, 32)
    assert dk1 != dk2


def test_pbkdf2_password_matters():
    dk1 = pbkdf2(b"pw1", b"salt", 10, 32)
    dk2 = pbkdf2(b"pw2", b"salt", 10, 32)
    assert dk1 != dk2


def test_pbkdf2_multi_block_output():
    dk = pbkdf2(b"password", b"salt", 1000, 100)
    assert len(dk) == 100


def test_pbkdf2_cross_block_exact():
    dk = pbkdf2(b"pw", b"salt", 5, 64)
    assert len(dk) == 64


def test_pbkdf2_sha512():
    dk = pbkdf2(b"pw", b"salt", 10, 64, "sha512")
    assert len(dk) == 64


def test_pbkdf2_rejects_zero_iterations():
    with pytest.raises(HKDFError, match="iterations"):
        pbkdf2(b"pw", b"salt", 0, 32)


def test_pbkdf2_rejects_negative_iterations():
    with pytest.raises(HKDFError, match="iterations"):
        pbkdf2(b"pw", b"salt", -1, 32)


def test_pbkdf2_rejects_bad_hash():
    with pytest.raises(HKDFError, match="Unsupported"):
        pbkdf2(b"pw", b"salt", 1, 32, "md5")


# ── HKDFExpand rejects unsupported hash ──────────────────────────────────


def test_hkdf_expand_rejects_bad_hash():
    with pytest.raises(HKDFError, match="Unsupported"):
        hkdf_expand(b"\x00" * 32, b"", 16, "md5")


# ── HMAC-KB rejects unsupported hash ────────────────────────────────────


def test_hmac_kb_rejects_bad_hash():
    with pytest.raises(HKDFError, match="Unsupported"):
        hmac_kb_kdf(b"k", b"L", b"C", 16, "md5")


# ── Constants ───────────────────────────────────────────────────────────


def test_hashlen_values():
    assert HASHLEN["sha256"] == 32
    assert HASHLEN["sha512"] == 64


def test_block_size_values():
    assert HMAC_BLOCK_SIZE["sha256"] == 64
    assert HMAC_BLOCK_SIZE["sha512"] == 128
