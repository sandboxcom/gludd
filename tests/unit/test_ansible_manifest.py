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
