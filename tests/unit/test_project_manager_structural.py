"""Structural tests for projects/manager.py — ProjectManager + helpers."""

from __future__ import annotations

import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytest

from general_ludd.projects.manager import (
    ProjectAllocationError,
    ProjectManager,
    ProjectWeight,
    _infer_location_kind,
    _normalize_repo_url,
    _resolve_self_repo_url,
    _detect_self_project,
    _VALID_RELATION_TYPES,
    _VALID_LOCATION_KINDS,
    normalize_relationship_config,
    parse_relationships,
    seed_from_config,
    materialize_project_workspace,
    persist_project,
    rebuild_manager_from_db,
    persist_relationships_from_config,
)


class TestProjectWeight:
    def test_defaults(self):
        pw = ProjectWeight(project_id="p1", name="test", weight=50.0)
        assert pw.description == ""
        assert pw.config == {}
        assert pw.workspace_path == ""
        assert pw.repo_url == ""
        assert pw.dispatch_mode == "active"
        assert pw.created_at == 0.0
        assert pw.active is True

    def test_explicit_fields(self):
        pw = ProjectWeight(
            project_id="p2", name="explicit", weight=25.0,
            description="desc", workspace_path="/ws", repo_url="https://example.com",
            dispatch_mode="passive", created_at=100.0, active=False,
        )
        assert pw.project_id == "p2"
        assert pw.weight == 25.0
        assert pw.repo_url == "https://example.com"
        assert pw.dispatch_mode == "passive"
        assert pw.active is False


class TestProjectManagerCore:
    def test_add_project_basic(self):
        mgr = ProjectManager()
        pw = mgr.add_project(name="alpha", weight=30.0, description="first")
        assert pw.name == "alpha"
        assert pw.weight == 30.0
        assert pw.active is True
        assert mgr.total_weight() == 30.0

    def test_add_project_exceeds_100_raises(self):
        mgr = ProjectManager()
        mgr.add_project(name="a", weight=90.0)
        with pytest.raises(ProjectAllocationError):
            mgr.add_project(name="b", weight=20.0)

    def test_add_project_at_100_allowed(self):
        mgr = ProjectManager()
        mgr.add_project(name="a", weight=100.0)
        assert mgr.total_weight() == 100.0

    def test_remove_project(self):
        mgr = ProjectManager()
        pw = mgr.add_project(name="alpha", weight=50.0)
        mgr.remove_project(pw.project_id)
        assert pw.active is False
        assert mgr.total_weight() == 0.0

    def test_remove_unknown_noop(self):
        mgr = ProjectManager()
        mgr.remove_project("nonexistent")

    def test_get_project(self):
        mgr = ProjectManager()
        pw = mgr.add_project(name="alpha", weight=50.0)
        found = mgr.get_project(pw.project_id)
        assert found is pw
        assert mgr.get_project("nonexistent") is None

    def test_set_weight_valid(self):
        mgr = ProjectManager()
        pw = mgr.add_project(name="a", weight=30.0)
        mgr.set_weight(pw.project_id, 40.0)
        assert pw.weight == 40.0

    def test_set_weight_exceeds_100_raises(self):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=70.0)
        _b = mgr.add_project(name="b", weight=30.0)
        with pytest.raises(ProjectAllocationError):
            mgr.set_weight(a.project_id, 80.0)

    def test_set_weight_unknown_raises(self):
        mgr = ProjectManager()
        with pytest.raises(ProjectAllocationError):
            mgr.set_weight("unknown", 50.0)

    def test_set_weight_inactive_raises(self):
        mgr = ProjectManager()
        pw = mgr.add_project(name="a", weight=50.0)
        mgr.remove_project(pw.project_id)
        with pytest.raises(ProjectAllocationError):
            mgr.set_weight(pw.project_id, 10.0)

    def test_set_weight_rejects_negative(self):
        mgr = ProjectManager()
        pw = mgr.add_project(name="a", weight=50.0)
        with pytest.raises(ProjectAllocationError):
            mgr.set_weight(pw.project_id, -5.0)

    def test_set_weight_rejects_nan(self):
        mgr = ProjectManager()
        pw = mgr.add_project(name="a", weight=50.0)
        with pytest.raises(ProjectAllocationError):
            mgr.set_weight(pw.project_id, float("nan"))

    def test_rebalance_happy_path(self):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=30.0)
        b = mgr.add_project(name="b", weight=30.0)
        mgr.rebalance({a.project_id: 60.0, b.project_id: 40.0})
        assert a.weight == 60.0
        assert b.weight == 40.0

    def test_rebalance_sum_not_100_raises(self):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=30.0)
        b = mgr.add_project(name="b", weight=30.0)
        with pytest.raises(ProjectAllocationError):
            mgr.rebalance({a.project_id: 30.0, b.project_id: 30.0})

    def test_rebalance_unknown_project_raises(self):
        mgr = ProjectManager()
        _a = mgr.add_project(name="a", weight=30.0)
        b = mgr.add_project(name="b", weight=30.0)
        with pytest.raises(ProjectAllocationError):
            mgr.rebalance({"unknown": 60.0, b.project_id: 40.0})

    def test_rebalance_inactive_raises(self):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=30.0)
        b = mgr.add_project(name="b", weight=30.0)
        mgr.remove_project(a.project_id)
        with pytest.raises(ProjectAllocationError):
            mgr.rebalance({a.project_id: 50.0, b.project_id: 50.0})

    def test_rebalance_rejects_nan(self):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=30.0)
        b = mgr.add_project(name="b", weight=30.0)
        with pytest.raises(ProjectAllocationError):
            mgr.rebalance({a.project_id: float("nan"), b.project_id: 70.0})


class TestProjectManagerListing:
    def test_list_projects_active_only(self):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=30.0)
        _b = mgr.add_project(name="b", weight=30.0)
        mgr.remove_project(a.project_id)
        active = mgr.list_projects(active_only=True)
        assert len(active) == 1

    def test_list_projects_all(self):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=30.0)
        _b = mgr.add_project(name="b", weight=30.0)
        mgr.remove_project(a.project_id)
        all_p = mgr.list_projects(active_only=False)
        assert len(all_p) == 2

    def test_list_active(self):
        mgr = ProjectManager()
        mgr.add_project(name="a", weight=30.0)
        mgr.add_project(name="b", weight=30.0)
        assert len(mgr.list_active()) == 2

    def test_get_allocation(self):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=30.0)
        b = mgr.add_project(name="b", weight=30.0)
        alloc = mgr.get_allocation()
        assert alloc[a.project_id] == 30.0
        assert alloc[b.project_id] == 30.0

    def test_get_summary(self):
        mgr = ProjectManager()
        mgr.add_project(name="a", weight=30.0)
        mgr.add_project(name="b", weight=50.0)
        summary = mgr.get_summary()
        assert summary["total_projects"] == 2
        assert summary["active_projects"] == 2
        assert summary["total_weight"] == 80.0
        assert summary["unallocated"] == 20.0


class TestSelectProject:
    def test_select_project_no_active_returns_none(self):
        mgr = ProjectManager()
        assert mgr.select_project() is None

    def test_select_project_skips_passive(self):
        mgr = ProjectManager()
        mgr.add_project(name="a", weight=50.0, dispatch_mode="passive")
        mgr.add_project(name="b", weight=50.0, dispatch_mode="passive")
        assert mgr.select_project() is None

    def test_select_project_mixed_modes(self):
        mgr = ProjectManager()
        mgr.add_project(name="a", weight=50.0, dispatch_mode="passive")
        b = mgr.add_project(name="b", weight=50.0, dispatch_mode="active")
        selected = mgr.select_project()
        assert selected is b

    def test_select_project_zero_total(self):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=0.0)
        selected = mgr.select_project()
        assert selected is a

    def test_select_project_single(self):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=50.0)
        selected = mgr.select_project()
        assert selected is a

    @mock.patch("random.random", return_value=0.0)
    def test_select_project_weighted_first(self, _mock_random):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=70.0)
        b = mgr.add_project(name="b", weight=30.0)
        selected = mgr.select_project()
        assert selected is a

    @mock.patch("random.random", return_value=0.99)
    def test_select_project_weighted_last(self, _mock_random):
        mgr = ProjectManager()
        a = mgr.add_project(name="a", weight=70.0)
        b = mgr.add_project(name="b", weight=30.0)
        selected = mgr.select_project()
        assert selected is b


class TestNormalizeRepoUrl:
    @pytest.mark.parametrize("url_in,expected", [
        ("https://github.com/owner/repo", "github.com/owner/repo"),
        ("git@github.com:owner/repo.git", "github.com/owner/repo"),
        ("https://github.com/owner/repo.git", "github.com/owner/repo"),
        ("http://github.com/owner/repo", "github.com/owner/repo"),
        ("ssh://git@github.com/owner/repo.git", "github.com/owner/repo"),
        ("", ""),
        (None, ""),
    ])
    def test_normalize(self, url_in, expected):
        assert _normalize_repo_url(url_in) == expected

    def test_case_insensitive(self):
        assert _normalize_repo_url("HTTPS://GITHUB.COM/Owner/Repo") == "github.com/owner/repo"


class TestInferLocationKind:
    def test_url_scheme(self):
        assert _infer_location_kind("https://example.com") == "url"

    def test_directory_absolute(self):
        assert _infer_location_kind("/home/user/project") == "directory"

    def test_directory_relative(self):
        assert _infer_location_kind("./subdir") == "directory"

    def test_gludd_project_name(self):
        assert _infer_location_kind("my-project") == "gludd_project_name"


class TestNormalizeRelationshipConfig:
    def test_valid_edge(self):
        rel = {"relation": "parent", "location": "my-project"}
        edge = normalize_relationship_config(rel)
        assert edge is not None
        assert edge["relation_type"] == "parent"
        assert edge["location_kind"] == "gludd_project_name"
        assert edge["controlled_by_gludd"] is False

    def test_infer_kind_when_missing(self):
        rel = {"relation": "child", "location": "https://example.com"}
        edge = normalize_relationship_config(rel)
        assert edge["location_kind"] == "url"

    def test_invalid_relation_returns_none(self):
        rel = {"relation": "invalid", "location": "x"}
        assert normalize_relationship_config(rel) is None

    def test_empty_location_returns_none(self):
        rel = {"relation": "parent", "location": ""}
        assert normalize_relationship_config(rel) is None

    def test_not_a_dict_returns_none(self):
        assert normalize_relationship_config("not a dict") is None

    def test_controlled_explicit(self):
        rel = {"relation": "parent", "location": "x", "controlled_by_gludd": True}
        edge = normalize_relationship_config(rel)
        assert edge["controlled_by_gludd"] is True
        assert edge["_controlled_explicit"] is True

    def test_interface_contract_json(self):
        rel = {"relation": "parent", "location": "x", "interface_contract": {"key": "value"}}
        edge = normalize_relationship_config(rel)
        assert edge["interface_contract"] == '{"key": "value"}'


class TestParseRelationships:
    def test_empty_list(self):
        assert parse_relationships({}) == []

    def test_not_a_list(self):
        assert parse_relationships({"relationships": "not-a-list"}) == []

    def test_parses_valid_edges(self):
        cfg = {"relationships": [
            {"relation": "parent", "location": "proj-a"},
            {"relation": "child", "location": "/workspace/b"},
        ]}
        edges = parse_relationships(cfg)
        assert len(edges) == 2

    def test_skips_malformed(self):
        cfg = {"relationships": [
            {"relation": "invalid", "location": "x"},
            {"relation": "parent", "location": "valid"},
        ]}
        edges = parse_relationships(cfg)
        assert len(edges) == 1


class TestSeedFromConfig:
    def test_basic(self):
        cfg = {"projects": [
            {"name": "alpha", "weight": 30},
            {"name": "beta", "weight": 40},
        ]}
        mgr = seed_from_config(cfg)
        assert len(mgr.list_active()) == 2
        assert mgr.total_weight() == 70.0

    def test_missing_projects_key(self):
        mgr = seed_from_config({})
        assert len(mgr.list_active()) == 0

    def test_projects_not_a_list(self):
        mgr = seed_from_config({"projects": "bad"})
        assert len(mgr.list_active()) == 0

    def test_skips_non_dict_entries(self):
        mgr = seed_from_config({"projects": ["bad", {"name": "good", "weight": 10}]})
        assert len(mgr.list_active()) == 1

    def test_allocation_exceeded_skip(self):
        cfg = {"projects": [
            {"name": "a", "weight": 90},
            {"name": "b", "weight": 20},
        ]}
        mgr = seed_from_config(cfg)
        assert len(mgr.list_active()) == 1

    def test_attaches_relationships(self):
        cfg = {"projects": [
            {"name": "a", "weight": 10, "relationships": [
                {"relation": "parent", "location": "proj-b"},
            ]},
        ]}
        mgr = seed_from_config(cfg)
        pw = mgr.list_active()[0]
        assert "relationships" in pw.config
        assert len(pw.config["relationships"]) == 1


class TestDetectSelfProject:
    def test_empty_url_false(self):
        pw = ProjectWeight(project_id="p1", name="test", weight=50.0, repo_url="")
        assert _detect_self_project(pw, self_repo_url="github.com/me/repo") is False

    def test_no_self_repo_false(self):
        pw = ProjectWeight(project_id="p1", name="test", weight=50.0, repo_url="git@github.com:me/repo.git")
        assert _detect_self_project(pw, self_repo_url="") is False

    @mock.patch("general_ludd.projects.manager._resolve_self_repo_url", return_value="git@github.com:me/repo.git")
    def test_detection_without_explicit_url(self, _mock_resolve):
        pw = ProjectWeight(project_id="p1", name="test", weight=50.0, repo_url="https://github.com/me/repo")
        assert _detect_self_project(pw) is True


class TestResolveSelfRepoUrl:
    def test_env_var_override(self):
        with mock.patch.dict("os.environ", {"GLUDD_SELF_REPO_URL": "https://override.example.com/repo"}):
            assert _resolve_self_repo_url() == "https://override.example.com/repo"


class TestConstants:
    def test_valid_relation_types(self):
        assert "parent" in _VALID_RELATION_TYPES
        assert "child" in _VALID_RELATION_TYPES
        assert "sibling" in _VALID_RELATION_TYPES
        assert "external" in _VALID_RELATION_TYPES

    def test_valid_location_kinds(self):
        assert "gludd_project_name" in _VALID_LOCATION_KINDS
        assert "directory" in _VALID_LOCATION_KINDS
        assert "url" in _VALID_LOCATION_KINDS
