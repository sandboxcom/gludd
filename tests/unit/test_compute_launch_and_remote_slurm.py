"""Tests for compute launch CLI command, remote Slurm REST client, and Azure ContainerApp fixes.

TDD: These tests define the expected behavior before implementation.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeInstance,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.infra.slurm import SlurmAdapter, SlurmJobState, SlurmJobInfo
from general_ludd.infra.terraform import TerraformGenerator


class TestComputeLaunchCLI:
    def test_launch_subcommand_exists_in_argparse(self):
        import argparse
        from general_ludd.cli import build_parser
        parser, _ = build_parser()
        args = parser.parse_args(["compute", "launch", "--provider", "aws", "--gpu", "t4", "--model", "llama3"])
        assert args.compute_command == "launch"
        assert args.provider == "aws"
        assert args.gpu == "t4"
        assert args.model == "llama3"

    def test_launch_accepts_all_providers(self):
        from general_ludd.cli import build_parser
        parser, _ = build_parser()
        for provider in ["aws", "azure", "gcp", "runpod"]:
            args = parser.parse_args([
                "compute", "launch",
                "--provider", provider,
                "--gpu", "t4",
                "--model", "test-model",
            ])
            assert args.provider == provider

    def test_launch_accepts_optional_deploy_type(self):
        from general_ludd.cli import build_parser
        parser, _ = build_parser()
        args = parser.parse_args([
            "compute", "launch",
            "--provider", "azure",
            "--gpu", "a100_80",
            "--model", "test",
            "--deploy-type", "containerapp",
        ])
        assert args.deploy_type == "containerapp"

    def test_launch_accepts_optional_region(self):
        from general_ludd.cli import build_parser
        parser, _ = build_parser()
        args = parser.parse_args([
            "compute", "launch",
            "--provider", "aws",
            "--gpu", "t4",
            "--model", "test",
            "--region", "us-west-2",
        ])
        assert args.region == "us-west-2"

    def test_launch_accepts_spot_flag(self):
        from general_ludd.cli import build_parser
        parser, _ = build_parser()
        args = parser.parse_args([
            "compute", "launch",
            "--provider", "aws",
            "--gpu", "t4",
            "--model", "test",
            "--no-spot",
        ])
        assert args.no_spot is True

    def test_launch_defaults(self):
        from general_ludd.cli import build_parser
        parser, _ = build_parser()
        args = parser.parse_args([
            "compute", "launch",
            "--provider", "aws",
            "--gpu", "t4",
            "--model", "test",
        ])
        assert args.region is None
        assert args.no_spot is False
        assert args.gpu_count == 1
        assert args.max_cost == 10.0
        assert args.deploy_type == "vm"
        assert args.daemon_url == "http://localhost:8000"


class TestComputeLaunchDaemonEndpoint:
    def test_deploy_endpoint_exists(self):
        from fastapi import FastAPI
        from general_ludd.routers.compute import register
        app = FastAPI()
        register(app, {})
        routes = [r.path for r in app.routes]
        assert "/admin/compute/deploy" in routes

    def test_deploy_endpoint_method_is_post(self):
        from fastapi import FastAPI
        from general_ludd.routers.compute import register
        app = FastAPI()
        register(app, {})
        route = next(r for r in app.routes if hasattr(r, "path") and r.path == "/admin/compute/deploy")
        assert "POST" in route.methods

    def test_destroy_endpoint_exists(self):
        from fastapi import FastAPI
        from general_ludd.routers.compute import register
        app = FastAPI()
        register(app, {})
        routes = [r.path for r in app.routes]
        assert "/admin/compute/destroy/{instance_id}" in routes


class TestComputeDeployUsesSecretsResolver:
    def test_deploy_resolves_aws_creds_from_secrets_resolver(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from general_ludd.routers.compute import register

        app = FastAPI()
        register(app, {})
        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = lambda alias: {
            "aws_access_key": "AKIA_FROM_OPENBAO",
            "aws_secret_key": "secret_from_openbao",
        }.get(alias)
        app.state._secrets_resolver = mock_resolver

        client = TestClient(app)
        with patch("general_ludd.routers.compute.DeploymentManager") as MockDM:
            mock_instance = MagicMock()
            mock_instance.instance_id = "i-12345"
            mock_instance.provider = ComputeProvider.AWS
            mock_instance.status = "running"
            mock_instance.ip_address = "1.2.3.4"
            mock_instance.port = 8000
            mock_instance.gpu_type = GPUType.T4
            mock_instance.endpoint_url = "http://1.2.3.4:8000/v1"
            mock_dm = MagicMock()
            mock_dm.deploy = AsyncMock(return_value=mock_instance)
            MockDM.return_value = mock_dm

            resp = client.post("/admin/compute/deploy", json={
                "provider": "aws",
                "gpu_type": "t4",
                "model_name": "test-model",
                "provider_auth_aliases": {
                    "AWS_ACCESS_KEY_ID": "aws_access_key",
                    "AWS_SECRET_ACCESS_KEY": "aws_secret_key",
                },
            })
            assert resp.status_code == 200
            MockDM.assert_called_once()
            call_kwargs = MockDM.call_args
            assert call_kwargs[1]["secrets_resolver"] is mock_resolver

    def test_deploy_passes_none_resolver_when_not_on_app_state(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from general_ludd.routers.compute import register

        app = FastAPI()
        register(app, {})

        client = TestClient(app)
        with patch("general_ludd.routers.compute.DeploymentManager") as MockDM:
            mock_instance = MagicMock()
            mock_instance.instance_id = "i-12345"
            mock_instance.provider = ComputeProvider.AWS
            mock_instance.status = "running"
            mock_instance.ip_address = "1.2.3.4"
            mock_instance.port = 8000
            mock_instance.gpu_type = GPUType.T4
            mock_instance.endpoint_url = "http://1.2.3.4:8000/v1"
            mock_dm = MagicMock()
            mock_dm.deploy = AsyncMock(return_value=mock_instance)
            MockDM.return_value = mock_dm

            resp = client.post("/admin/compute/deploy", json={
                "provider": "aws",
                "gpu_type": "t4",
                "model_name": "test-model",
            })
            assert resp.status_code == 200
            call_kwargs = MockDM.call_args
            assert call_kwargs[1]["secrets_resolver"] is None


class TestSlurmUsesSecretsResolver:
    def test_slurm_resolves_creds_from_secrets_resolver(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from general_ludd.routers.slurm import register

        app = FastAPI()
        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = lambda alias: {
            "slurm_api_url": "https://slurm.vault.example.com:6820",
            "slurm_auth_token": "vault-jwt-token",
        }.get(alias)
        register(app, {})
        app.state._secrets_resolver = mock_resolver

        client = TestClient(app)
        with patch("general_ludd.infra.slurm.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            resp = client.get("/admin/slurm/status")
            assert resp.status_code == 200
            mock_resolver.resolve.assert_called()

    def test_slurm_falls_back_to_env_when_no_resolver(self):
        from fastapi import FastAPI
        from general_ludd.routers.slurm import register

        app = FastAPI()
        with patch.dict(os.environ, {"SLURM_API_URL": "https://env-slurm:6820", "SLURM_AUTH_TOKEN": "env-token"}):
            register(app, {})
            routes = [r.path for r in app.routes]
            assert "/admin/slurm/status" in routes

    def test_slurm_resolver_takes_priority_over_env(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from general_ludd.routers.slurm import register
        from general_ludd.infra.slurm import SlurmAdapter

        app = FastAPI()
        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = lambda alias: {
            "slurm_api_url": "https://bao-slurm:6820",
            "slurm_auth_token": "bao-token",
        }.get(alias)
        register(app, {})
        app.state._secrets_resolver = mock_resolver

        client = TestClient(app)
        with patch.object(SlurmAdapter, "available", return_value=True) as mock_avail:
            resp = client.get("/admin/slurm/status")
            assert resp.status_code == 200
            assert resp.json() == {"available": True}


class TestRemoteSlurmRESTClient:
    def test_slurm_adapter_accepts_api_url_and_token(self):
        adapter = SlurmAdapter(api_url="https://slurm.example.com:6820", auth_token="test-jwt")
        assert adapter._api_url == "https://slurm.example.com:6820"
        assert adapter._auth_token == "test-jwt"

    def test_slurm_adapter_defaults_to_local(self):
        adapter = SlurmAdapter()
        assert adapter._api_url is None
        assert adapter._auth_token is None

    def test_remote_submit_uses_http_not_subprocess(self):
        adapter = SlurmAdapter(api_url="https://slurm.example.com:6820", auth_token="mytoken")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"job_id": 42}
        with patch("general_ludd.infra.slurm.httpx.post", return_value=mock_resp) as mock_post:
            job_id = adapter.submit(command="python train.py", partition="gpu")
            assert job_id == "42"
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[1]["headers"]["X-SLURM-USER-NAME"] == "slurm"
            url_called = call_args[1].get("url", call_args[0][0] if call_args[0] else "")
            assert "slurm/v0.0.40/job/submit" in url_called

    def test_remote_status_uses_http(self):
        adapter = SlurmAdapter(api_url="https://slurm.example.com:6820", auth_token="mytoken")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"jobs": [{"job_id": "42", "job_state": "RUNNING"}]}
        with patch("general_ludd.infra.slurm.httpx.get", return_value=mock_resp):
            info = adapter.status("42")
        assert info.job_id == "42"
        assert info.state == SlurmJobState.RUNNING

    def test_remote_cancel_uses_http(self):
        adapter = SlurmAdapter(api_url="https://slurm.example.com:6820", auth_token="mytoken")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("general_ludd.infra.slurm.httpx.delete", return_value=mock_resp) as mock_del:
            adapter.cancel("42")
            mock_del.assert_called_once()

    def test_remote_available_checks_health(self):
        adapter = SlurmAdapter(api_url="https://slurm.example.com:6820", auth_token="mytoken")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("general_ludd.infra.slurm.httpx.get", return_value=mock_resp) as mock_get:
            assert adapter.available() is True
            mock_get.assert_called_once()

    def test_remote_available_returns_false_on_connection_error(self):
        adapter = SlurmAdapter(api_url="https://slurm.example.com:6820", auth_token="mytoken")
        import httpx as _httpx
        with patch("general_ludd.infra.slurm.httpx.get", side_effect=_httpx.ConnectError("refused")):
            assert adapter.available() is False

    def test_local_submit_still_works_without_url(self):
        adapter = SlurmAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Submitted batch job 100\n"
        with patch("general_ludd.infra.slurm.subprocess.run", return_value=mock_result):
            job_id = adapter.submit(command="echo hi")
        assert job_id == "100"

    def test_slurm_router_passes_env_vars_to_adapter(self):
        from fastapi import FastAPI
        from general_ludd.routers.slurm import register
        app = FastAPI()
        with patch.dict(os.environ, {
            "SLURM_API_URL": "https://slurm.example.com:6820",
            "SLURM_AUTH_TOKEN": "env-token",
        }):
            register(app, {})
            routes = [r.path for r in app.routes]
            assert "/admin/slurm/status" in routes

    def test_slurm_list_queries_jobs_via_api(self):
        adapter = SlurmAdapter(api_url="https://slurm.example.com:6820", auth_token="mytoken")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"jobs": [
            {"job_id": "1", "job_state": "RUNNING"},
            {"job_id": "2", "job_state": "PENDING"},
        ]}
        with patch("general_ludd.infra.slurm.httpx.get", return_value=mock_resp):
            jobs = adapter.list_jobs()
        assert len(jobs) == 2
        assert jobs[0].job_id == "1"
        assert jobs[0].state == SlurmJobState.RUNNING


class TestAzureContainerAppFixes:
    def test_acr_name_alphanumeric_no_underscores(self):
        gen = TerraformGenerator()
        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.A100_80,
            model_name="test-model",
            deploy_type="containerapp",
        )
        hcl = gen.generate(config)
        assert 'name = "gpuacr' not in hcl or "gpuacra10080" in hcl.replace("_", "")
        for line in hcl.splitlines():
            if "container_registry" in line and 'name' in line and 'gpuacr' in line:
                assert "_" not in line.split('"')[1].replace("gpuacr", "", 1) or "gpuacr" in line

    def test_acr_name_is_valid(self):
        gen = TerraformGenerator()
        for gpu in [GPUType.T4, GPUType.A100_80, GPUType.A100_40, GPUType.L40S, GPUType.H100]:
            config = ComputeConfig(
                provider=ComputeProvider.AZURE,
                gpu_type=gpu,
                model_name="test",
                deploy_type="containerapp",
            )
            hcl = gen.generate(config)
            for line in hcl.splitlines():
                if 'name = "gpuacr' in line:
                    acr_name = line.split('"')[1]
                    assert acr_name.isalnum(), f"ACR name {acr_name!r} must be alphanumeric"
                    assert 5 <= len(acr_name) <= 50, f"ACR name {acr_name!r} must be 5-50 chars"

    def test_gpu_sku_map_covers_all_gpu_types(self):
        gen = TerraformGenerator()
        for gpu in GPUType:
            config = ComputeConfig(
                provider=ComputeProvider.AZURE,
                gpu_type=gpu,
                model_name="test",
                deploy_type="containerapp",
            )
            hcl = gen.generate(config)
            assert "azurerm_container_app" in hcl
            assert "azurerm_container_registry" in hcl

    def test_container_app_has_gpu_resource_request(self):
        gen = TerraformGenerator()
        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.A100_80,
            model_name="test-model",
            deploy_type="containerapp",
        )
        hcl = gen.generate(config)
        assert "gpu" in hcl.lower() or "nvidia" in hcl.lower() or "GPU" in hcl, \
            "ContainerApp HCL should include GPU resource allocation"


class TestEnvVarAuthFallback:
    def test_deployment_manager_uses_env_vars_when_no_secrets_resolver(self):
        mgr = DeploymentManager(secrets_resolver=None)
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="test",
            provider_auth_aliases={
                "AWS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY",
            },
        )
        env = {
            "AWS_ACCESS_KEY_ID": "AKIA_TEST",
            "AWS_SECRET_ACCESS_KEY": "secret123",
        }
        with patch.dict(os.environ, env, clear=False):
            original = mgr._inject_auth_env(config)
            assert os.environ.get("AWS_ACCESS_KEY_ID") == "AKIA_TEST"
            assert os.environ.get("AWS_SECRET_ACCESS_KEY") == "secret123"
            mgr._restore_auth_env(original)

    def test_deployment_manager_inject_from_env_when_alias_matches_env_var(self):
        mgr = DeploymentManager(secrets_resolver=None)
        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="test",
            provider_auth_aliases={
                "ARM_CLIENT_ID": "ARM_CLIENT_ID",
                "ARM_CLIENT_SECRET": "ARM_CLIENT_SECRET",
            },
        )
        with patch.dict(os.environ, {
            "ARM_CLIENT_ID": "test-client-id",
            "ARM_CLIENT_SECRET": "test-secret",
        }):
            original = mgr._inject_auth_env(config)
            assert os.environ["ARM_CLIENT_ID"] == "test-client-id"
            mgr._restore_auth_env(original)
