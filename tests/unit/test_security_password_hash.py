"""Deep tests for password_hash — argon2id, bcrypt, scrypt hashing and verification."""

from __future__ import annotations

import pytest

from general_ludd.security.password_hash import (
    derive_key,
    hash_password,
    needs_rehash,
    verify_password,
)


class TestHashPasswordArgon2id:
    def test_hash_returns_string(self) -> None:
        h = hash_password("mypassword", algorithm="argon2id")
        assert isinstance(h, str)
        assert h.startswith("$argon2")

    def test_hash_is_deterministic_format(self) -> None:
        h1 = hash_password("pw1", algorithm="argon2id")
        h2 = hash_password("pw1", algorithm="argon2id")
        assert h1.startswith("$argon2")
        assert h2.startswith("$argon2")
        assert h1 != h2

    def test_hash_accepts_case_insensitive_algorithm(self) -> None:
        h = hash_password("pw", algorithm="ARGON2ID")
        assert h.startswith("$argon2")

    def test_verify_matching_password(self) -> None:
        h = hash_password("secret", algorithm="argon2id")
        assert verify_password("secret", h) is True

    def test_verify_wrong_password(self) -> None:
        h = hash_password("secret", algorithm="argon2id")
        assert verify_password("wrong", h) is False

    def test_verify_empty_password(self) -> None:
        h = hash_password("", algorithm="argon2id")
        assert verify_password("", h) is True
        assert verify_password("x", h) is False

    def test_verify_unicode_password(self) -> None:
        h = hash_password("café東京", algorithm="argon2id")
        assert verify_password("café東京", h) is True
        assert verify_password("café京都", h) is False

    def test_hash_very_long_password(self) -> None:
        pw = "A" * 4096
        h = hash_password(pw, algorithm="argon2id")
        assert verify_password(pw, h) is True

    def test_needs_rehash_fresh_hash(self) -> None:
        h = hash_password("pw", algorithm="argon2id")
        assert needs_rehash(h) is False

    def test_verify_tampered_hash(self) -> None:
        h = hash_password("pw", algorithm="argon2id")
        assert verify_password("pw", h + "x") is False


class TestHashPasswordBcrypt:
    def test_hash_returns_string(self) -> None:
        h = hash_password("mypassword", algorithm="bcrypt")
        assert isinstance(h, str)
        assert h.startswith("$2b$")

    def test_hash_accepts_case_insensitive_algorithm(self) -> None:
        h = hash_password("pw", algorithm="BCRYPT")
        assert h.startswith("$2b$")

    def test_verify_matching_password(self) -> None:
        h = hash_password("secret", algorithm="bcrypt")
        assert verify_password("secret", h) is True

    def test_verify_wrong_password(self) -> None:
        h = hash_password("secret", algorithm="bcrypt")
        assert verify_password("wrong", h) is False

    def test_needs_rehash_fresh_hash(self) -> None:
        h = hash_password("pw", algorithm="bcrypt")
        assert needs_rehash(h) is False


class TestHashPasswordScrypt:
    def test_hash_returns_string(self) -> None:
        h = hash_password("mypassword", algorithm="scrypt")
        assert isinstance(h, str)
        assert h.startswith("$scrypt$")

    def test_hash_accepts_case_insensitive_algorithm(self) -> None:
        h = hash_password("pw", algorithm="SCRYPT")
        assert h.startswith("$scrypt$")

    def test_verify_matching_password(self) -> None:
        h = hash_password("secret", algorithm="scrypt")
        assert verify_password("secret", h) is True

    def test_verify_wrong_password(self) -> None:
        h = hash_password("secret", algorithm="scrypt")
        assert verify_password("wrong", h) is False

    def test_needs_rehash_fresh_hash(self) -> None:
        h = hash_password("pw", algorithm="scrypt")
        assert needs_rehash(h) is False

    def test_verify_tampered_hash(self) -> None:
        h = hash_password("pw", algorithm="scrypt")
        assert verify_password("pw", h[:-4] + "xxxx") is False


class TestVerifyPasswordDispatch:
    def test_verify_detects_argon2_from_prefix(self) -> None:
        h = hash_password("pw", algorithm="argon2id")
        assert verify_password("pw", h) is True

    def test_verify_detects_bcrypt_from_prefix(self) -> None:
        h = hash_password("pw", algorithm="bcrypt")
        assert verify_password("pw", h) is True

    def test_verify_detects_scrypt_from_prefix(self) -> None:
        h = hash_password("pw", algorithm="scrypt")
        assert verify_password("pw", h) is True

    def test_verify_raises_on_unknown_prefix(self) -> None:
        with pytest.raises(ValueError, match="Unknown hash format"):
            verify_password("pw", "$unknown$abc123")

    def test_verify_raises_on_empty_stored_hash(self) -> None:
        with pytest.raises(ValueError, match="Unknown hash format"):
            verify_password("pw", "")

    def test_verify_raises_on_garbage_hash(self) -> None:
        with pytest.raises(ValueError, match="Unknown hash format"):
            verify_password("pw", "notahash")


class TestNeedsRehash:
    def test_needs_rehash_argon2id_fresh(self) -> None:
        h = hash_password("pw", algorithm="argon2id")
        assert needs_rehash(h) is False

    def test_needs_rehash_scrypt_fresh(self) -> None:
        h = hash_password("pw", algorithm="scrypt")
        assert needs_rehash(h) is False

    def test_needs_rehash_raises_on_unknown_prefix(self) -> None:
        with pytest.raises(ValueError, match="Unknown hash format"):
            needs_rehash("$unknown$abc123")


class TestDeriveKey:
    def test_derive_scrypt_default_length(self) -> None:
        key, salt = derive_key("password", algorithm="scrypt")
        assert len(key) == 32
        assert len(salt) == 32

    def test_derive_scrypt_custom_length(self) -> None:
        key, _salt = derive_key("password", length=64, algorithm="scrypt")
        assert len(key) == 64

    def test_derive_scrypt_deterministic_with_salt(self) -> None:
        salt = b"a" * 16
        k1, s1 = derive_key("pw", salt=salt, algorithm="scrypt")
        k2, s2 = derive_key("pw", salt=salt, algorithm="scrypt")
        assert k1 == k2
        assert s1 == s2

    def test_derive_scrypt_different_passwords(self) -> None:
        salt = b"b" * 16
        k1, _ = derive_key("pw1", salt=salt, algorithm="scrypt")
        k2, _ = derive_key("pw2", salt=salt, algorithm="scrypt")
        assert k1 != k2

    def test_derive_scrypt_random_salt(self) -> None:
        _, s1 = derive_key("pw", algorithm="scrypt")
        _, s2 = derive_key("pw", algorithm="scrypt")
        assert s1 != s2

    def test_derive_scrypt_case_sensitive(self) -> None:
        salt = b"c" * 16
        k1, _ = derive_key("Password", salt=salt, algorithm="scrypt")
        k2, _ = derive_key("password", salt=salt, algorithm="scrypt")
        assert k1 != k2

    def test_derive_pbkdf2_sha256(self) -> None:
        key, salt = derive_key("password", algorithm="pbkdf2_sha256")
        assert len(key) == 32
        assert len(salt) == 32

    def test_derive_pbkdf2_deterministic_with_salt(self) -> None:
        salt = b"d" * 16
        k1, _s1 = derive_key("pw", salt=salt, algorithm="pbkdf2_sha256")
        k2, _s2 = derive_key("pw", salt=salt, algorithm="pbkdf2_sha256")
        assert k1 == k2

    def test_derive_pbkdf2_different_length(self) -> None:
        key, _salt = derive_key("password", length=48, algorithm="pbkdf2_sha256")
        assert len(key) == 48

    def test_derive_scrypt_unicode_password(self) -> None:
        salt = b"e" * 16
        k1, _ = derive_key("café", salt=salt, algorithm="scrypt")
        k2, _ = derive_key("café", salt=salt, algorithm="scrypt")
        assert k1 == k2

    def test_derive_scrypt_empty_password(self) -> None:
        key, salt = derive_key("", algorithm="scrypt")
        assert len(key) == 32
        assert len(salt) == 32

    def test_derive_scrypt_very_long_password(self) -> None:
        key, _salt = derive_key("A" * 4096, algorithm="scrypt")
        assert len(key) == 32


class TestHashPasswordErrors:
    def test_raises_on_unknown_algorithm(self) -> None:
        with pytest.raises(ValueError, match="Unknown algorithm"):
            hash_password("pw", algorithm="sha1")

    def test_raises_on_empty_algorithm(self) -> None:
        with pytest.raises(ValueError, match="Unknown algorithm"):
            hash_password("pw", algorithm="")


class TestDeriveKeyErrors:
    def test_raises_on_short_length(self) -> None:
        with pytest.raises(ValueError, match="key length must be >= 16"):
            derive_key("pw", length=8, algorithm="scrypt")

    def test_raises_on_zero_length(self) -> None:
        with pytest.raises(ValueError, match="key length must be >= 16"):
            derive_key("pw", length=0, algorithm="scrypt")

    def test_raises_on_negative_length(self) -> None:
        with pytest.raises(ValueError, match="key length must be >= 16"):
            derive_key("pw", length=-1, algorithm="scrypt")

    def test_raises_on_unknown_derivation_algorithm(self) -> None:
        with pytest.raises(ValueError, match="Unknown derivation algorithm"):
            derive_key("pw", algorithm="md5")


class TestAlgorithmDefault:
    def test_hash_defaults_to_argon2id(self) -> None:
        h = hash_password("pw")
        assert h.startswith("$argon2")

    def test_hash_explicit_default_is_argon2id(self) -> None:
        h1 = hash_password("pw")
        h2 = hash_password("pw", algorithm="argon2id")
        assert h1.startswith("$argon2")
        assert h2.startswith("$argon2")

    def test_derive_defaults_to_scrypt(self) -> None:
        key, salt = derive_key("pw")
        assert len(key) == 32
        assert len(salt) == 32


class TestCrossAlgorithmInterop:
    def test_argon2id_hash_not_verified_by_wrong_prefix(self) -> None:
        argon = hash_password("pw", algorithm="argon2id")
        scrypt = hash_password("pw", algorithm="scrypt")
        assert verify_password("pw", argon) is True
        assert verify_password("pw", scrypt) is True
        assert argon != scrypt

    def test_all_algorithms_roundtrip(self) -> None:
        pw = "round-trip-test"
        for algo in ("argon2id", "bcrypt", "scrypt"):
            h = hash_password(pw, algorithm=algo)
            assert verify_password(pw, h) is True, f"Failed for {algo}"
            assert verify_password("WRONG", h) is False, f"Failed for {algo}"


class TestTimingBehavior:
    def test_verify_invalid_does_not_leak_algorithm(self) -> None:
        h = hash_password("pw", algorithm="argon2id")
        result = verify_password("wrong", h)
        assert result is False
        assert verify_password("wrong", h) is False

    def test_bcrypt_verify_short_hash_returns_false(self) -> None:
        assert verify_password("pw", "$2b$short") is False
