"""Tests for the narrow FreeLLMAPI-to-Gludd discovery boundary."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from general_ludd.models.freellmapi_candidates import (
    FREELLMAPI_CANDIDATE_PROTOCOL,
    FreeModelCandidateSeed,
    discover_freellmapi_candidates,
)
from general_ludd.models.freellmapi_catalog import (
    CatalogLimits,
    CatalogModel,
    CatalogQuirk,
    CatalogQuirkTarget,
    CatalogTier,
    FreeLLMAPICatalog,
)


def _model(
    platform: str,
    model_id: str,
    *,
    enabled: bool = True,
    modality: str = "text",
    intelligence_rank: int = 10,
    speed_rank: int = 20,
) -> CatalogModel:
    return CatalogModel(
        platform=platform,
        model_id=model_id,
        display_name=model_id,
        intelligence_rank=intelligence_rank,
        speed_rank=speed_rank,
        size_label="hosted",
        limits=CatalogLimits(rpm=10, rpd=100, tpm=1_000, tpd=10_000),
        monthly_token_budget="1M",
        context_window=32_768,
        enabled=enabled,
        supports_vision=False,
        supports_tools=True,
        modality=modality,
        media_note=None,
        request_style=None,
    )


def _catalog() -> FreeLLMAPICatalog:
    return FreeLLMAPICatalog(
        version="2026.09.15",
        generated_at=datetime(2026, 9, 15, tzinfo=UTC),
        tier=CatalogTier.MONTHLY,
        models=(
            _model("groq", "zeta/code", intelligence_rank=1),
            _model("github", "alpha/code", intelligence_rank=50),
            _model("github", "disabled/code", enabled=False),
            _model("github", "image/model", modality="image"),
        ),
        quirks=(
            CatalogQuirk(
                slug="github-context-cap",
                title="Context cap",
                body="Untrusted prose must not enter a candidate seed.",
                severity="warning",
                targets=(
                    CatalogQuirkTarget(platform="github", model_glob="alpha/*"),
                ),
            ),
            CatalogQuirk(
                slug="all-alpha-models",
                title="Model family",
                body="Another prose advisory.",
                severity="info",
                targets=(
                    CatalogQuirkTarget(platform=None, model_glob="alpha/*"),
                ),
            ),
            CatalogQuirk(
                slug="groq-only",
                title="Provider",
                body="Provider-level advisory.",
                severity="warning",
                targets=(CatalogQuirkTarget(platform="groq", model_glob=None),),
            ),
        ),
        payload_sha256="a" * 64,
    )


def test_discovery_only_seeds_enabled_text_models_for_configured_platforms() -> None:
    seeds = discover_freellmapi_candidates(
        _catalog(),
        configured_platforms=frozenset({"github"}),
    )

    assert tuple(seed.identity for seed in seeds) == (("github", "alpha/code"),)
    seed = seeds[0]
    assert isinstance(seed, FreeModelCandidateSeed)
    assert seed.protocol == FREELLMAPI_CANDIDATE_PROTOCOL
    assert seed.catalog_payload_sha256 == "a" * 64
    assert seed.limits.rpd == 100
    assert seed.intelligence_rank == 50
    assert seed.quirk_slugs == ("all-alpha-models", "github-context-cap")


def test_candidate_seed_is_advisory_and_excludes_endpoints_credentials_and_prose() -> None:
    seed = discover_freellmapi_candidates(
        _catalog(),
        configured_platforms=frozenset({"github"}),
    )[0]
    rendered = repr(seed)

    assert "Untrusted prose" not in rendered
    assert "Another prose" not in rendered
    assert not hasattr(seed, "endpoint")
    assert not hasattr(seed, "api_key")
    assert not hasattr(seed, "credential")
    assert not hasattr(seed, "runnable")


def test_discovery_is_canonical_but_does_not_treat_upstream_rank_as_a_route() -> None:
    seeds = discover_freellmapi_candidates(
        _catalog(),
        configured_platforms=frozenset({"github", "groq"}),
    )

    assert tuple(seed.identity for seed in seeds) == (
        ("github", "alpha/code"),
        ("groq", "zeta/code"),
    )
    assert seeds[0].intelligence_rank == 50
    assert seeds[1].intelligence_rank == 1


def test_candidate_digest_binds_catalog_and_identity_without_secret_material() -> None:
    first = discover_freellmapi_candidates(
        _catalog(),
        configured_platforms=frozenset({"github"}),
    )[0]
    second = discover_freellmapi_candidates(
        _catalog(),
        configured_platforms=frozenset({"github"}),
    )[0]
    changed_catalog = _catalog()
    object.__setattr__(changed_catalog, "payload_sha256", "b" * 64)
    changed = discover_freellmapi_candidates(
        changed_catalog,
        configured_platforms=frozenset({"github"}),
    )[0]

    assert first.candidate_digest == second.candidate_digest
    assert len(first.candidate_digest) == 64
    assert first.candidate_digest != changed.candidate_digest


@pytest.mark.parametrize(
    "configured_platforms",
    [
        {"github"},
        frozenset({"GitHub"}),
        frozenset({" github"}),
        frozenset({"https://example.test"}),
        frozenset({1}),
    ],
)
def test_configured_platform_boundary_is_immutable_and_canonical(
    configured_platforms: object,
) -> None:
    with pytest.raises(ValueError, match="configured_platforms"):
        discover_freellmapi_candidates(
            _catalog(),
            configured_platforms=configured_platforms,  # type: ignore[arg-type]
        )


def test_empty_configured_platform_set_yields_no_candidates() -> None:
    assert discover_freellmapi_candidates(
        _catalog(),
        configured_platforms=frozenset(),
    ) == ()


def test_non_catalog_input_fails_before_candidate_construction() -> None:
    with pytest.raises(ValueError, match="catalog"):
        discover_freellmapi_candidates(
            object(),  # type: ignore[arg-type]
            configured_platforms=frozenset({"github"}),
        )
