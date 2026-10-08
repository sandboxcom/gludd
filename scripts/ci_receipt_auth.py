#!/usr/bin/env python3
"""Authenticate bounded CI receipt envelopes with Gludd's existing HMAC model.

The project gate attestation already standardizes 32-byte local keys,
HMAC-SHA256, constant-time comparison, and bounded freshness.  This module
reuses that primitive with a receipt-specific domain; it does not implement a
new cryptographic algorithm or authorize receipt reuse.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from scripts.gate_status_attestation import _read_key
else:
    from gate_status_attestation import _read_key

AUTH_SCHEMA_VERSION = 1
AUTH_ALGORITHM = "hmac-sha256"
AUTH_DOMAIN = "gludd-ci-batch-receipt-envelope-v1"
MAX_AUTH_ENVELOPE_BYTES = 4096
MAX_AUTH_VALIDITY_SECONDS = 30 * 24 * 60 * 60
DEFAULT_AUTH_VALIDITY_SECONDS = 7 * 24 * 60 * 60
MAX_AUTH_TRUSTED_SIGNERS = 8
MAX_AUTH_REVOKED_SIGNERS = 32
AUTH_CLOCK_SKEW_SECONDS = 60
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENVELOPE_FIELDS = frozenset(
    {
        "action_digest",
        "algorithm",
        "content_sha256",
        "domain",
        "expires_at",
        "issued_at",
        "receipt_kind",
        "schema_version",
        "signature",
        "signer_id",
    }
)
ReceiptAuthStatus = Literal[
    "verified",
    "unsigned-legacy",
    "unknown-signer",
    "tampered",
    "revoked",
    "expired",
    "malformed",
]


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _signer_id(key: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(b"gludd-gate-attestation-key-id-v1\0")
    digest.update(key)
    return digest.hexdigest()


@dataclass(frozen=True)
class ReceiptSigner:
    """One bounded local signer using the gate attestation key shape."""

    key: bytes = field(repr=False)
    issued_at: int
    validity_seconds: int

    def __post_init__(self) -> None:
        if type(self.key) is not bytes or len(self.key) != 32:
            raise ValueError("receipt attestation key must contain 32 bytes")
        if type(self.issued_at) is not int or self.issued_at < 0:
            raise ValueError("issued_at must be a nonnegative integer")
        if (
            type(self.validity_seconds) is not int
            or self.validity_seconds < 1
            or self.validity_seconds > MAX_AUTH_VALIDITY_SECONDS
        ):
            raise ValueError("receipt attestation validity is outside the bound")

    @property
    def signer_id(self) -> str:
        """Return a domain-separated non-secret fingerprint of the random key."""
        return _signer_id(self.key)


@dataclass(frozen=True)
class ReceiptTrustPolicy:
    """Bounded offline trust state captured once for deterministic verification."""

    trusted_keys: Mapping[str, bytes] = field(repr=False)
    verification_epoch: int
    revoked_signers: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        keys = dict(self.trusted_keys)
        if len(keys) > MAX_AUTH_TRUSTED_SIGNERS:
            raise ValueError("trusted signer count exceeds the receipt bound")
        if type(self.verification_epoch) is not int or self.verification_epoch < 0:
            raise ValueError("verification_epoch must be a nonnegative integer")
        if len(self.revoked_signers) > MAX_AUTH_REVOKED_SIGNERS:
            raise ValueError("revoked signer count exceeds the receipt bound")
        for signer_id, key in keys.items():
            if (
                not isinstance(signer_id, str)
                or not _SHA256.fullmatch(signer_id)
                or type(key) is not bytes
                or len(key) != 32
                or not hmac.compare_digest(signer_id, _signer_id(key))
            ):
                raise ValueError("trusted signer identity or key is invalid")
        if any(
            not isinstance(signer_id, str) or not _SHA256.fullmatch(signer_id)
            for signer_id in self.revoked_signers
        ):
            raise ValueError("revoked signer identity is invalid")
        object.__setattr__(self, "trusted_keys", MappingProxyType(keys))


@dataclass(frozen=True)
class ReceiptAuthentication:
    """Content-free authentication classification safe for logs and progress."""

    status: ReceiptAuthStatus
    verified: bool


@dataclass(frozen=True)
class ReceiptAuthContext:
    """In-memory signer and verifier state; representations omit key material."""

    signer: ReceiptSigner
    trust_policy: ReceiptTrustPolicy


def parse_revoked_signers(raw: str) -> frozenset[str]:
    """Parse a bounded comma-separated local revocation policy."""
    if not raw:
        return frozenset()
    values = tuple(part.strip() for part in raw.split(","))
    if (
        len(values) > MAX_AUTH_REVOKED_SIGNERS
        or any(not value or not _SHA256.fullmatch(value) for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError("receipt signer revocation policy is malformed or unbounded")
    return frozenset(values)


def load_gate_receipt_auth_context(
    key_path: Path,
    *,
    issued_at: int,
    revoked_signers: frozenset[str] = frozenset(),
) -> ReceiptAuthContext:
    """Reuse the gate-attestation key lifecycle without exposing its path or bytes."""
    if key_path.is_symlink():
        raise ValueError("receipt attestation key is unsafe")
    try:
        key = _read_key(key_path, create=True)
        metadata = key_path.stat()
    except (OSError, ValueError) as exc:
        raise ValueError("receipt attestation key is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        or stat.S_IMODE(metadata.st_mode) & ~0o600
    ):
        raise ValueError("receipt attestation key is unsafe")
    signer = ReceiptSigner(
        key=key,
        issued_at=issued_at,
        validity_seconds=DEFAULT_AUTH_VALIDITY_SECONDS,
    )
    if signer.signer_id in revoked_signers:
        raise ValueError("active receipt signer is revoked")
    policy = ReceiptTrustPolicy(
        trusted_keys={signer.signer_id: key},
        verification_epoch=issued_at,
        revoked_signers=revoked_signers,
    )
    return ReceiptAuthContext(signer=signer, trust_policy=policy)


def _unsigned_envelope(
    *,
    receipt_kind: str,
    action_digest: str,
    content_sha256: str,
    signer: ReceiptSigner,
) -> dict[str, object]:
    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "domain": AUTH_DOMAIN,
        "algorithm": AUTH_ALGORITHM,
        "signer_id": signer.signer_id,
        "issued_at": signer.issued_at,
        "expires_at": signer.issued_at + signer.validity_seconds,
        "receipt_kind": receipt_kind,
        "action_digest": action_digest,
        "content_sha256": content_sha256,
    }


def create_receipt_envelope(
    *,
    receipt_kind: Literal["pass", "failure"],
    action_digest: str,
    content_sha256: str,
    signer: ReceiptSigner,
) -> dict[str, object]:
    """Return one canonical envelope signed by the established HMAC primitive."""
    if not _SHA256.fullmatch(action_digest) or not _SHA256.fullmatch(content_sha256):
        raise ValueError("receipt authentication digest is invalid")
    unsigned = _unsigned_envelope(
        receipt_kind=receipt_kind,
        action_digest=action_digest,
        content_sha256=content_sha256,
        signer=signer,
    )
    signature = hmac.new(
        signer.key,
        _canonical_json_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return {**unsigned, "signature": signature}


def _malformed() -> ReceiptAuthentication:
    return ReceiptAuthentication("malformed", False)


def authenticate_receipt_envelope(
    envelope: Mapping[str, object] | None,
    *,
    expected_kind: Literal["pass", "failure"],
    expected_action_digest: str,
    expected_content_sha256: str,
    trust_policy: ReceiptTrustPolicy | None,
) -> ReceiptAuthentication:
    """Classify one envelope without returning signer, key, path, or content."""
    if envelope is None:
        return ReceiptAuthentication("unsigned-legacy", False)
    if set(envelope) != _ENVELOPE_FIELDS:
        return _malformed()
    if (
        envelope.get("schema_version") != AUTH_SCHEMA_VERSION
        or envelope.get("domain") != AUTH_DOMAIN
        or envelope.get("algorithm") != AUTH_ALGORITHM
        or envelope.get("receipt_kind") not in {"pass", "failure"}
    ):
        return _malformed()
    signer_id = envelope.get("signer_id")
    action_digest = envelope.get("action_digest")
    content_sha256 = envelope.get("content_sha256")
    signature = envelope.get("signature")
    issued_at = envelope.get("issued_at")
    expires_at = envelope.get("expires_at")
    if (
        not isinstance(signer_id, str)
        or not _SHA256.fullmatch(signer_id)
        or not isinstance(action_digest, str)
        or not _SHA256.fullmatch(action_digest)
        or not isinstance(content_sha256, str)
        or not _SHA256.fullmatch(content_sha256)
        or not isinstance(signature, str)
        or not _SHA256.fullmatch(signature)
        or type(issued_at) is not int
        or issued_at < 0
        or type(expires_at) is not int
        or expires_at <= issued_at
        or expires_at - issued_at > MAX_AUTH_VALIDITY_SECONDS
    ):
        return _malformed()
    if (
        envelope["receipt_kind"] != expected_kind
        or not hmac.compare_digest(action_digest, expected_action_digest)
        or not hmac.compare_digest(content_sha256, expected_content_sha256)
    ):
        return ReceiptAuthentication("tampered", False)
    if trust_policy is None:
        return ReceiptAuthentication("unknown-signer", False)
    if signer_id in trust_policy.revoked_signers:
        return ReceiptAuthentication("revoked", False)
    key = trust_policy.trusted_keys.get(signer_id)
    if key is None:
        return ReceiptAuthentication("unknown-signer", False)
    unsigned = {name: envelope[name] for name in _ENVELOPE_FIELDS - {"signature"}}
    expected_signature = hmac.new(
        key,
        _canonical_json_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return ReceiptAuthentication("tampered", False)
    if (
        trust_policy.verification_epoch < issued_at - AUTH_CLOCK_SKEW_SECONDS
        or trust_policy.verification_epoch > expires_at
    ):
        return ReceiptAuthentication("expired", False)
    return ReceiptAuthentication("verified", True)


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate envelope field")
        payload[key] = value
    return payload


def authenticate_receipt(
    receipt: Path,
    *,
    expected_kind: Literal["pass", "failure"],
    expected_action_digest: str,
    expected_content_sha256: str,
    trust_policy: ReceiptTrustPolicy | None,
) -> ReceiptAuthentication:
    """Read one optional private envelope and return only a fixed classification."""
    envelope_path = receipt / "attestation.json"
    if not envelope_path.exists() and not envelope_path.is_symlink():
        return ReceiptAuthentication("unsigned-legacy", False)
    try:
        if envelope_path.is_symlink():
            return _malformed()
        metadata = envelope_path.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
            or stat.S_IMODE(metadata.st_mode) & ~0o600
            or metadata.st_size < 1
            or metadata.st_size > MAX_AUTH_ENVELOPE_BYTES
        ):
            return _malformed()
        with envelope_path.open("r", encoding="ascii") as handle:
            payload = json.load(handle, object_pairs_hook=_reject_duplicate_pairs)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return _malformed()
    if not isinstance(payload, Mapping):
        return _malformed()
    return authenticate_receipt_envelope(
        payload,
        expected_kind=expected_kind,
        expected_action_digest=expected_action_digest,
        expected_content_sha256=expected_content_sha256,
        trust_policy=trust_policy,
    )


__all__ = [
    "AUTH_ALGORITHM",
    "AUTH_DOMAIN",
    "AUTH_SCHEMA_VERSION",
    "DEFAULT_AUTH_VALIDITY_SECONDS",
    "MAX_AUTH_ENVELOPE_BYTES",
    "MAX_AUTH_REVOKED_SIGNERS",
    "MAX_AUTH_TRUSTED_SIGNERS",
    "MAX_AUTH_VALIDITY_SECONDS",
    "ReceiptAuthContext",
    "ReceiptAuthStatus",
    "ReceiptAuthentication",
    "ReceiptSigner",
    "ReceiptTrustPolicy",
    "authenticate_receipt",
    "authenticate_receipt_envelope",
    "create_receipt_envelope",
    "load_gate_receipt_auth_context",
    "parse_revoked_signers",
]
