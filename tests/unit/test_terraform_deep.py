"""Deep Terraform provider management tests.

Covers:
  - Provider cache management (parse_required_providers, scan_stacks, Drift)
  - Version constraint resolution (drift detection, missing-pin detection)
  - Plan/apply dry-run validation (TerraformGenerator output structure)
  - State file integrity checks (Watchdog, BackendSelector, render_backend_block)
  - Variable substitution edge cases (override_apply, inference_bind_host, build_tfvars)
"""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.terraform import (
    TerraformGenerator,
    _inference_bind_host,
    _override_apply,
    escape_tfvar_value,
)
from general_ludd.infra.terraform_http_backend import (
    generate_http_backend_block,
    write_http_backend_file,
)
from general_ludd.infra.terraform_state import (
    StateBackendConfig,
    StateBackendSelector,
    render_backend_block,
)
from general_ludd.infra.terraform_watchdog import TerraformWatchdog, WatchdogFinding

try:
    from scripts.check_tf_provider_versions import (
        Drift,
        _parse_required_providers,
        parse_versions_tf,
        scan_stacks,
    )
except ImportError:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "check_tf_provider_versions",
        Path(__file__).resolve().parents[2] / "scripts" / "check_tf_provider_versions.py",
    )
    _mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    Drift = _mod.Drift
    _parse_required_providers = _mod._parse_required_providers
    parse_versions_tf = _mod.parse_versions_tf
    scan_stacks = _mod.scan_stacks


def _config(**overrides: object) -> ComputeConfig:
    defaults: dict[str, object] = {
        "provider": ComputeProvider.AWS,
        "gpu_type": GPUType.T4,
        "gpu_count": 1,
        "engine": InferenceEngine.VLLM,
        "model_name": "meta-llama/Llama-2-7b-hf",
        "allowed_cidr": "0.0.0.0/0",
    }
    defaults.update(overrides)
    return ComputeConfig(**defaults)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════
# Provider cache management tests
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderVersionParsing:
    def test_parse_required_providers_extracts_source_and_version(self) -> None:
        hcl = textwrap.dedent("""\
            terraform {
              required_providers {
                aws = {
                  source  = "hashicorp/aws"
                  version = "~> 5.0"
                }
                google = {
                  source  = "hashicorp/google"
                  version = "~> 5.0"
                }
              }
            }
        """)
        result = _parse_required_providers(hcl)
        assert result == {
            "hashicorp/aws": ("~> 5.0", "aws"),
            "hashicorp/google": ("~> 5.0", "google"),
        }

    def test_parse_required_providers_empty_hcl_returns_empty(self) -> None:
        assert _parse_required_providers("") == {}

    def test_parse_required_providers_missing_version_becomes_empty_string(self) -> None:
        hcl = textwrap.dedent("""\
            terraform {
              required_providers {
                aws = {
                  source  = "hashicorp/aws"
                }
              }
            }
        """)
        result = _parse_required_providers(hcl)
        assert result == {"hashicorp/aws": ("", "aws")}


class TestParseVersionsTf:
    def test_parse_versions_tf_returns_source_to_version_dict(self) -> None:
        content = textwrap.dedent("""\
            terraform {
              required_providers {
                aws = { source = "hashicorp/aws", version = "~> 5.0" }
                azurerm = { source = "hashicorp/azurerm", version = "~> 4.55" }
              }
            }
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tf", delete=False) as f:
            f.write(content)
            f.flush()
            try:
                result = parse_versions_tf(Path(f.name))
                assert result == {"hashicorp/aws": "~> 5.0", "hashicorp/azurerm": "~> 4.55"}
            finally:
                os.unlink(f.name)

    def test_parse_versions_tf_raises_on_missing_version(self) -> None:
        content = textwrap.dedent("""\
            terraform {
              required_providers {
                aws = { source = "hashicorp/aws" }
              }
            }
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tf", delete=False) as f:
            f.write(content)
            f.flush()
            try:
                with pytest.raises(ValueError, match="version pin"):
                    parse_versions_tf(Path(f.name))
            finally:
                os.unlink(f.name)


class TestDriftModel:
    def test_drift_str_format(self) -> None:
        d = Drift(stack="aws-vllm", provider="hashicorp/aws", detail="pinned ~> 4.0, contract says ~> 5.0")
        s = str(d)
        assert "aws-vllm" in s
        assert "hashicorp/aws" in s
        assert "pinned" in s

    def test_drift_repr(self) -> None:
        d = Drift(stack="x", provider="y", detail="z")
        assert "Drift" in repr(d)


class TestScanStacksDriftDetection:
    def test_scan_stacks_detects_version_mismatch(self) -> None:
        contract = {"hashicorp/aws": "~> 5.0"}
        with tempfile.TemporaryDirectory() as tmp:
            stacks_dir = Path(tmp) / "my-stack"
            stacks_dir.mkdir()
            (stacks_dir / "main.tf").write_text(
                textwrap.dedent("""\
                terraform {
                  required_providers {
                    aws = { source = "hashicorp/aws", version = "~> 4.0" }
                  }
                }
            """)
            )
            findings = scan_stacks(stacks_dir.parent, contract)
            assert len(findings) == 1
            assert findings[0].provider == "hashicorp/aws"
            assert "~> 4.0" in findings[0].detail
            assert "~> 5.0" in findings[0].detail

    def test_scan_stacks_detects_missing_version_pin(self) -> None:
        contract = {"hashicorp/aws": "~> 5.0"}
        with tempfile.TemporaryDirectory() as tmp:
            stacks_dir = Path(tmp) / "my-stack"
            stacks_dir.mkdir()
            (stacks_dir / "main.tf").write_text(
                textwrap.dedent("""\
                terraform {
                  required_providers {
                    aws = { source = "hashicorp/aws" }
                  }
                }
            """)
            )
            findings = scan_stacks(stacks_dir.parent, contract)
            assert len(findings) == 1
            assert "missing a version pin" in findings[0].detail

    def test_scan_stacks_passes_when_versions_match(self) -> None:
        contract = {"hashicorp/aws": "~> 5.0"}
        with tempfile.TemporaryDirectory() as tmp:
            stacks_dir = Path(tmp) / "my-stack"
            stacks_dir.mkdir()
            (stacks_dir / "main.tf").write_text(
                textwrap.dedent("""\
                terraform {
                  required_providers {
                    aws = { source = "hashicorp/aws", version = "~> 5.0" }
                  }
                }
            """)
            )
            findings = scan_stacks(stacks_dir.parent, contract)
            assert findings == []

    def test_scan_stacks_ignores_providers_not_in_contract(self) -> None:
        contract = {"hashicorp/aws": "~> 5.0"}
        with tempfile.TemporaryDirectory() as tmp:
            stacks_dir = Path(tmp) / "my-stack"
            stacks_dir.mkdir()
            (stacks_dir / "main.tf").write_text(
                textwrap.dedent("""\
                terraform {
                  required_providers {
                    aws = { source = "hashicorp/aws", version = "~> 5.0" }
                    random_pet = { source = "hashicorp/random", version = "~> 3.0" }
                  }
                }
            """)
            )
            findings = scan_stacks(stacks_dir.parent, contract)
            assert findings == []


# ═══════════════════════════════════════════════════════════════════════════
# Plan/apply dry-run validation tests
# ═══════════════════════════════════════════════════════════════════════════


class TestGeneratorOutputStructure:
    def test_generate_aws_produces_terraform_block(self) -> None:
        gen = TerraformGenerator()
        out = gen.generate(_config(provider=ComputeProvider.AWS))
        assert "required_providers" in out
        assert "aws" in out
        assert 'module "vllm_server"' in out
        assert 'output "instance_id"' in out
        assert 'output "base_url"' in out

    def test_generate_gcp_produces_terraform_block(self) -> None:
        gen = TerraformGenerator()
        out = gen.generate(_config(provider=ComputeProvider.GCP))
        assert "required_providers" in out
        assert "google" in out
        assert 'module "vllm_server"' in out

    def test_generate_azure_produces_terraform_block(self) -> None:
        gen = TerraformGenerator()
        out = gen.generate(_config(provider=ComputeProvider.AZURE, region="eastus"))
        assert "azurerm" in out
        assert 'module "vllm_server"' in out

    def test_generate_vast_ai_produces_terraform_block(self) -> None:
        gen = TerraformGenerator()
        out = gen.generate(_config(provider=ComputeProvider.VAST_AI))
        assert "vast-ai" in out
        assert 'module "vllm_server"' in out

    def test_generate_kubernetes_produces_terraform_block(self) -> None:
        gen = TerraformGenerator()
        out = gen.generate(_config(provider=ComputeProvider.KUBERNETES))
        assert "kubernetes" in out
        assert 'module "inference_server"' in out

    def test_generate_all_known_providers_do_not_raise(self) -> None:
        gen = TerraformGenerator()
        for provider in ComputeProvider:
            cfg = _config(provider=provider, allowed_cidr="127.0.0.1/32")
            try:
                out = gen.generate(cfg)
                assert isinstance(out, str)
                assert len(out) > 50
            except ValueError as exc:
                if "containerapp" in str(exc):
                    continue
                if "loopback" in str(exc):
                    continue
                raise

    def test_build_tfvars_includes_all_workload_fields(self) -> None:
        cfg = _config(
            workload_type="realtime_api",
            deployment_profile={
                "context_length": 8192,
                "max_tokens": 2048,
                "batch_size": 128,
                "quantization": "awq",
                "enforce_eager": True,
                "enable_prefix_caching": False,
            },
        )
        tfvars = TerraformGenerator().build_tfvars(cfg)
        assert "vllm_context_length" in tfvars
        assert "vllm_max_tokens" in tfvars
        assert "vllm_batch_size" in tfvars
        assert "vllm_quantization" in tfvars
        assert "vllm_enforce_eager = true" in tfvars
        assert "vllm_enable_prefix_caching = false" in tfvars

    def test_build_tfvars_model_name_is_escaped(self) -> None:
        cfg = _config(model_name="meta-llama/Llama-2-7b-hf")
        tfvars = TerraformGenerator().build_tfvars(cfg)
        assert '"meta-llama/Llama-2-7b-hf"' in tfvars

    def test_materialize_creates_main_tf(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(provider=ComputeProvider.AWS, region="us-east-1")
        with tempfile.TemporaryDirectory() as tmp:
            gen.materialize(cfg, tmp, deployment_name="test-deployment")
            main_tf = Path(tmp) / "main.tf"
            assert main_tf.exists()
            content = main_tf.read_text()
            assert 'output "instance_id"' in content


# ═══════════════════════════════════════════════════════════════════════════
# State file integrity checks
# ═══════════════════════════════════════════════════════════════════════════


class TestStateBackendSelection:
    def test_local_backend_below_threshold(self) -> None:
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(),
        )
        cfg = _config(max_cost_usd=10.0)
        result = selector.select(cfg)
        assert result.kind == "local"
        assert result.path == "terraform.tfstate"

    def test_remote_openbao_kv_above_threshold(self) -> None:
        reachable = MagicMock()
        reachable.health_check.return_value = True
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=reachable,
        )
        cfg = _config(max_cost_usd=100.0)
        result = selector.select(cfg, deployment_id="dep-abc")
        assert result.kind == "openbao_kv"
        assert "secret/data/gludd/tfstate/dep-abc" in result.path

    def test_fallback_local_when_openbao_unreachable(self) -> None:
        unreachable = MagicMock()
        unreachable.health_check.return_value = False
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=unreachable,
        )
        cfg = _config(max_cost_usd=500.0)
        result = selector.select(cfg)
        assert result.kind == "local"

    def test_http_backend_selected_when_api_url_configured(self) -> None:
        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(),
            config={"api_url": "https://gludd.example.com", "username": "gludd", "password": "s3cret"},
        )
        cfg = _config(max_cost_usd=10.0)
        result = selector.select(cfg, deployment_id="fixed-id")
        assert result.kind == "http"
        assert "fixed-id" in result.path
        assert result.username == "gludd"
        assert result.password == "s3cret"


class TestRenderBackendBlock:
    def test_render_local_backend(self) -> None:
        block = render_backend_block(StateBackendConfig(kind="local", path="terraform.tfstate"))
        assert 'backend "local"' in block
        assert 'path = "terraform.tfstate"' in block

    def test_render_openbao_kv_backend(self) -> None:
        block = render_backend_block(StateBackendConfig(kind="openbao_kv", path="secret/data/gludd/tfstate/x"))
        assert 'backend "http"' in block
        assert "secret/data/gludd/tfstate/x" in block

    def test_render_http_backend_with_credentials(self) -> None:
        block = render_backend_block(
            StateBackendConfig(
                kind="http",
                path="https://gludd.example.com/api/terraform/state/abc",
                lock_address="https://gludd.example.com/api/terraform/state/abc",
                unlock_address="https://gludd.example.com/api/terraform/state/abc",
                username="gludd-agent",
                password="token-123",
            )
        )
        assert 'backend "http"' in block
        assert 'address = "https://gludd.example.com/api/terraform/state/abc"' in block
        assert 'username = "gludd-agent"' in block
        assert 'password = "token-123"' in block

    def test_render_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown state backend kind"):
            render_backend_block(StateBackendConfig(kind="bogus", path="x"))


class TestHttpBackendGeneration:
    def test_generate_http_backend_block(self) -> None:
        block = generate_http_backend_block(
            stack_name="aws-vllm",
            api_url="https://gludd.example.com",
        )
        assert 'backend "http"' in block
        assert "/api/terraform/state/aws-vllm" in block
        assert "lock_address" in block
        assert "unlock_address" in block

    def test_write_http_backend_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_http_backend_file("my-stack", tmp, "https://gludd.example.com")
            assert os.path.isfile(path)
            content = Path(path).read_text()
            assert 'backend "http"' in content
            assert "my-stack" in content


class TestTerraformWatchdog:
    def test_is_applied_true_when_tfstate_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stacks = Path(tmp)
            (stacks / "stack-a").mkdir()
            (stacks / "stack-a" / "terraform.tfstate").write_text("{}")
            wd = TerraformWatchdog(str(stacks))
            assert wd.is_applied("stack-a") is True

    def test_is_applied_false_when_no_tfstate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stacks = Path(tmp)
            (stacks / "stack-b").mkdir()
            wd = TerraformWatchdog(str(stacks))
            assert wd.is_applied("stack-b") is False

    def test_current_cost_estimate_zero_when_no_tfstate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stacks = Path(tmp)
            (stacks / "stack-c").mkdir()
            wd = TerraformWatchdog(str(stacks))
            assert wd.current_cost_estimate("stack-c") == 0.0

    def test_current_cost_estimate_zero_for_corrupt_tfstate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stacks = Path(tmp)
            (stacks / "stack-d").mkdir()
            (stacks / "stack-d" / "terraform.tfstate").write_text("not json")
            wd = TerraformWatchdog(str(stacks))
            assert wd.current_cost_estimate("stack-d") == 0.0

    def test_current_cost_estimate_from_monthly_cost_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stacks = Path(tmp)
            (stacks / "stack-e").mkdir()
            state = {
                "resources": [
                    {
                        "type": "aws_instance",
                        "instances": [{"attributes": {"monthly_cost_estimate": "42.50"}}],
                    }
                ]
            }
            (stacks / "stack-e" / "terraform.tfstate").write_text(json.dumps(state))
            wd = TerraformWatchdog(str(stacks))
            assert wd.current_cost_estimate("stack-e") == 42.50

    def test_check_all_stacks_detects_budget_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stacks = Path(tmp)
            (stacks / "expensive").mkdir()
            (stacks / "expensive" / "terraform.tfstate").write_text(
                json.dumps(
                    {
                        "resources": [
                            {"type": "aws_instance", "instances": [{"attributes": {"monthly_cost_estimate": "75.0"}}]}
                        ]
                    }
                )
            )
            (stacks / "expensive" / "budget.json").write_text(json.dumps({"monthly_limit_usd": 50.0}))
            wd = TerraformWatchdog(str(stacks))
            findings = wd.check_all_stacks()
            assert len(findings) == 1
            assert findings[0].exceeded_budget is True
            assert findings[0].current_cost == 75.0
            assert findings[0].budget_limit == 50.0

    def test_check_all_stacks_skips_missing_stacks_dir(self) -> None:
        wd = TerraformWatchdog("/nonexistent/path")
        assert wd.check_all_stacks() == []

    def test_estimate_from_resource_type_fallback(self) -> None:
        assert TerraformWatchdog._estimate_from_resource_type("aws_instance", {}) == 50.0
        assert TerraformWatchdog._estimate_from_resource_type("google_compute_instance", {}) == 60.0
        assert TerraformWatchdog._estimate_from_resource_type("unknown_type", {}) == 10.0

    def test_watchdog_finding_attributes(self) -> None:
        f = WatchdogFinding(stack_name="s", exceeded_budget=True, current_cost=100.0, budget_limit=10.0)
        assert f.stack_name == "s"
        assert f.exceeded_budget is True
        assert f.current_cost == 100.0
        assert f.budget_limit == 10.0


# ═══════════════════════════════════════════════════════════════════════════
# Variable substitution edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestOverrideApply:
    def test_override_apply_returns_default_when_config_is_none(self) -> None:
        resolve = _override_apply(None)
        assert resolve("gpu_count", 1) == 1

    def test_override_apply_returns_compute_default_when_override_is_none(self) -> None:
        from types import SimpleNamespace

        ns = SimpleNamespace(gpu_count=None)
        resolve = _override_apply(ns)
        assert resolve("gpu_count", 2) == 2

    def test_override_apply_returns_override_when_set(self) -> None:
        from types import SimpleNamespace

        ns = SimpleNamespace(gpu_count=4, model_name="custom/model")
        resolve = _override_apply(ns)
        assert resolve("gpu_count", 2) == 4
        assert resolve("model_name", "default") == "custom/model"

    def test_override_apply_empty_string_falls_back_to_default(self) -> None:
        from types import SimpleNamespace

        ns = SimpleNamespace(model_name="", region="")
        resolve = _override_apply(ns)
        assert resolve("model_name", "fallback-model") == "fallback-model"
        assert resolve("region", "us-east-1") == "us-east-1"


class TestInferenceBindHost:
    def test_loopback_cidr_returns_loopback(self) -> None:
        cfg = _config(allowed_cidr="127.0.0.1/32")
        assert _inference_bind_host(cfg) == "127.0.0.1"

    def test_public_cidr_returns_unspecified(self) -> None:
        cfg = _config(allowed_cidr="0.0.0.0/0")
        assert _inference_bind_host(cfg) == "0.0.0.0"

    def test_mixed_cidrs_with_loopback_returns_unspecified(self) -> None:
        cfg = _config(allowed_cidr="127.0.0.1/32,10.0.0.0/8")
        assert _inference_bind_host(cfg) == "0.0.0.0"

    def test_empty_allowed_cidr_raises(self) -> None:
        cfg = MagicMock()
        cfg.allowed_cidr = ""
        with pytest.raises(ValueError, match="at least one network"):
            _inference_bind_host(cfg)

    def test_invalid_cidr_raises(self) -> None:
        cfg = MagicMock()
        cfg.allowed_cidr = "not-a-cidr"
        with pytest.raises(ValueError, match="invalid allowed_cidr"):
            _inference_bind_host(cfg)


class TestBuildTfvarsEdgeCases:
    def test_regionaless_provider_defaults_to_us_east_1(self) -> None:
        cfg = _config(provider=ComputeProvider.AWS, region=None)
        tfvars = TerraformGenerator().build_tfvars(cfg)
        assert '"us-east-1"' in tfvars

    def test_llamacpp_engine_produces_llamacpp_prefixed_fields(self) -> None:
        cfg = _config(engine=InferenceEngine.LLAMACPP, workload_type="batch_inference")
        tfvars = TerraformGenerator().build_tfvars(cfg)
        assert "llamacpp_context_length" in tfvars

    def test_vllm_engine_produces_vllm_prefixed_fields(self) -> None:
        cfg = _config(engine=InferenceEngine.VLLM, workload_type="realtime_api")
        tfvars = TerraformGenerator().build_tfvars(cfg)
        assert "vllm_context_length" in tfvars

    def test_tfvars_every_line_has_valid_hcl_assignment(self) -> None:
        cfg = _config()
        tfvars = TerraformGenerator().build_tfvars(cfg)
        for line in tfvars.strip().splitlines():
            if not line.strip():
                continue
            assert " = " in line, f"Line missing assignment: {line!r}"

    def test_build_tfvars_with_container_image_uses_escape(self) -> None:
        cfg = _config(container_image="ghcr.io/org/repo:v1.0")
        tfvars = TerraformGenerator().build_tfvars(cfg)
        assert '"ghcr.io/org/repo:v1.0"' in tfvars


class TestEscapeTfvarValueDeep:
    def test_backslash_escape_order(self) -> None:
        out = escape_tfvar_value("a\\b")
        assert out == '"a\\\\b"'

    def test_multiple_escape_classes_in_one_string(self) -> None:
        payload = 'hello "world" ${var.x}\nbye'
        out = escape_tfvar_value(payload)
        body = out[1:-1]
        assert '\\"' in body
        assert "\\${" in body
        assert "\\n" in body
        assert "\n" not in body

    def test_empty_string(self) -> None:
        assert escape_tfvar_value("") == '""'

    def test_forward_slash_is_not_escaped(self) -> None:
        assert escape_tfvar_value("a/b/c") == '"a/b/c"'


class TestValidationEdgeCases:
    def test_azure_containerapp_gpu_restriction_enforced(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.H100,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            deploy_type="containerapp",
        )
        with pytest.raises(ValueError, match="serverless GPU supports only"):
            gen.generate(cfg)

    def test_azure_containerapp_requires_vllm(self) -> None:
        gen = TerraformGenerator()
        cfg = _config(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            gpu_count=1,
            engine=InferenceEngine.LLAMACPP,
            deploy_type="containerapp",
        )
        with pytest.raises(ValueError, match="require the vLLM engine"):
            gen.generate(cfg)

    def test_compute_config_minimum_valid_fields(self) -> None:
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            model_name="test/model",
        )
        assert cfg.provider == ComputeProvider.AWS
        assert cfg.gpu_type == GPUType.T4
        assert cfg.model_name == "test/model"

    def test_compute_instance_minimum_valid_fields(self) -> None:
        from general_ludd.infra.compute import ComputeInstance

        inst = ComputeInstance(
            instance_id="i-abc123",
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
        )
        assert inst.instance_id == "i-abc123"
        assert inst.status == "pending"
        assert inst.port == 8000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
