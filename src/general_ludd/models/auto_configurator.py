"""Auto-configurator — generates model profiles from scraped provider data."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, cast

from general_ludd.models.gateway import ModelProfile
from general_ludd.models.provider_presets import (
    PROVIDER_FLAGSHIP_MODELS,
    PROVIDER_PRESETS,
    get_provider_preset,
)

logger = logging.getLogger(__name__)


def _safe_profile_id(model_id: str) -> str:
    return model_id.replace("/", "-").replace(".", "-").replace(":", "-").lower()


def _safe_float(value: str | float, default: float = 0.0) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


class AutoConfigurator:
    """Generates model profiles from scraped/discovered model data."""

    def auto_configure_from_env(
        self,
        environ: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        """Build one ModelProfile dict per provider whose credential env var is set.

        Iterates every entry in PROVIDER_PRESETS. For each provider whose
        ``credential_env_var`` is present and non-empty in the environment, a
        ModelProfile dict is synthesized using the provider's flagship model
        (PROVIDER_FLAGSHIP_MODELS) and preset connection metadata. Profiles are
        ``enabled=True`` so the daemon's ModelGateway can call them immediately.

        Args:
            environ: environment mapping to inspect. Defaults to ``os.environ``.

        Returns:
            List of profile dicts (valid kwargs for ``ModelProfile(**d)``).
        """
        env = environ if environ is not None else dict(os.environ)
        profiles: list[dict[str, object]] = []

        for provider_name, preset in PROVIDER_PRESETS.items():
            credential_env_var = cast(str, preset["credential_env_var"])
            credential_value = env.get(credential_env_var, "")
            if not credential_value:
                continue

            flagship = PROVIDER_FLAGSHIP_MODELS.get(provider_name, "")
            if not flagship:
                logger.warning(
                    "Auto-config: provider '%s' has credential but no flagship "
                    "model entry; skipping",
                    provider_name,
                )
                continue

            profile_id = f"{provider_name}-{_safe_profile_id(flagship)}"
            profiles.append(
                {
                    "model_profile_id": profile_id,
                    "provider": provider_name,
                    "model_name": flagship,
                    "display_name": f"{preset['display_name']} {flagship}",
                    "description": (
                        f"Auto-configured from {credential_env_var}; flagship "
                        f"model for {preset['display_name']}."
                    ),
                    "api_base_alias": preset["api_base_alias"],
                    "credential_alias": preset["credential_alias"],
                    "provider_package": preset["provider_package"],
                    "provider_class_hint": preset["provider_class"],
                    "context_window": 8192,
                    "max_output_tokens": 2048,
                    "cost_per_input_token": 0.0,
                    "cost_per_output_token": 0.0,
                    "role_names": ["coder", "reviewer"],
                    "quality_class": "medium",
                    "latency_class": "medium",
                    "api_metered": True,
                    "resource_profile": "ai_heavy",
                    "enabled": True,
                    "auto_discovered": True,
                    "auto_discovered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "is_free": False,
                }
            )

        if profiles:
            logger.info(
                "Auto-config: generated %d profile(s) from environment "
                "credentials: %s",
                len(profiles),
                [p["provider"] for p in profiles],
            )

        return profiles

    def auto_configure_profiles(
        self,
        environ: dict[str, str] | None = None,
    ) -> list[ModelProfile]:
        """Like ``auto_configure_from_env`` but returns ModelProfile objects.

        The daemon and worker build gateways from ``ModelProfile`` instances;
        this helper returns those directly so the gateway construction site
        stays unchanged when an operator relies on env-var auto-config.
        """
        return [ModelProfile(**cast(Any, p)) for p in self.auto_configure_from_env(environ)]

    def generate_profiles(
        self,
        provider: str,
        scraped_models: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Generate model profile dicts from scraped model data."""
        preset = get_provider_preset(provider)
        if preset is None:
            logger.warning("No preset for provider '%s', skipping auto-config", provider)
            return []

        profiles: list[dict[str, object]] = []
        seen_ids: set[str] = set()

        for model in scraped_models:
            model_id = cast(str, model.get("id", ""))
            if not model_id:
                continue
            profile_id = f"{provider}-{_safe_profile_id(model_id)}"
            if profile_id in seen_ids:
                continue
            seen_ids.add(profile_id)

            pricing = cast(dict[str, Any], model.get("pricing", {}) or {})
            input_cost = _safe_float(cast(str, pricing.get("prompt", "0")))
            output_cost = _safe_float(cast(str, pricing.get("completion", "0")))

            roles = self._assign_roles(model)
            quality_class = self._assign_quality(model)

            profile = {
                "model_profile_id": profile_id,
                "provider": provider,
                "model_name": model_id,
                "display_name": model.get("name", model_id),
                "description": model.get("description", ""),
                "context_window": int(cast(int, model.get("context_length", 8192))),
                "max_output_tokens": model.get("max_completion_tokens"),
                "cost_per_input_token": input_cost,
                "cost_per_output_token": output_cost,
                "api_base_alias": preset["api_base_alias"],
                "credential_alias": preset["credential_alias"],
                "provider_package": preset["provider_package"],
                "provider_class_hint": preset["provider_class"],
                "role_names": roles,
                "quality_class": quality_class,
                "latency_class": "medium",
                "api_metered": input_cost > 0 or output_cost > 0,
                "resource_profile": "ai_heavy",
                "enabled": True,
                "auto_discovered": True,
                "auto_discovered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "is_free": input_cost == 0.0 and output_cost == 0.0,
            }
            profiles.append(profile)

        return profiles

    def merge_profiles(
        self,
        existing: list[dict[str, object]],
        scraped: list[dict[str, object]],
        provider: str,
    ) -> list[dict[str, object]]:
        """Merge scraped models into existing profiles, preserving user overrides.

        New models are added. Existing models are updated with fresh metadata
        but user-set fields (enabled, user_priority, role_names) are preserved.
        """
        new_profiles = self.generate_profiles(provider, scraped)
        existing_by_id = {p["model_profile_id"]: p for p in existing}
        user_fields = {"enabled", "user_priority", "role_names", "credential_alias"}

        merged: list[dict[str, object]] = []
        for new_p in new_profiles:
            pid = new_p["model_profile_id"]
            if pid in existing_by_id:
                preserved = {k: existing_by_id[pid][k] for k in user_fields if k in existing_by_id[pid]}
                new_p.update(preserved)
            merged.append(new_p)

        for old_p in existing:
            if old_p["model_profile_id"] not in {p["model_profile_id"] for p in merged}:
                old_p["enabled"] = False
                merged.append(old_p)

        return merged

    @staticmethod
    def _assign_roles(model: dict[str, object]) -> list[str]:
        model_name = cast(str, model.get("name", "")).lower()
        model_id = cast(str, model.get("id", "")).lower()
        combined = f"{model_name} {model_id}"

        if any(w in combined for w in ("coder", "code-", "dev")):
            return ["coder", "reviewer", "test_writer"]
        if any(w in combined for w in ("reasoner", "think", "deep", "opus", "sonnet")):
            return ["reviewer", "coder", "planner"]
        if "flash" in combined or "mini" in combined or "small" in combined:
            return ["summarizer", "formatter", "fast_executor"]
        if any(w in combined for w in ("maverick", "pro", "ultra", "max")):
            return ["coder", "reviewer", "architect"]
        return ["coder", "reviewer"]

    @staticmethod
    def _assign_quality(model: dict[str, object]) -> str:
        model_name = cast(str, model.get("name", "")).lower()
        model_id = cast(str, model.get("id", "")).lower()
        combined = f"{model_name} {model_id}"
        context = int(cast(int, model.get("context_length", 0)))

        if any(w in combined for w in ("pro", "ultra", "max", "opus", "sonnet")):
            return "high"
        if context >= 200000:
            return "high"
        if context >= 100000:
            return "medium"
        if any(w in combined for w in ("mini", "small", "flash", "nano", "tiny")):
            return "low"
        return "medium"


class ModelPrioritizer:
    """Ranks model profiles based on configurable strategy."""

    VALID_STRATEGIES: frozenset[str] = frozenset({"cheapest_first", "largest_context_first", "balanced"})

    def __init__(self, strategy: str = "balanced") -> None:
        if strategy not in self.VALID_STRATEGIES:
            raise ValueError(f"Invalid strategy '{strategy}'. Use one of: {self.VALID_STRATEGIES}")
        self._strategy = strategy

    def rank(self, models: list[dict[str, object]]) -> list[dict[str, object]]:
        """Rank models by the configured strategy. User priority overrides all."""
        if not models:
            return []

        sorted_models = sorted(models, key=self._score, reverse=True)
        return sorted_models

    def _score(self, model: dict[str, object]) -> float:
        user_priority = model.get("user_priority", "")
        if user_priority == "prioritized":
            return 1e9
        if user_priority == "deprioritized":
            return -1e9
        if model.get("enabled") is False:
            return -500.0

        if self._strategy == "cheapest_first":
            input_cost = float(cast(float, model.get("cost_per_input_token", 0)))
            output_cost = float(cast(float, model.get("cost_per_output_token", 0)))
            return 1.0 / max(input_cost + output_cost, 1e-12)

        if self._strategy == "largest_context_first":
            return float(cast(float, model.get("context_window", 0)))

        context = float(cast(float, model.get("context_window", 8192)))
        context_score = min(context / 200000.0, 1.0)
        input_cost = float(cast(float, model.get("cost_per_input_token", 1e-6)))
        cost_score = 1.0 / max(input_cost * 1e6, 1.0)
        return context_score * 0.4 + cost_score * 0.6

    @property
    def strategy(self) -> str:
        return self._strategy
