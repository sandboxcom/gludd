"""Bind FreeLLMAPI evidence to Gludd's existing disabled probe profiles."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Final, Protocol

from general_ludd.models.auto_configurator import AutoConfigurator
from general_ludd.models.freellmapi_candidates import FreeModelCandidateSeed
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_presets import get_provider_preset
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.self_improve.model_candidates import (
    CatalogFreeTierCandidateIdentity,
)

FREELLMAPI_PROBE_PROFILE_PROTOCOL: Final = "gludd-freellmapi-probe-profile-v1"
_MAX_CANDIDATES = 5_000
_UNKNOWN_CONTEXT_WINDOW = 8_192
_PROBE_MAX_OUTPUT_TOKENS = 2_048


@dataclass(frozen=True, slots=True)
class FreeModelProbeProfile:
    """One advisory candidate paired with a disabled native Gludd profile."""

    candidate: FreeModelCandidateSeed
    profile: ModelProfile = field(repr=False)

    @property
    def protocol(self) -> str:
        """Return the versioned profile-binding protocol."""
        return FREELLMAPI_PROBE_PROFILE_PROTOCOL

    @property
    def candidate_digest(self) -> str:
        """Return the authenticated candidate identity retained by this binding."""
        return self.candidate.candidate_digest


class SecretsResolver(Protocol):
    """Structural subset required by a dedicated native probe gateway."""

    def resolve(self, alias_name: str) -> str | None:
        """Resolve one reviewed credential or endpoint alias at call time."""
        ...


def _profile_id(candidate: FreeModelCandidateSeed) -> str:
    identity = f"{candidate.platform}\0{candidate.model_id}".encode()
    suffix = hashlib.sha256(identity).hexdigest()[:20]
    return f"freellmapi-{candidate.platform}-{suffix}"


def _scraped_model(candidate: FreeModelCandidateSeed) -> dict[str, object]:
    context_window = candidate.context_window or _UNKNOWN_CONTEXT_WINDOW
    return {
        "context_length": context_window,
        "description": "Authenticated FreeLLMAPI free-tier discovery evidence",
        "id": candidate.model_id,
        "max_completion_tokens": min(_PROBE_MAX_OUTPUT_TOKENS, context_window),
        "name": candidate.display_name,
        "pricing": {"completion": "0", "prompt": "0"},
    }


def _native_probe_profile(
    candidate: FreeModelCandidateSeed,
    configurator: AutoConfigurator,
) -> ModelProfile:
    if get_provider_preset(candidate.platform) is None:
        raise ValueError(
            f"FreeLLMAPI candidate has no native Gludd provider preset: {candidate.platform}"
        )
    generated = configurator.generate_profiles(
        candidate.platform,
        [_scraped_model(candidate)],
    )
    if len(generated) != 1:
        raise ValueError("native Gludd profile construction did not produce one profile")
    profile = generated[0]
    profile.update(
        api_metered=False,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        enabled=False,
        model_profile_id=_profile_id(candidate),
        probe_enabled=True,
        run_budget_usd=0.0,
    )
    native_fields = {
        key: value
        for key, value in profile.items()
        if key in ModelProfile.model_fields
    }
    return ModelProfile.model_validate(native_fields)


def _validate_candidates(
    candidates: tuple[FreeModelCandidateSeed, ...],
) -> None:
    if (
        type(candidates) is not tuple
        or len(candidates) > _MAX_CANDIDATES
        or any(
            not isinstance(candidate, FreeModelCandidateSeed)
            for candidate in candidates
        )
    ):
        raise ValueError("candidates must be one bounded immutable typed tuple")
    identities = tuple(candidate.identity for candidate in candidates)
    if len(set(identities)) != len(identities):
        raise ValueError("candidates must contain unique provider/model identities")


def build_freellmapi_probe_profiles(
    candidates: tuple[FreeModelCandidateSeed, ...],
) -> tuple[FreeModelProbeProfile, ...]:
    """Reuse Gludd profile construction without making any candidate routable."""
    _validate_candidates(candidates)
    configurator = AutoConfigurator()
    return tuple(
        FreeModelProbeProfile(
            candidate=candidate,
            profile=_native_probe_profile(candidate, configurator),
        )
        for candidate in candidates
    )


def _validated_binding(
    binding: FreeModelProbeProfile,
) -> tuple[FreeModelCandidateSeed, ModelProfile]:
    if not isinstance(binding, FreeModelProbeProfile):
        raise ValueError("binding must be a FreeModelProbeProfile")
    candidate = binding.candidate
    profile = binding.profile
    if profile.enabled:
        raise ValueError("FreeLLMAPI probe profile must remain disabled")
    if profile.fallback_profiles:
        raise ValueError("FreeLLMAPI probe profile must not define fallback routes")
    if (
        profile.provider != candidate.platform
        or profile.model_name != candidate.model_id
        or profile.model_profile_id != _profile_id(candidate)
    ):
        raise ValueError("FreeLLMAPI probe profile identity drifted")
    return candidate, profile


def catalog_free_tier_identity(
    binding: FreeModelProbeProfile,
) -> CatalogFreeTierCandidateIdentity:
    """Bind one disabled native profile to exact signed-catalog evidence."""
    candidate, _profile = _validated_binding(binding)
    return CatalogFreeTierCandidateIdentity(
        platform=candidate.platform,
        model_id=candidate.model_id,
        catalog_version=candidate.catalog_version,
        catalog_payload_sha256=candidate.catalog_payload_sha256,
    )


def build_freellmapi_probe_gateway(
    binding: FreeModelProbeProfile,
    *,
    secrets_manager: SecretsResolver,
) -> ModelGateway:
    """Build an isolated one-profile gateway for an explicit Gludd trial.

    The profile remains disabled, so role/default/cost-aware routing cannot
    select it.  A caller may address its exact profile id through
    :meth:`ModelGateway.call_model`; that path retains Gludd's payload, budget,
    provider allowlist, health, metrics, and response controls.
    """
    _candidate, profile = _validated_binding(binding)
    if not callable(getattr(secrets_manager, "resolve", None)):
        raise ValueError("secrets_manager must expose a resolve method")
    return ModelGateway(
        profiles=[profile],
        provider_registry=ProviderRegistry.from_profiles([profile]),
        secrets_manager=secrets_manager,
    )


__all__ = [
    "FREELLMAPI_PROBE_PROFILE_PROTOCOL",
    "FreeModelProbeProfile",
    "SecretsResolver",
    "build_freellmapi_probe_gateway",
    "build_freellmapi_probe_profiles",
    "catalog_free_tier_identity",
]
