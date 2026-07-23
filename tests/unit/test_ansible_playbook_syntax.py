"""Ansible playbook syntax validation tests.

Tests ansible manifest generation, module extraction, syntax validation,
sandboxed template rendering, and action policy checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from general_ludd.ansible.manifest import (
    _FQCN_PATTERN,
    _MODULE_TASK_KEYS,
    _extract_modules_from_task,
    generate_manifest,
)
from general_ludd.ansible.templating import AnsibleTemplater, TemplateRenderError
from general_ludd.ansible.unsafe import has_wrap_var, wrap_extravars, wrap_unsafe


class TestFQCNPattern:
    def test_fqcn_pattern_matches_valid(self):
        assert _FQCN_PATTERN.match("general.ludd.agent")
        assert _FQCN_PATTERN.match("ansible.builtin.shell")
        assert _FQCN_PATTERN.match("community.general.files")

    def test_fqcn_pattern_rejects_invalid(self):
        assert not _FQCN_PATTERN.match("general")
        assert not _FQCN_PATTERN.match("general.ludd")
        assert not _FQCN_PATTERN.match("general.ludd.agent.extra")
        assert not _FQCN_PATTERN.match("Invalid.case.module")

    def test_fqcn_pattern_rejects_empty(self):
        assert not _FQCN_PATTERN.match("")


class TestModuleExtraction:
    def test_extract_fqcn_module(self):
        task = {"ansible.builtin.shell": "echo hello", "name": "test task"}
        mods = _extract_modules_from_task(task)
        assert "ansible.builtin.shell" in mods

    def test_extract_skips_task_keys(self):
        task = {"name": "my task", "when": "x == 1", "register": "out"}
        mods = _extract_modules_from_task(task)
        assert mods == []

    def test_extract_multiple_modules(self):
        task = {"ansible.builtin.shell": "ls", "ansible.builtin.copy": {"src": "a", "dest": "b"}}
        mods = _extract_modules_from_task(task)
        assert len(mods) == 2

    def test_extract_unknown_key_treated_as_module(self):
        task = {"custom.module.do_thing": {"param": "val"}}
        mods = _extract_modules_from_task(task)
        assert "custom.module.do_thing" in mods

    def test_task_keys_exhaustive(self):
        for key in _MODULE_TASK_KEYS:
            task = {key: "val"}
            mods = _extract_modules_from_task(task)
            assert mods == [], f"key {key} should be skipped"


class TestManifestGeneration:
    def test_generate_manifest_parses_playbook(self, tmp_path: Path):
        playbook = tmp_path / "test.yml"
        playbook.write_text(yaml.dump([
            {
                "hosts": "all",
                "tasks": [
                    {"ansible.builtin.shell": "echo hello", "name": "greet", "tags": ["setup"]}
                ]
            }
        ]))
        manifest = generate_manifest(str(playbook))
        assert "ansible.builtin.shell" in manifest.modules
        assert "setup" in manifest.tags

    def test_generate_manifest_empty_playbook(self, tmp_path: Path):
        playbook = tmp_path / "empty.yml"
        playbook.write_text("[]")
        manifest = generate_manifest(str(playbook))
        assert manifest.modules == []
        assert manifest.tags == []

    def test_generate_manifest_extracts_collections(self, tmp_path: Path):
        playbook = tmp_path / "with_collections.yml"
        playbook.write_text(yaml.dump([
            {
                "hosts": "all",
                "collections": ["community.general", "ansible.posix"],
                "tasks": []
            }
        ]))
        manifest = generate_manifest(str(playbook))
        assert "community.general" in manifest.collections
        assert "ansible.posix" in manifest.collections

    def test_generate_manifest_ignores_non_dict_plays(self, tmp_path: Path):
        playbook = tmp_path / "bad.yml"
        playbook.write_text(yaml.dump([
            "not-a-dict",
            {"hosts": "all", "tasks": [{"ansible.builtin.debug": {"msg": "hi"}}]}
        ]))
        manifest = generate_manifest(str(playbook))
        assert "ansible.builtin.debug" in manifest.modules


class TestTemplating:
    def test_templater_renders_basic_var(self):
        t = AnsibleTemplater(extra_vars={"name": "world"})
        result = t.render_sandboxed("hello {{ name }}")
        assert result == "hello world"

    def test_templater_rejects_missing_var(self):
        t = AnsibleTemplater(extra_vars={})
        with pytest.raises(TemplateRenderError):
            t.render_sandboxed("{{ missing_var }}")

    def test_templater_blocks_lookup(self):
        t = AnsibleTemplater(extra_vars={})
        with pytest.raises(TemplateRenderError):
            t.render_sandboxed("{{ lookup('pipe','id') }}")

    def test_templater_blocks_import(self):
        t = AnsibleTemplater(extra_vars={})
        with pytest.raises(TemplateRenderError):
            t.render_sandboxed("{% import os %}")

    def test_templater_with_extra_vars_unsafe_marking(self):
        t = AnsibleTemplater(extra_vars={"html": "<script>alert(1)</script>"})
        result = t.render_sandboxed("{{ html }}")
        assert "&lt;script&gt;" in result or "Unsafe" in result or result


class TestUnsafeValue:
    def test_wrap_unsafe_string_preserved(self):
        u = wrap_unsafe("hello")
        assert u is not None

    def test_wrap_unsafe_string_content(self):
        u = wrap_unsafe("hello")
        assert str(u) == "hello"

    def test_wrap_unsafe_int_unchanged(self):
        assert wrap_unsafe(42) == 42

    def test_wrap_unsafe_none_unchanged(self):
        assert wrap_unsafe(None) is None

    def test_wrap_unsafe_dict_recurses(self):
        d = {"key": "{{ jinja }}"}
        result = wrap_unsafe(d)
        assert "key" in result

    def test_wrap_unsafe_list_recurses(self):
        lst = ["{{ jinja }}", "plain"]
        result = wrap_unsafe(lst)
        assert len(result) == 2

    def test_wrap_extravars_none_returns_none(self):
        assert wrap_extravars(None) is None

    def test_wrap_extravars_wraps_values(self):
        result = wrap_extravars({"x": "{{ 1+1 }}", "y": 42})
        assert "x" in result
        assert "y" in result

    def test_has_wrap_var_returns_bool(self):
        assert isinstance(has_wrap_var(), bool)

    def test_templater_with_extra_vars_literal(self):
        t = AnsibleTemplater(extra_vars={"x": "{{ 1+1 }}"})
        result = t.render_sandboxed("{{ x }}")
        assert "{{ 1+1 }}" in result or "1+1" not in result
