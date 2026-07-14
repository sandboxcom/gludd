"""Structural tests for infra/deploy_precheck.py — precheck and build_deployment_dict."""

from __future__ import annotations

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.deploy_precheck import build_deployment_dict, precheck


class TestBuildDeploymentDict:
    def test_basic_config(self):
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            engine=InferenceEngine.VLLM,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
        )
        req = {}
        result = build_deployment_dict(config, req)
        assert result == {"engine": "vllm"}

    def test_includes_serving_knobs(self):
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            engine=InferenceEngine.VLLM,
            gpu_type=GPUType.H100,
            gpu_count=1,
        )
        req = {
            "gpu_memory_utilization": 0.9,
            "max_model_len": 4096,
            "tensor_parallel_size": 1,
        }
        result = build_deployment_dict(config, req)
        assert result["engine"] == "vllm"
        assert result["gpu_memory_utilization"] == 0.9
        assert result["max_model_len"] == 4096
        assert result["tensor_parallel_size"] == 1

    def test_ignores_unknown_request_keys(self):
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            engine=InferenceEngine.VLLM,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
        )
        req = {"unknown_key": "value"}
        result = build_deployment_dict(config, req)
        assert "unknown_key" not in result

    def test_includes_model_subdict(self):
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            engine=InferenceEngine.VLLM,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
        )
        req = {"model": {"name": "llama2", "revision": "main"}}
        result = build_deployment_dict(config, req)
        assert result["model"] == {"name": "llama2", "revision": "main"}
        assert result["engine"] == "vllm"

    def test_none_req_becomes_dict(self):
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            engine=InferenceEngine.VLLM,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
        )
        result = build_deployment_dict(config, None)  # type: ignore[arg-type]
        assert result == {"engine": "vllm"}

    def test_non_dict_req_becomes_dict(self):
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            engine=InferenceEngine.VLLM,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
        )
        result = build_deployment_dict(config, ["not", "a", "dict"])  # type: ignore[arg-type]
        assert result == {"engine": "vllm"}

    def test_llamacpp_engine(self):
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            engine=InferenceEngine.LLAMACPP,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
        )
        req = {}
        result = build_deployment_dict(config, req)
        assert result["engine"] == "llamacpp"


class TestPrecheck:
    def test_returns_two_lists(self):
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            engine=InferenceEngine.VLLM,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
        )
        findings, remediations = precheck(config, {})
        assert isinstance(findings, list)
        assert isinstance(remediations, list)

    def test_lists_same_length(self):
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            engine=InferenceEngine.VLLM,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
        )
        findings, remediations = precheck(config, {})
        assert len(findings) == len(remediations)

    def test_does_not_raise_on_basic_config(self):
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            engine=InferenceEngine.VLLM,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
        )
        findings, _remediations = precheck(config, {})
        assert isinstance(findings, list)
