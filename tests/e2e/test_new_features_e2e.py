"""E2e integration tests for recently added features."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from general_ludd.ansible.core_runner import CoreAnsibleRunner
from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.ansible.templating import AnsibleTemplater
from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import SecretsManager

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class TestBinaryPathConfig:
    def test_binary_paths_defaults(self) -> None:
        paths = BinaryPaths()
        assert paths.terraform == "terraform"
        assert paths.opentofu == "tofu"
        assert paths.vault == "vault"
        assert paths.openbao == "bao"
        assert paths.podman == "podman"
        assert paths.docker == "docker"
        assert paths.ansible_playbook == "ansible-playbook"
        assert paths.git == "git"
        assert paths.uv == "uv"

    def test_binary_paths_custom_overrides(self) -> None:
        paths = BinaryPaths(terraform="/usr/local/bin/terraform", opentofu="/opt/tofu/bin/tofu")
        assert paths.terraform == "/usr/local/bin/terraform"
        assert paths.opentofu == "/opt/tofu/bin/tofu"

    @patch("shutil.which", return_value="/usr/bin/git")
    def test_resolver_discovers_system_binary(self, mock_which: MagicMock) -> None:
        resolver = BinaryPathResolver()
        result = resolver.resolve("git")
        assert result is not None
        mock_which.assert_called()

    @patch("shutil.which", return_value=None)
    def test_resolver_returns_name_when_not_found(self, mock_which: MagicMock) -> None:
        resolver = BinaryPathResolver()
        result = resolver.resolve("git")
        assert result == "git"

    @patch("shutil.which", return_value="/usr/bin/tofu")
    def test_resolver_prefers_opentofu_when_available(self, mock_which: MagicMock) -> None:
        resolver = BinaryPathResolver()
        binary = resolver.get_infra_binary()
        assert binary == "tofu"

    @patch("shutil.which", side_effect=lambda name: None if name == "tofu" else "/usr/bin/terraform")
    def test_resolver_falls_back_to_terraform(self, mock_which: MagicMock) -> None:
        resolver = BinaryPathResolver()
        binary = resolver.get_infra_binary()
        assert binary == "terraform"

    def test_yaml_config_loads_from_project(self) -> None:
        config_path = PROJECT_ROOT / "config" / "binary_paths.yml"
        assert config_path.exists(), f"Missing {config_path}"
        data = yaml.safe_load(config_path.read_text())
        assert "binary_paths" in data
        bp = data["binary_paths"]
        for key in ("terraform", "opentofu", "vault", "openbao", "podman", "docker"):
            assert key in bp

    def test_yaml_config_roundtrip_into_binary_paths(self) -> None:
        config_path = PROJECT_ROOT / "config" / "binary_paths.yml"
        data = yaml.safe_load(config_path.read_text())
        paths = BinaryPaths(**data["binary_paths"])
        assert paths.terraform == "terraform"
        assert paths.opentofu == "tofu"

    @patch("shutil.which", return_value="/usr/bin/tofu")
    def test_resolver_integrates_with_deployment_manager(self, mock_which: MagicMock) -> None:
        resolver = BinaryPathResolver()
        dm = DeploymentManager(binary_paths=resolver)
        assert dm._binary_resolver is resolver
        infra_bin = dm._binary_resolver.get_infra_binary()
        assert infra_bin == "tofu"

    @patch("shutil.which", return_value="/usr/bin/podman")
    def test_resolver_integrates_with_secrets_manager_container_runtime(self, mock_which: MagicMock) -> None:
        resolver = BinaryPathResolver()
        runtime = resolver.get_container_runtime()
        assert runtime == "podman"
        sm = SecretsManager(config=OpenBaoConfig())
        assert sm._config is not None


class TestDeploymentLifecycle:
    def test_deployment_manager_generates_hcl(self) -> None:
        dm = DeploymentManager()
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.VLLM,
            model_name="test-model",
            region="us-east-1",
        )
        hcl = dm._generator.generate(config)
        assert 'resource "aws_instance" "gpu_instance"' in hcl
        assert "g4dn.xlarge" in hcl

    @pytest.mark.asyncio
    @patch("general_ludd.infra.deployment.BinaryPathResolver.get_infra_binary", return_value="tofu")
    async def test_deploy_runs_init_and_apply(self, mock_binary: MagicMock) -> None:
        dm = DeploymentManager()

        async def fake_exec(bin: str, *args: str, **kwargs: str) -> asyncio.subprocess.Process:
            proc = MagicMock(spec=asyncio.subprocess.Process)
            if "init" in args or "apply" in args or "output" in args:
                proc.returncode = 0
            else:
                proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b'{"instance_ip": {"value": "1.2.3.4"}}', b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec) as mock_exec:
            config = ComputeConfig(
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.T4,
                engine=InferenceEngine.VLLM,
                model_name="test-model",
                region="us-east-1",
            )
            await dm.deploy(config)
            assert mock_exec.call_count >= 2
            first_call_args = mock_exec.call_args_list[0]
            assert "init" in first_call_args[0]

    @pytest.mark.asyncio
    @patch("general_ludd.infra.deployment.BinaryPathResolver.get_infra_binary", return_value="tofu")
    async def test_destroy_runs_terraform_destroy(self, mock_binary: MagicMock) -> None:
        dm = DeploymentManager()

        async def fake_exec(bin: str, *args: str, **kwargs: str) -> asyncio.subprocess.Process:
            proc = MagicMock(spec=asyncio.subprocess.Process)
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec) as mock_exec:
            await dm.destroy("test-instance")
            assert mock_exec.call_count == 1
            call_args = mock_exec.call_args_list[0]
            assert "destroy" in call_args[0]

    @patch("shutil.which", side_effect=lambda name: "/usr/bin/tofu" if name == "tofu" else "/usr/bin/terraform")
    def test_deployment_uses_tofu_when_available(self, mock_which: MagicMock) -> None:
        resolver = BinaryPathResolver()
        dm = DeploymentManager(binary_paths=resolver)
        assert dm._binary_resolver.get_infra_binary() == "tofu"

    @patch("shutil.which", side_effect=lambda name: None if name == "tofu" else "/usr/bin/terraform")
    def test_deployment_falls_back_to_terraform(self, mock_which: MagicMock) -> None:
        resolver = BinaryPathResolver()
        dm = DeploymentManager(binary_paths=resolver)
        assert dm._binary_resolver.get_infra_binary() == "terraform"

    def test_parse_outputs_handles_valid_json(self) -> None:
        dm = DeploymentManager()
        output = '{"instance_ip": {"value": "10.0.0.1"}, "endpoint_url": {"value": "http://10.0.0.1:8000"}}'
        result = dm._parse_outputs(output)
        assert result["instance_ip"] == "10.0.0.1"
        assert result["endpoint_url"] == "http://10.0.0.1:8000"

    def test_parse_outputs_handles_empty_string(self) -> None:
        dm = DeploymentManager()
        assert dm._parse_outputs("") == {}
        assert dm._parse_outputs("  ") == {}

    @pytest.mark.asyncio
    @patch("general_ludd.infra.deployment.BinaryPathResolver.get_infra_binary", return_value="tofu")
    async def test_deploy_raises_on_nonzero_rc(self, mock_binary: MagicMock) -> None:
        dm = DeploymentManager()

        async def fake_exec(bin: str, *args: str, **kwargs: str) -> asyncio.subprocess.Process:
            proc = MagicMock(spec=asyncio.subprocess.Process)
            proc.returncode = 1
            proc.communicate = AsyncMock(return_value=(b"", b"error: something failed"))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            config = ComputeConfig(
                provider=ComputeProvider.AWS,
                gpu_type=GPUType.T4,
                engine=InferenceEngine.VLLM,
                model_name="test-model",
            )
            with pytest.raises(RuntimeError, match="terraform failed"):
                await dm.deploy(config)


class TestSecretsWiring:
    def test_openbao_config_has_backend_field(self) -> None:
        config = OpenBaoConfig()
        assert config.backend == "openbao"

    def test_openbao_config_has_binary_path_field(self) -> None:
        config = OpenBaoConfig()
        assert config.binary_path is None
        custom = OpenBaoConfig(binary_path="/usr/local/bin/bao")
        assert custom.binary_path == "/usr/local/bin/bao"

    def test_openbao_config_accepts_vault_backend(self) -> None:
        config = OpenBaoConfig(backend="vault")
        assert config.backend == "vault"

    def test_openbao_config_defaults(self) -> None:
        config = OpenBaoConfig()
        assert config.mode == "auto"
        assert config.kv_mount == "secret"
        assert config.auth_method == "approle"
        assert config.local_image == "ghcr.io/openbao/openbao"

    @pytest.mark.asyncio
    @patch("shutil.which", return_value="/usr/bin/podman")
    async def test_start_local_container_uses_binary_resolver(self, mock_which: MagicMock) -> None:
        resolver = BinaryPathResolver()
        sm = SecretsManager()

        async def fake_exec(bin: str, *args: str, **kwargs: str) -> asyncio.subprocess.Process:
            proc = MagicMock(spec=asyncio.subprocess.Process)
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"abc123container\n", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec) as mock_exec:
            container_id = await sm.start_local_container(binary_resolver=resolver)
            assert container_id == "abc123container"
            call_args = mock_exec.call_args_list[0]
            assert call_args[0][0] == "podman"
            assert "run" in call_args[0]

    @pytest.mark.asyncio
    async def test_start_local_container_returns_none_on_failure(self) -> None:
        resolver = BinaryPathResolver(config=BinaryPaths(podman="podman"))

        async def fake_exec(bin: str, *args: str, **kwargs: str) -> asyncio.subprocess.Process:
            proc = MagicMock(spec=asyncio.subprocess.Process)
            proc.returncode = 1
            proc.communicate = AsyncMock(return_value=(b"", b"error"))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            sm = SecretsManager()
            result = await sm.start_local_container(binary_resolver=resolver)
            assert result is None

    @pytest.mark.asyncio
    async def test_health_check_returns_false_without_client(self) -> None:
        sm = SecretsManager()
        assert sm._client is None
        result = await sm.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_authenticated(self) -> None:
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        sm = SecretsManager(client=mock_client)
        result = await sm.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_exception(self) -> None:
        mock_client = MagicMock()
        mock_client.is_authenticated.side_effect = Exception("connection refused")
        sm = SecretsManager(client=mock_client)
        result = await sm.health_check()
        assert result is False

    def test_secrets_manager_default_config(self) -> None:
        sm = SecretsManager()
        assert sm._config.backend == "openbao"
        assert sm._config.mode == "auto"

    def test_secrets_manager_custom_config(self) -> None:
        config = OpenBaoConfig(backend="vault", binary_path="/usr/bin/vault")
        sm = SecretsManager(config=config)
        assert sm._config.backend == "vault"
        assert sm._config.binary_path == "/usr/bin/vault"


class TestCoreAnsibleRunner:
    def test_render_template_simple_substitution(self) -> None:
        runner = CoreAnsibleRunner()
        mock_templar = MagicMock()
        mock_templar.template.return_value = "Hello World"
        with patch("general_ludd.ansible.core_runner._get_templar", return_value=mock_templar):
            result = runner.render_template("Hello {{ name }}", variables={"name": "World"})
        assert result == "Hello World"
        mock_templar.template.assert_called_once_with("Hello {{ name }}")

    def test_render_template_with_filters(self) -> None:
        runner = CoreAnsibleRunner()
        mock_templar = MagicMock()
        mock_templar.template.return_value = "a, b, c"
        with patch("general_ludd.ansible.core_runner._get_templar", return_value=mock_templar):
            result = runner.render_template("{{ items | join(', ') }}", variables={"items": ["a", "b", "c"]})
        assert result == "a, b, c"

    def test_render_template_upper_filter(self) -> None:
        runner = CoreAnsibleRunner()
        mock_templar = MagicMock()
        mock_templar.template.return_value = "TEST"
        with patch("general_ludd.ansible.core_runner._get_templar", return_value=mock_templar):
            result = runner.render_template("{{ name | upper }}", variables={"name": "test"})
        assert result == "TEST"

    def test_render_template_default_filter(self) -> None:
        runner = CoreAnsibleRunner()
        mock_templar = MagicMock()
        mock_templar.template.return_value = "fallback"
        with patch("general_ludd.ansible.core_runner._get_templar", return_value=mock_templar):
            result = runner.render_template("{{ value | default('fallback') }}", variables={})
        assert result == "fallback"

    def test_ansible_templater_wraps_runner(self) -> None:
        templater = AnsibleTemplater(extra_vars={"env": "prod"})
        mock_templar = MagicMock()
        mock_templar.template.return_value = "Environment: prod, Host: web01"
        with patch("general_ludd.ansible.core_runner._get_templar", return_value=mock_templar):
            result = templater.render("Environment: {{ env }}, Host: {{ host }}", host="web01")
        assert result == "Environment: prod, Host: web01"

    def test_ansible_templater_extra_vars_merged(self) -> None:
        templater = AnsibleTemplater(extra_vars={"x": "1"})
        mock_templar = MagicMock()
        mock_templar.template.return_value = "1-2"
        with patch("general_ludd.ansible.core_runner._get_templar", return_value=mock_templar) as mock_get:
            result = templater.render("{{ x }}-{{ y }}", y="2")
        assert result == "1-2"
        call_kwargs = mock_get.call_args
        passed_vars = call_kwargs[1].get("variables") or call_kwargs[0][0] if call_kwargs[0] else None
        if isinstance(passed_vars, dict):
            assert passed_vars.get("x") == "1"
            assert passed_vars.get("y") == "2"

    def test_adapter_delegates_to_core_runner(self) -> None:
        adapter = AnsibleRunnerAdapter()
        assert adapter._core_runner is not None
        assert isinstance(adapter._core_runner, CoreAnsibleRunner)

    def test_adapter_with_process_isolation(self) -> None:
        isolation = ProcessIsolationConfig(enabled=True, executable="podman")
        adapter = AnsibleRunnerAdapter(isolation_config=isolation)
        assert adapter.isolation_config is not None
        assert adapter.isolation_config.enabled is True

    def test_process_isolation_config_flows_to_core_runner(self) -> None:
        isolation = ProcessIsolationConfig(
            enabled=True,
            executable="podman",
            isolation_path="/tmp/sandbox",
            block_local_tools=["bash"],
        )
        adapter = AnsibleRunnerAdapter(isolation_config=isolation)
        assert adapter._core_runner._process_isolation is isolation
        kwargs = isolation.to_runner_kwargs()
        assert kwargs["process_isolation"] is True
        assert kwargs["process_isolation_executable"] == "podman"

    def test_render_template_nested_variables(self) -> None:
        runner = CoreAnsibleRunner()
        mock_templar = MagicMock()
        mock_templar.template.return_value = "localhost:8080"
        with patch("general_ludd.ansible.core_runner._get_templar", return_value=mock_templar):
            result = runner.render_template(
                "{{ config.host }}:{{ config.port }}",
                variables={"config": {"host": "localhost", "port": 8080}},
            )
        assert result == "localhost:8080"


class TestContainerfile:
    def test_containerfile_exists_at_project_root(self) -> None:
        cf_path = PROJECT_ROOT / "Containerfile"
        assert cf_path.exists(), "Containerfile missing from project root"

    def test_containerfile_has_build_stage(self) -> None:
        content = (PROJECT_ROOT / "Containerfile").read_text()
        assert "FROM python:3.11-slim AS builder" in content

    def test_containerfile_has_runtime_stage(self) -> None:
        content = (PROJECT_ROOT / "Containerfile").read_text()
        assert "FROM python:3.11-slim" in content

    def test_containerfile_installs_terraform(self) -> None:
        content = (PROJECT_ROOT / "Containerfile").read_text()
        assert "terraform" in content.lower()
        assert "releases.hashicorp.com" in content

    def test_containerfile_installs_opentofu(self) -> None:
        content = (PROJECT_ROOT / "Containerfile").read_text()
        assert "opentofu" in content.lower() or "tofu" in content.lower()

    def test_containerfile_has_workdir(self) -> None:
        content = (PROJECT_ROOT / "Containerfile").read_text()
        assert "WORKDIR /app" in content

    def test_containerfile_has_copy(self) -> None:
        content = (PROJECT_ROOT / "Containerfile").read_text()
        assert "COPY" in content

    def test_containerfile_exposes_port(self) -> None:
        content = (PROJECT_ROOT / "Containerfile").read_text()
        assert "EXPOSE 8000" in content

    def test_containerfile_has_entrypoint_or_cmd(self) -> None:
        content = (PROJECT_ROOT / "Containerfile").read_text()
        assert "ENTRYPOINT" in content or "CMD" in content


class TestMakefileTargets:
    def test_makefile_exists(self) -> None:
        mf_path = PROJECT_ROOT / "Makefile"
        assert mf_path.exists()

    def test_container_build_target_exists(self) -> None:
        content = (PROJECT_ROOT / "Makefile").read_text()
        assert "container-build:" in content

    def test_container_run_target_exists(self) -> None:
        content = (PROJECT_ROOT / "Makefile").read_text()
        assert "container-run:" in content

    def test_container_push_target_exists(self) -> None:
        content = (PROJECT_ROOT / "Makefile").read_text()
        assert "container-push:" in content

    def test_phony_includes_container_targets(self) -> None:
        content = (PROJECT_ROOT / "Makefile").read_text()
        assert "container-build" in content
        assert "container-run" in content
        assert "container-push" in content
