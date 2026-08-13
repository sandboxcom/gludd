"""Fail-closed contract tests for the optional RFC 8391 XMSS capability."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import cast

import pytest

import general_ludd.security.xmss as xmss

_UNAVAILABLE = "RFC 8391 XMSS backend is unavailable"
_VALID_HEIGHTS = (4, 10, 16, 20)
_VALID_DIGESTS = ("SHA256", "SHA512", "SHAKE256", "SHAKE512")


@pytest.mark.parametrize("height", _VALID_HEIGHTS)
@pytest.mark.parametrize("digest_name", _VALID_DIGESTS)
def test_generate_fails_closed_without_vetted_backend(
    height: int,
    digest_name: str,
) -> None:
    with pytest.raises(xmss.XMSSError, match=_UNAVAILABLE):
        xmss.generate_xmss_keypair(
            height=height,
            digest_algorithm=digest_name,
        )


@pytest.mark.parametrize("invalid_height", (-1, 0, 3, 21, 1.5, True))
def test_generate_rejects_invalid_height_before_backend_check(
    invalid_height: object,
) -> None:
    with pytest.raises(xmss.XMSSError, match="Height must be an integer between 4 and 20"):
        xmss.generate_xmss_keypair(height=cast(int, invalid_height))


@pytest.mark.parametrize("invalid_digest", ("", "MD5", "sha256", None, True))
def test_generate_rejects_invalid_digest_before_backend_check(
    invalid_digest: object,
) -> None:
    with pytest.raises(xmss.XMSSError, match="Invalid digest_algorithm"):
        xmss.generate_xmss_keypair(digest_algorithm=cast(str, invalid_digest))


@pytest.mark.parametrize(
    ("private_key", "message"),
    (
        (b"opaque", b"message"),
        (b"", b""),
        (b"legacy-v1-key", "unicode message"),
        (b"\x00" * 128, b"x" * 65_536),
    ),
    ids=("bytes", "empty", "legacy", "large"),
)
def test_sign_never_uses_unvetted_fallback(
    private_key: bytes,
    message: bytes | str,
) -> None:
    with pytest.raises(xmss.XMSSError, match=_UNAVAILABLE):
        xmss.xmss_sign(private_key, message)


@pytest.mark.parametrize(
    ("public_key", "message", "signature"),
    (
        (b"", b"", b""),
        (b"public", b"message", b"signature"),
        (b"\x00" * 64, "unicode message", b"\x00" * 64),
        (b"legacy-v1-key", b"message", b"legacy-v1-signature"),
        (b"x" * 10_000, b"y" * 10_000, b"z" * 10_000),
    ),
    ids=("empty", "bytes", "unicode", "legacy", "large"),
)
def test_verify_denies_all_inputs_without_backend(
    public_key: bytes,
    message: bytes | str,
    signature: bytes,
) -> None:
    assert xmss.xmss_verify(public_key, message, signature) is False


@pytest.mark.parametrize(
    "operation",
    (
        xmss.serialize_private_key,
        xmss.deserialize_private_key,
        xmss.serialize_public_key,
        xmss.deserialize_public_key,
        xmss.xmss_signature_count,
    ),
)
def test_key_state_operations_reject_opaque_or_legacy_material(
    operation: Callable[[bytes], object],
) -> None:
    with pytest.raises(xmss.XMSSError, match=_UNAVAILABLE):
        operation(b"legacy-or-opaque-key")


@pytest.mark.parametrize("height", _VALID_HEIGHTS)
def test_remaining_signatures_rejects_without_backend(height: int) -> None:
    with pytest.raises(xmss.XMSSError, match=_UNAVAILABLE):
        xmss.xmss_remaining_signatures(b"legacy-or-opaque-key", height=height)


def test_remaining_signatures_validates_height_first() -> None:
    with pytest.raises(xmss.XMSSError, match="Height must be an integer between 4 and 20"):
        xmss.xmss_remaining_signatures(b"key", height=21)


def test_unavailable_error_is_stable_across_valid_parameters() -> None:
    errors: set[str] = set()
    for height, digest_name in zip(_VALID_HEIGHTS, _VALID_DIGESTS, strict=True):
        with pytest.raises(xmss.XMSSError) as exc_info:
            xmss.generate_xmss_keypair(height, digest_name)
        errors.add(str(exc_info.value))
    assert errors == {
        "RFC 8391 XMSS backend is unavailable; no key material was generated"
    }


def test_verify_is_warning_free(recwarn: pytest.WarningsRecorder) -> None:
    assert xmss.xmss_verify(b"public", b"message", b"signature") is False
    assert not recwarn


def test_public_api_signatures_remain_compatible() -> None:
    assert tuple(inspect.signature(xmss.generate_xmss_keypair).parameters) == (
        "height",
        "digest_algorithm",
    )
    assert tuple(inspect.signature(xmss.xmss_sign).parameters) == (
        "private_key_bytes",
        "message",
    )
    assert tuple(inspect.signature(xmss.xmss_verify).parameters) == (
        "public_key_bytes",
        "message",
        "signature",
    )
    assert tuple(inspect.signature(xmss.xmss_remaining_signatures).parameters) == (
        "private_key_bytes",
        "height",
    )


def test_module_exposes_no_custom_wots_or_treehash_implementation() -> None:
    forbidden_helpers = (
        "_build_auth_path",
        "_chain",
        "_compute_root",
        "_ltree",
        "_treehash",
        "_wots_pk",
        "_wots_pk_from_msg",
        "_wots_sign_msg",
        "_wots_sk",
    )
    assert all(not hasattr(xmss, name) for name in forbidden_helpers)


def test_module_documents_the_fail_closed_capability_boundary() -> None:
    module_doc = xmss.__doc__ or ""
    assert "does not implement XMSS" in module_doc
    assert "fail closed" in module_doc
    assert "RFC 8391" in module_doc


def test_defaults_remain_stable_for_callers() -> None:
    assert xmss.DEFAULT_HEIGHT == 10
    assert xmss.DEFAULT_DIGEST == "SHA256"
