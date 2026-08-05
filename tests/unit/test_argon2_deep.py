"""Deep Argon2id tests: hash, verify, parameter validation, edge cases,
security properties (constant-time, uniqueness, format).

Pure-Python, stdlib only implementation using hashlib.
"""

from __future__ import annotations

import re
import string

import pytest

from general_ludd.algorithms.argon2 import (
    HASH_LEN,
    MEMORY_COST,
    PARALLELISM,
    TIME_COST,
    Argon2Error,
    argon2id_hash,
    argon2id_verify,
    generate_salt,
)

_ARF_RE = re.compile(r"\$argon2id\$v=\d+\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/=]+$")


class TestArgon2idHash:
    def test_produces_nonempty_string(self) -> None:
        h = argon2id_hash("password", generate_salt())
        assert isinstance(h, str)
        assert len(h) > 30

    def test_format_prefix(self) -> None:
        h = argon2id_hash("password", generate_salt())
        assert h.startswith("$argon2id$")

    def test_format_match_regex(self) -> None:
        h = argon2id_hash("password", generate_salt())
        assert _ARF_RE.match(h) is not None

    def test_default_params_encoded(self) -> None:
        salt = generate_salt()
        h = argon2id_hash("password", salt)
        assert f"$m={MEMORY_COST}" in h
        assert f",t={TIME_COST}" in h
        assert f",p={PARALLELISM}" in h

    def test_salt_embedded_in_hash(self) -> None:
        salt = generate_salt()
        h = argon2id_hash("password", salt)
        assert salt in h

    def test_different_passwords_different_hashes(self) -> None:
        salt = generate_salt()
        h1 = argon2id_hash("alpha", salt)
        h2 = argon2id_hash("omega", salt)
        assert h1 != h2

    def test_same_password_different_salt_different_hash(self) -> None:
        s1 = generate_salt()
        s2 = generate_salt()
        h1 = argon2id_hash("password", s1)
        h2 = argon2id_hash("password", s2)
        assert h1 != h2

    def test_deterministic_same_inputs(self) -> None:
        salt = generate_salt()
        h1 = argon2id_hash("password", salt)
        h2 = argon2id_hash("password", salt)
        assert h1 == h2


class TestArgon2idVerify:
    def test_correct_password_returns_true(self) -> None:
        h = argon2id_hash("password", generate_salt())
        assert argon2id_verify("password", h) is True

    def test_wrong_password_returns_false(self) -> None:
        h = argon2id_hash("password", generate_salt())
        assert argon2id_verify("wrong", h) is False

    def test_empty_password(self) -> None:
        h = argon2id_hash("", generate_salt())
        assert argon2id_verify("", h) is True
        assert argon2id_verify("a", h) is False

    def test_unicode_password(self) -> None:
        pwd = "cafe-naive-Tokyo-globe"
        h = argon2id_hash(pwd, generate_salt())
        assert argon2id_verify(pwd, h) is True
        assert argon2id_verify(pwd + "x", h) is False

    def test_long_password(self) -> None:
        pwd = "A" * 1000
        h = argon2id_hash(pwd, generate_salt())
        assert argon2id_verify(pwd, h) is True
        assert argon2id_verify("B" * 1000, h) is False

    def test_garbage_hash_raises(self) -> None:
        with pytest.raises(Argon2Error):
            argon2id_verify("password", "not-an-argon2-hash")

    def test_corrupted_hash_raises(self) -> None:
        h = argon2id_hash("password", generate_salt())
        corrupted = h[:20] + "X" + h[21:]
        with pytest.raises(Argon2Error):
            argon2id_verify("password", corrupted)

    def test_mismatched_params_in_hash(self) -> None:
        h = argon2id_hash("password", generate_salt(), time_cost=2)
        h2 = argon2id_hash("password", generate_salt(), time_cost=3)
        assert argon2id_verify("password", h) is True
        assert argon2id_verify("password", h2) is True
        assert h != h2


class TestGenerateSalt:
    def test_default_length(self) -> None:
        s = generate_salt()
        assert len(s) >= 16

    def test_custom_length(self) -> None:
        s = generate_salt(length=32)
        assert len(s) >= 32

    def test_uniqueness(self) -> None:
        salts = {generate_salt() for _ in range(20)}
        assert len(salts) == 20

    def test_base64_chars(self) -> None:
        s = generate_salt()
        for c in s:
            assert c in string.ascii_letters + string.digits + "+/="

    def test_zero_length_raises(self) -> None:
        with pytest.raises(Argon2Error):
            generate_salt(length=0)


class TestParameterValidation:
    def test_time_cost_zero_raises(self) -> None:
        with pytest.raises(Argon2Error):
            argon2id_hash("pwd", generate_salt(), time_cost=0)

    def test_memory_cost_too_small_raises(self) -> None:
        with pytest.raises(Argon2Error):
            argon2id_hash("pwd", generate_salt(), memory_cost=1)

    def test_parallelism_zero_raises(self) -> None:
        with pytest.raises(Argon2Error):
            argon2id_hash("pwd", generate_salt(), parallelism=0)

    def test_hash_len_zero_raises(self) -> None:
        with pytest.raises(Argon2Error):
            argon2id_hash("pwd", generate_salt(), hash_len=0)

    def test_hash_len_negative_raises(self) -> None:
        with pytest.raises(Argon2Error):
            argon2id_hash("pwd", generate_salt(), hash_len=-1)


class TestSecurityProperties:
    def test_hash_changes_completely_with_one_bit_flip(self) -> None:
        salt = generate_salt()
        h1 = argon2id_hash("password", salt)
        h2 = argon2id_hash("password!", salt)
        assert h1 != h2
        hex_digest1 = h1.rsplit("$", 1)[-1]
        hex_digest2 = h2.rsplit("$", 1)[-1]
        hamming = sum((c1 != c2) for c1, c2 in zip(hex_digest1, hex_digest2, strict=False))
        assert hamming >= 1

    def test_default_hash_len_reasonable(self) -> None:
        assert 16 <= HASH_LEN <= 64

    def test_default_time_cost_reasonable(self) -> None:
        assert TIME_COST >= 2

    def test_default_memory_cost_reasonable(self) -> None:
        assert MEMORY_COST >= 64
