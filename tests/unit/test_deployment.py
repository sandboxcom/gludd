"""Unit tests for deployment manager (terraform/opentofu lifecycle)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths
from general_ludd.infra.compute import ComputeConfig, ComputeInstance, ComputeProvider, GPUType
from general_ludd.infra.deployment import DeploymentManager


def _make_config() -> ComputeConfig:
    return ComputeConfig(
        provider=ComputeProvider.AWS,
        gpu_type=GPUType.T4,
        model_name="test-model",
        region="us-east-1",
    )


def _mock_subprocess(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout.encode()
    proc.stderr = stderr.encode()
    proc.returncode = returncode
    proc.wait = AsyncMock(return_value=returncode)
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    return proc


class TestDeploymentManagerInit:
    def test_default_init(self):
        mgr = DeploymentManager()
        assert mgr._binary_resolver is not None
        assert mgr._working_dir is not None
        assert mgr._secrets_resolver is None

    def test_custom_init(self):
        resolver = BinaryPathResolver(config=BinaryPaths(terraform="/custom/tf"))
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/tf-work")
        assert mgr._binary_resolver is resolver
        assert mgr._working_dir == "/tmp/tf-work"

    def test_init_with_secrets_resolver(self):
        from general_ludd.secrets.env import EnvSecretsManager
        env_resolver = EnvSecretsManager(overrides={"TEST_KEY": "test-value"})
        mgr = DeploymentManager(secrets_resolver=env_resolver)
        assert mgr._secrets_resolver is env_resolver
        assert mgr._working_dir is not None and "gludd-tf-" in mgr._working_dir


class TestDeploymentManagerDeploy:
    @pytest.mark.asyncio
    async def test_deploy_generates_hcl_and_runs_terraform(self):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/test-deploy")

        mock_proc_init = _mock_subprocess(
            stdout="Terraform has been successfully initialized!",
            returncode=0,
        )
        mock_proc_apply = _mock_subprocess(
            stdout='Apply complete! Resources: 1 added, 0 changed, 0 destroyed.',
            returncode=0,
        )
        mock_proc_output = _mock_subprocess(
            stdout=json.dumps({
                "instance_ip": {"value": "1.2.3.4"},
                "endpoint_url": {"value": "http://1.2.3.4:8000/v1"},
            }),
            returncode=0,
        )

        procs = iter([mock_proc_init, mock_proc_apply, mock_proc_output])

        def next_proc(*a: object, **kw: object) -> object:
            return next(procs)

        with patch(
            "general_ludd.infra.deployment.asyncio.create_subprocess_exec",
            side_effect=next_proc,
        ) as mock_exec, \
             patch("general_ludd.infra.deployment.os.makedirs"), \
             patch("builtins.open", MagicMock()):
            instance = await mgr.deploy(_make_config())

            assert mock_exec.call_count == 3
            first_call_args = mock_exec.call_args_list[0]
            assert "init" in first_call_args[0]

        assert isinstance(instance, ComputeInstance)
        assert instance.ip_address == "1.2.3.4"
        assert instance.endpoint_url == "http://1.2.3.4:8000/v1"
        assert instance.status == "running"
        assert instance.provider == ComputeProvider.AWS

    @pytest.mark.asyncio
    async def test_deploy_uses_tofu_when_available(self):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/test-deploy")

        with patch.object(resolver, "get_infra_binary", return_value="tofu"), \
             patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {
                "instance_ip": "10.0.0.1",
                "endpoint_url": "http://10.0.0.1:8000/v1",
            }
            await mgr.deploy(_make_config())

            calls = [c[0][0] for c in mock_run.call_args_list]
            assert any("init" in c for c in calls)
            assert any("apply" in c for c in calls)

    @pytest.mark.asyncio
    async def test_deploy_uses_terraform_when_tofu_unavailable(self):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/test-deploy")

        with patch.object(resolver, "get_infra_binary", return_value="terraform"), \
             patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {
                "instance_ip": "10.0.0.1",
                "endpoint_url": "http://10.0.0.1:8000/v1",
            }
            await mgr.deploy(_make_config())

            calls = [c[0][0] for c in mock_run.call_args_list]
            assert any("init" in c for c in calls)


class TestDeploymentManagerDestroy:
    @pytest.mark.asyncio
    async def test_destroy_runs_terraform_destroy(self):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/test-deploy")

        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {}
            await mgr.destroy("i-12345")

            mock_run.assert_called_once()
            assert "destroy" in mock_run.call_args[0][0]

    @pytest.mark.asyncio
    async def test_destroy_uses_correct_binary(self):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/test-deploy")

        with patch.object(resolver, "get_infra_binary", return_value="tofu"), \
             patch("general_ludd.infra.deployment.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = _mock_subprocess(
                stdout="Resources: 0 destroyed.",
                returncode=0,
            )
            mock_exec.return_value = mock_proc
            await mgr.destroy("i-12345")

            call_args = mock_exec.call_args[0]
            assert call_args[0] == "tofu"


class TestDeploymentManagerRunTerraform:
    @pytest.mark.asyncio
    async def test_run_terraform_captures_output(self):
        resolver = BinaryPathResolver(config=BinaryPaths(terraform="/usr/bin/terraform"))
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/test")

        with patch.object(resolver, "get_infra_binary", return_value="terraform"), \
             patch("general_ludd.infra.deployment.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = _mock_subprocess(stdout="success output", returncode=0)
            mock_exec.return_value = mock_proc

            result = await mgr._run_terraform(["init"])
            assert result["stdout"] == "success output"
            assert result["returncode"] == 0

    @pytest.mark.asyncio
    async def test_run_terraform_raises_on_failure(self):
        resolver = BinaryPathResolver(config=BinaryPaths(terraform="/usr/bin/terraform"))
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/test")

        with patch.object(resolver, "get_infra_binary", return_value="terraform"), \
             patch("general_ludd.infra.deployment.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = _mock_subprocess(stdout="", stderr="error!", returncode=1)
            mock_exec.return_value = mock_proc

            with pytest.raises(RuntimeError, match="terraform failed"):
                await mgr._run_terraform(["apply"])


class TestDeploymentManagerParseOutputs:
    def test_parse_outputs_extracts_ip_and_port(self):
        mgr = DeploymentManager()
        output = json.dumps({
            "instance_ip": {"value": "203.0.113.5"},
            "endpoint_url": {"value": "http://203.0.113.5:8000/v1"},
        })
        parsed = mgr._parse_outputs(output)
        assert parsed["instance_ip"] == "203.0.113.5"
        assert parsed["endpoint_url"] == "http://203.0.113.5:8000/v1"

    def test_parse_outputs_empty_string(self):
        mgr = DeploymentManager()
        parsed = mgr._parse_outputs("")
        assert parsed == {}

    def test_parse_outputs_invalid_json(self):
        mgr = DeploymentManager()
        parsed = mgr._parse_outputs("not json at all")
        assert parsed == {}

    def test_parse_outputs_partial(self):
        mgr = DeploymentManager()
        output = json.dumps({"instance_ip": {"value": "10.0.0.1"}})
        parsed = mgr._parse_outputs(output)
        assert parsed["instance_ip"] == "10.0.0.1"
