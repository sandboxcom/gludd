"""Additional coverage tests for src/general_ludd/models/router.py.

Targets all branches in ModelRouter:
- __init__: with/without role_mapping, with/without defaults
- resolve_role: "weak" with/without weak_model_profile_id, found in mapping,
  not found + strict=True → ValueError, not found + strict=False + default → default,
  not found + strict=False + no default → None
- add_role / set_role_routing
- add_quality_mapping / add_latency_mapping / add_pattern_mapping
- resolve_pattern: pattern not found → None, pattern found → resolve_role result
- list_patterns / list_roles / list_profiles_by_role
- resolve_by_quality / resolve_by_latency
- build_from_profiles: with role_names, quality_class, latency_class
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# ModelRouter.__init__
# ---------------------------------------------------------------------------

class TestModelRouterInit:
    def test_init_no_args(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        assert router._mapping == {}
        assert router.default_profile_id is None
        assert router.weak_model_profile_id is None

    def test_init_with_role_mapping(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"writer": "gpt-4", "coder": "gpt-3.5"})
        assert router._mapping == {"writer": "gpt-4", "coder": "gpt-3.5"}

    def test_init_role_mapping_is_copied(self) -> None:
        from general_ludd.models.router import ModelRouter
        original = {"a": "b"}
        router = ModelRouter(role_mapping=original)
        original["c"] = "d"
        assert "c" not in router._mapping

    def test_init_with_default_profile_id(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(default_profile_id="fallback-model")
        assert router.default_profile_id == "fallback-model"

    def test_init_with_weak_model_profile_id(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(weak_model_profile_id="gpt-3.5")
        assert router.weak_model_profile_id == "gpt-3.5"


# ---------------------------------------------------------------------------
# resolve_role
# ---------------------------------------------------------------------------

class TestResolveRole:
    def test_weak_role_with_weak_model_profile_id(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(weak_model_profile_id="gpt-3.5")
        assert router.resolve_role("weak") == "gpt-3.5"

    def test_weak_role_without_weak_model_profile_id_falls_through(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(default_profile_id="default")
        # "weak" without weak_model_profile_id falls through to mapping / default
        assert router.resolve_role("weak") == "default"

    def test_weak_role_no_weak_id_no_default_returns_none(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        assert router.resolve_role("weak") is None

    def test_found_in_mapping(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"writer": "gpt-4"})
        assert router.resolve_role("writer") == "gpt-4"

    def test_not_found_strict_raises(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"writer": "gpt-4"})
        with pytest.raises(ValueError, match="Unrecognised role"):
            router.resolve_role("unknown", strict=True)

    def test_not_found_not_strict_with_default(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(default_profile_id="default-model")
        assert router.resolve_role("unknown") == "default-model"

    def test_not_found_not_strict_no_default_returns_none(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        assert router.resolve_role("unknown") is None

    def test_strict_does_not_raise_for_mapped_role(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"writer": "gpt-4"})
        # strict=True should NOT raise when role is explicitly mapped
        assert router.resolve_role("writer", strict=True) == "gpt-4"

    def test_weak_with_weak_id_bypasses_strict(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(weak_model_profile_id="gpt-3.5")
        # weak sentinel always accepted regardless of strict
        assert router.resolve_role("weak", strict=True) == "gpt-3.5"


# ---------------------------------------------------------------------------
# add_role / set_role_routing
# ---------------------------------------------------------------------------

class TestAddRole:
    def test_add_role(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        router.add_role("writer", "gpt-4")
        assert router.resolve_role("writer") == "gpt-4"

    def test_add_role_overwrites(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"writer": "gpt-3.5"})
        router.add_role("writer", "gpt-4")
        assert router.resolve_role("writer") == "gpt-4"

    def test_set_role_routing(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        router.set_role_routing("coder", "claude-3")
        assert router.resolve_role("coder") == "claude-3"

    def test_set_role_routing_overwrites(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"coder": "old"})
        router.set_role_routing("coder", "new")
        assert router.resolve_role("coder") == "new"


# ---------------------------------------------------------------------------
# add_quality_mapping / add_latency_mapping / add_pattern_mapping
# ---------------------------------------------------------------------------

class TestMappingMethods:
    def test_add_quality_mapping(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        router.add_quality_mapping("high", "gpt-4")
        assert router.resolve_by_quality("high") == "gpt-4"

    def test_add_latency_mapping(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        router.add_latency_mapping("fast", "gpt-3.5")
        assert router.resolve_by_latency("fast") == "gpt-3.5"

    def test_add_pattern_mapping(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"coder": "gpt-4"})
        router.add_pattern_mapping("python-task", "coder")
        assert router.resolve_pattern("python-task") == "gpt-4"


# ---------------------------------------------------------------------------
# resolve_pattern
# ---------------------------------------------------------------------------

class TestResolvePattern:
    def test_pattern_not_found_returns_none(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        assert router.resolve_pattern("nonexistent-pattern") is None

    def test_pattern_found_resolves_to_role_result(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"writer": "gpt-4"})
        router.add_pattern_mapping("blog-post", "writer")
        assert router.resolve_pattern("blog-post") == "gpt-4"

    def test_pattern_found_role_not_in_mapping_returns_none(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        router.add_pattern_mapping("task", "unregistered-role")
        # "unregistered-role" is not in mapping and no default → None
        assert router.resolve_pattern("task") is None

    def test_pattern_found_with_default_profile(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(default_profile_id="default-gpt")
        router.add_pattern_mapping("task", "unregistered-role")
        # Falls through to default
        assert router.resolve_pattern("task") == "default-gpt"


# ---------------------------------------------------------------------------
# list_patterns / list_roles / list_profiles_by_role
# ---------------------------------------------------------------------------

class TestListMethods:
    def test_list_patterns_empty(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        assert router.list_patterns() == []

    def test_list_patterns_returns_added_patterns(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        router.add_pattern_mapping("p1", "role1")
        router.add_pattern_mapping("p2", "role2")
        assert set(router.list_patterns()) == {"p1", "p2"}

    def test_list_roles_empty(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        assert router.list_roles() == []

    def test_list_roles_returns_mapped_roles(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"writer": "gpt-4", "coder": "gpt-3.5"})
        assert set(router.list_roles()) == {"writer", "coder"}

    def test_list_profiles_by_role_no_match(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"writer": "gpt-4"})
        assert router.list_profiles_by_role("gpt-3.5") == []

    def test_list_profiles_by_role_single_match(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"writer": "gpt-4"})
        assert router.list_profiles_by_role("gpt-4") == ["writer"]

    def test_list_profiles_by_role_multiple_roles_same_profile(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter(role_mapping={"writer": "gpt-4", "editor": "gpt-4"})
        result = set(router.list_profiles_by_role("gpt-4"))
        assert result == {"writer", "editor"}


# ---------------------------------------------------------------------------
# resolve_by_quality / resolve_by_latency
# ---------------------------------------------------------------------------

class TestResolveByQualityAndLatency:
    def test_resolve_by_quality_found(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        router.add_quality_mapping("high", "gpt-4")
        assert router.resolve_by_quality("high") == "gpt-4"

    def test_resolve_by_quality_not_found(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        assert router.resolve_by_quality("nonexistent") is None

    def test_resolve_by_latency_found(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        router.add_latency_mapping("fast", "gpt-3.5")
        assert router.resolve_by_latency("fast") == "gpt-3.5"

    def test_resolve_by_latency_not_found(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter()
        assert router.resolve_by_latency("nonexistent") is None


# ---------------------------------------------------------------------------
# build_from_profiles
# ---------------------------------------------------------------------------

class TestBuildFromProfiles:
    def _make_profile(self, pid: str, role_names=None, quality_class=None, latency_class=None):
        p = MagicMock()
        p.model_profile_id = pid
        p.role_names = role_names or []
        p.quality_class = quality_class
        p.latency_class = latency_class
        return p

    def test_build_from_profiles_empty_list(self) -> None:
        from general_ludd.models.router import ModelRouter
        router = ModelRouter.build_from_profiles([])
        assert router.list_roles() == []

    def test_build_from_profiles_with_role_names(self) -> None:
        from general_ludd.models.router import ModelRouter
        p = self._make_profile("gpt-4", role_names=["writer", "editor"])
        router = ModelRouter.build_from_profiles([p])
        assert router.resolve_role("writer") == "gpt-4"
        assert router.resolve_role("editor") == "gpt-4"

    def test_build_from_profiles_with_quality_class(self) -> None:
        from general_ludd.models.router import ModelRouter
        p = self._make_profile("gpt-4", quality_class="high")
        router = ModelRouter.build_from_profiles([p])
        assert router.resolve_by_quality("high") == "gpt-4"

    def test_build_from_profiles_with_latency_class(self) -> None:
        from general_ludd.models.router import ModelRouter
        p = self._make_profile("gpt-3.5", latency_class="fast")
        router = ModelRouter.build_from_profiles([p])
        assert router.resolve_by_latency("fast") == "gpt-3.5"

    def test_build_from_profiles_multiple_profiles(self) -> None:
        from general_ludd.models.router import ModelRouter
        p1 = self._make_profile("gpt-4", role_names=["writer"], quality_class="high")
        p2 = self._make_profile("gpt-3.5", role_names=["coder"], latency_class="fast")
        router = ModelRouter.build_from_profiles([p1, p2])
        assert router.resolve_role("writer") == "gpt-4"
        assert router.resolve_role("coder") == "gpt-3.5"
        assert router.resolve_by_quality("high") == "gpt-4"
        assert router.resolve_by_latency("fast") == "gpt-3.5"

    def test_build_from_profiles_no_role_names_attr(self) -> None:
        """Profiles without role_names attribute (getattr fallback) are skipped."""
        from general_ludd.models.router import ModelRouter
        p = MagicMock(spec=[])  # no attributes
        p.model_profile_id = "m"
        router = ModelRouter.build_from_profiles([p])
        assert router.list_roles() == []

    def test_build_from_profiles_later_profile_overwrites_quality(self) -> None:
        from general_ludd.models.router import ModelRouter
        p1 = self._make_profile("gpt-4", quality_class="high")
        p2 = self._make_profile("gpt-5", quality_class="high")
        router = ModelRouter.build_from_profiles([p1, p2])
        # Last profile wins for same quality_class key
        assert router.resolve_by_quality("high") == "gpt-5"
