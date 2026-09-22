"""Authenticate and bound FreeLLMAPI catalog data without importing its router.

The signed upstream catalog is advisory knowledge.  Rows admitted here are not
provider sessions, runnable candidates, routes, or authorization.  Gludd still
owns probing, policy, calibration, routing, execution, and lifecycle decisions.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from enum import StrEnum

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from general_ludd.models.freellmapi_catalog_schema import (
    DuplicateCatalogKey,
    parse_catalog,
    reject_duplicate_pairs,
    reject_json_constant,
    version_tuple,
)
from general_ludd.models.freellmapi_catalog_types import (
    CatalogLimits,
    CatalogModel,
    CatalogQuirk,
    CatalogQuirkTarget,
    CatalogSchemaError,
    CatalogTier,
    FreeLLMAPICatalog,
)

FREELLMAPI_UPSTREAM_RELEASE = "v0.9.9"
FREELLMAPI_UPSTREAM_COMMIT = "780a7d8d6dcbc818eb10ec17da210635b569ae22"
FREELLMAPI_MIN_CATALOG_VERSION = "2026.06.07"
FREELLMAPI_CATALOG_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAq9yv4+3EeyMHKsfVYBhkcz1lYgIXSUeHNnN6tNgYX3k=
-----END PUBLIC KEY-----
"""

DEFAULT_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024


class CatalogAdmissionFailure(StrEnum):
    """Content-free reasons a catalog can be rejected before it affects routing."""

    SIZE = "size"
    SIGNATURE = "signature"
    ENCODING = "encoding"
    JSON = "json"
    SCHEMA = "schema"
    STALE = "stale"
    ROLLBACK = "rollback"


class CatalogAdmissionError(ValueError):
    """Fail-closed catalog error that never embeds payload or signature content."""

    def __init__(self, failure: CatalogAdmissionFailure) -> None:
        """Retain only one bounded rejection category."""
        self.failure = failure
        super().__init__(f"FreeLLMAPI catalog rejected: {failure.value}")


def _verify_signature(payload: bytes, signature: str, public_key_pem: bytes) -> None:
    if not isinstance(signature, str) or not signature:
        raise CatalogAdmissionError(CatalogAdmissionFailure.SIGNATURE)
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
        key = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(key, Ed25519PublicKey) or len(signature_bytes) != 64:
            raise ValueError
        key.verify(signature_bytes, payload)
    except (
        InvalidSignature,
        UnsupportedAlgorithm,
        ValueError,
        TypeError,
        binascii.Error,
    ) as exc:
        raise CatalogAdmissionError(CatalogAdmissionFailure.SIGNATURE) from exc


def admit_signed_catalog(
    payload: bytes,
    signature: str,
    *,
    public_key_pem: bytes,
    minimum_version: str,
    previous_version: str | None = None,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> FreeLLMAPICatalog:
    """Authenticate exact bytes and admit a bounded advisory catalog.

    This generic primitive exists for deterministic signature fixtures. Runtime
    composition must call :func:`admit_freellmapi_catalog`, which pins the source
    key and minimum version and exposes no override surface.
    """
    if (
        type(payload) is not bytes
        or not payload
        or isinstance(max_payload_bytes, bool)
        or not isinstance(max_payload_bytes, int)
        or max_payload_bytes <= 0
        or len(payload) > max_payload_bytes
    ):
        raise CatalogAdmissionError(CatalogAdmissionFailure.SIZE)
    _verify_signature(payload, signature, public_key_pem)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CatalogAdmissionError(CatalogAdmissionFailure.ENCODING) from exc
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_json_constant,
        )
    except (json.JSONDecodeError, DuplicateCatalogKey, ValueError) as exc:
        raise CatalogAdmissionError(CatalogAdmissionFailure.JSON) from exc
    try:
        catalog = parse_catalog(
            decoded,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
        )
        catalog_version = version_tuple(catalog.version)
        minimum = version_tuple(minimum_version)
        previous = (
            version_tuple(previous_version) if previous_version is not None else None
        )
    except CatalogSchemaError as exc:
        raise CatalogAdmissionError(CatalogAdmissionFailure.SCHEMA) from exc
    if catalog_version < minimum:
        raise CatalogAdmissionError(CatalogAdmissionFailure.STALE)
    if previous is not None and catalog_version < previous:
        raise CatalogAdmissionError(CatalogAdmissionFailure.ROLLBACK)
    return catalog


def admit_freellmapi_catalog(
    payload: bytes,
    signature: str,
    *,
    previous_version: str | None = None,
) -> FreeLLMAPICatalog:
    """Admit one catalog using only the release-pinned FreeLLMAPI trust root."""
    return admit_signed_catalog(
        payload,
        signature,
        public_key_pem=FREELLMAPI_CATALOG_PUBLIC_KEY_PEM,
        minimum_version=FREELLMAPI_MIN_CATALOG_VERSION,
        previous_version=previous_version,
    )


__all__ = (
    "FREELLMAPI_CATALOG_PUBLIC_KEY_PEM",
    "FREELLMAPI_MIN_CATALOG_VERSION",
    "FREELLMAPI_UPSTREAM_COMMIT",
    "FREELLMAPI_UPSTREAM_RELEASE",
    "CatalogAdmissionError",
    "CatalogAdmissionFailure",
    "CatalogLimits",
    "CatalogModel",
    "CatalogQuirk",
    "CatalogQuirkTarget",
    "CatalogSchemaError",
    "CatalogTier",
    "FreeLLMAPICatalog",
    "admit_freellmapi_catalog",
)
