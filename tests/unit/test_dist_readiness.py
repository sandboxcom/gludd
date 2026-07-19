from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _require_dist() -> None:
    if not (REPO_ROOT / "dist").is_dir() or not list((REPO_ROOT / "dist").iterdir()):
        pytest.skip("dist/ directory is empty or missing")
    if not (REPO_ROOT / "dist" / "README.md").exists() or not (REPO_ROOT / "dist" / "general-ludd.service").exists():
        pytest.skip("dist/ is not a full distribution (run make dist to generate)")


class TestDistributionConfig:
    def test_model_routing_has_non_null_default(self):
        path = REPO_ROOT / "config" / "model_routing.yml"
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["default_profile"] is not None, "model_routing.yml default_profile must not be null"

    def test_default_agents_have_model_profiles(self):
        path = REPO_ROOT / "config" / "agents" / "default_agents.yml"
        with open(path) as f:
            data = yaml.safe_load(f)
        for agent in data["agents"]:
            assert agent["model_profile"] is not None, f"agent '{agent['name']}' must have a non-null model_profile"

    def test_general_ludd_yml_exists_and_valid(self):
        path = REPO_ROOT / "config" / "general-ludd.yml"
        assert path.exists(), "config/general-ludd.yml must exist as the main user-facing config"
        with open(path) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict)
        assert "model_routing" in data
        assert data["model_routing"].get("default_profile") is not None

    def test_user_config_example_references_real_profiles(self):
        path = REPO_ROOT / "config" / "examples" / "user_config_example.yml"
        with open(path) as f:
            data = yaml.safe_load(f)
        if data.get("model_routing"):
            profile = data["model_routing"].get("default_profile")
            assert profile is not None, "user_config_example must reference a real default_profile"


class TestUserFacingDocs:
    def test_quickstart_doc_exists(self):
        assert (REPO_ROOT / "docs" / "quickstart.md").exists(), "docs/quickstart.md must exist"

    def test_configuration_doc_exists(self):
        assert (REPO_ROOT / "docs" / "configuration.md").exists(), "docs/configuration.md must exist"

    def test_architecture_doc_exists(self):
        assert (REPO_ROOT / "docs" / "architecture.md").exists(), "docs/architecture.md must exist"

    def test_readme_in_dist_dir(self):
        _require_dist()
        assert (REPO_ROOT / "dist" / "README.md").exists(), "dist/README.md must exist as the tarball readme"


class TestInstallScript:
    def test_install_script_exists(self):
        _require_dist()
        assert (REPO_ROOT / "dist" / "install.sh").exists()

    def test_install_script_does_not_auto_start(self):
        _require_dist()
        content = (REPO_ROOT / "dist" / "install.sh").read_text()
        lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        for line in lines:
            is_echo = line.startswith("echo ")
            assert is_echo or "systemctl start" not in line
            assert is_echo or "systemctl enable" not in line

    def test_install_script_has_preflight(self):
        _require_dist()
        content = (REPO_ROOT / "dist" / "install.sh").read_text()
        has_preflight = (
            "preflight" in content.lower() or "pre-flight" in content.lower()
        )
        assert has_preflight

    def test_install_script_mentions_config_setup(self):
        _require_dist()
        content = (REPO_ROOT / "dist" / "install.sh").read_text()
        assert "general-ludd.yml" in content, "install.sh must mention the main config file"


class TestSystemdUnit:
    def test_systemd_unit_has_env_file(self):
        _require_dist()
        content = (REPO_ROOT / "dist" / "general-ludd.service").read_text()
        assert "EnvironmentFile" in content, "systemd unit must have EnvironmentFile directive"

    def test_systemd_unit_runs_as_dedicated_user(self):
        _require_dist()
        content = (REPO_ROOT / "dist" / "general-ludd.service").read_text()
        assert "User=" in content, "systemd unit must run as a dedicated user"

    def test_systemd_unit_does_not_protect_home(self):
        _require_dist()
        content = (REPO_ROOT / "dist" / "general-ludd.service").read_text()
        assert "ProtectHome=true" not in content, "ProtectHome=true blocks reading ~/.config/general-ludd"

    def test_systemd_unit_binds_localhost(self):
        _require_dist()
        content = (REPO_ROOT / "dist" / "general-ludd.service").read_text()
        assert "127.0.0.1" in content, "systemd unit must bind to localhost, not 0.0.0.0"
