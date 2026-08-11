"""Unit tests for deployment manager (terraform/opentofu lifecycle)."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
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
            stdout="Apply complete! Resources: 1 added, 0 changed, 0 destroyed.",
            returncode=0,
        )
        mock_proc_output = _mock_subprocess(
            stdout=json.dumps(
                {
                    "instance_ip": {"value": "1.2.3.4"},
                    "endpoint_url": {"value": "http://1.2.3.4:8000/v1"},
                }
            ),
            returncode=0,
        )

        procs = iter([mock_proc_init, mock_proc_apply, mock_proc_output])

        def next_proc(*a: object, **kw: object) -> object:
            return next(procs)

        with (
            patch(
                "general_ludd.infra.deployment.asyncio.create_subprocess_exec",
                side_effect=next_proc,
            ) as mock_exec,
            patch("general_ludd.infra.deployment.os.makedirs"),
            patch("builtins.open", MagicMock()),
        ):
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

        with (
            patch.object(resolver, "get_infra_binary", return_value="tofu"),
            patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run,
        ):
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

        with (
            patch.object(resolver, "get_infra_binary", return_value="terraform"),
            patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run,
        ):
            mock_run.return_value = {
                "instance_ip": "10.0.0.1",
                "endpoint_url": "http://10.0.0.1:8000/v1",
            }
            await mgr.deploy(_make_config())

            calls = [c[0][0] for c in mock_run.call_args_list]
            assert any("init" in c for c in calls)


class TestDeploymentManagerDestroy:
    @pytest.mark.asyncio
    async def test_destroy_runs_terraform_destroy(self, tmp_path):
        """C5 fix: must DEPLOY before destroy; destroy then runs terraform destroy
        against the recorded deployment (no blind destroy)."""
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir=str(tmp_path))

        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"stdout": json.dumps({"instance_ip": {"value": "1.2.3.4"}})}
            instance = await mgr.deploy(_make_config())
            mock_run.reset_mock()
            await mgr.destroy(instance.instance_id)

            mock_run.assert_called_once()
            assert "destroy" in mock_run.call_args[0][0]

    @pytest.mark.asyncio
    async def test_destroy_uses_correct_binary(self, tmp_path):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir=str(tmp_path))

        with (
            patch.object(resolver, "get_infra_binary", return_value="tofu"),
            patch("general_ludd.infra.deployment.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_exec.side_effect = lambda *a, **k: _mock_subprocess(
                stdout=json.dumps({"instance_ip": {"value": "1.2.3.4"}}),
                returncode=0,
            )
            instance = await mgr.deploy(_make_config())
            mock_exec.reset_mock()
            mock_exec.side_effect = None
            mock_exec.return_value = _mock_subprocess(
                stdout="Resources: 0 destroyed.",
                returncode=0,
            )
            await mgr.destroy(instance.instance_id)

            call_args = mock_exec.call_args[0]
            assert call_args[0] == "tofu"

    @pytest.mark.asyncio
    async def test_destroy_unknown_instance_refused(self, tmp_path):
        """C5: destroying an instance never deployed must raise, not silently run."""
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir=str(tmp_path))
        with pytest.raises(ValueError, match=r"unknown|no deployment"):
            await mgr.destroy("i-never-deployed")


class TestDeploymentManagerRunTerraform:
    @pytest.mark.asyncio
    async def test_run_terraform_captures_output(self):
        resolver = BinaryPathResolver(config=BinaryPaths(terraform="/usr/bin/terraform"))
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/test")

        with (
            patch.object(resolver, "get_infra_binary", return_value="terraform"),
            patch("general_ludd.infra.deployment.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = _mock_subprocess(stdout="success output", returncode=0)
            mock_exec.return_value = mock_proc

            result = await mgr._run_terraform(["init"])
            assert result["stdout"] == "success output"
            assert result["returncode"] == 0

    @pytest.mark.asyncio
    async def test_run_terraform_raises_on_failure(self):
        resolver = BinaryPathResolver(config=BinaryPaths(terraform="/usr/bin/terraform"))
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/test")

        with (
            patch.object(resolver, "get_infra_binary", return_value="terraform"),
            patch("general_ludd.infra.deployment.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = _mock_subprocess(stdout="", stderr="error!", returncode=1)
            mock_exec.return_value = mock_proc

            with pytest.raises(RuntimeError, match="terraform failed"):
                await mgr._run_terraform(["apply"])


class TestDeploymentManagerParseOutputs:
    def test_parse_outputs_extracts_ip_and_port(self):
        mgr = DeploymentManager()
        output = json.dumps(
            {
                "instance_ip": {"value": "203.0.113.5"},
                "endpoint_url": {"value": "http://203.0.113.5:8000/v1"},
            }
        )
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


class TestDeployEnvIsolation:
    """Regression tests: concurrent deploys must not cross-contaminate os.environ."""

    def test_build_auth_env_does_not_mutate_global_environ(self, tmp_path, monkeypatch):
        """_build_auth_env must return a copy, never touch os.environ."""
        monkeypatch.setenv("MY_ALIAS", "secret-value")
        # Ensure the target var is absent from global env before the call.
        monkeypatch.delenv("TF_VAR_provider_key", raising=False)

        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="m",
            region="us-east-1",
            provider_auth_aliases={"TF_VAR_provider_key": "MY_ALIAS"},
        )
        mgr = DeploymentManager(working_dir=str(tmp_path))
        result_env = mgr._build_auth_env(config)

        # The returned dict has the cred injected.
        assert result_env["TF_VAR_provider_key"] == "secret-value"
        # Global os.environ is untouched.
        assert "TF_VAR_provider_key" not in os.environ

    @pytest.mark.asyncio
    async def test_concurrent_deploys_receive_isolated_envs(self, tmp_path):
        """Two concurrent deploy() calls must pass *different* env dicts to
        create_subprocess_exec — never the same mutable reference."""
        import os as _os

        config_a = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="model-a",
            region="us-east-1",
            provider_auth_aliases={"TF_VAR_key": "ALIAS_A"},
        )
        config_b = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="model-b",
            region="us-east-1",
            provider_auth_aliases={"TF_VAR_key": "ALIAS_B"},
        )

        captured_envs: list[dict[str, str]] = []

        class FakeResolver:
            def resolve(self, alias: str) -> str | None:
                return f"cred-for-{alias}"

        mgr = DeploymentManager(
            working_dir=str(tmp_path),
            secrets_resolver=FakeResolver(),
        )

        async def fake_run_terraform(
            args: list[str],
            *,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            if env is not None:
                captured_envs.append(dict(env))
            return {"stdout": '{"instance_ip": {"value": "1.2.3.4"}}', "stderr": "", "returncode": 0}

        with (
            patch.object(mgr, "_run_terraform", side_effect=fake_run_terraform),
            patch("general_ludd.infra.deployment.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch.object(mgr, "_generator") as mock_gen,
            patch.object(mgr, "_save_registry"),
        ):
            mock_gen.generate.return_value = ""

            await asyncio.gather(mgr.deploy(config_a), mgr.deploy(config_b))

        # We captured at least two env dicts (one per deploy, first terraform call).
        assert len(captured_envs) >= 2
        # The two envs differ on the injected credential.
        creds = [e.get("TF_VAR_key") for e in captured_envs]
        assert "cred-for-ALIAS_A" in creds
        assert "cred-for-ALIAS_B" in creds
        # Global os.environ was never written to with the credential key.
        assert "TF_VAR_key" not in _os.environ


class TestEstimateElapsedCost:
    def test_azure_dedicated_vm(self):
        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            region="eastus",
            deploy_type="vm",
            spot=False,
        )
        cost = DeploymentManager._estimate_elapsed_cost(config, 3600.0)
        assert cost > 0.0

    def test_azure_spot_vm(self):
        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            region="eastus",
            deploy_type="vm_spot",
            spot=True,
        )
        cost = DeploymentManager._estimate_elapsed_cost(config, 3600.0)
        assert cost > 0.0

    def test_azure_containerapp(self):
        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            region="eastus",
            deploy_type="containerapp",
            spot=False,
        )
        cost = DeploymentManager._estimate_elapsed_cost(config, 1800.0)
        assert cost > 0.0

    def test_non_azure_returns_zero(self):
        for provider in (ComputeProvider.AWS, ComputeProvider.GCP):
            config = ComputeConfig(
                provider=provider,
                gpu_type=GPUType.T4,
                model_name="m",
                region="us-east-1",
                deploy_type="vm",
            )
            cost = DeploymentManager._estimate_elapsed_cost(config, 3600.0)
            assert cost == 0.0

    def test_zero_elapsed(self):
        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            region="eastus",
            deploy_type="vm",
        )
        cost = DeploymentManager._estimate_elapsed_cost(config, 0.0)
        assert cost == 0.0

    def test_negative_elapsed_clamped(self):
        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            region="eastus",
            deploy_type="vm",
        )
        cost = DeploymentManager._estimate_elapsed_cost(config, -100.0)
        assert cost == 0.0

    def test_multi_gpu_scales_linearly(self):
        config_single = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            region="eastus",
            deploy_type="vm",
            gpu_count=1,
        )
        config_multi = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            region="eastus",
            deploy_type="vm",
            gpu_count=4,
        )
        single = DeploymentManager._estimate_elapsed_cost(config_single, 3600.0)
        multi = DeploymentManager._estimate_elapsed_cost(config_multi, 3600.0)
        assert multi == pytest.approx(single * 4, rel=0.01)


class TestBuildAuthEnvEdgeCases:
    def test_secrets_resolver_provides_value(self, tmp_path):
        class Resolver:
            def resolve(self, alias: str, project_id: str | None = None) -> str | None:
                if alias == "MY_SECRET":
                    return "resolved-secret"
                return None

        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="m",
            region="us-east-1",
            provider_auth_aliases={"TF_VAR_key": "MY_SECRET"},
        )
        mgr = DeploymentManager(working_dir=str(tmp_path), secrets_resolver=Resolver())
        env = mgr._build_auth_env(config)
        assert env["TF_VAR_key"] == "resolved-secret"

    def test_secrets_resolver_returns_none_falls_back_to_osenviron(self, tmp_path, monkeypatch):
        class NoneResolver:
            def resolve(self, alias: str, project_id: str | None = None) -> str | None:
                return None

        monkeypatch.setenv("FALLBACK_ALIAS", "env-value")
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="m",
            region="us-east-1",
            provider_auth_aliases={"TF_VAR_key": "FALLBACK_ALIAS"},
        )
        mgr = DeploymentManager(working_dir=str(tmp_path), secrets_resolver=NoneResolver())
        env = mgr._build_auth_env(config)
        assert env["TF_VAR_key"] == "env-value"

    def test_alias_not_found_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MISSING_ALIAS", raising=False)
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="m",
            region="us-east-1",
            provider_auth_aliases={"TF_VAR_key": "MISSING_ALIAS"},
        )
        mgr = DeploymentManager(working_dir=str(tmp_path))
        with pytest.raises(RuntimeError, match="Could not resolve auth alias"):
            mgr._build_auth_env(config)

    def test_no_auth_aliases_returns_env_copy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KEEP_ME", "kept")
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="m",
            region="us-east-1",
            provider_auth_aliases={},
        )
        mgr = DeploymentManager(working_dir=str(tmp_path))
        env = mgr._build_auth_env(config)
        assert env["KEEP_ME"] == "kept"
        assert "TF_VAR_key" not in env

    def test_env_copy_is_independent_of_original(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCE_KEY", "original-value")
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="m",
            region="us-east-1",
            provider_auth_aliases={"DEST_KEY": "SOURCE_KEY"},
        )
        mgr = DeploymentManager(working_dir=str(tmp_path))
        env = mgr._build_auth_env(config)
        assert env["DEST_KEY"] == "original-value"
        env["DEST_KEY"] = "modified"
        assert os.environ["SOURCE_KEY"] == "original-value"
        assert "DEST_KEY" not in os.environ


class TestClose:
    def test_close_removes_working_dir(self, tmp_path):
        work = tmp_path / "tf-work"
        work.mkdir()
        (work / "some-file").write_text("data")
        mgr = DeploymentManager(working_dir=str(work))
        assert work.exists()
        mgr.close()
        assert not work.exists()

    def test_close_handles_missing_dir(self, tmp_path):
        work = tmp_path / "nonexistent"
        mgr = DeploymentManager(working_dir=str(work))
        mgr.close()

    def test_close_clears_working_dir_ref(self, tmp_path):
        work = tmp_path / "tf-close"
        work.mkdir()
        mgr = DeploymentManager(working_dir=str(work))
        mgr.close()
        assert mgr._working_dir == ""


class TestRegistryPersistence:
    def test_save_and_load_registry_roundtrip(self, tmp_path):
        from general_ludd.schemas.deployment import DeploymentRecord

        mgr = DeploymentManager(working_dir=str(tmp_path))
        record = DeploymentRecord(
            instance_id="inst-1",
            working_dir="/tmp/foo",
            provider="aws",
            model_name="m",
            state="running",
            ip_address="10.0.0.1",
            endpoint_url="http://10.0.0.1:8000",
        )
        mgr._registry["inst-1"] = record
        mgr._save_registry()
        mgr2 = DeploymentManager(working_dir=str(tmp_path))
        assert "inst-1" in mgr2._registry
        loaded = mgr2._registry["inst-1"]
        assert loaded.instance_id == "inst-1"
        assert loaded.provider == "aws"
        assert loaded.ip_address == "10.0.0.1"

    def test_load_registry_missing_file(self, tmp_path):
        mgr = DeploymentManager(working_dir=str(tmp_path))
        assert mgr._registry == {}

    def test_load_registry_corrupt_json(self, tmp_path):
        work = tmp_path / "tf-reg"
        work.mkdir()
        (work / "deployments.json").write_text("{not valid json")
        mgr = DeploymentManager(working_dir=str(work))
        assert mgr._registry == {}

    def test_get_deployment(self):
        from general_ludd.schemas.deployment import DeploymentRecord

        mgr = DeploymentManager()
        record = DeploymentRecord(
            instance_id="inst-1",
            working_dir="/tmp/foo",
            provider="aws",
            model_name="m",
            state="running",
        )
        mgr._registry["inst-1"] = record
        assert mgr.get_deployment("inst-1") is record
        assert mgr.get_deployment("nonexistent") is None

    def test_list_deployments(self):
        from general_ludd.schemas.deployment import DeploymentRecord

        mgr = DeploymentManager()
        r1 = DeploymentRecord(
            instance_id="inst-1",
            working_dir="/tmp/a",
            provider="aws",
            model_name="m1",
            state="running",
        )
        r2 = DeploymentRecord(
            instance_id="inst-2",
            working_dir="/tmp/b",
            provider="gcp",
            model_name="m2",
            state="stopped",
        )
        mgr._registry["inst-1"] = r1
        mgr._registry["inst-2"] = r2
        deployments = mgr.list_deployments()
        assert len(deployments) == 2
        assert {d.instance_id for d in deployments} == {"inst-1", "inst-2"}


class TestPlan:
    @pytest.mark.asyncio
    async def test_plan_no_changes(self, tmp_path):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir=str(tmp_path))
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"stdout": "", "stderr": "", "returncode": 0}
            with patch("general_ludd.infra.deployment.asyncio.create_subprocess_exec") as mock_exec:
                proc = _mock_subprocess(stdout="No changes.", returncode=0)
                mock_exec.return_value = proc
                result = await mgr.plan(_make_config())
                assert result["changes_present"] is False
                assert result["returncode"] == 0

    @pytest.mark.asyncio
    async def test_plan_with_changes(self, tmp_path):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir=str(tmp_path))
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"stdout": "", "stderr": "", "returncode": 0}
            with patch("general_ludd.infra.deployment.asyncio.create_subprocess_exec") as mock_exec:
                proc = _mock_subprocess(stdout="Plan: 1 to add.", returncode=2)
                mock_exec.return_value = proc
                result = await mgr.plan(_make_config())
                assert result["changes_present"] is True
                assert result["returncode"] == 2

    @pytest.mark.asyncio
    async def test_plan_failure_raises(self, tmp_path):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir=str(tmp_path))
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"stdout": "", "stderr": "", "returncode": 0}
            with patch("general_ludd.infra.deployment.asyncio.create_subprocess_exec") as mock_exec:
                proc = _mock_subprocess(stdout="", stderr="Error: invalid config", returncode=1)
                mock_exec.return_value = proc
                with pytest.raises(RuntimeError, match="plan failed"):
                    await mgr.plan(_make_config())


class TestValidate:
    @pytest.mark.asyncio
    async def test_validate_runs_init_and_validate(self, tmp_path):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir=str(tmp_path))
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [
                {"stdout": "Initialized", "stderr": "", "returncode": 0},
                {"stdout": "Success!", "stderr": "", "returncode": 0},
            ]
            result = await mgr.validate(_make_config())
            assert result == {"stdout": "Success!", "stderr": "", "returncode": 0}
            assert mock_run.call_count == 2
            calls = [c[0][0] for c in mock_run.call_args_list]
            assert "init" in calls[0]
            assert "validate" in calls[1]

    @pytest.mark.asyncio
    async def test_validate_failure_raises(self, tmp_path):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir=str(tmp_path))
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = RuntimeError("validate: invalid configuration")
            with pytest.raises(RuntimeError, match="validate"):
                await mgr.validate(_make_config())


class TestPublishEvent:
    def test_publish_event_no_bus_silently_returns(self):
        mgr = DeploymentManager()
        mgr._publish_event("test_event", key="value")

    def test_publish_event_error_swallowed(self):
        class FailingBus:
            def publish(self, event):
                raise RuntimeError("bus down")

        mgr = DeploymentManager(event_bus=FailingBus())
        mgr._publish_event("test_event", key="value")

    def test_publish_event_calls_bus(self):
        events = []

        class CapturingBus:
            def publish(self, event):
                events.append(event)

        mgr = DeploymentManager(event_bus=CapturingBus())
        mgr._publish_event("deployment.started", instance_id="abc")
        assert len(events) == 1
        assert events[0].name == "deployment.started"
        assert events[0].payload["instance_id"] == "abc"
        assert events[0].source == "terraform_deployment"


class TestCleanupOrphanedInstances:
    def test_cleanup_without_lifecycle_returns_zero(self):
        mgr = DeploymentManager()
        result = mgr.cleanup_orphaned_instances()
        assert result == 0
