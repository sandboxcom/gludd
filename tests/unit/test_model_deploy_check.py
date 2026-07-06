"""Misconfig matrix + safety tests for the model-deployment detector (#76).

Each rule must FIRE on a representative bad config and stay SILENT on a good
one. Malformed input must never raise — it must surface as a 'critical'
finding. Remediation patches must have a stable, yaml-first shape.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, cast

import pytest

from general_ludd.infra.model_deploy_check import (
    Finding,
    MisconfigDetector,
)

# ---------------------------------------------------------------------------
# Fixtures: representative "good" baselines per engine.
# ---------------------------------------------------------------------------


def _good_vllm() -> dict:
    # 8B fp16 (~16GB weights) on a single H100 (80GB), conservative knobs.
    return {
        "engine": "vllm",
        "model": {
            "name": "llama-3-8b",
            "num_layers": 32,
            "num_kv_heads": 8,
            "head_dim": 128,
            "params_b": 8.0,
            "is_moe": False,
        },
        "gpu_memory_utilization": 0.90,
        "tensor_parallel_size": 1,
        "pipeline_parallel_size": 1,
        "max_model_len": 8192,
        "max_num_seqs": 16,
        "quantization": None,
        "dtype": "bf16",
        "kv_cache_dtype": "auto",
        "enforce_eager": False,
    }


def _good_vllm_gpu() -> dict:
    # Hopper H100, single GPU, NVLink-capable (irrelevant at TP=1), fp8 ok.
    return {
        "gpu_type": "H100",
        "gpu_count": 1,
        "vram_gb": 80.0,
        "arch": "hopper",
        "has_nvlink": True,
        "supports_fp8": True,
        "compute_capability": 9.0,
    }


def _good_llamacpp() -> dict:
    # 8B Q4 (~5GB) on a 24GB consumer GPU, all layers offloaded.
    return {
        "engine": "llamacpp",
        "model": {
            "name": "llama-3-8b-gguf",
            "num_layers": 32,
            "params_b": 8.0,
        },
        "n_gpu_layers": 33,  # 32 layers + output layer
        "n_ctx": 4096,
        "n_parallel": 1,
        "quantization": "Q4_K_M",
        "flash_attn": True,
    }


def _good_llamacpp_gpu() -> dict:
    return {
        "gpu_type": "RTX4090",
        "gpu_count": 1,
        "vram_gb": 24.0,
        "arch": "ada",
        "has_nvlink": False,
        "supports_fp8": True,
        "compute_capability": 8.9,
    }


def _rule_ids(findings: list[Finding]) -> set[str]:
    return {f.rule_id for f in findings}


# ---------------------------------------------------------------------------
# Dataclass / API shape.
# ---------------------------------------------------------------------------


def test_finding_is_dataclass_with_required_fields() -> None:
    f = Finding(
        rule_id="x",
        severity="warn",
        engine="any",
        message="m",
        remediation="r",
        evidence={"k": 1},
    )
    assert is_dataclass(f)
    d = asdict(f)
    assert set(d) == {"rule_id", "severity", "engine", "message", "remediation", "evidence"}


def test_severity_values_are_constrained() -> None:
    det = MisconfigDetector()
    findings = det.check(_good_vllm(), _good_vllm_gpu())
    for f in findings:
        assert f.severity in {"info", "warn", "critical"}
        assert f.engine in {"vllm", "llamacpp", "any"}
        assert isinstance(f.remediation, str) and f.remediation
        assert isinstance(f.evidence, dict)


# ---------------------------------------------------------------------------
# Good configs are silent (no critical/warn findings).
# ---------------------------------------------------------------------------


def test_good_vllm_is_clean() -> None:
    det = MisconfigDetector()
    findings = det.check(_good_vllm(), _good_vllm_gpu())
    actionable = [f for f in findings if f.severity in {"warn", "critical"}]
    assert actionable == [], actionable


def test_good_llamacpp_is_clean() -> None:
    det = MisconfigDetector()
    findings = det.check(_good_llamacpp(), _good_llamacpp_gpu())
    actionable = [f for f in findings if f.severity in {"warn", "critical"}]
    assert actionable == [], actionable


# ---------------------------------------------------------------------------
# Rule a: gpu_memory_utilization too high / too low.
# ---------------------------------------------------------------------------


def test_rule_a_gpu_mem_util_too_high_fires() -> None:
    cfg = _good_vllm()
    cfg["gpu_memory_utilization"] = 0.99
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "a" in _rule_ids(findings)


def test_rule_a_gpu_mem_util_too_low_fires() -> None:
    cfg = _good_vllm()
    cfg["gpu_memory_utilization"] = 0.20
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "a" in _rule_ids(findings)


def test_rule_a_silent_on_good() -> None:
    findings = MisconfigDetector().check(_good_vllm(), _good_vllm_gpu())
    assert "a" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Rule b: max_model_len exceeds KV-cache budget.
# ---------------------------------------------------------------------------


def test_rule_b_max_len_exceeds_kv_budget_fires() -> None:
    cfg = _good_vllm()
    # Absurd context * many sequences blows past a 80GB card's KV pool.
    cfg["max_model_len"] = 1_000_000
    cfg["max_num_seqs"] = 256
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "b" in _rule_ids(findings)


def test_rule_b_silent_on_good() -> None:
    findings = MisconfigDetector().check(_good_vllm(), _good_vllm_gpu())
    assert "b" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Rule c: tensor_parallel_size invalid (not dividing GPU count / > GPUs /
# on a non-NVLink box).
# ---------------------------------------------------------------------------


def test_rule_c_tp_exceeds_gpu_count_fires() -> None:
    cfg = _good_vllm()
    cfg["tensor_parallel_size"] = 4
    gpu = _good_vllm_gpu()
    gpu["gpu_count"] = 2
    findings = MisconfigDetector().check(cfg, gpu)
    assert "c" in _rule_ids(findings)


def test_rule_c_tp_not_dividing_gpu_count_fires() -> None:
    cfg = _good_vllm()
    cfg["tensor_parallel_size"] = 3
    gpu = _good_vllm_gpu()
    gpu["gpu_count"] = 4
    findings = MisconfigDetector().check(cfg, gpu)
    assert "c" in _rule_ids(findings)


def test_rule_c_tp_on_non_nvlink_fires() -> None:
    cfg = _good_vllm()
    cfg["tensor_parallel_size"] = 2
    gpu = _good_vllm_gpu()
    gpu["gpu_count"] = 2
    gpu["has_nvlink"] = False
    gpu["gpu_type"] = "L40S"
    gpu["arch"] = "ada"
    findings = MisconfigDetector().check(cfg, gpu)
    assert "c" in _rule_ids(findings)


def test_rule_c_silent_on_good_multi_gpu_nvlink() -> None:
    cfg = _good_vllm()
    cfg["tensor_parallel_size"] = 2
    gpu = _good_vllm_gpu()
    gpu["gpu_count"] = 2
    gpu["has_nvlink"] = True
    findings = MisconfigDetector().check(cfg, gpu)
    assert "c" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Rule d: CPU offload / swap thrash — any offload on a GPU box is warned.
# ---------------------------------------------------------------------------


def test_rule_d_cpu_offload_fires() -> None:
    cfg = _good_vllm()
    cfg["cpu_offload_gb"] = 40
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "d" in _rule_ids(findings)


def test_rule_d_swap_space_fires() -> None:
    cfg = _good_vllm()
    cfg["swap_space"] = 16
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "d" in _rule_ids(findings)


def test_rule_d_zero_offload_silent() -> None:
    cfg = _good_vllm()
    cfg["cpu_offload_gb"] = 0
    cfg["swap_space"] = 0
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "d" not in _rule_ids(findings)


def test_rule_d_no_offload_silent() -> None:
    findings = MisconfigDetector().check(_good_vllm(), _good_vllm_gpu())
    assert "d" not in _rule_ids(findings)


def test_rule_d_remediation_drops_all_offload() -> None:
    cfg = _good_vllm()
    cfg["cpu_offload_gb"] = 20
    det = MisconfigDetector()
    findings = det.check(cfg, _good_vllm_gpu())
    finding = next(f for f in findings if f.rule_id == "d")
    patch = det.remediate(finding)
    assert patch["config_patch"] == {"cpu_offload_gb": 0, "swap_space": 0}
    assert patch["requires_restart"] is True


# ---------------------------------------------------------------------------
# Rule e: quant/dtype unsupported on arch (fp8 on pre-Hopper).
# ---------------------------------------------------------------------------


def test_rule_e_fp8_on_pre_hopper_fires() -> None:
    cfg = _good_vllm()
    cfg["quantization"] = "fp8"
    gpu = _good_vllm_gpu()
    gpu["gpu_type"] = "A100"
    gpu["arch"] = "ampere"
    gpu["supports_fp8"] = False
    gpu["compute_capability"] = 8.0
    findings = MisconfigDetector().check(cfg, gpu)
    assert "e" in _rule_ids(findings)


def test_rule_e_fp8_on_hopper_silent() -> None:
    cfg = _good_vllm()
    cfg["quantization"] = "fp8"
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "e" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Rule g: flash-attn / CUDA-graphs off on capable card.
# ---------------------------------------------------------------------------


def test_rule_g_enforce_eager_on_capable_card_fires() -> None:
    cfg = _good_vllm()
    cfg["enforce_eager"] = True
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "g" in _rule_ids(findings)


def test_rule_g_silent_on_good() -> None:
    findings = MisconfigDetector().check(_good_vllm(), _good_vllm_gpu())
    assert "g" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Rule f: llama.cpp n_gpu_layers exceeds model layers (or 0 on a GPU box).
# ---------------------------------------------------------------------------


def test_rule_f_ngl_exceeds_model_layers_fires() -> None:
    cfg = _good_llamacpp()
    cfg["n_gpu_layers"] = 99  # model only has 32 (+1 output)
    findings = MisconfigDetector().check(cfg, _good_llamacpp_gpu())
    assert "f" in _rule_ids(findings)


def test_rule_f_silent_on_good() -> None:
    findings = MisconfigDetector().check(_good_llamacpp(), _good_llamacpp_gpu())
    assert "f" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Rule h (superset): llama.cpp n_gpu_layers == 0 on a GPU box (wasted GPU).
# ---------------------------------------------------------------------------


def test_rule_h_zero_ngl_on_gpu_box_fires() -> None:
    cfg = _good_llamacpp()
    cfg["n_gpu_layers"] = 0
    findings = MisconfigDetector().check(cfg, _good_llamacpp_gpu())
    assert "h" in _rule_ids(findings)


def test_rule_h_zero_ngl_no_gpu_silent() -> None:
    cfg = _good_llamacpp()
    cfg["n_gpu_layers"] = 0
    gpu = {"gpu_count": 0, "vram_gb": 0.0}
    findings = MisconfigDetector().check(cfg, gpu)
    assert "h" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Rule i (superset): llama.cpp n_ctx vs VRAM (context too big to fit KV).
# ---------------------------------------------------------------------------


def test_rule_i_ctx_too_big_for_vram_fires() -> None:
    cfg = _good_llamacpp()
    cfg["n_ctx"] = 2_000_000
    cfg["n_parallel"] = 8
    findings = MisconfigDetector().check(cfg, _good_llamacpp_gpu())
    assert "i" in _rule_ids(findings)


def test_rule_i_silent_on_good() -> None:
    findings = MisconfigDetector().check(_good_llamacpp(), _good_llamacpp_gpu())
    assert "i" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Rule j (superset): dtype vs compute capability (bf16 below sm_80).
# ---------------------------------------------------------------------------


def test_rule_j_bf16_below_sm80_fires() -> None:
    cfg = _good_vllm()
    cfg["dtype"] = "bf16"
    gpu = _good_vllm_gpu()
    gpu["gpu_type"] = "V100"
    gpu["arch"] = "volta"
    gpu["compute_capability"] = 7.0
    gpu["supports_fp8"] = False
    findings = MisconfigDetector().check(cfg, gpu)
    assert "j" in _rule_ids(findings)


def test_rule_j_silent_on_good() -> None:
    findings = MisconfigDetector().check(_good_vllm(), _good_vllm_gpu())
    assert "j" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Rule k (superset): missing tensor/pipeline parallel on multi-GPU box.
# ---------------------------------------------------------------------------


def test_rule_k_no_parallel_on_multi_gpu_fires() -> None:
    cfg = _good_vllm()
    cfg["tensor_parallel_size"] = 1
    cfg["pipeline_parallel_size"] = 1
    gpu = _good_vllm_gpu()
    gpu["gpu_count"] = 4
    gpu["has_nvlink"] = True
    findings = MisconfigDetector().check(cfg, gpu)
    assert "k" in _rule_ids(findings)


def test_rule_k_silent_on_single_gpu() -> None:
    findings = MisconfigDetector().check(_good_vllm(), _good_vllm_gpu())
    assert "k" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Rule l (superset): max_num_seqs / parallel slots vs memory.
# ---------------------------------------------------------------------------


def test_rule_l_too_many_seqs_fires() -> None:
    cfg = _good_vllm()
    cfg["max_num_seqs"] = 100_000
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "l" in _rule_ids(findings)


def test_rule_l_silent_on_good() -> None:
    findings = MisconfigDetector().check(_good_vllm(), _good_vllm_gpu())
    assert "l" not in _rule_ids(findings)


# ---------------------------------------------------------------------------
# Malformed-input safety: NEVER raise; surface as a 'critical' finding.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "not-a-dict",
        42,
        [],
        {"engine": "vllm", "gpu_memory_utilization": "lots"},
        {"engine": "vllm", "tensor_parallel_size": "two"},
        {"engine": "unknown-engine"},
        {"engine": "vllm", "model": "should-be-a-dict"},
        {"engine": "llamacpp", "n_gpu_layers": None},
        {"engine": "vllm", "max_model_len": -5},
    ],
)
def test_malformed_input_never_raises(bad: object) -> None:
    det = MisconfigDetector()
    findings = cast(Any, det).check(bad)
    assert isinstance(findings, list)
    assert all(isinstance(f, Finding) for f in findings)


def test_malformed_input_yields_critical() -> None:
    det = MisconfigDetector()
    findings = cast(Any, det).check("totally-broken")
    assert any(f.severity == "critical" for f in findings)


def test_malformed_gpu_info_never_raises() -> None:
    det = MisconfigDetector()
    findings = cast(Any, det).check(_good_vllm(), gpu_info="not-a-dict")
    assert isinstance(findings, list)


def test_gpu_info_none_is_safe() -> None:
    det = MisconfigDetector()
    findings = det.check(_good_vllm(), None)
    assert isinstance(findings, list)


# ---------------------------------------------------------------------------
# Injected GPU-info provider.
# ---------------------------------------------------------------------------


def test_injected_gpu_provider_is_used() -> None:
    calls: list[bool] = []

    def provider() -> dict:
        calls.append(True)
        return _good_vllm_gpu()

    det = MisconfigDetector(gpu_info_provider=provider)
    det.check(_good_vllm())  # no gpu_info passed -> provider used
    assert calls == [True]


def test_explicit_gpu_info_overrides_provider() -> None:
    def provider() -> dict:
        raise AssertionError("provider should not be called when gpu_info given")

    det = MisconfigDetector(gpu_info_provider=provider)
    findings = det.check(_good_vllm(), _good_vllm_gpu())
    assert isinstance(findings, list)


def test_provider_that_raises_is_contained() -> None:
    def provider() -> dict:
        raise RuntimeError("nvidia-smi exploded")

    det = MisconfigDetector(gpu_info_provider=provider)
    findings = det.check(_good_vllm())
    assert isinstance(findings, list)  # did not propagate


# ---------------------------------------------------------------------------
# Remediation patch shape.
# ---------------------------------------------------------------------------


def test_remediate_returns_patch_shape() -> None:
    cfg = _good_vllm()
    cfg["gpu_memory_utilization"] = 0.99
    det = MisconfigDetector()
    findings = det.check(cfg, _good_vllm_gpu())
    finding = next(f for f in findings if f.rule_id == "a")
    patch = det.remediate(finding)
    assert isinstance(patch, dict)
    assert patch["rule_id"] == "a"
    assert "config_patch" in patch
    assert isinstance(patch["config_patch"], dict)
    assert "requires_restart" in patch
    assert isinstance(patch["requires_restart"], bool)
    assert "format" in patch and patch["format"] == "yaml"


def test_remediate_every_rule_yields_patch() -> None:
    det = MisconfigDetector()
    # Build a config that trips several rules at once.
    cfg = _good_vllm()
    cfg["gpu_memory_utilization"] = 0.99
    cfg["enforce_eager"] = True
    cfg["max_num_seqs"] = 100_000
    findings = det.check(cfg, _good_vllm_gpu())
    assert findings
    for f in findings:
        patch = det.remediate(f)
        assert isinstance(patch, dict)
        assert isinstance(patch["config_patch"], dict)


def test_remediate_unknown_finding_is_safe() -> None:
    det = MisconfigDetector()
    f = Finding(
        rule_id="zzz-unknown",
        severity="info",
        engine="any",
        message="m",
        remediation="r",
        evidence={},
    )
    patch = det.remediate(f)
    assert isinstance(patch, dict)
    assert patch["rule_id"] == "zzz-unknown"
