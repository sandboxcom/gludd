"""Bounded, fail-closed schema parsing for untrusted FreeLLMAPI catalogs."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import cast

from general_ludd.models.freellmapi_catalog_types import (
    CatalogLimits,
    CatalogModel,
    CatalogQuirk,
    CatalogQuirkTarget,
    CatalogSchemaError,
    CatalogTier,
    FreeLLMAPICatalog,
)

_MAX_MODELS = 5_000
_MAX_QUIRKS = 1_000
_MAX_TARGETS_PER_QUIRK = 100
_MAX_OPTIONAL_ROWS = 5_000
_MAX_QUOTA = 1_000_000_000_000
_MAX_CONTEXT_WINDOW = 100_000_000
_VERSION_RE = re.compile(r"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$")
_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class DuplicateCatalogKey(ValueError):
    """Mark a duplicate key in an untrusted JSON object."""


def reject_duplicate_pairs(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    """Build an object while refusing ambiguous duplicate keys."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateCatalogKey
        result[key] = value
    return result


def reject_json_constant(_value: str) -> object:
    """Reject JSON extensions such as NaN and infinity."""
    raise ValueError


def _as_object(
    value: object,
    *,
    required: frozenset[str],
    allowed: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict:
        raise CatalogSchemaError
    record = cast(dict[str, object], value)
    keys = frozenset(record)
    if not required <= keys or not keys <= allowed:
        raise CatalogSchemaError
    return record


def _as_list(value: object, *, maximum: int) -> list[object]:
    if not isinstance(value, list) or len(value) > maximum:
        raise CatalogSchemaError
    return cast(list[object], value)


def _bounded_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise CatalogSchemaError
    if not value or len(value.encode("utf-8")) > maximum:
        raise CatalogSchemaError
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise CatalogSchemaError
    return value


def _optional_text(value: object, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, maximum=maximum)


def _bounded_integer(value: object, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise CatalogSchemaError
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
        raise CatalogSchemaError
    return value


def version_tuple(value: object) -> tuple[int, int, int]:
    """Parse the catalog's strict dotted ISO-date version."""
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise CatalogSchemaError
    try:
        parsed = date.fromisoformat(value.replace(".", "-"))
    except ValueError as exc:
        raise CatalogSchemaError from exc
    return parsed.year, parsed.month, parsed.day


def _generated_at(value: object) -> datetime:
    text = _bounded_text(value, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogSchemaError from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CatalogSchemaError
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
        raise CatalogSchemaError
    modality = _bounded_text(record.get("modality", "text"), maximum=16)
    if modality not in {"text", "image", "audio"}:
        raise CatalogSchemaError
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
        raise CatalogSchemaError
    model_glob = _optional_text(record["modelGlob"], maximum=512)
    if platform is None and model_glob is None:
        raise CatalogSchemaError
    return CatalogQuirkTarget(platform=platform, model_glob=model_glob)


def _catalog_quirk(value: object) -> CatalogQuirk:
    fields = frozenset({"slug", "title", "body", "severity", "targets"})
    record = _as_object(value, required=fields, allowed=fields)
    slug = _bounded_text(record["slug"], maximum=128)
    if _SLUG_RE.fullmatch(slug) is None:
        raise CatalogSchemaError
    severity = _bounded_text(record["severity"], maximum=16)
    if severity not in {"blocker", "warning", "info"}:
        raise CatalogSchemaError
    targets = tuple(
        _quirk_target(target)
        for target in _as_list(
            record["targets"], maximum=_MAX_TARGETS_PER_QUIRK
        )
    )
    if not targets:
        raise CatalogSchemaError
    return CatalogQuirk(
        slug=slug,
        title=_bounded_text(record["title"], maximum=512),
        body=_bounded_text(record["body"], maximum=16_384),
        severity=severity,
        targets=targets,
    )


def parse_catalog(value: object, *, payload_sha256: str) -> FreeLLMAPICatalog:
    """Parse one authenticated JSON value into bounded advisory contracts."""
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
    version_tuple(version)
    try:
        tier = CatalogTier(_bounded_text(record["tier"], maximum=16))
    except ValueError as exc:
        raise CatalogSchemaError from exc
    models = tuple(
        _catalog_model(model)
        for model in _as_list(record["models"], maximum=_MAX_MODELS)
    )
    identities = tuple(model.identity for model in models)
    if len(set(identities)) != len(identities):
        raise CatalogSchemaError
    quirks = tuple(
        _catalog_quirk(quirk)
        for quirk in _as_list(record["quirks"], maximum=_MAX_QUIRKS)
    )
    if len({quirk.slug for quirk in quirks}) != len(quirks):
        raise CatalogSchemaError
    return FreeLLMAPICatalog(
        version=version,
        generated_at=_generated_at(record["generatedAt"]),
        tier=tier,
        models=models,
        quirks=quirks,
        payload_sha256=payload_sha256,
    )


__all__ = (
    "DuplicateCatalogKey",
    "parse_catalog",
    "reject_duplicate_pairs",
    "reject_json_constant",
    "version_tuple",
)
