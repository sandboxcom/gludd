"""Unit tests for the misconfig detector ruleset a-g (#76)."""

from __future__ import annotations

from typing import Any

from general_ludd.infra.deployment_optimizer import (
    ModelProfile,
    hardware_profile_for,
)
from general_ludd.infra.misconfig_detector import (
    DEFAULT_RULES,
    Finding,
    Severity,
    Signal,
    detect,
)


def model_8b() -> ModelProfile:
    return ModelProfile(
        name="llama-3-8b",
        num_layers=32,
        num_kv_heads=8,
        head_dim=128,
        params_b=8.0,
    )


def model_70b() -> ModelProfile:
    return ModelProfile(
        name="llama-3-70b",
        num_layers=80,
        num_kv_heads=8,
        head_dim=128,
        params_b=70.0,
    )


def _ids(findings: list[Finding]) -> set[str]:
    return {f.rule_id for f in findings}


def _by_id(findings: list[Finding], rule_id: str) -> Finding:
    for f in findings:
        if f.rule_id == rule_id:
            return f
    raise AssertionError(f"rule {rule_id!r} did not fire; got {_ids(findings)}")


# ---------------------------------------------------------------------------
# Rule a — gpu-mem-util too high -> OOM
# ---------------------------------------------------------------------------


class TestRuleA:
    def test_detects_high_util_with_oom(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        config = {"engine": "vllm", "gpu_memory_utilization": 0.98, "max_num_seqs": 256}
        metrics = {Signal.OOM_EVENTS.value: 3, Signal.GPU_MEM_UTIL.value: 0.99}
        f = _by_id(detect(config, hw, model_8b(), metrics), "a")
        assert f.severity == Severity.FATAL
        # Remediation lowers util by 0.05 (floored at 0.80) and caps seqs.
        assert f.proposed_patch["gpu_memory_utilization"] == 0.93
        assert f.proposed_patch["max_num_seqs"] == 128

    def test_util_floor_080(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        config = {"engine": "vllm", "gpu_memory_utilization": 0.95}
        metrics = {Signal.LOG_LINES.value: ["CUDA out of memory"]}
        f = _by_id(detect(config, hw, model_8b(), metrics), "a")
        assert f.proposed_patch["gpu_memory_utilization"] >= 0.80

    def test_no_fire_when_safe(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        config = {"engine": "vllm", "gpu_memory_utilization": 0.90}
        metrics = {Signal.GPU_MEM_UTIL.value: 0.70}
        assert "a" not in _ids(detect(config, hw, model_8b(), metrics))


# ---------------------------------------------------------------------------
# Rule b — max-model-len exceeds KV budget
# ---------------------------------------------------------------------------


class TestRuleB:
    def test_detects_log_line(self) -> None:
        hw = hardware_profile_for("a100_80", gpu_count=1)
        config = {"engine": "vllm", "max_model_len": 8192, "max_num_seqs": 4, "gpu_memory_utilization": 0.9}
        metrics = {Signal.LOG_LINES.value: ["ValueError: No available memory for the KV cache"]}
        f = _by_id(detect(config, hw, model_8b(), metrics), "b")
        assert f.severity == Severity.FATAL

    def test_detects_block_shortfall(self) -> None:
        hw = hardware_profile_for("a100_80", gpu_count=1)
        config = {"engine": "vllm", "max_model_len": 4096, "max_num_seqs": 8, "gpu_memory_utilization": 0.9}
        metrics = {Signal.GPU_BLOCKS_FREE.value: 100, Signal.GPU_BLOCKS_NEEDED.value: 5000}
        assert "b" in _ids(detect(config, hw, model_8b(), metrics))

    def test_static_overcommit_fires_without_signals(self) -> None:
        # 8B weights (~16GB) fit the A100-80, but a huge max_len * high
        # concurrency KV cannot fit the remaining pool -> fires from static
        # config alone (no signals). A100 has no fp8 -> patch reduces length.
        hw = hardware_profile_for("a100_80", gpu_count=1)
        config = {
            "engine": "vllm",
            "max_model_len": 131072,
            "max_num_seqs": 256,
            "gpu_memory_utilization": 0.9,
            "quantization": "bf16",
        }
        f = _by_id(detect(config, hw, model_8b(), {}), "b")
        # No fp8 on A100 -> patch reduces length or concurrency.
        assert any(k in f.proposed_patch for k in ("max_model_len", "max_num_seqs"))

    def test_patch_prefers_fp8_kv_on_capable_card(self) -> None:
        # Same overcommit on an H100 (fp8-capable) -> patch first tries fp8 KV.
        hw = hardware_profile_for("h100", gpu_count=1)
        config = {
            "engine": "vllm",
            "max_model_len": 131072,
            "max_num_seqs": 256,
            "gpu_memory_utilization": 0.9,
            "quantization": "bf16",
        }
        f = _by_id(detect(config, hw, model_8b(), {}), "b")
        assert f.proposed_patch.get("kv_cache_dtype") == "fp8" or "max_model_len" in f.proposed_patch


# ---------------------------------------------------------------------------
# Rule c — TP invalid / TP on non-NVLink
# ---------------------------------------------------------------------------


class TestRuleC:
    def test_tp_on_non_nvlink_fires(self) -> None:
        hw = hardware_profile_for("l40s", gpu_count=2)  # PCIe only
        config = {"engine": "vllm", "tensor_parallel_size": 2}
        f = _by_id(detect(config, hw, model_8b(), {}), "c")
        assert f.proposed_patch["tensor_parallel_size"] == 1
        assert f.proposed_patch["pipeline_parallel_size"] == 2

    def test_tp_not_dividing_heads_fires(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=8)
        # 8 kv heads; TP=3 does not divide.
        config = {"engine": "vllm", "tensor_parallel_size": 3}
        f = _by_id(detect(config, hw, model_8b(), {}), "c")
        # Remediation picks largest valid divisor <= gpu_count.
        assert 8 % f.proposed_patch["tensor_parallel_size"] == 0

    def test_tp_exceeds_gpu_count_fires(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=2)
        config = {"engine": "vllm", "tensor_parallel_size": 8}
        assert "c" in _ids(detect(config, hw, model_8b(), {}))

    def test_valid_tp_no_fire(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=8)
        config = {"engine": "vllm", "tensor_parallel_size": 8}
        assert "c" not in _ids(detect(config, hw, model_8b(), {}))


# ---------------------------------------------------------------------------
# Rule d — CPU offload / swap thrash
# ---------------------------------------------------------------------------


class TestRuleD:
    def test_offload_with_collapsed_throughput(self) -> None:
        hw = hardware_profile_for("a100_80", gpu_count=1)
        config = {"engine": "vllm", "cpu_offload_gb": 40}
        metrics = {
            Signal.TOKENS_PER_SEC.value: 5.0,
            Signal.BASELINE_TOKENS_PER_SEC.value: 100.0,
        }
        f = _by_id(detect(config, hw, model_8b(), metrics), "d")
        assert f.proposed_patch["cpu_offload_gb"] == 0
        assert "quantization" in f.proposed_patch

    def test_swap_pressure_fires(self) -> None:
        hw = hardware_profile_for("a100_80", gpu_count=1)
        config = {"engine": "vllm", "swap_space": 16}
        metrics = {Signal.HOST_RAM_UTIL.value: 0.95, Signal.SWAP_USED_GB.value: 8.0}
        assert "d" in _ids(detect(config, hw, model_8b(), metrics))

    def test_no_offload_no_fire(self) -> None:
        hw = hardware_profile_for("a100_80", gpu_count=1)
        config = {"engine": "vllm"}
        metrics = {Signal.TOKENS_PER_SEC.value: 5.0, Signal.BASELINE_TOKENS_PER_SEC.value: 100.0}
        assert "d" not in _ids(detect(config, hw, model_8b(), metrics))


# ---------------------------------------------------------------------------
# Rule e — quant/dtype unsupported on arch
# ---------------------------------------------------------------------------


class TestRuleE:
    def test_fp8_on_a100_fires_statically(self) -> None:
        hw = hardware_profile_for("a100_80", gpu_count=1)  # no fp8
        config = {"engine": "vllm", "quantization": "fp8", "dtype": "fp8"}
        f = _by_id(detect(config, hw, model_8b(), {}), "e")
        assert f.proposed_patch["quantization"] == "awq"
        assert f.proposed_patch["dtype"] != "fp8"

    def test_kernel_error_log_fires(self) -> None:
        hw = hardware_profile_for("a100_80", gpu_count=1)
        config = {"engine": "vllm", "quantization": "awq"}
        metrics = {Signal.LOG_LINES.value: ["RuntimeError: no kernel image is available"]}
        assert "e" in _ids(detect(config, hw, model_8b(), metrics))

    def test_fp8_on_hopper_no_fire(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)  # fp8 native
        config = {"engine": "vllm", "quantization": "fp8", "dtype": "fp8"}
        assert "e" not in _ids(detect(config, hw, model_8b(), {}))


# ---------------------------------------------------------------------------
# Rule f — llama.cpp -ngl exceeds VRAM
# ---------------------------------------------------------------------------


class TestRuleF:
    def test_ngl_all_layers_over_vram_fires(self) -> None:
        # 70B q4 (~35GB) on a 24GB 4090, -ngl=-1 (all) exceeds VRAM.
        hw = hardware_profile_for("rtx_4090", gpu_count=1)
        config = {"engine": "llamacpp", "n_gpu_layers": -1, "gguf_quant": "q4_k_m"}
        f = _by_id(detect(config, hw, model_70b(), {}), "f")
        ngl = f.proposed_patch["n_gpu_layers"]
        assert isinstance(ngl, int) and 0 <= ngl < 80
        assert f.proposed_patch["flash_attn"] is True

    def test_signal_based_fire(self) -> None:
        hw = hardware_profile_for("rtx_4090", gpu_count=1)
        # 8B q4 fits, so static won't fire; the runtime signals (VRAM full +
        # low tok/s + CPU busy) should.
        config = {"engine": "llamacpp", "n_gpu_layers": 32, "gguf_quant": "q4_k_m"}
        metrics = {
            Signal.GPU_MEM_UTIL.value: 0.99,
            Signal.TOKENS_PER_SEC.value: 4.0,
            Signal.BASELINE_TOKENS_PER_SEC.value: 60.0,
            Signal.CPU_UTIL.value: 0.95,
        }
        assert "f" in _ids(detect(config, hw, model_8b(), metrics))

    def test_fits_no_fire(self) -> None:
        hw = hardware_profile_for("rtx_4090", gpu_count=1)
        config = {"engine": "llamacpp", "n_gpu_layers": -1, "gguf_quant": "q4_k_m"}
        assert "f" not in _ids(detect(config, hw, model_8b(), {}))


# ---------------------------------------------------------------------------
# Rule g — flash-attn / CUDA-graphs off on capable card
# ---------------------------------------------------------------------------


class TestRuleG:
    def test_enforce_eager_fires_vllm(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        config = {"engine": "vllm", "enforce_eager": True}
        f = _by_id(detect(config, hw, model_8b(), {}), "g")
        assert f.proposed_patch["enforce_eager"] is False
        assert f.severity == Severity.WARNING

    def test_missing_flash_attn_fires_llamacpp(self) -> None:
        hw = hardware_profile_for("rtx_4090", gpu_count=1)
        config = {"engine": "llamacpp", "n_gpu_layers": -1, "flash_attn": False, "gguf_quant": "q4_k_m"}
        f = _by_id(detect(config, hw, model_8b(), {}), "g")
        assert f.proposed_patch["flash_attn"] is True

    def test_no_fire_when_enabled(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        config = {"engine": "vllm", "enforce_eager": False}
        assert "g" not in _ids(detect(config, hw, model_8b(), {}))


# ---------------------------------------------------------------------------
# detect() machinery
# ---------------------------------------------------------------------------


class TestDetectMachinery:
    def test_all_seven_rules_present(self) -> None:
        assert {r.rule_id for r in DEFAULT_RULES} == {"a", "b", "c", "d", "e", "f", "g"}

    def test_findings_carry_evidence_and_patch(self) -> None:
        hw = hardware_profile_for("l40s", gpu_count=2)
        config = {"engine": "vllm", "tensor_parallel_size": 2}
        f = _by_id(detect(config, hw, model_8b(), {}), "c")
        assert f.evidence["gpu_type"] == "l40s"
        assert f.evidence["has_nvlink"] is False
        assert isinstance(f.proposed_patch, dict) and f.proposed_patch

    def test_patches_are_pure_no_mutation(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        config: dict[str, Any] = {"engine": "vllm", "enforce_eager": True}
        snapshot = dict(config)
        detect(config, hw, model_8b(), {})
        assert config == snapshot  # detect must not mutate the input config

    def test_engine_filtering(self) -> None:
        # llama.cpp rule f must not fire for a vLLM config.
        hw = hardware_profile_for("rtx_4090", gpu_count=1)
        config = {"engine": "vllm", "enforce_eager": False, "tensor_parallel_size": 1}
        ids = _ids(detect(config, hw, model_8b(), {}))
        assert "f" not in ids

    def test_clean_config_no_findings(self) -> None:
        hw = hardware_profile_for("h100", gpu_count=1)
        config = {
            "engine": "vllm",
            "tensor_parallel_size": 1,
            "gpu_memory_utilization": 0.90,
            "max_model_len": 4096,
            "max_num_seqs": 8,
            "enforce_eager": False,
            "quantization": "fp8",
            "dtype": "fp8",
        }
        assert detect(config, hw, model_8b(), {}) == []
