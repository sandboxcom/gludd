import os
import stat

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSystemdUnit:
    SERVICE_PATH = os.path.join(PROJECT_ROOT, "dist", "general-ludd.service")

    def test_service_file_exists(self):
        assert os.path.isfile(self.SERVICE_PATH)

    def test_service_file_has_exec_start(self):
        with open(self.SERVICE_PATH) as f:
            content = f.read()
        assert "ExecStart=/usr/local/bin/gludd daemon" in content

    def test_service_file_has_restart(self):
        with open(self.SERVICE_PATH) as f:
            content = f.read()
        assert "Restart=on-failure" in content

    def test_service_file_has_wantedby(self):
        with open(self.SERVICE_PATH) as f:
            content = f.read()
        assert "WantedBy=multi-user.target" in content

    def test_service_file_has_security_hardening(self):
        with open(self.SERVICE_PATH) as f:
            content = f.read()
        for directive in [
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "PrivateTmp=true",
        ]:
            assert directive in content

    def test_service_file_uses_gludd_daemon(self):
        with open(self.SERVICE_PATH) as f:
            content = f.read()
        assert "gludd daemon --host 0.0.0.0 --port 8000" in content


class TestInstallScript:
    SCRIPT_PATH = os.path.join(PROJECT_ROOT, "dist", "install.sh")

    def test_install_script_exists(self):
        assert os.path.isfile(self.SCRIPT_PATH)

    def test_install_script_is_executable(self):
        st = os.stat(self.SCRIPT_PATH)
        assert st.st_mode & stat.S_IXUSR

    def test_install_script_copies_binary(self):
        with open(self.SCRIPT_PATH) as f:
            content = f.read()
        assert "/usr/local/bin/gludd" in content
        assert "cp" in content or "install" in content

    def test_install_script_installs_systemd_unit(self):
        with open(self.SCRIPT_PATH) as f:
            content = f.read()
        assert "general-ludd.service" in content
        assert "systemd" in content or "systemctl" in content

    def test_install_script_creates_dirs(self):
        with open(self.SCRIPT_PATH) as f:
            content = f.read()
        assert "/var/log/general-ludd" in content
        assert "/var/lib/general-ludd" in content
        assert "/etc/general-ludd" in content

    def test_install_script_starts_service(self):
        with open(self.SCRIPT_PATH) as f:
            content = f.read()
        assert "systemctl enable" in content
        assert "systemctl start" in content

    def test_install_script_checks_root(self):
        with open(self.SCRIPT_PATH) as f:
            content = f.read()
        assert "root" in content.lower()
        assert "EUID" in content or "UID" in content or "id -u" in content

    def test_install_script_uses_set_e(self):
        with open(self.SCRIPT_PATH) as f:
            content = f.read()
        assert "set -e" in content


class TestTarballStructure:
    def test_makefile_has_dist_target(self):
        with open(os.path.join(PROJECT_ROOT, "Makefile")) as f:
            makefile = f.read()
        assert "\ndist:" in makefile

    def test_makefile_has_dist_clean_target(self):
        with open(os.path.join(PROJECT_ROOT, "Makefile")) as f:
            makefile = f.read()
        assert "dist-clean" in makefile

    def test_makefile_dist_builds_pyinstaller(self):
        with open(os.path.join(PROJECT_ROOT, "Makefile")) as f:
            makefile = f.read()
        dist_section = makefile.split("dist:")[1].split("\n\n")[0] if "dist:" in makefile else ""
        assert "pyinstaller" in dist_section or "build-executable" in dist_section

    def test_config_files_exist_for_tarball(self):
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "config", "binary_paths.yml"))
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "config", "model_routing.yml"))
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "config", "infra", "providers.yml"))
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "config", "ansible", "isolation.yml"))
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "config", "examples", "user_config_example.yml"))
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "config", "examples", "agent_config_example.yml"))
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "config", "tasks", "example_tasks.yml"))

    def test_template_files_exist_for_tarball(self):
        assert os.path.isfile(os.path.join(PROJECT_ROOT, "templates", "prompts", "return_review.md.j2"))

    def test_docs_directory_exists(self):
        assert os.path.isdir(os.path.join(PROJECT_ROOT, "docs"))
        md_files = [f for f in os.listdir(os.path.join(PROJECT_ROOT, "docs")) if f.endswith(".md")]
        assert len(md_files) > 0


class TestPyInstallerSpec:
    SPEC_PATH = os.path.join(PROJECT_ROOT, "gludd.spec")

    def test_spec_file_exists(self):
        assert os.path.isfile(self.SPEC_PATH)

    def test_spec_file_has_correct_entry(self):
        with open(self.SPEC_PATH) as f:
            content = f.read()
        assert "cli.py" in content
        assert "general_ludd" in content

    def test_spec_file_includes_config(self):
        with open(self.SPEC_PATH) as f:
            content = f.read()
        assert "('config'" in content

    def test_spec_file_includes_templates(self):
        with open(self.SPEC_PATH) as f:
            content = f.read()
        assert "('templates'" in content

    def test_spec_file_includes_playbooks(self):
        with open(self.SPEC_PATH) as f:
            content = f.read()
        assert "('playbooks'" in content

    def test_spec_file_has_hidden_imports(self):
        with open(self.SPEC_PATH) as f:
            content = f.read()
        assert "hiddenimports" in content
        assert "general_ludd.cli" in content

    def test_spec_file_excludes_dev_deps(self):
        with open(self.SPEC_PATH) as f:
            content = f.read()
        assert "pytest" in content
        assert "mypy" in content
