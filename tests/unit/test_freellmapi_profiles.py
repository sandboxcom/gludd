"""Tests for reusing Gludd profile construction with FreeLLMAPI evidence."""

from __future__ import annotations

import pytest

from general_ludd.models.auto_configurator import AutoConfigurator
from general_ludd.models.candidate_identity import (
    CatalogFreeTierCandidateIdentity,
    ModelCandidateProvider,
)
from general_ludd.models.freellmapi_candidates import FreeModelCandidateSeed
from general_ludd.models.freellmapi_catalog import CatalogLimits
from general_ludd.models.freellmapi_profiles import (
    FREELLMAPI_PROBE_PROFILE_PROTOCOL,
    FreeModelProbeProfile,
    build_freellmapi_probe_gateway,
    build_freellmapi_probe_profiles,
    catalog_free_tier_identity,
)
from general_ludd.models.gateway import ModelProfile


def _seed(
    platform: str = "groq",
    model_id: str = "acme/coder-model",
    *,
    context_window: int | None = 32_768,
) -> FreeModelCandidateSeed:
    return FreeModelCandidateSeed(
        platform=platform,
        model_id=model_id,
        display_name="Acme Coder",
        intelligence_rank=4,
        speed_rank=2,
        size_label="70B",
        limits=CatalogLimits(rpm=10, rpd=100, tpm=2_000, tpd=20_000),
        monthly_token_budget="1M",
        context_window=context_window,
        supports_vision=False,
        supports_tools=True,
        catalog_version="2026.09.15",
        catalog_payload_sha256="a" * 64,
        quirk_slugs=("groq-output-cap",),
    )


def test_probe_profile_reuses_native_provider_and_profile_contracts() -> None:
    bindings = build_freellmapi_probe_profiles((_seed(),))

    assert len(bindings) == 1
    binding = bindings[0]
    assert isinstance(binding, FreeModelProbeProfile)
    assert isinstance(binding.profile, ModelProfile)
    assert binding.protocol == FREELLMAPI_PROBE_PROFILE_PROTOCOL
    assert binding.candidate == _seed()
    assert binding.candidate_digest == _seed().candidate_digest
    assert binding.profile.provider == "groq"
    assert binding.profile.model_name == "acme/coder-model"
    assert binding.profile.provider_package == "langchain-openai"
    assert binding.profile.provider_class_hint == "ChatOpenAI"
    assert binding.profile.api_base_alias == "groq_api_base"
    assert binding.profile.credential_alias == "groq_api_key"
    assert "coder" in binding.profile.role_names


def test_probe_profile_cannot_be_routed_before_gludd_promotes_it() -> None:
    profile = build_freellmapi_probe_profiles((_seed(),))[0].profile

    assert profile.enabled is False
    assert profile.probe_enabled is True
    assert profile.api_metered is False
    assert profile.run_budget_usd == 0.0
    assert profile.cost_per_input_token == 0.0
    assert profile.cost_per_output_token == 0.0


def test_probe_profile_contains_aliases_not_endpoint_or_credential_values() -> None:
    dumped = build_freellmapi_probe_profiles((_seed(),))[0].profile.model_dump()
    rendered = repr(dumped)

    assert "https://api.groq.com" not in rendered
    assert "GROQ_API_KEY" not in rendered
    assert "groq_api_base" in rendered
    assert "groq_api_key" in rendered
    assert "groq-output-cap" not in rendered


def test_unknown_context_uses_existing_conservative_discovery_default_only_for_probe() -> None:
    binding = build_freellmapi_probe_profiles(
        (_seed(context_window=None),)
    )[0]

    assert binding.candidate.context_window is None
    assert binding.profile.context_window == 8192
    assert binding.profile.enabled is False


def test_profile_identity_is_deterministic_and_collision_resistant() -> None:
    slash = build_freellmapi_probe_profiles((_seed(model_id="acme/a-b"),))[0]
    hyphen = build_freellmapi_probe_profiles((_seed(model_id="acme-a/b"),))[0]
    replay = build_freellmapi_probe_profiles((_seed(model_id="acme/a-b"),))[0]

    assert slash.profile.model_profile_id == replay.profile.model_profile_id
    assert slash.profile.model_profile_id != hyphen.profile.model_profile_id
    assert slash.profile.model_profile_id.startswith("freellmapi-groq-")


@pytest.mark.parametrize(
    "candidates",
    [
        [_seed()],
        (_seed(), object()),
        (_seed(), _seed()),
    ],
)
def test_candidate_batch_must_be_immutable_typed_and_unique(
    candidates: object,
) -> None:
    with pytest.raises(ValueError, match="candidates"):
        build_freellmapi_probe_profiles(candidates)  # type: ignore[arg-type]


def test_provider_without_a_native_gludd_preset_fails_closed() -> None:
    with pytest.raises(ValueError, match="provider preset"):
        build_freellmapi_probe_profiles((_seed(platform="unknown"),))


def test_empty_candidate_batch_is_valid() -> None:
    assert build_freellmapi_probe_profiles(()) == ()


class _Secrets:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, _alias_name: str) -> str | None:
        self.calls += 1
        return "test-secret"


def test_dedicated_probe_gateway_owns_exactly_one_disabled_native_profile() -> None:
    binding = build_freellmapi_probe_profiles((_seed(),))[0]
    secrets = _Secrets()

    gateway = build_freellmapi_probe_gateway(binding, secrets_manager=secrets)
    try:
        assert gateway.list_profiles() == [binding.profile]
        assert gateway.is_available(binding.profile.model_profile_id) is False
        assert secrets.calls == 0
    finally:
        gateway.close()


def test_probe_binding_becomes_exact_native_routing_identity() -> None:
    binding = build_freellmapi_probe_profiles((_seed(),))[0]

    identity = catalog_free_tier_identity(binding)

    assert isinstance(identity, CatalogFreeTierCandidateIdentity)
    assert identity.provider is ModelCandidateProvider.CATALOG_FREE_TIER
    assert identity.platform == binding.candidate.platform
    assert identity.model_id == binding.candidate.model_id
    assert identity.catalog_version == binding.candidate.catalog_version
    assert (
        identity.catalog_payload_sha256
        == binding.candidate.catalog_payload_sha256
    )
    assert identity.evidence_identity_digest == identity.identity_digest
    assert "endpoint" not in repr(identity).casefold()
    assert "credential" not in repr(identity).casefold()


def test_routing_identity_rejects_profile_or_candidate_drift() -> None:
    original = build_freellmapi_probe_profiles((_seed(),))[0]
    wrong_model = FreeModelProbeProfile(
        candidate=_seed(model_id="different/model"),
        profile=original.profile,
    )

    with pytest.raises(ValueError, match="identity"):
        catalog_free_tier_identity(wrong_model)
    with pytest.raises(ValueError, match="binding"):
        catalog_free_tier_identity(object())  # type: ignore[arg-type]


def test_probe_gateway_rejects_profile_or_candidate_identity_drift() -> None:
    original = build_freellmapi_probe_profiles((_seed(),))[0]
    enabled = FreeModelProbeProfile(
        candidate=original.candidate,
        profile=original.profile.model_copy(update={"enabled": True}),
    )
    wrong_model = FreeModelProbeProfile(
        candidate=_seed(model_id="different/model"),
        profile=original.profile,
    )

    with pytest.raises(ValueError, match="disabled"):
        build_freellmapi_probe_gateway(enabled, secrets_manager=_Secrets())
    with pytest.raises(ValueError, match="identity"):
        build_freellmapi_probe_gateway(wrong_model, secrets_manager=_Secrets())


def test_probe_gateway_requires_a_structural_secret_resolver() -> None:
    binding = build_freellmapi_probe_profiles((_seed(),))[0]

    with pytest.raises(ValueError, match="secrets_manager"):
        build_freellmapi_probe_gateway(binding, secrets_manager=object())  # type: ignore[arg-type]


@pytest.mark.parametrize("generated", [[], [{}, {}]])
def test_native_profile_cardinality_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    generated: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(
        AutoConfigurator,
        "generate_profiles",
        lambda *_args, **_kwargs: generated,
    )

    with pytest.raises(ValueError, match="one profile"):
        build_freellmapi_probe_profiles((_seed(),))


def test_probe_gateway_rejects_untyped_binding_and_fallback_routes() -> None:
    binding = build_freellmapi_probe_profiles((_seed(),))[0]
    fallback = FreeModelProbeProfile(
        candidate=binding.candidate,
        profile=binding.profile.model_copy(update={"fallback_profiles": ["other"]}),
    )

    with pytest.raises(ValueError, match="binding"):
        build_freellmapi_probe_gateway(object(), secrets_manager=_Secrets())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="fallback"):
        build_freellmapi_probe_gateway(fallback, secrets_manager=_Secrets())
