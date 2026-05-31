from __future__ import annotations

from agentic_harness.models.gateway import ModelProfile
from agentic_harness.models.router import ModelRouter


class TestResolveRoleWithDefault:
    def test_resolve_role_with_default(self):
        router = ModelRouter(default_profile_id="fallback")
        assert router.resolve_role("unknown") == "fallback"

    def test_resolve_role_no_default_returns_none(self):
        router = ModelRouter()
        assert router.resolve_role("unknown") is None

    def test_weak_model_profile(self):
        router = ModelRouter(weak_model_profile_id="haiku")
        assert router.resolve_role("weak") == "haiku"

    def test_resolve_role_prefers_explicit_over_default(self):
        router = ModelRouter(
            role_mapping={"coder": "gpt4"},
            default_profile_id="fallback",
        )
        assert router.resolve_role("coder") == "gpt4"

    def test_resolve_by_quality_class(self):
        router = ModelRouter()
        router.add_quality_mapping("high", "gpt4")
        assert router.resolve_by_quality("high") == "gpt4"

    def test_resolve_by_latency_class(self):
        router = ModelRouter()
        router.add_latency_mapping("fast", "haiku")
        assert router.resolve_by_latency("fast") == "haiku"

    def test_list_profiles_by_role(self):
        router = ModelRouter(
            role_mapping={"coder": "gpt4", "reviewer": "gpt4", "planner": "claude3"},
        )
        profiles = router.list_profiles_by_role("gpt4")
        assert sorted(profiles) == ["coder", "reviewer"]

    def test_add_mapping_overwrites_existing(self):
        router = ModelRouter(role_mapping={"coder": "gpt4"})
        router.add_role("coder", "claude3")
        assert router.resolve_role("coder") == "claude3"


class TestBuildFromProfiles:
    def test_build_from_profiles_auto_maps_roles(self):
        profiles = [
            ModelProfile(
                model_profile_id="gpt4",
                role_names=["coder", "reviewer"],
                quality_class="high",
                latency_class="slow",
            ),
            ModelProfile(
                model_profile_id="haiku",
                role_names=["planner"],
                quality_class="low",
                latency_class="fast",
            ),
        ]
        router = ModelRouter.build_from_profiles(profiles)
        assert router.resolve_role("coder") == "gpt4"
        assert router.resolve_role("reviewer") == "gpt4"
        assert router.resolve_role("planner") == "haiku"

    def test_build_from_profiles_maps_quality_and_latency(self):
        profiles = [
            ModelProfile(
                model_profile_id="gpt4",
                role_names=["coder"],
                quality_class="high",
                latency_class="slow",
            ),
            ModelProfile(
                model_profile_id="haiku",
                role_names=["planner"],
                quality_class="low",
                latency_class="fast",
            ),
        ]
        router = ModelRouter.build_from_profiles(profiles)
        assert router.resolve_by_quality("high") == "gpt4"
        assert router.resolve_by_quality("low") == "haiku"
        assert router.resolve_by_latency("fast") == "haiku"
        assert router.resolve_by_latency("slow") == "gpt4"

    def test_build_from_profiles_empty(self):
        router = ModelRouter.build_from_profiles([])
        assert router.resolve_role("anything") is None
        assert router.resolve_by_quality("high") is None
        assert router.resolve_by_latency("fast") is None
