import os
import shutil
import stat
import subprocess

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SERVICE_PATH = os.path.join(PROJECT_ROOT, "dist", "general-ludd.service")
_INSTALL_SCRIPT_PATH = os.path.join(PROJECT_ROOT, "dist", "install.sh")

needs_dist_service = pytest.mark.skipif(
    not os.path.isfile(_SERVICE_PATH), reason="dist/general-ludd.service not generated"
)
needs_dist_install_script = pytest.mark.skipif(
    not os.path.isfile(_INSTALL_SCRIPT_PATH), reason="dist/install.sh not generated"
)


@pytest.mark.skipif(not os.path.isfile(_SERVICE_PATH), reason="dist/general-ludd.service not generated")
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
            "PrivateTmp=true",
        ]:
            assert directive in content

    def test_service_file_uses_gludd_daemon(self):
        with open(self.SERVICE_PATH) as f:
            content = f.read()
        assert "gludd daemon --host 127.0.0.1 --port 8000" in content


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(PROJECT_ROOT, "dist", "install.sh")),
    reason="dist/install.sh not generated",
)
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
        assert 'INSTALL_DIR="${1:-/usr/local/bin}"' in content
        assert '"${INSTALL_DIR}/gludd"' in content
        assert "cp" in content or "install" in content

    def test_install_script_is_portable_not_systemd_specific(self):
        with open(self.SCRIPT_PATH) as f:
            content = f.read()
        assert "general-ludd.service" not in content
        assert "systemctl" not in content

    def test_install_script_does_not_create_system_state_dirs(self):
        with open(self.SCRIPT_PATH) as f:
            content = f.read()
        assert "/var/log/general-ludd" not in content
        assert "/var/lib/general-ludd" not in content
        assert "/etc/general-ludd" not in content

    def test_install_script_does_not_start_service(self):
        with open(self.SCRIPT_PATH) as f:
            content = f.read()
        assert "systemctl" not in content
        assert "daemon-reload" not in content

    def test_install_script_does_not_require_root(self):
        with open(self.SCRIPT_PATH) as f:
            content = f.read()
        assert "EUID" not in content
        assert "id -u" not in content

    def test_install_script_installs_into_custom_directory(self, tmp_path):
        bundle_dir = tmp_path / "bundle"
        install_dir = tmp_path / "bin"
        bundle_dir.mkdir()
        install_dir.mkdir()
        script = bundle_dir / "install.sh"
        shutil.copy2(self.SCRIPT_PATH, script)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        source_binary = bundle_dir / "gludd"
        source_binary.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")

        result = subprocess.run(
            [str(script), str(install_dir)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        installed = install_dir / "gludd"
        assert result.returncode == 0, result.stderr
        assert installed.read_bytes() == source_binary.read_bytes()
        assert installed.stat().st_mode & stat.S_IXUSR

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
        # Anchor on the real line-start "dist:" target, not any substring like
        # "test-xdist:" (otherwise an unrelated target whose name ends in "dist"
        # is parsed as the dist recipe).
        dist_section = makefile.split("\ndist:")[1].split("\n\n")[0] if "\ndist:" in makefile else ""
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
