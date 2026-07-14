"""Structural tests for ``gludd perm`` CLI (cli_perm.py)."""

from __future__ import annotations

import argparse
import sys
from io import StringIO
from pathlib import Path

import pytest
import yaml

from general_ludd.cli_perm import (
    PERM_SUBCOMMANDS,
    SpecStore,
    _actions_list,
    _add_common,
    _add_http_common,
    _cmd_perm_deny,
    _cmd_perm_diff,
    _cmd_perm_edit,
    _cmd_perm_grant,
    _cmd_perm_list,
    _cmd_perm_project,
    _cmd_perm_revoke,
    _cmd_perm_show,
    _cmd_perm_validate,
    _emit,
    _parse_constraints,
    _resolve_config_dir,
    _structural_validate,
    register,
    validate_spec,
)

# ---------------------------------------------------------------------------
# PERM_SUBCOMMANDS
# ---------------------------------------------------------------------------


class TestPermSubcommands:
    def test_list_is_non_empty(self) -> None:
        assert len(PERM_SUBCOMMANDS) > 0

    def test_contains_expected_file_backed_commands(self) -> None:
        expected = {"list", "show", "grant", "deny", "revoke", "edit", "validate", "diff", "project"}
        assert expected <= set(PERM_SUBCOMMANDS)

    def test_contains_expected_http_backed_commands(self) -> None:
        expected = {"sts", "audit", "escalations"}
        assert expected <= set(PERM_SUBCOMMANDS)

    def test_no_duplicates(self) -> None:
        assert len(PERM_SUBCOMMANDS) == len(set(PERM_SUBCOMMANDS))

    def test_endpoint_count_matches_known(self) -> None:
        assert len(PERM_SUBCOMMANDS) == 12


# ---------------------------------------------------------------------------
# SpecStore — path computation
# ---------------------------------------------------------------------------


class TestSpecStorePaths:
    def test_instantiation_normalizes_path(self, tmp_path: Path) -> None:
        store = SpecStore(str(tmp_path))
        assert store.config_dir == tmp_path
        assert store.perms_dir == tmp_path / "permissions"

    def test_instantiation_expands_tilde(self) -> None:
        store = SpecStore("~/.config/gludd")
        assert store.config_dir == Path.home() / ".config" / "gludd"

    def test_spec_path_without_project(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        path = store.spec_path("human-operator")
        assert path == tmp_path / "permissions" / "human-operator.yml"

    def test_spec_path_with_project(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        path = store.spec_path("human-operator", project="myproject")
        assert path == tmp_path / "permissions" / "projects" / "myproject" / "human-operator.yml"

    def test_spec_path_project_none_is_no_project(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        path = store.spec_path("human-operator", project=None)
        assert path == tmp_path / "permissions" / "human-operator.yml"

    def test_all_spec_paths_empty_when_no_perms_dir(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        assert store.all_spec_paths() == []

    def test_all_spec_paths_empty_when_dir_exists_but_no_yml(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        store.perms_dir.mkdir(parents=True)
        assert store.all_spec_paths() == []

    def test_all_spec_paths_returns_sorted_yml_files(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        store.perms_dir.mkdir(parents=True)
        (store.perms_dir / "z-agent.yml").write_text("agent_type: z-agent\n")
        (store.perms_dir / "a-agent.yml").write_text("agent_type: a-agent\n")
        paths = store.all_spec_paths()
        assert len(paths) == 2
        assert paths[0].name == "a-agent.yml"
        assert paths[1].name == "z-agent.yml"

    def test_all_spec_paths_excludes_non_yml(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        store.perms_dir.mkdir(parents=True)
        (store.perms_dir / "spec.yml").write_text("agent_type: x\n")
        (store.perms_dir / "README.md").write_text("# docs\n")
        paths = store.all_spec_paths()
        assert len(paths) == 1
        assert paths[0].name == "spec.yml"

    def test_all_spec_paths_excludes_subdirs(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        store.perms_dir.mkdir(parents=True)
        (store.perms_dir / "projects").mkdir(parents=True)
        (store.perms_dir / "projects" / "p" / "x.yml").mkdir(parents=True)  # file in subdir
        (store.perms_dir / "x.yml").write_text("x: 1\n")
        paths = store.all_spec_paths()
        assert len(paths) == 1  # only top-level *.yml, not projects/p/x.yml
        assert paths[0].name == "x.yml"


# ---------------------------------------------------------------------------
# SpecStore — YAML load / save
# ---------------------------------------------------------------------------


class TestSpecStoreLoad:
    def test_load_returns_dict(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        store.save("test-agent", {"agent_type": "test-agent", "capabilities": []})
        result = store.load("test-agent")
        assert isinstance(result, dict)
        assert result["agent_type"] == "test-agent"
        assert result["capabilities"] == []

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        assert store.load("nonexistent") is None

    def test_load_sets_default_agent_type(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        store.save("nudge", {"capabilities": [{"resource": "x", "actions": ["read"]}]})
        result = store.load("nudge")
        assert result is not None
        assert result["agent_type"] == "nudge"

    def test_load_returns_none_for_non_dict_yaml(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        path = store.spec_path("list-agent")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("- item1\n- item2\n")
        assert store.load("list-agent") is None

    def test_load_empty_file_returns_dict(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        path = store.spec_path("empty-agent")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        result = store.load("empty-agent")
        assert result is not None
        assert result["agent_type"] == "empty-agent"

    def test_load_with_project(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        path = store.save("x", {"capabilities": []}, project="p1")
        assert "projects" in str(path)
        assert "p1" in str(path)
        result = store.load("x", project="p1")
        assert result is not None
        assert result["agent_type"] == "x"

    def test_load_all_empty(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        assert store.load_all() == []

    def test_load_all_returns_all_specs(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        store.save("a", {"capabilities": [{"resource": "r1", "actions": ["read"]}]})
        store.save("b", {"capabilities": [{"resource": "r2", "actions": ["write"]}]})
        specs = store.load_all()
        assert len(specs) == 2
        agent_types = {s["agent_type"] for s in specs}
        assert agent_types == {"a", "b"}

    def test_load_all_skips_non_dict_yaml(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        store.save("good", {"capabilities": []})
        (store.perms_dir / "bad.yml").write_text("- list not dict\n")
        specs = store.load_all()
        assert len(specs) == 1
        assert specs[0]["agent_type"] == "good"


class TestSpecStoreSave:
    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        assert not store.perms_dir.exists()
        path = store.save("x", {"agent_type": "x"})
        assert store.perms_dir.exists()
        assert path.exists()

    def test_save_returns_path(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        path = store.save("x", {"agent_type": "x"})
        assert isinstance(path, Path)
        assert path.name == "x.yml"

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        spec = {
            "agent_type": "human-operator",
            "capabilities": [{"resource": "file:*", "actions": ["read", "write"]}],
            "denied": [{"resource": "credential:*", "actions": ["*"]}],
            "max_sts_ttl": 3600,
        }
        store.save("human-operator", spec)
        loaded = store.load("human-operator")
        assert loaded is not None
        assert loaded["agent_type"] == "human-operator"
        assert loaded["capabilities"] == spec["capabilities"]
        assert loaded["denied"] == spec["denied"]
        assert loaded["max_sts_ttl"] == 3600

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        store.save("a", {"agent_type": "a", "max_sts_ttl": 100})
        store.save("a", {"agent_type": "a", "max_sts_ttl": 200})
        result = store.load("a")
        assert result is not None
        assert result["max_sts_ttl"] == 200

    def test_save_to_project(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        path = store.save("a", {"capabilities": []}, project="p1")
        assert path == store.perms_dir / "projects" / "p1" / "a.yml"
        assert path.exists()

    def test_save_malformed_yaml(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        # bytes data that yaml.safe_dump will happily serialize
        spec = {"agent_type": "x", "raw": "hello"}
        path = store.save("x", spec)
        assert path.exists()
        reloaded = yaml.safe_load(path.read_text())
        assert reloaded["raw"] == "hello"


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


class TestStructuralValidate:
    def test_valid_spec_passes(self) -> None:
        spec = {
            "agent_type": "human-operator",
            "capabilities": [{"resource": "file:*", "actions": ["read"]}],
            "denied": [],
            "max_sts_ttl": 3600,
        }
        assert _structural_validate(spec) == []

    def test_empty_dict_fails(self) -> None:
        errors = _structural_validate({})
        assert len(errors) >= 1
        assert any("agent_type" in e for e in errors)

    def test_missing_agent_type_fails(self) -> None:
        spec: dict[str, object] = {"capabilities": []}
        errors = _structural_validate(spec)  # type: ignore[arg-type]
        assert any("agent_type" in e for e in errors)

    def test_agent_type_not_string_fails(self) -> None:
        errors = _structural_validate({"agent_type": 42, "capabilities": []})  # type: ignore[dict-item]
        assert any("agent_type" in e for e in errors)

    def test_capabilities_not_list_fails(self) -> None:
        errors = _structural_validate({"agent_type": "x", "capabilities": "nope"})  # type: ignore[dict-item]
        assert any("capabilities must be a list" in e for e in errors)

    def test_capability_not_dict_fails(self) -> None:
        errors = _structural_validate({"agent_type": "x", "capabilities": ["not a map"]})  # type: ignore[list-item]
        assert any("must be a mapping" in e for e in errors)

    def test_capability_missing_resource_fails(self) -> None:
        errors = _structural_validate({
            "agent_type": "x",
            "capabilities": [{"actions": ["read"]}],
        })
        assert any("resource is required" in e for e in errors)

    def test_capability_actions_not_list_fails(self) -> None:
        errors = _structural_validate({
            "agent_type": "x",
            "capabilities": [{"resource": "r", "actions": "bad"}],
        })  # type: ignore[dict-item]
        assert any("actions must be a list" in e for e in errors)

    def test_denied_not_list_fails(self) -> None:
        errors = _structural_validate({"agent_type": "x", "denied": "nope"})  # type: ignore[dict-item]
        assert any("denied must be a list" in e for e in errors)

    def test_max_sts_ttl_negative_fails(self) -> None:
        errors = _structural_validate({"agent_type": "x", "max_sts_ttl": -1})
        assert any("max_sts_ttl" in e for e in errors)

    def test_max_sts_ttl_not_int_fails(self) -> None:
        errors = _structural_validate({"agent_type": "x", "max_sts_ttl": "forever"})  # type: ignore[dict-item]
        assert any("max_sts_ttl" in e for e in errors)

    def test_valid_spec_without_capabilities(self) -> None:
        errors = _structural_validate({"agent_type": "x"})
        assert errors == []

    def test_valid_spec_with_empty_capabilities(self) -> None:
        errors = _structural_validate({"agent_type": "x", "capabilities": []})
        assert errors == []

    def test_capability_index_in_error_message(self) -> None:
        errors = _structural_validate({
            "agent_type": "x",
            "capabilities": [{"resource": "r1", "actions": ["read"]}, "bad"],
        })  # type: ignore[list-item]
        assert len(errors) >= 1
        assert any("capabilities[1]" in e for e in errors)


class TestValidateSpec:
    def test_delegates_to_structural(self) -> None:
        spec = {"agent_type": "x", "capabilities": []}
        errors = validate_spec(spec)
        assert isinstance(errors, list)
        assert errors == []

    def test_catches_invalid_spec(self) -> None:
        errors = validate_spec({})
        assert len(errors) > 0

    def test_does_not_import_nonexistent_module(self) -> None:
        spec = {"agent_type": "x", "capabilities": []}
        errors = validate_spec(spec)
        assert errors == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_resolve_config_dir_from_args(self) -> None:
        args = argparse.Namespace(config_dir="/custom/config")
        path = _resolve_config_dir(args)
        assert path == Path("/custom/config")

    def test_resolve_config_dir_default(self) -> None:
        args = argparse.Namespace()
        path = _resolve_config_dir(args)
        assert path == Path.home() / ".config" / "gludd"

    def test_actions_list_splits_commas(self) -> None:
        assert _actions_list("read,write,delete") == ["read", "write", "delete"]

    def test_actions_list_trims_whitespace(self) -> None:
        assert _actions_list(" read , write ") == ["read", "write"]

    def test_actions_list_empty_string(self) -> None:
        assert _actions_list("") == []

    def test_actions_list_single(self) -> None:
        assert _actions_list("read") == ["read"]

    def test_parse_constraints_key_val(self) -> None:
        result = _parse_constraints(["env=prod", "region=us-east-1"])
        assert result == {"env": "prod", "region": "us-east-1"}

    def test_parse_constraints_empty(self) -> None:
        assert _parse_constraints([]) == {}

    def test_parse_constraints_none(self) -> None:
        assert _parse_constraints(None) == {}

    def test_parse_constraints_missing_equals_raises(self) -> None:
        with pytest.raises(ValueError, match="KEY=VAL"):
            _parse_constraints(["badformat"])

    def test_parse_constraints_trims_keys_and_values(self) -> None:
        result = _parse_constraints([" key = value "])
        assert result == {"key": "value"}

    def test_emit_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        args = argparse.Namespace(json=True)
        captured = StringIO()

        def _capture(s: str) -> None:
            captured.write(s + "\n")

        import builtins

        monkeypatch.setattr(builtins, "print", _capture)
        _emit({"key": "val"}, args)
        output = captured.getvalue()
        assert '"key"' in output
        assert '"val"' in output

    def test_emit_no_json_does_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        args = argparse.Namespace(json=False)
        called = False

        def _capture(_s: str) -> None:
            nonlocal called
            called = True

        import builtins

        monkeypatch.setattr(builtins, "print", _capture)
        _emit({"key": "val"}, args)
        assert not called


# ---------------------------------------------------------------------------
# CLI argument parser registration
# ---------------------------------------------------------------------------


class TestRegisterParser:
    def test_register_returns_parser(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        parser = register(sub)
        assert parser is not None

    def test_perm_command_registered(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm"])
        assert ns.command == "perm"

    def test_list_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "list"])
        assert ns.perm_command == "list"
        assert ns.func == _cmd_perm_list

    def test_list_with_agent_type(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "list", "--agent-type", "human-operator"])
        assert ns.agent_type == "human-operator"

    def test_list_with_json(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "list", "--json"])
        assert ns.json is True

    def test_show_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "show", "human-operator"])
        assert ns.perm_command == "show"
        assert ns.agent_type == "human-operator"
        assert ns.func == _cmd_perm_show

    def test_grant_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "grant", "human-operator", "file:repo", "read,write"])
        assert ns.perm_command == "grant"
        assert ns.agent_type == "human-operator"
        assert ns.resource == "file:repo"
        assert ns.actions == "read,write"
        assert ns.func == _cmd_perm_grant

    def test_grant_with_constraints(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args([
            "perm", "grant", "x", "r", "read",
            "--constraints", "env=prod", "region=us-east-1",
        ])
        assert ns.constraints == ["env=prod", "region=us-east-1"]

    def test_deny_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "deny", "human-operator", "file:secrets", "read,write"])
        assert ns.perm_command == "deny"
        assert ns.func == _cmd_perm_deny

    def test_revoke_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "revoke", "human-operator", "file:secrets"])
        assert ns.perm_command == "revoke"
        assert ns.yes is False
        assert ns.func == _cmd_perm_revoke

    def test_revoke_with_yes(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "revoke", "x", "r", "-y"])
        assert ns.yes is True

    def test_edit_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "edit", "human-operator"])
        assert ns.perm_command == "edit"
        assert ns.agent_type == "human-operator"
        assert ns.editor is None
        assert ns.func == _cmd_perm_edit

    def test_edit_with_editor(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "edit", "x", "--editor", "vim"])
        assert ns.editor == "vim"

    def test_validate_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "validate"])
        assert ns.perm_command == "validate"
        assert ns.agent_type is None
        assert ns.func == _cmd_perm_validate

    def test_validate_with_agent_type(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "validate", "human-operator"])
        assert ns.agent_type == "human-operator"

    def test_diff_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "diff", "human-operator", "human-admin"])
        assert ns.perm_command == "diff"
        assert ns.agent_type_a == "human-operator"
        assert ns.agent_type_b == "human-admin"
        assert ns.func == _cmd_perm_diff

    def test_project_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "project", "myproject"])
        assert ns.perm_command == "project"
        assert ns.project_name == "myproject"
        assert ns.set_default_agent_type is None
        assert ns.func == _cmd_perm_project

    def test_project_with_set_default(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "project", "myproject", "--set-default-agent-type", "human-operator"])
        assert ns.set_default_agent_type == "human-operator"

    def test_sts_subcommand_registered(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "sts"])
        assert ns.perm_command == "sts"

    def test_sts_list(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "sts", "list"])
        assert ns.perm_sts_command == "list"

    def test_sts_issue(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "sts", "issue", "agent-7", "--spec-yaml", "/path/spec.yml"])
        assert ns.perm_sts_command == "issue"
        assert ns.subject_agent_id == "agent-7"
        assert ns.spec_yaml == "/path/spec.yml"

    def test_sts_inspect(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "sts", "inspect", "tok-123"])
        assert ns.perm_sts_command == "inspect"
        assert ns.token_id == "tok-123"

    def test_sts_revoke(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "sts", "revoke", "tok-123"])
        assert ns.perm_sts_command == "revoke"
        assert ns.token_id == "tok-123"

    def test_audit_subcommand(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "audit"])
        assert ns.perm_command == "audit"
        assert ns.agent_id is None

    def test_common_args_appear_on_file_backed(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args(["perm", "list", "--config-dir", "/tmp/cfg", "--json", "--quiet"])
        assert ns.config_dir == "/tmp/cfg"
        assert ns.json is True
        assert ns.quiet is True

    def test_http_common_args_appear_on_audit(self) -> None:
        top = argparse.ArgumentParser(prog="gludd")
        sub = top.add_subparsers(dest="command")
        register(sub)
        ns = top.parse_args([
            "perm", "audit",
            "--daemon-url", "http://localhost:9000",
            "--psk", "secret123",
            "--json", "--quiet",
        ])
        assert ns.daemon_url == "http://localhost:9000"
        assert ns.psk == "secret123"
        assert ns.json is True
        assert ns.quiet is True


class TestAddCommon:
    def test_adds_config_dir(self) -> None:
        p = argparse.ArgumentParser()
        _add_common(p)
        ns = p.parse_args(["--config-dir", "/tmp/c"])
        assert ns.config_dir == "/tmp/c"

    def test_adds_json(self) -> None:
        p = argparse.ArgumentParser()
        _add_common(p)
        ns = p.parse_args(["--json"])
        assert ns.json is True

    def test_defaults(self) -> None:
        p = argparse.ArgumentParser()
        _add_common(p)
        ns = p.parse_args([])
        assert ns.config_dir is None
        assert ns.json is False
        assert ns.quiet is False


class TestAddHttpCommon:
    def test_adds_daemon_url(self) -> None:
        p = argparse.ArgumentParser()
        _add_http_common(p)
        ns = p.parse_args(["--daemon-url", "http://localhost:9000"])
        assert ns.daemon_url == "http://localhost:9000"

    def test_adds_psk(self) -> None:
        p = argparse.ArgumentParser()
        _add_http_common(p)
        ns = p.parse_args(["--psk", "mypsk"])
        assert ns.psk == "mypsk"

    def test_default_daemon_url(self) -> None:
        p = argparse.ArgumentParser()
        _add_http_common(p)
        ns = p.parse_args([])
        assert ns.daemon_url == "http://localhost:8000"


# ---------------------------------------------------------------------------
# Subcommand handler behavior (non-HTTP, no side effects that need daemon)
# ---------------------------------------------------------------------------


class TestCmdPermList:
    def test_no_specs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        SpecStore(tmp_path)
        args = argparse.Namespace(config_dir=str(tmp_path), json=False, quiet=False, agent_type=None)
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_list(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert "No permission specs found" in output

    def test_with_specs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("human-operator", {"capabilities": []})
        args = argparse.Namespace(config_dir=str(tmp_path), json=False, quiet=False, agent_type=None)
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_list(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert "human-operator" in output

    def test_with_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("human-operator", {"capabilities": []})
        args = argparse.Namespace(config_dir=str(tmp_path), json=True, quiet=False, agent_type=None)
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_list(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert '"agent_type"' in output

    def test_filter_by_agent_type(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("alpha", {"capabilities": []})
        store.save("zeta", {"capabilities": []})
        args = argparse.Namespace(config_dir=str(tmp_path), json=False, quiet=False, agent_type="alpha")
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_list(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert "alpha" in output
        assert "zeta" not in output


class TestCmdPermShow:
    def test_nonexistent_exits(self, tmp_path: Path) -> None:
        args = argparse.Namespace(config_dir=str(tmp_path), agent_type="nope", json=False, quiet=False)
        with pytest.raises(SystemExit):
            _cmd_perm_show(args)

    def test_prints_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("test-agent", {"capabilities": [{"resource": "r", "actions": ["read"]}]})
        args = argparse.Namespace(config_dir=str(tmp_path), agent_type="test-agent", json=False, quiet=False)
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_show(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert "test-agent" in output
        assert "capabilities" in output

    def test_show_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("x", {"capabilities": []})
        args = argparse.Namespace(config_dir=str(tmp_path), agent_type="x", json=True, quiet=False)
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_show(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert '"agent_type"' in output


class TestCmdPermValidate:
    def test_single_valid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("x", {"agent_type": "x", "capabilities": []})
        args = argparse.Namespace(config_dir=str(tmp_path), agent_type="x", json=False, quiet=False)
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_validate(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert "OK" in output or "valid" in output

    def test_nonexistent_exits(self, tmp_path: Path) -> None:
        args = argparse.Namespace(config_dir=str(tmp_path), agent_type="nope", json=False, quiet=False)
        with pytest.raises(SystemExit):
            _cmd_perm_validate(args)

    def test_all_valid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("a", {"agent_type": "a", "capabilities": []})
        store.save("b", {"agent_type": "b", "capabilities": []})
        args = argparse.Namespace(config_dir=str(tmp_path), agent_type=None, json=False, quiet=False)
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_validate(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert "valid" in output

    def test_all_invalid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("bad", {"capabilities": "not a list"})  # invalid
        args = argparse.Namespace(config_dir=str(tmp_path), agent_type=None, json=False, quiet=False)
        with pytest.raises(SystemExit):
            _cmd_perm_validate(args)

    def test_validate_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("a", {"agent_type": "a", "capabilities": []})
        args = argparse.Namespace(config_dir=str(tmp_path), agent_type=None, json=True, quiet=False)
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_validate(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert '"valid"' in output


class TestCmdPermDiff:
    def test_a_missing_exits(self, tmp_path: Path) -> None:
        args = argparse.Namespace(
            config_dir=str(tmp_path),
            agent_type_a="nonexistent", agent_type_b="x", json=False, quiet=False,
        )
        with pytest.raises(SystemExit):
            _cmd_perm_diff(args)

    def test_b_missing_exits(self, tmp_path: Path) -> None:
        store = SpecStore(tmp_path)
        store.save("x", {"capabilities": []})
        args = argparse.Namespace(
            config_dir=str(tmp_path),
            agent_type_a="x", agent_type_b="nonexistent", json=False, quiet=False,
        )
        with pytest.raises(SystemExit):
            _cmd_perm_diff(args)

    def test_identical_specs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("a", {"capabilities": [{"resource": "r", "actions": ["read"]}]})
        store.save("b", {"capabilities": [{"resource": "r", "actions": ["read"]}]})
        args = argparse.Namespace(config_dir=str(tmp_path), agent_type_a="a", agent_type_b="b", json=False, quiet=False)
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_diff(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert "Diff:" in output

    def test_diff_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("a", {"capabilities": [{"resource": "r", "actions": ["read"]}]})
        store.save("b", {"capabilities": [{"resource": "r", "actions": ["write"]}]})
        args = argparse.Namespace(config_dir=str(tmp_path), agent_type_a="a", agent_type_b="b", json=True, quiet=False)
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_diff(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert '"only_in_a"' in output or '"only_in_b"' in output or '"action_diff"' in output


class TestCmdPermProject:
    def test_no_overrides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        args = argparse.Namespace(
            config_dir=str(tmp_path), project_name="noproject",
            set_default_agent_type=None, json=False, quiet=False,
        )
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_project(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert "No project overrides" in output

    def test_project_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("human-operator", {"capabilities": []})
        args = argparse.Namespace(
            config_dir=str(tmp_path), project_name="myproject",
            set_default_agent_type="human-operator", json=False, quiet=False,
        )
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_project(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert "Project override written" in output
        assert (store.perms_dir / "projects" / "myproject" / "human-operator.yml").exists()

    def test_list_project_overrides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SpecStore(tmp_path)
        store.save("x", {"capabilities": []}, project="p1")
        store.save("y", {"capabilities": []}, project="p1")
        args = argparse.Namespace(
            config_dir=str(tmp_path), project_name="p1",
            set_default_agent_type=None, json=False, quiet=False,
        )
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        _cmd_perm_project(args)
        monkeypatch.undo()
        output = captured.getvalue()
        assert "x" in output
        assert "y" in output
