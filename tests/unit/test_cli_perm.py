"""Unit tests for cli_perm.py internals: SpecStore, validate_spec, helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from general_ludd.cli_perm import (
    SpecStore,
    _actions_list,
    _parse_constraints,
    _resolve_config_dir,
    validate_spec,
)

# ── SpecStore ───────────────────────────────────────────────────────────


class TestSpecStore:
    def test_spec_path_default(self, tmp_path):
        store = SpecStore(tmp_path)
        path = store.spec_path("build")
        assert path == tmp_path / "permissions" / "build.yml"

    def test_spec_path_with_project(self, tmp_path):
        store = SpecStore(tmp_path)
        path = store.spec_path("build", project="myproj")
        assert path == tmp_path / "permissions" / "projects" / "myproj" / "build.yml"

    def test_all_spec_paths_empty_when_no_dir(self, tmp_path):
        store = SpecStore(tmp_path / "nonexistent")
        assert store.all_spec_paths() == []

    def test_all_spec_paths_finds_yml_files(self, tmp_path):
        perms = tmp_path / "permissions"
        perms.mkdir(parents=True)
        (perms / "admin.yml").write_text("agent_type: admin")
        (perms / "build.yml").write_text("agent_type: build")
        (perms / "other.txt").write_text("not yml")
        store = SpecStore(tmp_path)
        paths = store.all_spec_paths()
        assert len(paths) == 2
        assert all(p.suffix == ".yml" for p in paths)

    def test_load_missing_returns_none(self, tmp_path):
        store = SpecStore(tmp_path)
        assert store.load("nonexistent") is None

    def test_load_returns_dict_with_agent_type(self, tmp_path):
        perms = tmp_path / "permissions"
        perms.mkdir(parents=True)
        (perms / "build.yml").write_text(
            yaml.safe_dump({"capabilities": [], "denied": [], "max_sts_ttl": 3600})
        )
        store = SpecStore(tmp_path)
        spec = store.load("build")
        assert spec == {
            "agent_type": "build",
            "capabilities": [],
            "denied": [],
            "max_sts_ttl": 3600,
        }

    def test_load_handles_empty_file(self, tmp_path):
        perms = tmp_path / "permissions"
        perms.mkdir(parents=True)
        (perms / "empty.yml").write_text("")
        store = SpecStore(tmp_path)
        spec = store.load("empty")
        assert spec == {"agent_type": "empty"}

    def test_load_returns_none_on_non_dict(self, tmp_path):
        perms = tmp_path / "permissions"
        perms.mkdir(parents=True)
        (perms / "list.yml").write_text("- item1\n- item2\n")
        store = SpecStore(tmp_path)
        spec = store.load("list")
        assert spec is None

    def test_save_creates_dir_and_writes(self, tmp_path):
        store = SpecStore(tmp_path)
        spec = {"agent_type": "review", "capabilities": [], "denied": [], "max_sts_ttl": 1800}
        path = store.save("review", spec)
        assert path.is_file()
        loaded = yaml.safe_load(path.read_text())
        assert loaded["agent_type"] == "review"
        assert loaded["max_sts_ttl"] == 1800

    def test_save_with_project(self, tmp_path):
        store = SpecStore(tmp_path)
        spec = {"capabilities": [{"resource": "file:src", "actions": ["read"]}]}
        path = store.save("build", spec, project="p1")
        assert path.parent.name == "build.yml" or str(path).endswith("build.yml")
        assert "projects" in str(path)

    def test_load_all(self, tmp_path):
        perms = tmp_path / "permissions"
        perms.mkdir(parents=True)
        (perms / "a.yml").write_text("agent_type: a\nmax_sts_ttl: 100")
        (perms / "b.yml").write_text("max_sts_ttl: 200")
        store = SpecStore(tmp_path)
        specs = store.load_all()
        assert len(specs) == 2
        agent_types = {s["agent_type"] for s in specs}
        assert agent_types == {"a", "b"}

    def test_load_all_skips_non_dict(self, tmp_path):
        perms = tmp_path / "permissions"
        perms.mkdir(parents=True)
        (perms / "good.yml").write_text("agent_type: x")
        (perms / "bad.yml").write_text("- list")
        store = SpecStore(tmp_path)
        specs = store.load_all()
        assert len(specs) == 1
        assert specs[0]["agent_type"] == "x"


# ── validate_spec / _structural_validate ───────────────────────────────


class TestValidateSpec:
    def test_valid_spec_returns_empty(self):
        spec = {
            "agent_type": "build",
            "capabilities": [{"resource": "file:repo", "actions": ["read"]}],
            "denied": [],
            "max_sts_ttl": 3600,
        }
        assert validate_spec(spec) == []

    def test_missing_agent_type(self):
        errors = validate_spec({"capabilities": []})
        assert any("agent_type must be" in e for e in errors)

    def test_empty_agent_type(self):
        errors = validate_spec({"agent_type": "", "capabilities": []})
        assert any("agent_type must be" in e for e in errors)

    def test_capabilities_not_list(self):
        errors = validate_spec({"agent_type": "x", "capabilities": "bad"})
        assert any("capabilities must be a list" in e for e in errors)

    def test_capability_missing_resource(self):
        spec = {
            "agent_type": "x",
            "capabilities": [{"actions": ["read"]}],
        }
        errors = validate_spec(spec)
        assert any("resource is required" in e for e in errors)

    def test_capability_actions_not_list(self):
        spec = {
            "agent_type": "x",
            "capabilities": [{"resource": "r", "actions": "bad"}],
        }
        errors = validate_spec(spec)
        assert any("actions must be a list" in e for e in errors)

    def test_capability_not_dict(self):
        spec = {
            "agent_type": "x",
            "capabilities": ["string_not_dict"],
        }
        errors = validate_spec(spec)
        assert any("must be a mapping" in e for e in errors)

    def test_denied_not_list(self):
        spec = {"agent_type": "x", "denied": "bad"}
        errors = validate_spec(spec)
        assert any("denied must be a list" in e for e in errors)

    def test_max_sts_ttl_non_integer(self):
        spec = {"agent_type": "x", "max_sts_ttl": "notanumber"}
        errors = validate_spec(spec)
        assert any("max_sts_ttl must be" in e for e in errors)

    def test_max_sts_ttl_negative(self):
        spec = {"agent_type": "x", "max_sts_ttl": -1}
        errors = validate_spec(spec)
        assert any("max_sts_ttl must be" in e for e in errors)

    def test_max_sts_ttl_zero_valid(self):
        spec = {"agent_type": "x", "max_sts_ttl": 0, "capabilities": []}
        errors = validate_spec(spec)
        assert errors == []

    def test_multiple_capability_errors(self):
        spec = {
            "agent_type": "x",
            "capabilities": [
                {"resource": "r1", "actions": ["read"]},
                {"actions": ["write"]},
                "bad",
            ],
        }
        errors = validate_spec(spec)
        assert len(errors) >= 2


# ── helpers ────────────────────────────────────────────────────────────


class TestParseConstraints:
    def test_empty_returns_empty(self):
        assert _parse_constraints(None) == {}
        assert _parse_constraints([]) == {}

    def test_single_pair(self):
        assert _parse_constraints(["key=val"]) == {"key": "val"}

    def test_multiple_pairs(self):
        result = _parse_constraints(["a=1", "b=2"])
        assert result == {"a": "1", "b": "2"}

    def test_value_contains_equals(self):
        assert _parse_constraints(["url=http://x?a=1"]) == {"url": "http://x?a=1"}

    def test_missing_equals_raises(self):
        with pytest.raises(ValueError, match="expects KEY=VAL"):
            _parse_constraints(["invalid"])


class TestActionsList:
    def test_empty_string(self):
        assert _actions_list("") == []

    def test_single_action(self):
        assert _actions_list("read") == ["read"]

    def test_multiple_actions(self):
        assert _actions_list("read, write, execute") == ["read", "write", "execute"]

    def test_strips_whitespace(self):
        assert _actions_list("  read ,  write  ") == ["read", "write"]


class TestResolveConfigDir:
    def test_from_args(self, tmp_path):
        import argparse

        args = argparse.Namespace(config_dir=str(tmp_path))
        result = _resolve_config_dir(args)
        assert result == tmp_path

    def test_default(self):
        import argparse

        args = argparse.Namespace()
        with patch.object(
            Path, "home", return_value=Path("/home/user")
        ):
            result = _resolve_config_dir(args)
        assert result == Path("/home/user") / ".config" / "gludd"
