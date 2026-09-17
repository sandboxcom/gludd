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
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import cast

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FREELLMAPI_UPSTREAM_RELEASE = "v0.9.9"
FREELLMAPI_UPSTREAM_COMMIT = "780a7d8d6dcbc818eb10ec17da210635b569ae22"
FREELLMAPI_MIN_CATALOG_VERSION = "2026.06.07"
FREELLMAPI_CATALOG_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAq9yv4+3EeyMHKsfVYBhkcz1lYgIXSUeHNnN6tNgYX3k=
-----END PUBLIC KEY-----
"""

DEFAULT_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
_MAX_MODELS = 5_000
_MAX_QUIRKS = 1_000
_MAX_TARGETS_PER_QUIRK = 100
_MAX_OPTIONAL_ROWS = 5_000
_MAX_QUOTA = 1_000_000_000_000
_MAX_CONTEXT_WINDOW = 100_000_000
_VERSION_RE = re.compile(r"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$")
_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


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


class CatalogTier(StrEnum):
    """Upstream catalog freshness tiers."""

    LIVE = "live"
    MONTHLY = "monthly"


@dataclass(frozen=True, slots=True)
class CatalogLimits:
    """Advertised provider quota hints; observed Gludd accounting remains final."""

    rpm: int | None
    rpd: int | None
    tpm: int | None
    tpd: int | None


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """One authenticated but still-unprobed upstream model row."""

    platform: str
    model_id: str
    display_name: str
    intelligence_rank: int
    speed_rank: int
    size_label: str
    limits: CatalogLimits
    monthly_token_budget: str | None
    context_window: int | None
    enabled: bool
    supports_vision: bool
    supports_tools: bool
    modality: str
    media_note: str | None
    request_style: str | None

    @property
    def identity(self) -> tuple[str, str]:
        """Return the upstream namespace pair, not a runnable Gludd identity."""
        return self.platform, self.model_id


@dataclass(frozen=True, slots=True)
class CatalogQuirkTarget:
    """A bounded selector attached to one advisory provider quirk."""

    platform: str | None
    model_glob: str | None


@dataclass(frozen=True, slots=True)
class CatalogQuirk:
    """Authenticated provider behavior text that remains an advisory hint."""

    slug: str
    title: str
    body: str
    severity: str
    targets: tuple[CatalogQuirkTarget, ...]


@dataclass(frozen=True, slots=True)
class FreeLLMAPICatalog:
    """Immutable exact-byte evidence admitted from the pinned upstream feed."""

    version: str
    generated_at: datetime
    tier: CatalogTier
    models: tuple[CatalogModel, ...]
    quirks: tuple[CatalogQuirk, ...]
    payload_sha256: str

    @property
    def chat_seeds(self) -> tuple[CatalogModel, ...]:
        """Return enabled text rows suitable for Gludd-owned probing only."""
        return tuple(
            model
            for model in self.models
            if model.enabled and model.modality == "text"
        )


class CatalogSchemaError(ValueError):
    """Mark an untrusted FreeLLMAPI catalog schema failure."""


_SchemaError = CatalogSchemaError


class _DuplicateKey(ValueError):
    """Internal marker for duplicate JSON object keys."""


def _reject_duplicate_pairs(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError


def _as_object(
    value: object,
    *,
    required: frozenset[str],
    allowed: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict:
        raise _SchemaError
    record = cast(dict[str, object], value)
    keys = frozenset(record)
    if not required <= keys or not keys <= allowed:
        raise _SchemaError
    return record


def _as_list(value: object, *, maximum: int) -> list[object]:
    if not isinstance(value, list) or len(value) > maximum:
        raise _SchemaError
    return cast(list[object], value)


def _bounded_text(
    value: object,
    *,
    maximum: int,
) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise _SchemaError
    if not value or len(value.encode("utf-8")) > maximum:
        raise _SchemaError
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise _SchemaError
    return value


def _optional_text(value: object, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, maximum=maximum)


def _bounded_integer(
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise _SchemaError
    return value


def _optional_integer(
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    return _bounded_integer(value, minimum=minimum, maximum=maximum)


def _strict_boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise _SchemaError
    return value


def _version_tuple(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise _SchemaError
    try:
        parsed = date.fromisoformat(value.replace(".", "-"))
    except ValueError as exc:
        raise _SchemaError from exc
    return parsed.year, parsed.month, parsed.day


def _generated_at(value: object) -> datetime:
    text = _bounded_text(value, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _SchemaError from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _SchemaError
    return parsed


def _catalog_limits(value: object) -> CatalogLimits:
    fields = frozenset({"rpm", "rpd", "tpm", "tpd"})
    record = _as_object(value, required=fields, allowed=fields)
    return CatalogLimits(
        rpm=_optional_integer(record["rpm"], minimum=0, maximum=_MAX_QUOTA),
        rpd=_optional_integer(record["rpd"], minimum=0, maximum=_MAX_QUOTA),
        tpm=_optional_integer(record["tpm"], minimum=0, maximum=_MAX_QUOTA),
        tpd=_optional_integer(record["tpd"], minimum=0, maximum=_MAX_QUOTA),
    )


def _catalog_model(value: object) -> CatalogModel:
    required = frozenset(
        {
            "platform",
            "modelId",
            "displayName",
            "intelligenceRank",
            "speedRank",
            "sizeLabel",
            "limits",
            "monthlyTokenBudget",
            "contextWindow",
            "enabled",
            "supportsVision",
            "supportsTools",
        }
    )
    allowed = required | frozenset({"modality", "mediaNote", "requestStyle"})
    record = _as_object(value, required=required, allowed=allowed)
    platform = _bounded_text(record["platform"], maximum=64)
    if _PLATFORM_RE.fullmatch(platform) is None:
        raise _SchemaError
    modality = _bounded_text(record.get("modality", "text"), maximum=16)
    if modality not in {"text", "image", "audio"}:
        raise _SchemaError
    return CatalogModel(
        platform=platform,
        model_id=_bounded_text(record["modelId"], maximum=512),
        display_name=_bounded_text(record["displayName"], maximum=512),
        intelligence_rank=_bounded_integer(
            record["intelligenceRank"], minimum=0, maximum=1_000_000
        ),
        speed_rank=_bounded_integer(
            record["speedRank"], minimum=0, maximum=1_000_000
        ),
        size_label=_bounded_text(record["sizeLabel"], maximum=128),
        limits=_catalog_limits(record["limits"]),
        monthly_token_budget=_optional_text(
            record["monthlyTokenBudget"], maximum=64
        ),
        context_window=_optional_integer(
            record["contextWindow"], minimum=1, maximum=_MAX_CONTEXT_WINDOW
        ),
        enabled=_strict_boolean(record["enabled"]),
        supports_vision=_strict_boolean(record["supportsVision"]),
        supports_tools=_strict_boolean(record["supportsTools"]),
        modality=modality,
        media_note=_optional_text(record.get("mediaNote"), maximum=512),
        request_style=_optional_text(record.get("requestStyle"), maximum=128),
    )


def _quirk_target(value: object) -> CatalogQuirkTarget:
    fields = frozenset({"platform", "modelGlob"})
    record = _as_object(value, required=fields, allowed=fields)
    platform = _optional_text(record["platform"], maximum=64)
    if platform is not None and _PLATFORM_RE.fullmatch(platform) is None:
        raise _SchemaError
    model_glob = _optional_text(record["modelGlob"], maximum=512)
    if platform is None and model_glob is None:
        raise _SchemaError
    return CatalogQuirkTarget(platform=platform, model_glob=model_glob)


def _catalog_quirk(value: object) -> CatalogQuirk:
    fields = frozenset({"slug", "title", "body", "severity", "targets"})
    record = _as_object(value, required=fields, allowed=fields)
    slug = _bounded_text(record["slug"], maximum=128)
    if _SLUG_RE.fullmatch(slug) is None:
        raise _SchemaError
    severity = _bounded_text(record["severity"], maximum=16)
    if severity not in {"blocker", "warning", "info"}:
        raise _SchemaError
    targets = tuple(
        _quirk_target(target)
        for target in _as_list(
            record["targets"], maximum=_MAX_TARGETS_PER_QUIRK
        )
    )
    if not targets:
        raise _SchemaError
    return CatalogQuirk(
        slug=slug,
        title=_bounded_text(record["title"], maximum=512),
        body=_bounded_text(record["body"], maximum=16_384),
        severity=severity,
        targets=targets,
    )


def _parse_catalog(value: object, *, payload_sha256: str) -> FreeLLMAPICatalog:
    required = frozenset({"version", "generatedAt", "tier", "models", "quirks"})
    ignored_optional = frozenset(
        {"embeddings", "transcriptionModels", "videoModels"}
    )
    record = _as_object(
        value,
        required=required,
        allowed=required | ignored_optional,
    )
    for field in ignored_optional & record.keys():
        _as_list(record[field], maximum=_MAX_OPTIONAL_ROWS)
    version = _bounded_text(record["version"], maximum=10)
    _version_tuple(version)
    try:
        tier = CatalogTier(_bounded_text(record["tier"], maximum=16))
    except ValueError as exc:
        raise _SchemaError from exc
    models = tuple(
        _catalog_model(model)
        for model in _as_list(record["models"], maximum=_MAX_MODELS)
    )
    identities = tuple(model.identity for model in models)
    if len(set(identities)) != len(identities):
        raise _SchemaError
    quirks = tuple(
        _catalog_quirk(quirk)
        for quirk in _as_list(record["quirks"], maximum=_MAX_QUIRKS)
    )
    if len({quirk.slug for quirk in quirks}) != len(quirks):
        raise _SchemaError
    return FreeLLMAPICatalog(
        version=version,
        generated_at=_generated_at(record["generatedAt"]),
        tier=tier,
        models=models,
        quirks=quirks,
        payload_sha256=payload_sha256,
    )


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
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, _DuplicateKey, ValueError) as exc:
        raise CatalogAdmissionError(CatalogAdmissionFailure.JSON) from exc
    try:
        catalog = _parse_catalog(
            decoded,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
        )
        catalog_version = _version_tuple(catalog.version)
        minimum = _version_tuple(minimum_version)
        previous = (
            _version_tuple(previous_version) if previous_version is not None else None
        )
    except _SchemaError as exc:
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
