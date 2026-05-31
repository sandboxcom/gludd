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


class TestPatternRouting:
    def test_resolve_pattern_through_router(self):
        router = ModelRouter(
            role_mapping={"reviewer": "gpt4", "coder": "gpt4", "fast": "haiku"},
            weak_model_profile_id="haiku",
        )
        router.add_pattern_mapping("return_review", "reviewer")
        router.add_pattern_mapping("commit_message", "weak")
        router.add_pattern_mapping("gap_analysis", "fast")
        assert router.resolve_pattern("return_review") == "gpt4"
        assert router.resolve_pattern("commit_message") == "haiku"
        assert router.resolve_pattern("gap_analysis") == "haiku"

    def test_resolve_pattern_unknown_returns_none(self):
        router = ModelRouter()
        assert router.resolve_pattern("nonexistent") is None

    def test_resolve_pattern_falls_to_default(self):
        router = ModelRouter(
            role_mapping={"coder": "gpt4"},
            default_profile_id="fallback",
        )
        router.add_pattern_mapping("custom_task", "coder")
        assert router.resolve_pattern("custom_task") == "gpt4"

    def test_list_patterns(self):
        router = ModelRouter()
        router.add_pattern_mapping("return_review", "reviewer")
        router.add_pattern_mapping("commit_message", "weak")
        patterns = router.list_patterns()
        assert "return_review" in patterns
        assert "commit_message" in patterns
