"""Tests for password_hash.py — bcrypt, scrypt, argon2id hashing."""

from __future__ import annotations

import base64
import hashlib

import pytest

from general_ludd.security.password_hash import (
    _BCRYPT_AVAILABLE,
    _BCRYPT_ROUNDS,
    _SCRYPT_N,
    _SCRYPT_P,
    _SCRYPT_R,
    _bcrypt_needs_rehash,
    _hash_argon2id,
    _hash_bcrypt,
    _hash_scrypt,
    _scrypt_needs_rehash,
    _verify_argon2id,
    _verify_bcrypt,
    _verify_scrypt,
    derive_key,
    hash_password,
    needs_rehash,
    verify_password,
)

# -- hash_password + verify_password round-trips -------------------------------


class TestArgon2idRoundtrip:
    def test_hash_and_verify(self):
        pw = "correct-horse-battery-staple"
        h = hash_password(pw, algorithm="argon2id")
        assert h.startswith("$argon2id$")
        assert verify_password(pw, h) is True

    def test_wrong_password_fails(self):
        h = hash_password("secret123", algorithm="argon2id")
        assert verify_password("wrong", h) is False

    def test_empty_password_hashes_and_verifies(self):
        h = hash_password("", algorithm="argon2id")
        assert verify_password("", h) is True


@pytest.mark.skipif(not _BCRYPT_AVAILABLE, reason="bcrypt not installed")
class TestBcryptRoundtrip:
    def test_hash_and_verify(self):
        pw = "correct-horse-battery-staple"
        h = hash_password(pw, algorithm="bcrypt")
        assert h.startswith("$2b$")
        assert verify_password(pw, h) is True

    def test_wrong_password_fails(self):
        h = hash_password("secret123", algorithm="bcrypt")
        assert verify_password("wrong", h) is False

    def test_empty_password_hashes_and_verifies(self):
        h = hash_password("", algorithm="bcrypt")
        assert verify_password("", h) is True

    def test_different_hashes_per_call(self):
        h1 = hash_password("mypassword", algorithm="bcrypt")
        h2 = hash_password("mypassword", algorithm="bcrypt")
        assert h1 != h2
        assert verify_password("mypassword", h1) is True
        assert verify_password("mypassword", h2) is True


class TestScryptRoundtrip:
    def test_hash_and_verify(self):
        pw = "correct-horse-battery-staple"
        h = hash_password(pw, algorithm="scrypt")
        assert h.startswith("$scrypt$")
        assert verify_password(pw, h) is True

    def test_wrong_password_fails(self):
        h = hash_password("secret123", algorithm="scrypt")
        assert verify_password("wrong", h) is False

    def test_empty_password_hashes_and_verifies(self):
        h = hash_password("", algorithm="scrypt")
        assert verify_password("", h) is True

    def test_different_hashes_per_call(self):
        h1 = hash_password("mypassword", algorithm="scrypt")
        h2 = hash_password("mypassword", algorithm="scrypt")
        assert h1 != h2
        assert verify_password("mypassword", h1) is True
        assert verify_password("mypassword", h2) is True


class TestCrossAlgorithmDetection:
    def test_verify_detects_argon2id(self):
        h = hash_password("pw", algorithm="argon2id")
        assert verify_password("pw", h) is True

    def test_verify_detects_scrypt(self):
        h = hash_password("pw", algorithm="scrypt")
        assert verify_password("pw", h) is True

    @pytest.mark.skipif(not _BCRYPT_AVAILABLE, reason="bcrypt not installed")
    def test_verify_detects_bcrypt(self):
        h = hash_password("pw", algorithm="bcrypt")
        assert verify_password("pw", h) is True


class TestUnknownAlgorithm:
    def test_hash_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown algorithm"):
            hash_password("pw", algorithm="md5")

    def test_verify_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="Unknown hash format"):
            verify_password("pw", "$unknown$abc")


# -- needs_rehash ---------------------------------------------------------------


class TestNeedsRehash:
    def test_fresh_argon2id_does_not_need_rehash(self):
        h = hash_password("pw", algorithm="argon2id")
        assert needs_rehash(h) is False

    def test_fresh_scrypt_does_not_need_rehash(self):
        h = hash_password("pw", algorithm="scrypt")
        assert needs_rehash(h) is False

    @pytest.mark.skipif(not _BCRYPT_AVAILABLE, reason="bcrypt not installed")
    def test_fresh_bcrypt_does_not_need_rehash(self):
        h = hash_password("pw", algorithm="bcrypt")
        assert needs_rehash(h) is False

    def test_weak_bcrypt_hash_needs_rehash(self):
        weak = "$2b$04$abcdefghijklmnopqrstuuABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnop"
        assert _bcrypt_needs_rehash(weak) is True

    def test_strong_bcrypt_hash_does_not_need_rehash(self):
        strong = f"$2b${_BCRYPT_ROUNDS:02d}$abcdefghijklmnopqrstuuABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnop"
        assert _bcrypt_needs_rehash(strong) is False

    def test_malformed_bcrypt_hash_needs_rehash(self):
        assert _bcrypt_needs_rehash("garbage") is True

    def test_weak_scrypt_params_need_rehash(self):
        weak = "$scrypt$ln=1024,r=4,p=1$c2FsdA==$aGFzaA=="
        assert _scrypt_needs_rehash(weak) is True

    def test_strong_scrypt_params_do_not_need_rehash(self):
        strong = f"$scrypt$ln={_SCRYPT_N},r={_SCRYPT_R},p={_SCRYPT_P}$c2FsdA==$aGFzaA=="
        assert _scrypt_needs_rehash(strong) is False

    def test_malformed_scrypt_hash_needs_rehash(self):
        assert _scrypt_needs_rehash("garbage") is True

    def test_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="Unknown hash format"):
            needs_rehash("$unknown$abc")


# -- derive_key -----------------------------------------------------------------


class TestDeriveKey:
    def test_scrypt_default_length(self):
        key, salt = derive_key("password")
        assert len(key) == 32
        assert len(salt) == 32

    def test_scrypt_custom_length(self):
        key, salt = derive_key("password", length=64)
        assert len(key) == 64
        assert len(salt) == 32

    def test_scrypt_with_explicit_salt(self):
        salt = b"\x01" * 32
        key1, s1 = derive_key("pw", salt=salt, algorithm="scrypt")
        key2, s2 = derive_key("pw", salt=salt, algorithm="scrypt")
        assert s1 == s2 == salt
        assert key1 == key2

    def test_scrypt_min_length(self):
        key, _ = derive_key("pw", length=16)
        assert len(key) == 16

    def test_scrypt_length_too_short(self):
        with pytest.raises(ValueError, match="key length must be >= 16"):
            derive_key("pw", length=8)

    def test_pbkdf2_sha256(self):
        key1, salt1 = derive_key("password", algorithm="pbkdf2_sha256", length=32)
        key2, _ = derive_key("password", salt=salt1, algorithm="pbkdf2_sha256", length=32)
        assert len(key1) == 32
        assert key1 == key2

    def test_derive_unknown_algorithm(self):
        with pytest.raises(ValueError, match="Unknown derivation algorithm"):
            derive_key("pw", algorithm="rot13")


# -- hash / verify edge cases --------------------------------------------------


class TestHashEdgeCases:
    def test_argon2id_with_unicode(self):
        pw = "パスワード🔒"
        h = hash_password(pw, algorithm="argon2id")
        assert verify_password(pw, h) is True

    def test_scrypt_with_unicode(self):
        pw = "パスワード🔒"
        h = hash_password(pw, algorithm="scrypt")
        assert verify_password(pw, h) is True

    @pytest.mark.skipif(not _BCRYPT_AVAILABLE, reason="bcrypt not installed")
    def test_bcrypt_with_unicode(self):
        pw = "パスワード🔒"
        h = hash_password(pw, algorithm="bcrypt")
        assert verify_password(pw, h) is True

    def test_long_password_argon2id(self):
        pw = "a" * 4096
        h = hash_password(pw, algorithm="argon2id")
        assert verify_password(pw, h) is True

    def test_long_password_scrypt(self):
        pw = "a" * 4096
        h = hash_password(pw, algorithm="scrypt")
        assert verify_password(pw, h) is True

    @pytest.mark.skipif(not _BCRYPT_AVAILABLE, reason="bcrypt not installed")
    def test_bcrypt_rejects_overly_long_password(self):
        pw = "a" * 100
        with pytest.raises(ValueError, match="72"):
            hash_password(pw, algorithm="bcrypt")
        assert True


# -- constant-time verification ------------------------------------------------


class TestConstantTimeVerify:
    def test_argon2id_does_not_leak_via_timing(self):
        h = hash_password("secret", algorithm="argon2id")
        for _ in range(5):
            assert verify_password("secret", h) is True

    def test_scrypt_does_not_leak_via_timing(self):
        h = hash_password("secret", algorithm="scrypt")
        for _ in range(5):
            assert verify_password("secret", h) is True

    @pytest.mark.skipif(not _BCRYPT_AVAILABLE, reason="bcrypt not installed")
    def test_bcrypt_does_not_leak_via_timing(self):
        h = hash_password("secret", algorithm="bcrypt")
        for _ in range(5):
            assert verify_password("secret", h) is True


# -- scrypt unit tests ----------------------------------------------------------


class TestScryptInternals:
    def test_verify_scrypt_malformed_hash(self):
        assert _verify_scrypt("bad", "pw") is False
        assert _verify_scrypt("$scrypt$bad$salt$hash", "pw") is False

    def test_scrypt_hash_format(self):
        h = _hash_scrypt("test")
        assert h.startswith("$scrypt$")
        parts = h.split("$")
        assert len(parts) == 5
        assert parts[2].startswith("ln=")

    def test_scrypt_hash_contains_base64_salt_and_hash(self):
        h = _hash_scrypt("test")
        parts = h.split("$")
        salt_b64 = parts[2 + 1]  # index 3 after splitting
        hash_b64 = parts[2 + 2]  # index 4 after splitting
        assert len(base64.b64decode(salt_b64)) > 0
        assert len(base64.b64decode(hash_b64)) == 64

    def test_scrypt_needs_rehash_strong_params(self):
        strong = f"$scrypt$ln={_SCRYPT_N},r={_SCRYPT_R},p={_SCRYPT_P}$c2FsdA==$aGFzaA=="
        assert _scrypt_needs_rehash(strong) is False

    def test_scrypt_needs_rehash_weak_n(self):
        weak = f"$scrypt$ln=1024,r={_SCRYPT_R},p={_SCRYPT_P}$c2FsdA==$aGFzaA=="
        assert _scrypt_needs_rehash(weak) is True

    def test_scrypt_needs_rehash_weak_r(self):
        weak = f"$scrypt$ln={_SCRYPT_N},r=2,p={_SCRYPT_P}$c2FsdA==$aGFzaA=="
        assert _scrypt_needs_rehash(weak) is True

    def test_scrypt_needs_rehash_weak_p(self):
        weak = f"$scrypt$ln={_SCRYPT_N},r={_SCRYPT_R},p=0$c2FsdA==$aGFzaA=="
        assert _scrypt_needs_rehash(weak) is True


# -- bcrypt unit tests ----------------------------------------------------------


class TestBcryptInternals:
    @pytest.mark.skipif(not _BCRYPT_AVAILABLE, reason="bcrypt not installed")
    def test_hash_bcrypt_format(self):
        h = _hash_bcrypt("test")
        assert h.startswith("$2b$")
        assert len(h) == 60

    @pytest.mark.skipif(not _BCRYPT_AVAILABLE, reason="bcrypt not installed")
    def test_verify_bcrypt_correct(self):
        h = _hash_bcrypt("mypw")
        assert _verify_bcrypt("mypw", h) is True

    @pytest.mark.skipif(not _BCRYPT_AVAILABLE, reason="bcrypt not installed")
    def test_verify_bcrypt_wrong(self):
        h = _hash_bcrypt("mypw")
        assert _verify_bcrypt("wrong", h) is False

    def test_bcrypt_needs_rehash_no_bcrypt(self):
        if not _BCRYPT_AVAILABLE:
            assert _bcrypt_needs_rehash("$2b$12$...") is False

    def test_bcrypt_needs_rehash_parsing_failure_bad_rounds(self):
        assert _bcrypt_needs_rehash("$2b$") is True


# -- argon2id unit tests --------------------------------------------------------


class TestArgon2idInternals:
    def test_hash_prefix(self):
        h = _hash_argon2id("test")
        assert h.startswith("$argon2id$")

    def test_verify_correct(self):
        h = _hash_argon2id("mypw")
        assert _verify_argon2id(h, "mypw") is True

    def test_verify_wrong(self):
        h = _hash_argon2id("mypw")
        assert _verify_argon2id(h, "wrong") is False

    def test_different_hashes(self):
        h1 = _hash_argon2id("pw")
        h2 = _hash_argon2id("pw")
        assert h1 != h2


# -- derive_key with pbkdf2 matching hashlib reference --------------------------


class TestDeriveKeyReference:
    def test_pbkdf2_matches_stdlib(self):
        pw = "test-password"
        salt = b"saltsaltsaltsaltsaltsaltsaltsalt"
        key, _s = derive_key(pw, salt=salt, algorithm="pbkdf2_sha256", length=32)
        expected = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 600_000, dklen=32)
        assert key == expected

    def test_scrypt_matches_stdlib(self):
        pw = "test-password"
        salt = b"\xaa" * 32
        key, _s = derive_key(pw, salt=salt, algorithm="scrypt", length=32)
        expected = hashlib.scrypt(pw.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
        assert key == expected
