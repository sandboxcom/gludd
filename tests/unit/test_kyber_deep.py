"""ML-KEM provider-contract tests for all FIPS 203 parameter sets."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Protocol, cast

import pytest

import general_ludd.algorithms.kyber as kyber_module
from general_ludd.algorithms.kyber import (
    BACKEND,
    PARAMS_512,
    PARAMS_768,
    PARAMS_1024,
    KyberError,
    KyberParams,
    decapsulate,
    decapsulate_512,
    decapsulate_768,
    decapsulate_1024,
    encapsulate,
    encapsulate_512,
    encapsulate_768,
    encapsulate_1024,
    keygen,
    keygen_512,
    keygen_768,
    keygen_1024,
)

_CASES = (
    (PARAMS_512, 800, 1632, 768),
    (PARAMS_768, 1184, 2400, 1088),
    (PARAMS_1024, 1568, 3168, 1568),
)


class _MutableBackend(Protocol):
    def generate_keypair(self) -> tuple[bytes, bytes]: ...

    def encrypt(self, public_key: bytes) -> tuple[bytes, bytes]: ...

    def decrypt(self, secret_key: bytes, ciphertext: bytes) -> bytes: ...


def _provider_512() -> _MutableBackend:
    backends = cast(
        dict[KyberParams, _MutableBackend],
        kyber_module.__dict__["_BACKENDS"],
    )
    return backends[PARAMS_512]


class TestProviderBoundary:
    def test_uses_maintained_pqcrypto_backend(self) -> None:
        assert BACKEND == "pqcrypto"

    @pytest.mark.parametrize(("params", "pk_size", "sk_size", "ct_size"), _CASES)
    def test_fips_203_dimensions(
        self,
        params: KyberParams,
        pk_size: int,
        sk_size: int,
        ct_size: int,
    ) -> None:
        assert params.pk_bytes == pk_size
        assert params.sk_bytes == sk_size
        assert params.ct_bytes == ct_size
        assert params.ss_bytes == 32
        assert params.algorithm == f"ml_kem_{params.k * 256}"

    def test_parameter_descriptors_are_frozen(self) -> None:
        with pytest.raises(FrozenInstanceError):
            PARAMS_512.k = 4  # type: ignore[misc]

    def test_equivalent_parameter_value_selects_canonical_backend(self) -> None:
        equivalent = KyberParams(k=2, eta1=3, eta2=2, du=10, dv=4)
        public_key, secret_key = keygen(equivalent)
        ciphertext, shared_secret = encapsulate(public_key, equivalent)
        assert decapsulate(ciphertext, secret_key, equivalent) == shared_secret

    def test_unknown_parameters_fail_closed(self) -> None:
        unsupported = KyberParams(k=1, eta1=2, eta2=2, du=9, dv=3)
        with pytest.raises(KyberError, match="unsupported ML-KEM"):
            keygen(unsupported)
        with pytest.raises(KyberError, match="unsupported ML-KEM"):
            encapsulate(b"", unsupported)
        with pytest.raises(KyberError, match="unsupported ML-KEM"):
            decapsulate(b"", b"", unsupported)

    def test_non_parameter_value_fails_closed(self) -> None:
        with pytest.raises(KyberError, match="KyberParams"):
            keygen(cast(KyberParams, object()))


class TestKeyGeneration:
    @pytest.mark.parametrize(("params", "pk_size", "sk_size", "_ct_size"), _CASES)
    def test_key_lengths(
        self,
        params: KyberParams,
        pk_size: int,
        sk_size: int,
        _ct_size: int,
    ) -> None:
        public_key, secret_key = keygen(params)
        assert len(public_key) == pk_size
        assert len(secret_key) == sk_size

    def test_key_generation_is_randomized(self) -> None:
        first = keygen_512()
        second = keygen_512()
        assert first[0] != second[0]
        assert first[1] != second[1]


class TestEncapsulation:
    @pytest.mark.parametrize(("params", "_pk_size", "_sk_size", "ct_size"), _CASES)
    def test_generic_roundtrip(
        self,
        params: KyberParams,
        _pk_size: int,
        _sk_size: int,
        ct_size: int,
    ) -> None:
        public_key, secret_key = keygen(params)
        ciphertext, shared_secret = encapsulate(public_key, params)
        assert len(ciphertext) == ct_size
        assert len(shared_secret) == params.ss_bytes
        assert decapsulate(ciphertext, secret_key, params) == shared_secret

    def test_default_parameter_set_roundtrip(self) -> None:
        public_key, secret_key = keygen()
        ciphertext, shared_secret = encapsulate(public_key)
        assert decapsulate(ciphertext, secret_key) == shared_secret

    def test_convenience_roundtrips(self) -> None:
        operations = (
            (keygen_512, encapsulate_512, decapsulate_512),
            (keygen_768, encapsulate_768, decapsulate_768),
            (keygen_1024, encapsulate_1024, decapsulate_1024),
        )
        for make_keys, seal, open_secret in operations:
            public_key, secret_key = make_keys()
            ciphertext, shared_secret = seal(public_key)
            assert open_secret(ciphertext, secret_key) == shared_secret

    def test_repeated_encapsulation_is_randomized(self) -> None:
        public_key, _secret_key = keygen_512()
        first = encapsulate_512(public_key)
        second = encapsulate_512(public_key)
        assert first[0] != second[0]
        assert first[1] != second[1]

    def test_wrong_secret_key_uses_implicit_rejection(self) -> None:
        public_key, _secret_key = keygen_512()
        _other_public_key, other_secret_key = keygen_512()
        ciphertext, shared_secret = encapsulate_512(public_key)
        rejected_secret = decapsulate_512(ciphertext, other_secret_key)
        assert len(rejected_secret) == 32
        assert rejected_secret != shared_secret

    def test_tampered_ciphertext_uses_implicit_rejection(self) -> None:
        public_key, secret_key = keygen_512()
        ciphertext, shared_secret = encapsulate_512(public_key)
        tampered = bytes([ciphertext[0] ^ 1]) + ciphertext[1:]
        rejected_secret = decapsulate_512(tampered, secret_key)
        assert len(rejected_secret) == 32
        assert rejected_secret != shared_secret


class TestFailClosedInputs:
    def test_public_key_length_is_exact(self) -> None:
        public_key, _secret_key = keygen_512()
        with pytest.raises(KyberError, match="public key must be exactly"):
            encapsulate_512(public_key[:-1])
        with pytest.raises(KyberError, match="public key must be exactly"):
            encapsulate_512(public_key + b"\x00")

    def test_public_key_type_is_bytes(self) -> None:
        public_key, _secret_key = keygen_512()
        with pytest.raises(KyberError, match="public key must be bytes"):
            encapsulate_512(cast(bytes, bytearray(public_key)))

    def test_ciphertext_length_is_exact(self) -> None:
        public_key, secret_key = keygen_512()
        ciphertext, _shared_secret = encapsulate_512(public_key)
        with pytest.raises(KyberError, match="ciphertext must be exactly"):
            decapsulate_512(ciphertext[:-1], secret_key)
        with pytest.raises(KyberError, match="ciphertext must be exactly"):
            decapsulate_512(ciphertext + b"\x00", secret_key)

    def test_ciphertext_type_is_bytes(self) -> None:
        public_key, secret_key = keygen_512()
        ciphertext, _shared_secret = encapsulate_512(public_key)
        with pytest.raises(KyberError, match="ciphertext must be bytes"):
            decapsulate_512(cast(bytes, bytearray(ciphertext)), secret_key)

    def test_secret_key_length_is_exact(self) -> None:
        public_key, secret_key = keygen_512()
        ciphertext, _shared_secret = encapsulate_512(public_key)
        with pytest.raises(KyberError, match="secret key must be exactly"):
            decapsulate_512(ciphertext, secret_key[:-1])
        with pytest.raises(KyberError, match="secret key must be exactly"):
            decapsulate_512(ciphertext, secret_key + b"\x00")

    def test_secret_key_type_is_bytes(self) -> None:
        public_key, secret_key = keygen_512()
        ciphertext, _shared_secret = encapsulate_512(public_key)
        with pytest.raises(KyberError, match="secret key must be bytes"):
            decapsulate_512(ciphertext, cast(bytes, bytearray(secret_key)))


class TestBackendFailureBoundary:
    def test_keygen_exception_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail() -> tuple[bytes, bytes]:
            raise RuntimeError("provider failure")

        monkeypatch.setattr(_provider_512(), "generate_keypair", fail)
        with pytest.raises(KyberError, match="key generation failed"):
            keygen_512()

    def test_keygen_rejects_invalid_provider_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            _provider_512(),
            "generate_keypair",
            lambda: (b"", b""),
        )
        with pytest.raises(KyberError, match="public key backend output"):
            keygen_512()

    def test_encapsulation_exception_is_bounded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        public_key, _secret_key = keygen_512()

        def fail(_public_key: bytes) -> tuple[bytes, bytes]:
            raise RuntimeError("provider failure")

        monkeypatch.setattr(_provider_512(), "encrypt", fail)
        with pytest.raises(KyberError, match="encapsulation failed"):
            encapsulate_512(public_key)

    def test_encapsulation_rejects_invalid_provider_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        public_key, _secret_key = keygen_512()
        monkeypatch.setattr(
            _provider_512(),
            "encrypt",
            lambda _public_key: (b"", b""),
        )
        with pytest.raises(KyberError, match="ciphertext backend output"):
            encapsulate_512(public_key)

    def test_decapsulation_exception_is_bounded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        public_key, secret_key = keygen_512()
        ciphertext, _shared_secret = encapsulate_512(public_key)

        def fail(_secret_key: bytes, _ciphertext: bytes) -> bytes:
            raise RuntimeError("provider failure")

        monkeypatch.setattr(_provider_512(), "decrypt", fail)
        with pytest.raises(KyberError, match="decapsulation failed"):
            decapsulate_512(ciphertext, secret_key)

    def test_decapsulation_rejects_invalid_provider_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        public_key, secret_key = keygen_512()
        ciphertext, _shared_secret = encapsulate_512(public_key)
        monkeypatch.setattr(
            _provider_512(),
            "decrypt",
            lambda _secret_key, _ciphertext: b"",
        )
        with pytest.raises(KyberError, match="shared secret backend output"):
            decapsulate_512(ciphertext, secret_key)
