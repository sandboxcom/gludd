"""Structural tests for projects/manager.py helpers.

Covers _normalize_repo_url, _infer_location_kind, normalize_relationship_config,
parse_relationships, seed_from_config.
"""

from __future__ import annotations

from general_ludd.projects.manager import (
    ProjectAllocationError,
    ProjectWeight,
    _infer_location_kind,
    _normalize_repo_url,
    normalize_relationship_config,
    parse_relationships,
    seed_from_config,
)


class TestNormalizeRepoUrl:
    def test_https_url(self):
        assert _normalize_repo_url("https://github.com/user/repo.git") == "github.com/user/repo"

    def test_https_no_dotgit(self):
        assert _normalize_repo_url("https://github.com/user/repo") == "github.com/user/repo"

    def test_ssh_git_url(self):
        assert _normalize_repo_url("git@github.com:user/repo.git") == "github.com/user/repo"

    def test_ssh_url(self):
        assert _normalize_repo_url("ssh://git@github.com/user/repo.git") == "github.com/user/repo"

    def test_http_url(self):
        assert _normalize_repo_url("http://github.com/ORG/REPO.git") == "github.com/org/repo"

    def test_lowercase_normalization(self):
        assert _normalize_repo_url("https://GitHub.com/User/Repo") == "github.com/user/repo"

    def test_empty_string(self):
        assert _normalize_repo_url("") == ""

    def test_strips_trailing_slash(self):
        assert _normalize_repo_url("https://github.com/user/repo/") == "github.com/user/repo"


class TestInferLocationKind:
    def test_url_with_scheme(self):
        assert _infer_location_kind("https://github.com/user/repo") == "url"

    def test_directory_with_slash(self):
        assert _infer_location_kind("/home/user/project") == "directory"

    def test_relative_directory(self):
        assert _infer_location_kind("./subdir") == "directory"

    def test_project_name(self):
        assert _infer_location_kind("my-project") == "gludd_project_name"

    def test_empty_string(self):
        assert _infer_location_kind("") == "gludd_project_name"


class TestNormalizeRelationshipConfig:
    def test_valid_parent_relation(self):
        rel = {"relation": "parent", "location": "my-proj"}
        result = normalize_relationship_config(rel)
        assert result is not None
        assert result["relation_type"] == "parent"
        assert result["location_kind"] == "gludd_project_name"
        assert result["location_value"] == "my-proj"
        assert result["controlled_by_gludd"] is False
        assert result["related_project_id"] is None

    def test_valid_child_with_kind(self):
        rel = {"relation": "child", "location": "https://github.com/u/r", "kind": "url"}
        result = normalize_relationship_config(rel)
        assert result is not None
        assert result["relation_type"] == "child"
        assert result["location_kind"] == "url"

    def test_valid_sibling(self):
        rel = {"relation": "sibling", "location": "../other-project"}
        result = normalize_relationship_config(rel)
        assert result is not None
        assert result["relation_type"] == "sibling"
        assert result["location_kind"] == "directory"

    def test_valid_external(self):
        rel = {"relation": "external", "location": "some-service"}
        result = normalize_relationship_config(rel)
        assert result is not None
        assert result["relation_type"] == "external"

    def test_invalid_relation_type(self):
        rel = {"relation": "grandparent", "location": "foo"}
        result = normalize_relationship_config(rel)
        assert result is None

    def test_empty_location(self):
        rel = {"relation": "parent", "location": ""}
        result = normalize_relationship_config(rel)
        assert result is None

    def test_missing_relation(self):
        rel = {"location": "foo"}
        result = normalize_relationship_config(rel)
        assert result is None

    def test_controlled_by_gludd_explicit(self):
        rel = {"relation": "parent", "location": "x", "controlled_by_gludd": True}
        result = normalize_relationship_config(rel)
        assert result is not None
        assert result["controlled_by_gludd"] is True
        assert result["_controlled_explicit"] is True

    def test_interface_contract_dict(self):
        rel = {"relation": "parent", "location": "x", "interface_contract": {"port": 8080}}
        result = normalize_relationship_config(rel)
        assert result is not None
        assert '"port": 8080' in result["interface_contract"]

    def test_not_dict_input(self):
        assert normalize_relationship_config("not a dict") is None

    def test_interface_hint(self):
        rel = {"relation": "parent", "location": "x", "interface_hint": "REST"}
        result = normalize_relationship_config(rel)
        assert result is not None
        assert result["interface_hint"] == "REST"


class TestParseRelationships:
    def test_empty_list(self):
        assert parse_relationships({"relationships": []}) == []

    def test_missing_relationships(self):
        assert parse_relationships({}) == []

    def test_non_list_relationships(self):
        assert parse_relationships({"relationships": "not-a-list"}) == []

    def test_valid_edges(self):
        cfg = {
            "relationships": [
                {"relation": "parent", "location": "proj-a"},
                {"relation": "sibling", "location": "proj-b"},
            ]
        }
        edges = parse_relationships(cfg)
        assert len(edges) == 2
        assert edges[0]["relation_type"] == "parent"
        assert edges[1]["relation_type"] == "sibling"

    def test_mixed_valid_and_invalid(self):
        cfg = {
            "relationships": [
                {"relation": "parent", "location": "good"},
                {"relation": "invalid", "location": "bad"},
                {"relation": "child", "location": "also-good"},
            ]
        }
        edges = parse_relationships(cfg)
        assert len(edges) == 2


class TestSeedFromConfig:
    def test_empty_config(self):
        mgr = seed_from_config({})
        assert mgr.list_projects() == []

    def test_single_project(self):
        cfg = {
            "projects": [
                {"name": "test-proj", "weight": 50, "description": "a test"}
            ]
        }
        mgr = seed_from_config(cfg)
        projects = mgr.list_projects()
        assert len(projects) == 1
        assert projects[0].name == "test-proj"
        assert projects[0].weight == 50
        assert projects[0].description == "a test"

    def test_multiple_projects(self):
        cfg = {
            "projects": [
                {"name": "p1", "weight": 30},
                {"name": "p2", "weight": 30},
                {"name": "p3", "weight": 40},
            ]
        }
        mgr = seed_from_config(cfg)
        assert len(mgr.list_projects()) == 3

    def test_project_exceeding_100_percent_skipped(self):
        cfg = {
            "projects": [
                {"name": "p1", "weight": 80},
                {"name": "p2", "weight": 30},  # would exceed 100
                {"name": "p3", "weight": 20},
            ]
        }
        mgr = seed_from_config(cfg)
        projects = mgr.list_projects()
        assert len(projects) == 2
        names = {p.name for p in projects}
        assert "p2" not in names

    def test_non_list_projects(self):
        mgr = seed_from_config({"projects": "not-a-list"})
        assert mgr.list_projects() == []

    def test_non_dict_entries_skipped(self):
        cfg = {"projects": [{"name": "p1", "weight": 50}, "not-a-dict"]}
        mgr = seed_from_config(cfg)
        assert len(mgr.list_projects()) == 1

    def test_relationships_attached_to_config(self):
        cfg = {
            "projects": [
                {
                    "name": "p1",
                    "weight": 50,
                    "relationships": [
                        {"relation": "parent", "location": "proj-x"}
                    ],
                }
            ]
        }
        mgr = seed_from_config(cfg)
        projects = mgr.list_projects()
        assert len(projects) == 1
        assert "relationships" in projects[0].config
        assert len(projects[0].config["relationships"]) == 1


class TestProjectWeightDefaults:
    def test_default_dispatch_mode(self):
        pw = ProjectWeight(project_id="p1", name="test", weight=50.0)
        assert pw.dispatch_mode == "active"

    def test_default_active(self):
        pw = ProjectWeight(project_id="p2", name="test", weight=50.0)
        assert pw.active is True


class TestProjectAllocationError:
    def test_is_exception(self):
        err = ProjectAllocationError("test")
        assert isinstance(err, Exception)
        assert str(err) == "test"
