"""Tests for Ansible playbook manifest generator."""

from __future__ import annotations

import tempfile

import yaml

from general_ludd.ansible.manifest import _extract_modules_from_task, generate_manifest


class TestExtractModulesFromTask:
    def test_extracts_fqcn_module(self):
        task = {
            "name": "Install nginx",
            "community.general.apt": {"name": "nginx", "state": "present"},
        }
        modules = _extract_modules_from_task(task)
        assert "community.general.apt" in modules

    def test_extracts_fqcn_module_full_match(self):
        task = {
            "name": "Manage user",
            "community.general.user": {"name": "deploy", "state": "present"},
        }
        modules = _extract_modules_from_task(task)
        assert "community.general.user" in modules

    def test_ignores_non_module_keys(self):
        task = {
            "name": "Task",
            "when": "ansible_os_family == 'Debian'",
            "become": True,
            "tags": ["install"],
        }
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_extracts_shell_module(self):
        task = {"name": "Run command", "ansible.builtin.shell": "echo hello"}
        modules = _extract_modules_from_task(task)
        assert "ansible.builtin.shell" in modules

    def test_ignores_non_fqcn_module(self):
        task = {"name": "Install pkg", "apt": {"name": "nginx"}}
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_extracts_command_module(self):
        task = {"name": "Run", "ansible.builtin.command": "ls -la"}
        modules = _extract_modules_from_task(task)
        assert "ansible.builtin.command" in modules


class TestGenerateManifest:
    def test_generates_manifest_from_playbook(self):
        playbook = [
            {
                "hosts": "all",
                "roles": ["common", "web"],
                "tags": ["setup"],
                "tasks": [
                    {"name": "Install pkg", "ansible.builtin.apt": {"name": "nginx"}},
                    {
                        "name": "Start svc",
                        "ansible.builtin.systemd": {
                            "name": "nginx",
                            "state": "started",
                        },
                        "tags": ["service"],
                    },
                ],
                "collections": ["community.general"],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)

        assert manifest.playbook.endswith(".yml")
        assert "common" in manifest.roles
        assert "web" in manifest.roles
        assert "community.general" in manifest.collections
        assert "setup" in manifest.tags
        assert "service" in manifest.tags
        assert "ansible.builtin.apt" in manifest.modules
        assert "ansible.builtin.systemd" in manifest.modules

    def test_handles_dict_role_entry(self):
        playbook = [
            {
                "hosts": "all",
                "roles": [{"role": "nginx"}],
                "tasks": [],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)

        assert "nginx" in manifest.roles

    def test_handles_empty_playbook(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump([], f)
            f.flush()
            manifest = generate_manifest(f.name)

        assert manifest.roles == []
        assert manifest.modules == []
        assert manifest.tags == []

    def test_skips_non_dict_plays(self):
        playbook = ["not a dict", {"hosts": "all", "tasks": []}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)

        assert manifest.roles == []

    def test_deduplicates_modules_and_tags(self):
        playbook = [
            {
                "hosts": "all",
                "tags": ["setup"],
                "tasks": [
                    {"name": "T1", "ansible.builtin.apt": {"name": "pkg1"}, "tags": ["setup"]},
                    {"name": "T2", "ansible.builtin.apt": {"name": "pkg2"}, "tags": ["deploy"]},
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)

        assert manifest.modules.count("ansible.builtin.apt") == 1
        assert manifest.tags.count("setup") == 1


class TestExtractModulesFromTaskEdgeCases:
    def test_ignores_block_key(self):
        task = {"block": [{"ansible.builtin.copy": {"src": "a", "dest": "b"}}]}
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_ignores_rescue_key(self):
        task = {"rescue": [{"ansible.builtin.shell": "echo fail"}]}
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_ignores_always_key(self):
        task = {"always": [{"ansible.builtin.debug": {"msg": "cleanup"}}]}
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_ignores_become_key(self):
        task = {"become": True, "ansible.builtin.copy": {"src": "a", "dest": "b"}}
        modules = _extract_modules_from_task(task)
        assert "ansible.builtin.copy" in modules
        assert "become" not in modules

    def test_ignores_delegate_to_key(self):
        task = {"delegate_to": "other-host", "ansible.builtin.command": "uptime"}
        modules = _extract_modules_from_task(task)
        assert "ansible.builtin.command" in modules
        assert "delegate_to" not in modules

    def test_ignores_loop_keywords(self):
        for keyword in ("loop", "with_items", "with_dict"):
            task = {keyword: ["item1", "item2"], "ansible.builtin.debug": {"msg": "{{ item }}"}}
            modules = _extract_modules_from_task(task)
            assert "ansible.builtin.debug" in modules

    def test_ignores_notify_key(self):
        task = {"notify": "restart nginx", "ansible.builtin.template": {"src": "a.j2", "dest": "b"}}
        modules = _extract_modules_from_task(task)
        assert "ansible.builtin.template" in modules

    def test_ignores_vars_key(self):
        task = {"vars": {"port": 8080}, "ansible.builtin.uri": {"url": "http://localhost"}}
        modules = _extract_modules_from_task(task)
        assert "ansible.builtin.uri" in modules
        assert "vars" not in modules

    def test_fqcn_pattern_without_enough_segments_ignored(self):
        task = {"community.general": {"name": "nginx"}}
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_fqcn_pattern_with_caps_ignored(self):
        task = {"Community.General.apt": {"name": "nginx"}}
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_multiple_modules_in_one_task(self):
        task = {
            "ansible.builtin.copy": {"src": "a", "dest": "b"},
            "ansible.builtin.template": {"src": "c.j2", "dest": "d"},
            "name": "multi-action task",
        }
        modules = _extract_modules_from_task(task)
        assert sorted(modules) == ["ansible.builtin.copy", "ansible.builtin.template"]

    def test_fqcn_like_key_without_enough_chars_per_segment(self):
        task = {"a.b.c": "some string value, not a dict"}
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_fqcn_key_with_string_value_still_captured(self):
        task = {"aa.bb.cc": "some string value, not a dict"}
        modules = _extract_modules_from_task(task)
        assert "aa.bb.cc" in modules


class TestGenerateManifestEdgeCases:
    def test_skips_non_dict_tasks(self):
        playbook = [{"hosts": "all", "tasks": ["not a dict task", {"ansible.builtin.debug": {"msg": "hi"}}]}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)
        assert len(manifest.modules) == 1
        assert "ansible.builtin.debug" in manifest.modules

    def test_tasks_not_a_list_is_skipped(self):
        playbook = [{"hosts": "all", "tasks": {"ansible.builtin.shell": "uptime"}}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)
        assert manifest.modules == []

    def test_role_dict_without_role_key_skipped(self):
        playbook = [{"hosts": "all", "roles": [{"name": "nginx"}], "tasks": []}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)
        assert "nginx" not in manifest.roles

    def test_roles_not_a_list_handled(self):
        playbook = [{"hosts": "all", "roles": "common", "tasks": []}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)
        assert "common" not in manifest.roles

    def test_collections_not_a_list_handled(self):
        playbook = [{"hosts": "all", "collections": "community.general", "tasks": []}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)
        assert "community.general" not in manifest.collections

    def test_nested_block_tasks_extract_modules(self):
        playbook = [
            {
                "hosts": "all",
                "tasks": [
                    {
                        "block": [
                            {"ansible.builtin.copy": {"src": "a", "dest": "b"}},
                            {"ansible.builtin.template": {"src": "c.j2", "dest": "d"}},
                        ]
                    }
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)
        assert len(manifest.modules) == 0

    def test_role_mixed_string_and_dict_entries(self):
        playbook = [{"hosts": "all", "roles": ["common", {"role": "nginx", "vars": {"port": 80}}], "tasks": []}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)
        assert "common" in manifest.roles
        assert "nginx" in manifest.roles

    def test_tags_from_tasks_not_duplicated_with_play_tags(self):
        playbook = [
            {
                "hosts": "all",
                "tags": ["setup", "deploy"],
                "tasks": [
                    {"tags": ["setup"], "ansible.builtin.debug": {"msg": "hi"}},
                    {"tags": ["deploy"], "ansible.builtin.shell": "uptime"},
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)
        assert sorted(manifest.tags) == ["deploy", "setup"]

    def test_multiple_plays_aggregate_correctly(self):
        playbook = [
            {"hosts": "web", "roles": ["common"], "tasks": [{"ansible.builtin.copy": {"src": "a", "dest": "b"}}]},
            {
                "hosts": "db",
                "roles": ["postgres"],
                "tasks": [{"ansible.builtin.template": {"src": "c.j2", "dest": "d"}}],
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(playbook, f)
            f.flush()
            manifest = generate_manifest(f.name)
        assert sorted(manifest.roles) == ["common", "postgres"]
        assert sorted(manifest.modules) == ["ansible.builtin.copy", "ansible.builtin.template"]
