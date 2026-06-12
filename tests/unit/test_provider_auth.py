"""Unit tests for provider auth injection in DeploymentManager."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.secrets.env import EnvSecretsManager


def _make_config(**kwargs: object) -> ComputeConfig:
    defaults = {
        "provider": ComputeProvider.AZURE,
        "gpu_type": GPUType.T4,
        "model_name": "test-model",
        "deploy_type": "containerapp",
        "region": "eastus",
    }
    defaults.update(kwargs)
    return ComputeConfig(**defaults)  # type: ignore[arg-type]


class TestProviderAuthAliases:
    def test_compute_config_accepts_provider_auth_aliases(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            provider_auth_aliases={
                "ARM_CLIENT_ID": "AZURE_CLIENT_ID",
                "ARM_CLIENT_SECRET": "AZURE_CLIENT_SECRET",
            },
        )
        assert cfg.provider_auth_aliases is not None
        assert cfg.provider_auth_aliases["ARM_CLIENT_ID"] == "AZURE_CLIENT_ID"

    def test_compute_config_defaults_provider_auth_aliases_none(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="m",
        )
        assert cfg.provider_auth_aliases is None

    def test_compute_config_serialization_with_auth_aliases(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            provider_auth_aliases={
                "ARM_SUBSCRIPTION_ID": "AZURE_SUB_ID",
            },
        )
        data = cfg.model_dump()
        restored = ComputeConfig.model_validate(data)
        assert restored.provider_auth_aliases == {"ARM_SUBSCRIPTION_ID": "AZURE_SUB_ID"}

    def test_compute_config_json_roundtrip_with_auth_aliases(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.A100_80,
            model_name="llama",
            provider_auth_aliases={
                "ARM_CLIENT_ID": "AZURE_CLIENT_ID",
                "ARM_CLIENT_SECRET": "AZURE_CLIENT_SECRET",
                "ARM_SUBSCRIPTION_ID": "AZURE_SUB_ID",
                "ARM_TENANT_ID": "AZURE_TENANT_ID",
            },
        )
        json_str = cfg.model_dump_json()
        restored = ComputeConfig.model_validate_json(json_str)
        assert restored.provider_auth_aliases == cfg.provider_auth_aliases


class TestDeploymentManagerAuthInjection:
    @pytest.mark.asyncio
    async def test_deploy_resolves_auth_aliases_from_env(self):
        env_resolver = EnvSecretsManager(overrides={
            "AZURE_CLIENT_ID": "resolved-client-id",
            "AZURE_CLIENT_SECRET": "resolved-secret",
            "AZURE_SUB_ID": "resolved-sub-id",
            "AZURE_TENANT_ID": "resolved-tenant-id",
        })
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(
            binary_paths=resolver,
            working_dir="/tmp/test-auth-deploy",
            secrets_resolver=env_resolver,
        )

        cfg = _make_config(
            provider_auth_aliases={
                "ARM_CLIENT_ID": "AZURE_CLIENT_ID",
                "ARM_CLIENT_SECRET": "AZURE_CLIENT_SECRET",
                "ARM_SUBSCRIPTION_ID": "AZURE_SUB_ID",
                "ARM_TENANT_ID": "AZURE_TENANT_ID",
            },
        )

        env_keys = ("ARM_CLIENT_ID", "ARM_CLIENT_SECRET", "ARM_SUBSCRIPTION_ID", "ARM_TENANT_ID")
        original_env = {k: os.environ.get(k) for k in env_keys}

        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_tf:
            mock_tf.side_effect = [
                {"stdout": "Init done", "stderr": "", "returncode": 0},
                {"stdout": "Apply done", "stderr": "", "returncode": 0},
                {"stdout": json.dumps({"endpoint_url": {"value": "https://gpu-inference"
                                                       ".eastus.azurecontainerapps.io"}}),
                 "stderr": "", "returncode": 0},
            ]

            def check_env_during_deploy(*args: object, **kwargs: object) -> None:
                assert os.environ.get("ARM_CLIENT_ID") == "resolved-client-id"
                assert os.environ.get("ARM_CLIENT_SECRET") == "resolved-secret"
                assert os.environ.get("ARM_SUBSCRIPTION_ID") == "resolved-sub-id"
                assert os.environ.get("ARM_TENANT_ID") == "resolved-tenant-id"

            instance = await mgr.deploy(cfg)

        assert instance.endpoint_url is not None
        assert "azurecontainerapps" in instance.endpoint_url

        for key, orig_val in original_env.items():
            assert os.environ.get(key) == orig_val

    @pytest.mark.asyncio
    async def test_deploy_restores_original_env_after_completion(self):
        env_resolver = EnvSecretsManager(overrides={
            "AZURE_SUB_ID": "my-sub-id",
        })
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(
            binary_paths=resolver,
            working_dir="/tmp/test-env-restore",
            secrets_resolver=env_resolver,
        )

        cfg = _make_config(
            provider_auth_aliases={"ARM_SUBSCRIPTION_ID": "AZURE_SUB_ID"},
        )

        original_sub = os.environ.get("ARM_SUBSCRIPTION_ID")
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_tf:
            mock_tf.side_effect = [
                {"stdout": "ok", "stderr": "", "returncode": 0},
                {"stdout": "ok", "stderr": "", "returncode": 0},
                {"stdout": json.dumps({"endpoint_url": {"value": "https://test.azurecontainerapps.io"}}),
                 "stderr": "", "returncode": 0},
            ]
            await mgr.deploy(cfg)

        assert os.environ.get("ARM_SUBSCRIPTION_ID") == original_sub

    @pytest.mark.asyncio
    async def test_deploy_without_auth_aliases_works_normally(self):
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(binary_paths=resolver, working_dir="/tmp/test-no-auth")

        cfg = _make_config()

        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_tf:
            mock_tf.side_effect = [
                {"stdout": "ok", "stderr": "", "returncode": 0},
                {"stdout": "ok", "stderr": "", "returncode": 0},
                {"stdout": json.dumps({"endpoint_url": {"value": "https://test.azurecontainerapps.io"}}),
                 "stderr": "", "returncode": 0},
            ]
            instance = await mgr.deploy(cfg)
            assert instance.provider == ComputeProvider.AZURE

    @pytest.mark.asyncio
    async def test_deploy_cleans_up_on_failure(self):
        env_resolver = EnvSecretsManager(overrides={
            "AZURE_SECRET": "secret-value",
        })
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(
            binary_paths=resolver,
            working_dir="/tmp/test-auth-fail",
            secrets_resolver=env_resolver,
        )

        cfg = _make_config(
            provider_auth_aliases={"ARM_CLIENT_SECRET": "AZURE_SECRET"},
        )

        original_arm_secret = os.environ.get("ARM_CLIENT_SECRET")
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_tf:
            mock_tf.side_effect = RuntimeError("terraform failed")
            with pytest.raises(RuntimeError):
                await mgr.deploy(cfg)

        assert os.environ.get("ARM_CLIENT_SECRET") == original_arm_secret

    @pytest.mark.asyncio
    async def test_destroy_resolves_auth_aliases(self, tmp_path):
        env_resolver = EnvSecretsManager(overrides={
            "AZURE_SUB_ID": "sub-123",
        })
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(
            binary_paths=resolver,
            working_dir=str(tmp_path),
            secrets_resolver=env_resolver,
        )

        cfg = _make_config(
            provider_auth_aliases={"ARM_SUBSCRIPTION_ID": "AZURE_SUB_ID"},
        )

        original_sub = os.environ.get("ARM_SUBSCRIPTION_ID")
        # C5: must deploy (registering the instance) before destroy can run.
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_tf:
            mock_tf.return_value = {
                "stdout": json.dumps({"instance_ip": {"value": "1.2.3.4"}}),
                "stderr": "", "returncode": 0,
            }
            instance = await mgr.deploy(cfg)
            await mgr.destroy(instance.instance_id)

        assert os.environ.get("ARM_SUBSCRIPTION_ID") == original_sub

    @pytest.mark.asyncio
    async def test_deploy_with_managed_identity_flags(self):
        env_resolver = EnvSecretsManager(overrides={
            "AZURE_USE_MSI": "true",
            "AZURE_SUB_ID": "sub-456",
        })
        resolver = BinaryPathResolver(config=BinaryPaths())
        mgr = DeploymentManager(
            binary_paths=resolver,
            working_dir="/tmp/test-msi",
            secrets_resolver=env_resolver,
        )

        cfg = _make_config(
            provider_auth_aliases={
                "ARM_USE_MSI": "AZURE_USE_MSI",
                "ARM_SUBSCRIPTION_ID": "AZURE_SUB_ID",
            },
        )

        original_env = {k: os.environ.get(k) for k in ("ARM_USE_MSI", "ARM_SUBSCRIPTION_ID")}

        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as mock_tf:
            mock_tf.side_effect = [
                {"stdout": "ok", "stderr": "", "returncode": 0},
                {"stdout": "ok", "stderr": "", "returncode": 0},
                {"stdout": json.dumps({"endpoint_url": {
                    "value": "https://msi-test.azurecontainerapps.io"}}),
                 "stderr": "", "returncode": 0},
            ]
            instance = await mgr.deploy(cfg)
            assert instance.endpoint_url is not None

        for key, orig_val in original_env.items():
            assert os.environ.get(key) == orig_val
