"""Deep tests for manifest.py — FQCN pattern edge cases and recursive task extraction."""

from __future__ import annotations

import yaml

from general_ludd.ansible.manifest import _FQCN_PATTERN, _extract_modules_from_task, generate_manifest


class TestFQCNPattern:
    def test_accepts_three_segment_lowercase(self):
        assert _FQCN_PATTERN.match("community.general.apt")

    def test_accepts_segments_with_digits(self):
        assert _FQCN_PATTERN.match("a1.b2.c3")

    def test_accepts_segments_with_underscore(self):
        assert _FQCN_PATTERN.match("my_namespace.my_collection.my_module")

    def test_rejects_two_segments(self):
        assert not _FQCN_PATTERN.match("namespace.collection")

    def test_rejects_four_segments(self):
        assert not _FQCN_PATTERN.match("a.b.c.d")

    def test_rejects_single_segment(self):
        assert not _FQCN_PATTERN.match("module")

    def test_rejects_empty_string(self):
        assert not _FQCN_PATTERN.match("")

    def test_rejects_leading_digit(self):
        assert not _FQCN_PATTERN.match("1community.general.apt")

    def test_rejects_leading_underscore(self):
        assert not _FQCN_PATTERN.match("_community.general.apt")

    def test_rejects_uppercase_segment(self):
        assert not _FQCN_PATTERN.match("Community.General.Apt")

    def test_rejects_mixed_case(self):
        assert not _FQCN_PATTERN.match("community.General.apt")

    def test_rejects_hyphen_in_segment(self):
        assert not _FQCN_PATTERN.match("my-collection.general.apt")

    def test_rejects_space_in_fqcn(self):
        assert not _FQCN_PATTERN.match("a b.c d.e f")

    def test_accepts_builtin_form(self):
        assert _FQCN_PATTERN.match("ansible.builtin.copy")

    def test_accepts_builtin_shell(self):
        assert _FQCN_PATTERN.match("ansible.builtin.shell")

    def test_accepts_builtin_command(self):
        assert _FQCN_PATTERN.match("ansible.builtin.command")

    def test_rejects_dot_only(self):
        assert not _FQCN_PATTERN.match("..")

    def test_rejects_trailing_dot(self):
        assert not _FQCN_PATTERN.match("a.b.")

    def test_rejects_leading_dot(self):
        assert not _FQCN_PATTERN.match(".a.b")

    def test_accepts_minimum_valid_length(self):
        assert _FQCN_PATTERN.match("aa.bb.cc")

    def test_rejects_newline(self):
        assert not _FQCN_PATTERN.match("a.b\n.c")


class TestExtractModulesRecursion:
    def test_block_tasks_not_extracted_due_to_key_check(self):
        task = {
            "block": [
                {"community.general.apt": {"name": "pkg"}},
            ]
        }
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_rescue_not_extracted(self):
        task = {
            "rescue": [
                {"ansible.builtin.shell": "echo fail"},
            ]
        }
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_always_not_extracted(self):
        task = {
            "always": [
                {"ansible.builtin.debug": {"msg": "cleanup"}},
            ]
        }
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_module_inside_rescue_not_found(self):
        task = {
            "block": [
                {"ansible.builtin.command": "do-thing"},
            ],
            "rescue": [
                {"ansible.builtin.shell": "recover"},
            ],
            "name": "block-task",
        }
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_module_inside_always_not_found(self):
        task = {
            "block": [
                {"ansible.builtin.command": "do-thing"},
            ],
            "always": [
                {"ansible.builtin.debug": {"msg": "done"}},
            ],
            "name": "block-task",
        }
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_ansible_builtin_shell_without_fqcn_prefix(self):
        task = {"shell": "echo hello"}
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_ansible_builtin_command_without_fqcn_prefix(self):
        task = {"command": "ls -la"}
        modules = _extract_modules_from_task(task)
        assert modules == []

    def test_module_key_with_empty_dict_value(self):
        task = {"community.general.apt": {}}
        modules = _extract_modules_from_task(task)
        assert "community.general.apt" in modules

    def test_module_key_with_none_value(self):
        task = {"community.general.apt": None}
        modules = _extract_modules_from_task(task)
        assert "community.general.apt" in modules


class TestGenerateManifestExtraEdgeCases:
    def test_empty_yaml_file(self, tmp_path):
        playbook = tmp_path / "empty.yml"
        playbook.write_text("")
        manifest = generate_manifest(str(playbook))
        assert manifest.roles == []
        assert manifest.modules == []
        assert manifest.tags == []
        assert manifest.collections == []

    def test_null_top_level_yaml(self, tmp_path):
        playbook = tmp_path / "null.yml"
        playbook.write_text("null")
        manifest = generate_manifest(str(playbook))
        assert manifest.roles == []

    def test_play_with_every_field_set(self, tmp_path):
        playbook_yaml = [
            {
                "hosts": "all",
                "name": "Full play",
                "roles": [
                    "common",
                    {"role": "nginx", "vars": {"port": 80}},
                    {"role": "postgres", "tags": ["db"]},
                ],
                "tags": ["setup", "production"],
                "tasks": [
                    {
                        "name": "Install nginx",
                        "ansible.builtin.apt": {"name": "nginx", "state": "present"},
                        "tags": ["install"],
                    },
                    {
                        "name": "Deploy config",
                        "ansible.builtin.template": {"src": "nginx.conf.j2", "dest": "/etc/nginx/nginx.conf"},
                        "become": True,
                    },
                ],
                "collections": ["community.general", "ansible.posix"],
            }
        ]
        p = tmp_path / "full.yml"
        p.write_text(yaml.dump(playbook_yaml))
        manifest = generate_manifest(str(p))
        assert sorted(manifest.roles) == ["common", "nginx", "postgres"]
        assert sorted(manifest.tags) == ["install", "production", "setup"]
        assert sorted(manifest.collections) == ["ansible.posix", "community.general"]
        assert "ansible.builtin.apt" in manifest.modules
        assert "ansible.builtin.template" in manifest.modules

    def test_role_dict_with_additional_keys_mixed(self, tmp_path):
        playbook_yaml = [
            {
                "hosts": "all",
                "roles": [
                    {"role": "common"},
                    "webserver",
                    {"role": "monitoring", "tags": ["monitor"]},
                ],
                "tasks": [],
            }
        ]
        p = tmp_path / "mixed.yml"
        p.write_text(yaml.dump(playbook_yaml))
        manifest = generate_manifest(str(p))
        assert sorted(manifest.roles) == ["common", "monitoring", "webserver"]

    def test_task_tags_not_list_handled(self, tmp_path):
        playbook_yaml = [
            {
                "hosts": "all",
                "tasks": [
                    {"name": "T1", "ansible.builtin.debug": {"msg": "hi"}, "tags": "not-a-list"},
                ],
            }
        ]
        p = tmp_path / "bad-tags.yml"
        p.write_text(yaml.dump(playbook_yaml))
        manifest = generate_manifest(str(p))
        assert manifest.tags == []

    def test_play_tags_not_list_handled(self, tmp_path):
        playbook_yaml = [
            {
                "hosts": "all",
                "tags": "not-a-list",
                "tasks": [{"ansible.builtin.debug": {"msg": "hi"}}],
            }
        ]
        p = tmp_path / "bad-play-tags.yml"
        p.write_text(yaml.dump(playbook_yaml))
        manifest = generate_manifest(str(p))
        assert manifest.tags == []

    def test_roles_not_list_but_dict(self, tmp_path):
        playbook_yaml = [
            {
                "hosts": "all",
                "roles": {"role": "common"},
                "tasks": [],
            }
        ]
        p = tmp_path / "dict-roles.yml"
        p.write_text(yaml.dump(playbook_yaml))
        manifest = generate_manifest(str(p))
        assert "common" not in manifest.roles

    def test_deeply_nested_block_tasks_not_extracted(self, tmp_path):
        playbook_yaml = [
            {
                "hosts": "all",
                "tasks": [
                    {
                        "block": [
                            {
                                "block": [
                                    {"ansible.builtin.copy": {"src": "a", "dest": "b"}},
                                ]
                            }
                        ]
                    }
                ],
            }
        ]
        p = tmp_path / "nested.yml"
        p.write_text(yaml.dump(playbook_yaml))
        manifest = generate_manifest(str(p))
        assert manifest.modules == []

    def test_manifest_playbook_name_matches_file(self, tmp_path):
        p = tmp_path / "deploy-app.yml"
        p.write_text(yaml.dump([{"hosts": "all", "tasks": []}]))
        manifest = generate_manifest(str(p))
        assert manifest.playbook == "deploy-app.yml"

    def test_manifest_from_subdirectory_path(self, tmp_path):
        sub = tmp_path / "plays"
        sub.mkdir()
        p = sub / "site.yml"
        p.write_text(yaml.dump([{"hosts": "all", "tasks": []}]))
        manifest = generate_manifest(str(p))
        assert manifest.playbook == "site.yml"
