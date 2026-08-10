from __future__ import annotations

from dataclasses import dataclass

import pytest

from general_ludd.models.gateway import ModelProfile
from general_ludd.models.router import ModelRouter

# --- Duck-typed profiles for build_from_profiles protocol check ---


@dataclass
class DuckProfile:
    model_profile_id: str
    role_names: list[str]
    quality_class: str | None
    latency_class: str | None


# ======================================================================
# resolve_role — edge cases beyond basic mapping/fallback
# ======================================================================


class TestResolveRoleDeep:
    def test_empty_string_role_name(self):
        router = ModelRouter(role_mapping={"": "empty_profile"})
        assert router.resolve_role("") == "empty_profile"

    def test_empty_string_role_name_no_default(self):
        router = ModelRouter()
        assert router.resolve_role("") is None

    def test_weak_role_when_weak_profile_is_none(self):
        router = ModelRouter(weak_model_profile_id=None)
        assert router.resolve_role("weak") is None

    def test_weak_role_when_weak_profile_is_none_with_default(self):
        router = ModelRouter(weak_model_profile_id=None, default_profile_id="fallback")
        assert router.resolve_role("weak") == "fallback"

    def test_weak_role_strict_when_weak_profile_is_none_raises(self):
        router = ModelRouter(weak_model_profile_id=None, role_mapping={"coder": "p"})
        with pytest.raises(ValueError, match="weak"):
            router.resolve_role("weak", strict=True)

    def test_weak_role_strict_when_weak_profile_is_none_no_default_raises(self):
        router = ModelRouter(weak_model_profile_id=None)
        with pytest.raises(ValueError, match="weak"):
            router.resolve_role("weak", strict=True)

    def test_weak_role_no_weak_profile_but_explicit_match(self):
        router = ModelRouter(role_mapping={"weak": "explicit_weak"})
        assert router.resolve_role("weak") == "explicit_weak"

    def test_weak_role_explicit_wins_over_weak_sentinel(self):
        router = ModelRouter(
            role_mapping={"weak": "explicit_weak"},
            weak_model_profile_id="sentinel_weak",
        )
        assert router.resolve_role("weak") == "sentinel_weak"

    def test_strict_weak_role_no_weak_profile_but_explicit_raises_non_weak(self):
        router = ModelRouter(role_mapping={"weak": "explicit_weak"})
        assert router.resolve_role("weak", strict=True) == "explicit_weak"

    def test_role_mapping_none_ctor(self):
        router = ModelRouter(role_mapping=None)
        assert router.list_roles() == []

    def test_role_mapping_empty_dict_ctor(self):
        router = ModelRouter(role_mapping={})
        assert router.list_roles() == []

    def test_known_role_then_overwritten(self):
        router = ModelRouter(role_mapping={"coder": "p1"})
        router.add_role("coder", "p2")
        router.set_role_routing("coder", "p3")
        assert router.resolve_role("coder") == "p3"


# ======================================================================
# build_from_profiles — edge cases
# ======================================================================


class TestBuildFromProfilesDeep:
    def test_profile_with_empty_role_names(self):
        p = ModelProfile(
            model_profile_id="no_roles",
            role_names=[],
            quality_class="high",
            latency_class="fast",
        )
        router = ModelRouter.build_from_profiles([p])
        assert router.list_roles() == []
        assert router.resolve_by_quality("high") == "no_roles"
        assert router.resolve_by_latency("fast") == "no_roles"

    def test_profile_with_none_quality_and_latency(self):
        p = ModelProfile(
            model_profile_id="bare",
            role_names=["coder"],
            quality_class=None,
            latency_class=None,
        )
        router = ModelRouter.build_from_profiles([p])
        assert router.resolve_role("coder") == "bare"
        assert router.resolve_by_quality("anything") is None
        assert router.resolve_by_latency("anything") is None

    def test_duplicate_role_across_profiles_last_wins(self):
        p1 = ModelProfile(model_profile_id="first", role_names=["shared"], quality_class=None, latency_class=None)
        p2 = ModelProfile(model_profile_id="second", role_names=["shared"], quality_class=None, latency_class=None)
        router = ModelRouter.build_from_profiles([p1, p2])
        assert router.resolve_role("shared") == "second"

    def test_duplicate_quality_class_last_wins(self):
        p1 = ModelProfile(model_profile_id="p1", role_names=["r1"], quality_class="qc", latency_class=None)
        p2 = ModelProfile(model_profile_id="p2", role_names=["r2"], quality_class="qc", latency_class=None)
        router = ModelRouter.build_from_profiles([p1, p2])
        assert router.resolve_by_quality("qc") == "p2"

    def test_duplicate_latency_class_last_wins(self):
        p1 = ModelProfile(model_profile_id="p1", role_names=["r1"], quality_class=None, latency_class="lc")
        p2 = ModelProfile(model_profile_id="p2", role_names=["r2"], quality_class=None, latency_class="lc")
        router = ModelRouter.build_from_profiles([p1, p2])
        assert router.resolve_by_latency("lc") == "p2"

    def test_duck_typed_profiles(self):
        dp1 = DuckProfile(model_profile_id="duck1", role_names=["coder"], quality_class="high", latency_class="slow")
        dp2 = DuckProfile(model_profile_id="duck2", role_names=["reviewer"], quality_class="low", latency_class="fast")
        router = ModelRouter.build_from_profiles([dp1, dp2])
        assert router.resolve_role("coder") == "duck1"
        assert router.resolve_role("reviewer") == "duck2"
        assert router.resolve_by_quality("high") == "duck1"
        assert router.resolve_by_latency("fast") == "duck2"

    def test_duck_profile_with_none_classes(self):
        dp = DuckProfile(model_profile_id="duck", role_names=["coder"], quality_class=None, latency_class=None)
        router = ModelRouter.build_from_profiles([dp])
        assert router.resolve_role("coder") == "duck"
        assert router.resolve_by_quality("any") is None

    def test_profiles_do_not_set_default_or_weak(self):
        profiles = [
            ModelProfile(model_profile_id="p", role_names=["coder"], quality_class=None, latency_class=None),
        ]
        router = ModelRouter.build_from_profiles(profiles)
        assert router.default_profile_id is None
        assert router.weak_model_profile_id is None

    def test_multiple_profiles_same_role_different_quality(self):
        p1 = ModelProfile(model_profile_id="hi", role_names=["coder"], quality_class="high", latency_class=None)
        p2 = ModelProfile(model_profile_id="lo", role_names=["coder"], quality_class="low", latency_class=None)
        router = ModelRouter.build_from_profiles([p1, p2])
        assert router.resolve_by_quality("high") == "hi"
        assert router.resolve_by_quality("low") == "lo"
        assert router.resolve_role("coder") == "lo"


# ======================================================================
# pattern routing — edge cases
# ======================================================================


class TestPatternRoutingDeep:
    def test_pattern_mapped_to_unknown_role_with_default(self):
        router = ModelRouter(default_profile_id="fallback")
        router.add_pattern_mapping("custom", "ghost_role")
        assert router.resolve_pattern("custom") == "fallback"

    def test_pattern_mapped_to_unknown_role_no_default(self):
        router = ModelRouter()
        router.add_pattern_mapping("custom", "ghost_role")
        assert router.resolve_pattern("custom") is None

    def test_pattern_mapped_to_weak_role_with_weak_profile(self):
        router = ModelRouter(weak_model_profile_id="cheap")
        router.add_pattern_mapping("quick", "weak")
        assert router.resolve_pattern("quick") == "cheap"

    def test_pattern_mapped_to_weak_role_no_weak_profile_no_default(self):
        router = ModelRouter()
        router.add_pattern_mapping("quick", "weak")
        assert router.resolve_pattern("quick") is None

    def test_pattern_mapped_to_weak_role_no_default(self):
        router = ModelRouter(role_mapping={"coder": "p"})
        router.add_pattern_mapping("quick", "weak")
        assert router.resolve_pattern("quick") is None

    def test_list_patterns_empty_initially(self):
        router = ModelRouter()
        assert router.list_patterns() == []

    def test_overwrite_pattern_mapping(self):
        router = ModelRouter(role_mapping={"r1": "p1", "r2": "p2"})
        router.add_pattern_mapping("pat", "r1")
        router.add_pattern_mapping("pat", "r2")
        assert router.resolve_pattern("pat") == "p2"

    def test_pattern_mapped_to_unknown_role_no_default_no_fallback(self):
        router = ModelRouter(role_mapping={"known": "profile"})
        router.add_pattern_mapping("pat", "unknown")
        assert router.resolve_pattern("pat") is None

    def test_pattern_whitespace_name(self):
        router = ModelRouter(role_mapping={"role a": "pa"})
        router.add_pattern_mapping("pattern x", "role a")
        assert router.resolve_pattern("pattern x") == "pa"


# ======================================================================
# quality / latency — edge cases
# ======================================================================


class TestQualityLatencyDeep:
    def test_resolve_by_quality_unknown_class(self):
        router = ModelRouter()
        assert router.resolve_by_quality("nonexistent") is None

    def test_resolve_by_latency_unknown_class(self):
        router = ModelRouter()
        assert router.resolve_by_latency("nonexistent") is None

    def test_overwrite_quality_mapping(self):
        router = ModelRouter()
        router.add_quality_mapping("high", "first")
        router.add_quality_mapping("high", "second")
        assert router.resolve_by_quality("high") == "second"

    def test_overwrite_latency_mapping(self):
        router = ModelRouter()
        router.add_latency_mapping("fast", "first")
        router.add_latency_mapping("fast", "second")
        assert router.resolve_by_latency("fast") == "second"

    def test_quality_and_latency_independent(self):
        router = ModelRouter()
        router.add_quality_mapping("high", "qp")
        router.add_latency_mapping("fast", "lp")
        assert router.resolve_by_quality("high") == "qp"
        assert router.resolve_by_latency("fast") == "lp"
        assert router.resolve_by_latency("high") is None
        assert router.resolve_by_quality("fast") is None

    def test_empty_string_quality_class(self):
        router = ModelRouter()
        router.add_quality_mapping("", "empty")
        assert router.resolve_by_quality("") == "empty"

    def test_empty_string_latency_class(self):
        router = ModelRouter()
        router.add_latency_mapping("", "empty")
        assert router.resolve_by_latency("") == "empty"


# ======================================================================
# list_profiles_by_role — edge cases
# ======================================================================


class TestListProfilesByRoleDeep:
    def test_no_roles_mapped(self):
        router = ModelRouter()
        assert router.list_profiles_by_role("anything") == []

    def test_multiple_roles_same_profile(self):
        router = ModelRouter(role_mapping={"a": "p", "b": "p", "c": "p"})
        result = router.list_profiles_by_role("p")
        assert sorted(result) == ["a", "b", "c"]

    def test_roles_for_nonexistent_profile(self):
        router = ModelRouter(role_mapping={"a": "p1"})
        assert router.list_profiles_by_role("nonexistent") == []

    def test_add_role_reassigns_old_role_keys(self):
        router = ModelRouter(role_mapping={"r1": "p1", "r2": "p1"})
        router.add_role("r1", "p2")
        assert sorted(router.list_profiles_by_role("p1")) == ["r2"]
        assert router.list_profiles_by_role("p2") == ["r1"]


# ======================================================================
# set_role_routing — alias verification
# ======================================================================


class TestSetRoleRoutingDeep:
    def test_set_role_routing_adds_new(self):
        router = ModelRouter()
        router.set_role_routing("new_role", "new_profile")
        assert router.resolve_role("new_role") == "new_profile"

    def test_set_role_routing_overwrites(self):
        router = ModelRouter(role_mapping={"r": "old"})
        router.set_role_routing("r", "new")
        assert router.resolve_role("r") == "new"

    def test_set_role_routing_then_visible_in_list(self):
        router = ModelRouter()
        router.set_role_routing("r", "p")
        assert "r" in router.list_roles()


# ======================================================================
# combined scenarios — integration-level edge cases
# ======================================================================


class TestCombinedScenariosDeep:
    def build_full_router(self) -> ModelRouter:
        profiles = [
            ModelProfile(
                model_profile_id="gpt4", role_names=["coder", "reviewer"], quality_class="high", latency_class="slow"
            ),
            ModelProfile(
                model_profile_id="haiku", role_names=["planner", "fast"], quality_class="low", latency_class="fast"
            ),
            ModelProfile(model_profile_id="sonnet", role_names=["auditor"], quality_class="mid", latency_class="mid"),
            ModelProfile(
                model_profile_id="cheap", role_names=["fallback_role"], quality_class=None, latency_class=None
            ),
        ]
        return ModelRouter.build_from_profiles(profiles)

    def test_all_roles_resolve_after_build(self):
        router = self.build_full_router()
        assert router.resolve_role("coder") == "gpt4"
        assert router.resolve_role("reviewer") == "gpt4"
        assert router.resolve_role("planner") == "haiku"
        assert router.resolve_role("fast") == "haiku"
        assert router.resolve_role("auditor") == "sonnet"
        assert router.resolve_role("fallback_role") == "cheap"

    def test_all_quality_classes_resolve_after_build(self):
        router = self.build_full_router()
        assert router.resolve_by_quality("high") == "gpt4"
        assert router.resolve_by_quality("low") == "haiku"
        assert router.resolve_by_quality("mid") == "sonnet"
        assert router.resolve_by_quality("nonexistent") is None

    def test_all_latency_classes_resolve_after_build(self):
        router = self.build_full_router()
        assert router.resolve_by_latency("slow") == "gpt4"
        assert router.resolve_by_latency("fast") == "haiku"
        assert router.resolve_by_latency("mid") == "sonnet"

    def test_list_roles_after_build(self):
        router = self.build_full_router()
        roles = router.list_roles()
        assert "coder" in roles
        assert "reviewer" in roles
        assert "planner" in roles
        assert "fast" in roles

    def test_list_profiles_by_role_after_build(self):
        router = self.build_full_router()
        gpt4_roles = router.list_profiles_by_role("gpt4")
        assert sorted(gpt4_roles) == ["coder", "reviewer"]

    def test_runtime_additions_after_build(self):
        router = self.build_full_router()
        router.add_role("emergency", "sonnet")
        router.add_quality_mapping("extreme", "gpt4")
        router.add_pattern_mapping("hotfix", "emergency")
        assert router.resolve_role("emergency") == "sonnet"
        assert router.resolve_by_quality("extreme") == "gpt4"
        assert router.resolve_pattern("hotfix") == "sonnet"

    def test_strict_mode_with_built_router_known_role(self):
        router = self.build_full_router()
        assert router.resolve_role("coder", strict=True) == "gpt4"

    def test_strict_mode_with_built_router_unknown_role_raises(self):
        router = self.build_full_router()
        with pytest.raises(ValueError, match="unknown"):
            router.resolve_role("unknown", strict=True)

    def test_weak_role_on_built_router_without_weak_profile(self):
        router = self.build_full_router()
        assert router.resolve_role("weak") is None

    def test_pattern_through_built_router_to_default(self):
        router = self.build_full_router()
        router.default_profile_id = "sonnet"
        router.add_pattern_mapping("mystery_task", "ghost")
        assert router.resolve_pattern("mystery_task") == "sonnet"
