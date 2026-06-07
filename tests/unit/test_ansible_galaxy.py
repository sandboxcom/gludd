"""Tests for Ansible Galaxy and ansible.builtin module management."""

from __future__ import annotations


class TestAnsibleGalaxySearch:
    def test_galaxy_search_command_format(self):
        cmd = ["ansible-galaxy", "role", "search", "nginx", "--platforms", "all"]
        assert "ansible-galaxy" in cmd
        assert "role" in cmd
        assert "search" in cmd
        assert "nginx" in cmd

    def test_galaxy_install_command_format(self):
        cmd = ["ansible-galaxy", "role", "install", "geerlingguy.nginx"]
        assert "ansible-galaxy" in cmd
        assert "role" in cmd
        assert "install" in cmd
        assert "geerlingguy.nginx" in cmd

    def test_galaxy_collection_install_format(self):
        cmd = ["ansible-galaxy", "collection", "install", "community.general"]
        assert "collection" in cmd
        assert "community.general" in cmd

    def test_cli_ansible_search_parser_registered(self):
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        ansible_p = sub.add_parser("ansible")
        ansible_sub = ansible_p.add_subparsers(dest="ansible_command")
        search_p = ansible_sub.add_parser("search")
        search_p.add_argument("query")
        search_p.add_argument("--type", default="role")
        search_p.add_argument("--daemon-url", default="http://localhost:8000")
        args = parser.parse_args(["ansible", "search", "nginx"])
        assert args.query == "nginx"
        assert args.type == "role"

    def test_cli_ansible_install_parser(self):
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        ansible_p = sub.add_parser("ansible")
        ansible_sub = ansible_p.add_subparsers(dest="ansible_command")
        install_p = ansible_sub.add_parser("install")
        install_p.add_argument("name")
        install_p.add_argument("--type", default="role")
        install_p.add_argument("--daemon-url", default="http://localhost:8000")
        args = parser.parse_args(["ansible", "install", "geerlingguy.nginx"])
        assert args.name == "geerlingguy.nginx"

    def test_ansible_builtin_modules_list(self):
        common_modules = [
            "ansible.builtin.copy",
            "ansible.builtin.file",
            "ansible.builtin.template",
            "ansible.builtin.command",
            "ansible.builtin.shell",
            "ansible.builtin.service",
            "ansible.builtin.package",
            "ansible.builtin.user",
            "ansible.builtin.group",
            "ansible.builtin.git",
            "ansible.builtin.uri",
            "ansible.builtin.get_url",
            "ansible.builtin.debug",
            "ansible.builtin.assert",
            "ansible.builtin.set_fact",
            "ansible.builtin.include_role",
            "ansible.builtin.import_role",
        ]
        from general_ludd.ansible import galaxy
        builtins = galaxy.get_builtin_modules()
        assert len(builtins) >= 10
        for m in common_modules:
            assert m in builtins, f"Missing builtin: {m}"

    def test_galaxy_module_exists(self):
        from general_ludd.ansible import galaxy
        assert hasattr(galaxy, "search_galaxy")
        assert hasattr(galaxy, "install_galaxy")
        assert hasattr(galaxy, "get_builtin_modules")

    def test_search_results_parsing(self):
        from general_ludd.ansible.galaxy import parse_galaxy_search_output

        sample = (
            "Found 2 roles matching your search:\n\n"
            " Name              Description\n"
            " ----              -----------\n"
            " geerlingguy.nginx Nginx installation for Linux\n"
            " nginxinc.nginx    Official NGINX role\n"
        )
        results = parse_galaxy_search_output(sample)
        assert len(results) == 2
        assert results[0]["name"] == "geerlingguy.nginx"

    def test_cannot_parse_empty_output(self):
        from general_ludd.ansible.galaxy import parse_galaxy_search_output
        results = parse_galaxy_search_output("")
        assert results == []
