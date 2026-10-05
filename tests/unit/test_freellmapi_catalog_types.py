"""Contracts for the split FreeLLMAPI catalog value objects."""

from __future__ import annotations

from datetime import UTC, datetime

from general_ludd.models.freellmapi_catalog_types import (
    CatalogLimits,
    CatalogModel,
    CatalogQuirk,
    CatalogQuirkTarget,
    CatalogTier,
    FreeLLMAPICatalog,
)


def _model(*, enabled: bool = True, modality: str = "text") -> CatalogModel:
    return CatalogModel(
        platform="example",
        model_id="model-1",
        display_name="Model One",
        intelligence_rank=1,
        speed_rank=2,
        size_label="small",
        limits=CatalogLimits(rpm=10, rpd=None, tpm=1_000, tpd=None),
        monthly_token_budget=None,
        context_window=4_096,
        enabled=enabled,
        supports_vision=False,
        supports_tools=True,
        modality=modality,
        media_note=None,
        request_style=None,
    )


def test_catalog_model_identity_is_the_upstream_namespace_pair() -> None:
    assert _model().identity == ("example", "model-1")


def test_chat_seeds_keep_only_enabled_text_models() -> None:
    text = _model()
    disabled = _model(enabled=False)
    image = _model(modality="image")
    catalog = FreeLLMAPICatalog(
        version="2026.06.07",
        generated_at=datetime(2026, 6, 7, tzinfo=UTC),
        tier=CatalogTier.LIVE,
        models=(text, disabled, image),
        quirks=(
            CatalogQuirk(
                slug="example",
                title="Example",
                body="Advisory only",
                severity="info",
                targets=(CatalogQuirkTarget(platform="example", model_glob=None),),
            ),
        ),
        payload_sha256="0" * 64,
    )

    assert catalog.chat_seeds == (text,)
