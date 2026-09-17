"""Fail-closed admission tests for FreeLLMAPI's signed advisory catalog."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from general_ludd.models.freellmapi_catalog import (
    FREELLMAPI_CATALOG_PUBLIC_KEY_PEM,
    FREELLMAPI_MIN_CATALOG_VERSION,
    FREELLMAPI_UPSTREAM_COMMIT,
    FREELLMAPI_UPSTREAM_RELEASE,
    CatalogAdmissionError,
    CatalogAdmissionFailure,
    CatalogTier,
    FreeLLMAPICatalog,
    admit_freellmapi_catalog,
    admit_signed_catalog,
)


def _catalog() -> dict[str, object]:
    return {
        "version": "2026.09.15",
        "generatedAt": "2026-09-15T12:30:00Z",
        "tier": "monthly",
        "models": [
            {
                "platform": "github",
                "modelId": "openai/gpt-4.1-mini",
                "displayName": "GPT-4.1 Mini",
                "intelligenceRank": 12,
                "speedRank": 4,
                "sizeLabel": "hosted",
                "limits": {"rpm": 15, "rpd": 150, "tpm": None, "tpd": 1_000_000},
                "monthlyTokenBudget": "50M",
                "contextWindow": 128_000,
                "enabled": True,
                "supportsVision": True,
                "supportsTools": True,
            },
            {
                "platform": "cloudflare",
                "modelId": "black-forest-labs/flux-1-schnell",
                "displayName": "FLUX.1 Schnell",
                "intelligenceRank": 3,
                "speedRank": 6,
                "sizeLabel": "keyless",
                "limits": {"rpm": None, "rpd": None, "tpm": None, "tpd": None},
                "monthlyTokenBudget": None,
                "contextWindow": None,
                "enabled": True,
                "supportsVision": False,
                "supportsTools": False,
                "modality": "image",
                "mediaNote": "Keyless",
                "requestStyle": "json",
            },
        ],
        "quirks": [
            {
                "slug": "github-context-cap",
                "title": "Advertised context differs from routable context",
                "body": "Probe the effective context before promotion.",
                "severity": "warning",
                "targets": [
                    {"platform": "github", "modelGlob": "openai/gpt-4.1*"}
                ],
            }
        ],
    }


def _signed(
    catalog: dict[str, object] | None = None,
) -> tuple[bytes, str, bytes]:
    payload = json.dumps(
        catalog or _catalog(), separators=(",", ":"), sort_keys=True
    ).encode()
    private_key = Ed25519PrivateKey.generate()
    signature = base64.b64encode(private_key.sign(payload)).decode("ascii")
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return payload, signature, public_key


def _admit(
    catalog: dict[str, object] | None = None,
    *,
    previous_version: str | None = None,
) -> FreeLLMAPICatalog:
    payload, signature, public_key = _signed(catalog)
    return admit_signed_catalog(
        payload,
        signature,
        public_key_pem=public_key,
        minimum_version="2026.06.07",
        previous_version=previous_version,
    )


def test_upstream_identity_and_key_are_immutable_release_facts() -> None:
    assert FREELLMAPI_UPSTREAM_RELEASE == "v0.9.9"
    assert FREELLMAPI_UPSTREAM_COMMIT == "780a7d8d6dcbc818eb10ec17da210635b569ae22"
    assert FREELLMAPI_MIN_CATALOG_VERSION == "2026.06.07"
    key = serialization.load_pem_public_key(FREELLMAPI_CATALOG_PUBLIC_KEY_PEM)
    assert key.__class__.__name__ == "Ed25519PublicKey"


def test_valid_catalog_becomes_immutable_advisory_evidence() -> None:
    admitted = _admit()

    assert admitted.version == "2026.09.15"
    assert admitted.tier is CatalogTier.MONTHLY
    assert admitted.payload_sha256
    assert len(admitted.models) == 2
    assert admitted.models[0].identity == ("github", "openai/gpt-4.1-mini")
    assert admitted.models[0].limits.rpd == 150
    assert admitted.models[0].supports_tools is True
    assert admitted.models[1].modality == "image"
    assert admitted.quirks[0].targets[0].model_glob == "openai/gpt-4.1*"
    assert tuple(model.identity for model in admitted.chat_seeds) == (
        ("github", "openai/gpt-4.1-mini"),
    )
    field_name = "platform"
    with pytest.raises((AttributeError, TypeError)):
        setattr(admitted.models[0], field_name, "changed")


@pytest.mark.parametrize(
    ("payload_change", "signature_change", "key_change"),
    [
        (lambda value: value + b"\n", lambda value: value, lambda value: value),
        (lambda value: value, lambda _value: "", lambda value: value),
        (lambda value: value, lambda _value: "not-base64", lambda value: value),
        (
            lambda value: value,
            lambda value: value,
            lambda _value: _signed()[2],
        ),
    ],
)
def test_signature_is_over_exact_bytes_and_fails_closed(
    payload_change: Callable[[bytes], bytes],
    signature_change: Callable[[str], str],
    key_change: Callable[[bytes], bytes],
) -> None:
    payload, signature, public_key = _signed()

    with pytest.raises(CatalogAdmissionError) as raised:
        admit_signed_catalog(
            payload_change(payload),
            signature_change(signature),
            public_key_pem=key_change(public_key),
            minimum_version="2026.06.07",
        )

    assert raised.value.failure is CatalogAdmissionFailure.SIGNATURE
    assert payload.decode() not in str(raised.value)


def test_payload_size_is_bounded_before_parsing() -> None:
    payload, signature, public_key = _signed()

    with pytest.raises(CatalogAdmissionError) as raised:
        admit_signed_catalog(
            payload,
            signature,
            public_key_pem=public_key,
            minimum_version="2026.06.07",
            max_payload_bytes=len(payload) - 1,
        )

    assert raised.value.failure is CatalogAdmissionFailure.SIZE


def test_duplicate_json_keys_and_invalid_utf8_are_rejected_after_signature() -> None:
    duplicate = b'{"version":"2026.09.15","version":"2026.09.16"}'
    invalid_utf8 = b"\xff"
    for payload, expected in (
        (duplicate, CatalogAdmissionFailure.JSON),
        (invalid_utf8, CatalogAdmissionFailure.ENCODING),
    ):
        private_key = Ed25519PrivateKey.generate()
        signature = base64.b64encode(private_key.sign(payload)).decode("ascii")
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        with pytest.raises(CatalogAdmissionError) as raised:
            admit_signed_catalog(
                payload,
                signature,
                public_key_pem=public_key,
                minimum_version="2026.06.07",
            )

        assert raised.value.failure is expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"[]", CatalogAdmissionFailure.SCHEMA),
        (b'{"notFinite":NaN}', CatalogAdmissionFailure.JSON),
    ],
)
def test_non_object_root_and_non_finite_json_are_rejected(
    payload: bytes,
    expected: CatalogAdmissionFailure,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    signature = base64.b64encode(private_key.sign(payload)).decode("ascii")
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    with pytest.raises(CatalogAdmissionError) as raised:
        admit_signed_catalog(
            payload,
            signature,
            public_key_pem=public_key,
            minimum_version="2026.06.07",
        )

    assert raised.value.failure is expected


@pytest.mark.parametrize(
    "version,previous,failure",
    [
        ("2026.06.06", None, CatalogAdmissionFailure.STALE),
        ("2026.09.14", "2026.09.15", CatalogAdmissionFailure.ROLLBACK),
        ("2026-09-15", None, CatalogAdmissionFailure.SCHEMA),
        ("2026.02.30", None, CatalogAdmissionFailure.SCHEMA),
    ],
)
def test_version_floor_and_rollback_are_independent_gates(
    version: str,
    previous: str | None,
    failure: CatalogAdmissionFailure,
) -> None:
    catalog = _catalog()
    catalog["version"] = version

    with pytest.raises(CatalogAdmissionError) as raised:
        _admit(catalog, previous_version=previous)

    assert raised.value.failure is failure


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(tier="paid"),
        lambda value: value.update(generatedAt="2026-09-15T12:30:00"),
        lambda value: value.update(generatedAt="not-a-date"),
        lambda value: value.update(models="not-a-list"),
        lambda value: value.update(quirks="not-a-list"),
        lambda value: value.update(unexpected="drift"),
        lambda value: value["models"][0].update(platform=" https://bad.example"),
        lambda value: value["models"][0].update(platform="Bad!"),
        lambda value: value["models"][0].update(modelId=""),
        lambda value: value["models"][0].update(displayName="bad\nname"),
        lambda value: value["models"][0].update(contextWindow=True),
        lambda value: value["models"][0].update(contextWindow=100_000_001),
        lambda value: value["models"][0].update(enabled=1),
        lambda value: value["models"][0].update(modality="video"),
        lambda value: value["models"][0]["limits"].update(rpm=-1),
        lambda value: value["models"][0]["limits"].update(secret="leak"),
        lambda value: value["models"][0].update(apiKey="leak"),
        lambda value: value["quirks"][0].update(severity="critical"),
        lambda value: value["quirks"][0].update(slug="Bad!"),
        lambda value: value["quirks"][0].update(targets=[]),
        lambda value: value["quirks"][0]["targets"][0].update(platform="Bad!"),
        lambda value: value["quirks"][0]["targets"][0].update(
            platform=None, modelGlob=None
        ),
        lambda value: value["quirks"][0]["targets"][0].update(endpoint="https://bad"),
    ],
)
def test_schema_is_bounded_allowlisted_and_contains_no_secret_or_endpoint_surface(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    catalog = deepcopy(_catalog())
    mutate(catalog)

    with pytest.raises(CatalogAdmissionError) as raised:
        _admit(catalog)

    assert raised.value.failure is CatalogAdmissionFailure.SCHEMA
    assert "leak" not in str(raised.value)
    assert "bad.example" not in str(raised.value)


def test_duplicate_model_identity_is_rejected() -> None:
    catalog = _catalog()
    models = catalog["models"]
    assert isinstance(models, list)
    models.append(deepcopy(models[0]))

    with pytest.raises(CatalogAdmissionError) as raised:
        _admit(catalog)

    assert raised.value.failure is CatalogAdmissionFailure.SCHEMA


def test_catalog_collection_bounds_are_enforced() -> None:
    catalog = _catalog()
    catalog["models"] = [deepcopy(catalog["models"][0])] * 5_001  # type: ignore[index]

    with pytest.raises(CatalogAdmissionError) as raised:
        _admit(catalog)

    assert raised.value.failure is CatalogAdmissionFailure.SCHEMA


def test_optional_non_chat_registries_are_bounded_but_not_imported() -> None:
    catalog = _catalog()
    catalog.update(
        embeddings=[],
        transcriptionModels=[],
        videoModels=[],
    )

    admitted = _admit(catalog)

    assert len(admitted.models) == 2


def test_duplicate_quirk_slug_is_rejected() -> None:
    catalog = _catalog()
    quirks = catalog["quirks"]
    assert isinstance(quirks, list)
    quirks.append(deepcopy(quirks[0]))

    with pytest.raises(CatalogAdmissionError) as raised:
        _admit(catalog)

    assert raised.value.failure is CatalogAdmissionFailure.SCHEMA


def test_wrong_length_signature_and_pinned_wrapper_fail_without_content() -> None:
    payload, _signature, public_key = _signed()
    short_signature = base64.b64encode(b"x" * 63).decode("ascii")

    with pytest.raises(CatalogAdmissionError) as short:
        admit_signed_catalog(
            payload,
            short_signature,
            public_key_pem=public_key,
            minimum_version="2026.06.07",
        )
    with pytest.raises(CatalogAdmissionError) as pinned:
        admit_freellmapi_catalog(payload, short_signature)

    assert short.value.failure is CatalogAdmissionFailure.SIGNATURE
    assert pinned.value.failure is CatalogAdmissionFailure.SIGNATURE
