"""Unit tests for the pre-deploy misconfig precheck (SLICE B)."""

from __future__ import annotations

from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deploy_precheck import build_deployment_dict, precheck


def _config(**overrides: object) -> ComputeConfig:
    base: dict[str, object] = {
        "provider": ComputeProvider.AWS,
        "gpu_type": GPUType.A10,
        "gpu_count": 1,
        "engine": InferenceEngine.VLLM,
        "model_name": "org/model",
    }
    base.update(overrides)
    return ComputeConfig(**base)  # type: ignore[arg-type]  # test stub


def test_build_deployment_dict_maps_engine_and_serving_knobs() -> None:
    config = _config(engine=InferenceEngine.VLLM, gpu_type=GPUType.A10, gpu_count=1)
    dep = build_deployment_dict(config, {"gpu_memory_utilization": 0.99})

    assert dep["engine"] == "vllm"
    assert dep["gpu_memory_utilization"] == 0.99


def test_build_deployment_dict_pulls_model_subdict_and_all_knobs() -> None:
    config = _config(engine=InferenceEngine.VLLM)
    req = {
        "max_model_len": 8192,
        "max_num_seqs": 32,
        "tensor_parallel_size": 2,
        "quantization": "fp8",
        "dtype": "bf16",
        "n_gpu_layers": 40,
        "n_ctx": 4096,
        "n_parallel": 4,
        "model": {"num_layers": 32, "params_b": 7.0},
        "ignored_key": "dropped",
    }
    dep = build_deployment_dict(config, req)

    assert dep["engine"] == "vllm"
    assert dep["max_model_len"] == 8192
    assert dep["max_num_seqs"] == 32
    assert dep["tensor_parallel_size"] == 2
    assert dep["quantization"] == "fp8"
    assert dep["dtype"] == "bf16"
    assert dep["n_gpu_layers"] == 40
    assert dep["n_ctx"] == 4096
    assert dep["n_parallel"] == 4
    assert dep["model"] == {"num_layers": 32, "params_b": 7.0}
    # Unknown keys are not copied through.
    assert "ignored_key" not in dep


def test_build_deployment_dict_omits_absent_knobs() -> None:
    config = _config()
    dep = build_deployment_dict(config, {})
    assert dep == {"engine": "vllm"}


def test_precheck_returns_critical_rule_a_finding_and_patch() -> None:
    config = _config(engine=InferenceEngine.VLLM, gpu_type=GPUType.A10, gpu_count=1)
    findings, remediations = precheck(config, {"gpu_memory_utilization": 0.99})

    rule_a = [f for f in findings if f.rule_id == "a"]
    assert rule_a, "expected a rule-a finding for gpu_memory_utilization=0.99"
    assert rule_a[0].severity == "critical"

    # Findings and remediations are index-aligned; rule-a yields a config patch.
    assert len(remediations) == len(findings)
    a_index = findings.index(rule_a[0])
    patch = remediations[a_index]
    assert patch["rule_id"] == "a"
    assert "gpu_memory_utilization" in patch["config_patch"]


def test_precheck_clean_config_has_no_critical_findings() -> None:
    config = _config(engine=InferenceEngine.VLLM, gpu_type=GPUType.A10, gpu_count=1)
    findings, remediations = precheck(config, {"gpu_memory_utilization": 0.90})

    assert not [f for f in findings if f.severity == "critical"]
    assert len(remediations) == len(findings)
