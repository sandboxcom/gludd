"""Structural tests for projects/manager.py — URL normalization, relationship parsing, self-detection."""

from __future__ import annotations

import json

from general_ludd.projects.manager import (
    ProjectWeight,
    _detect_self_project,
    _infer_location_kind,
    _normalize_repo_url,
    normalize_relationship_config,
    parse_relationships,
    seed_from_config,
)


class TestNormalizeRepoURL:
    def test_strips_git_suffix(self):
        result = _normalize_repo_url("https://github.com/sandboxcom/gludd.git")
        assert result == "github.com/sandboxcom/gludd"

    def test_lowercases(self):
        result = _normalize_repo_url("HTTPS://GitHub.COM/OWNER/REPO")
        assert result == "github.com/owner/repo"

    def test_strips_https_scheme(self):
        result = _normalize_repo_url("https://github.com/user/repo")
        assert result == "github.com/user/repo"

    def test_strips_http_scheme(self):
        result = _normalize_repo_url("http://github.com/user/repo")
        assert result == "github.com/user/repo"

    def test_strips_ssh_scheme(self):
        result = _normalize_repo_url("ssh://git@github.com/user/repo")
        assert result == "github.com/user/repo"

    def test_scp_form_conversion(self):
        result = _normalize_repo_url("git@github.com:sandboxcom/gludd.git")
        assert result == "github.com/sandboxcom/gludd"

    def test_scp_form_without_git(self):
        result = _normalize_repo_url("git@github.com:owner/repo")
        assert result == "github.com/owner/repo"

    def test_empty_string(self):
        assert _normalize_repo_url("") == ""

    def test_none_string(self):
        result = _normalize_repo_url("None")
        assert result == "none"

    def test_trailing_slash_stripped(self):
        result = _normalize_repo_url("github.com/user/repo/")
        assert result == "github.com/user/repo"

    def test_urls_equal_after_normalization(self):
        a = _normalize_repo_url("git@github.com:sandboxcom/gludd.git")
        b = _normalize_repo_url("https://github.com/sandboxcom/gludd")
        assert a == b


class TestInferLocationKind:
    def test_url_scheme(self):
        assert _infer_location_kind("https://github.com/owner/repo") == "url"

    def test_directory_path(self):
        assert _infer_location_kind("/home/user/project") == "directory"

    def test_relative_path(self):
        assert _infer_location_kind("./relative/path") == "directory"

    def test_project_name(self):
        assert _infer_location_kind("my-project") == "gludd_project_name"

    def test_empty_string(self):
        assert _infer_location_kind("") == "gludd_project_name"


class TestDetectSelfProject:
    def test_empty_repo_url(self):
        pw = ProjectWeight(project_id="p1", name="test", weight=10.0, repo_url="")
        assert _detect_self_project(pw, self_repo_url="https://github.com/me/gludd") is False

    def test_no_repo_url_attr(self):
        pw = ProjectWeight(project_id="p1", name="test", weight=10.0)
        assert _detect_self_project(pw, self_repo_url="https://github.com/me/gludd") is False

    def test_matching_urls(self):
        pw = ProjectWeight(project_id="p1", name="test", weight=10.0,
                           repo_url="https://github.com/me/gludd")
        assert _detect_self_project(pw, self_repo_url="https://github.com/me/gludd") is True

    def test_matching_ssh_vs_https(self):
        pw = ProjectWeight(project_id="p1", name="test", weight=10.0,
                           repo_url="git@github.com:me/gludd.git")
        assert _detect_self_project(pw, self_repo_url="https://github.com/me/gludd") is True

    def test_non_matching_urls(self):
        pw = ProjectWeight(project_id="p1", name="test", weight=10.0,
                           repo_url="https://github.com/other/project")
        assert _detect_self_project(pw, self_repo_url="https://github.com/me/gludd") is False

    def test_empty_self_url(self):
        pw = ProjectWeight(project_id="p1", name="test", weight=10.0,
                           repo_url="https://github.com/me/gludd")
        assert _detect_self_project(pw, self_repo_url="") is False


class TestNormalizeRelationshipConfig:
    def test_valid_parent_relationship(self):
        result = normalize_relationship_config({
            "relation": "parent", "location": "my-parent-project",
        })
        assert result is not None
        assert result["relation_type"] == "parent"
        assert result["location_value"] == "my-parent-project"
        assert result["location_kind"] == "gludd_project_name"

    def test_valid_child_with_explicit_kind(self):
        result = normalize_relationship_config({
            "relation": "child", "location": "https://git.example.com/child.git",
            "kind": "url",
        })
        assert result is not None
        assert result["relation_type"] == "child"
        assert result["location_kind"] == "url"

    def test_controlled_by_gludd(self):
        result = normalize_relationship_config({
            "relation": "sibling", "location": "../neighbor",
            "controlled_by_gludd": True,
        })
        assert result is not None
        assert result["controlled_by_gludd"] is True
        assert result["_controlled_explicit"] is True

    def test_controlled_explicit_false(self):
        result = normalize_relationship_config({
            "relation": "sibling", "location": "../neighbor",
            "controlled_by_gludd": False,
        })
        assert result is not None
        assert result["controlled_by_gludd"] is False
        assert result["_controlled_explicit"] is True

    def test_interface_contract_dict(self):
        result = normalize_relationship_config({
            "relation": "external", "location": "https://api.example.com",
            "interface_contract": {"api_version": "v2"},
        })
        assert result is not None
        assert json.loads(result["interface_contract"]) == {"api_version": "v2"}

    def test_interface_contract_string(self):
        result = normalize_relationship_config({
            "relation": "external", "location": "ext-svc",
            "interface_contract": "REST v1",
        })
        assert result is not None
        assert result["interface_contract"] == "REST v1"

    def test_invalid_relation_returns_none(self):
        result = normalize_relationship_config({
            "relation": "invalid", "location": "somewhere",
        })
        assert result is None

    def test_empty_location_returns_none(self):
        result = normalize_relationship_config({
            "relation": "parent", "location": "",
        })
        assert result is None

    def test_not_a_dict_returns_none(self):
        result = normalize_relationship_config("not a dict")  # type: ignore[arg-type]
        assert result is None

    def test_interface_hint_set(self):
        result = normalize_relationship_config({
            "relation": "parent", "location": "parent-proj",
            "interface_hint": "REST API on port 8080",
        })
        assert result is not None
        assert result["interface_hint"] == "REST API on port 8080"


class TestParseRelationships:
    def test_empty_config(self):
        assert parse_relationships({}) == []

    def test_no_relationships_key(self):
        assert parse_relationships({"no_rels": True}) == []

    def test_relationships_not_a_list(self):
        assert parse_relationships({"relationships": "invalid"}) == []

    def test_valid_relationships(self):
        edges = parse_relationships({
            "relationships": [
                {"relation": "parent", "location": "upstream"},
                {"relation": "child", "location": "downstream"},
            ],
        })
        assert len(edges) == 2

    def test_mixed_valid_invalid(self):
        edges = parse_relationships({
            "relationships": [
                {"relation": "parent", "location": "upstream"},
                {"relation": "bad", "location": ""},
                {"relation": "sibling", "location": "neighbor"},
            ],
        })
        assert len(edges) == 2


class TestSeedFromConfig:
    def test_empty_config(self):
        mgr = seed_from_config({})
        assert mgr.list_active() == []

    def test_single_project(self):
        mgr = seed_from_config({
            "projects": [{"name": "alpha", "weight": 50}],
        })
        projects = mgr.list_active()
        assert len(projects) == 1
        assert projects[0].name == "alpha"
        assert projects[0].weight == 50.0

    def test_multiple_projects(self):
        mgr = seed_from_config({
            "projects": [
                {"name": "alpha", "weight": 30},
                {"name": "beta", "weight": 40},
            ],
        })
        projects = mgr.list_active()
        assert len(projects) == 2
        names = {p.name for p in projects}
        assert names == {"alpha", "beta"}

    def test_skips_overweight(self):
        mgr = seed_from_config({
            "projects": [
                {"name": "alpha", "weight": 90},
                {"name": "beta", "weight": 50},
            ],
        })
        projects = mgr.list_active()
        assert len(projects) == 1
        assert projects[0].name == "alpha"

    def test_projects_not_a_list(self):
        mgr = seed_from_config({"projects": "invalid"})
        assert mgr.list_active() == []

    def test_skips_non_dict_entries(self):
        mgr = seed_from_config({
            "projects": ["not-a-dict", {"name": "real", "weight": 10}],
        })
        projects = mgr.list_active()
        assert len(projects) == 1
        assert projects[0].name == "real"

    def test_carries_relationships_on_config(self):
        mgr = seed_from_config({
            "projects": [{
                "name": "linked",
                "weight": 10,
                "relationships": [{"relation": "parent", "location": "upstream"}],
            }],
        })
        projects = mgr.list_active()
        assert len(projects) == 1
        assert "relationships" in projects[0].config
