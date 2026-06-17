"""Misconfig detector for vLLM / llama.cpp serving (#76).

Implements the rule a-g ruleset from ``docs/MODEL_DEPLOYMENT.md``. Each rule
pairs a predicate (over static config + runtime signals) with a
:class:`Remediation` whose ``config_patch`` is a *pure config transform*: given
the offending config it returns the keys to overlay to fix it.

``detect`` evaluates every rule against a config + hardware/model profiles and a
metrics/signal bag, returning structured :class:`Finding` objects. Each Finding
carries machine-readable evidence and a proposed patch the remediation loop can
apply.

Detection is **fail-closed**: a rule whose required evidence is *missing* but
whose static precondition is dangerous still fires (e.g. TP on a non-NVLink box
fires from the static config alone, no log line required).
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from general_ludd.infra.deployment_optimizer import (
    HardwareProfile,
    ModelProfile,
    kv_cache_bytes,
)

__all__ = [
    "DEFAULT_RULES",
    "Finding",
    "MisconfigRule",
    "Remediation",
    "Severity",
    "Signal",
    "detect",
]


class Severity(enum.IntEnum):
    """Ordered so callers can threshold (``>= Severity.FATAL``)."""

    INFO = 10
    WARNING = 20
    ERROR = 30
    FATAL = 40


class Signal(enum.StrEnum):
    """The observability signals the rules key off of."""

    GPU_MEM_UTIL = "gpu_mem_util"  # 0..1 fraction of VRAM in use
    TOKENS_PER_SEC = "tokens_per_sec"
    BASELINE_TOKENS_PER_SEC = "baseline_tokens_per_sec"
    TTFT_MS = "ttft_ms"
    OOM_EVENTS = "oom_events"  # count of CUDA OOM / EngineDeadError lines
    PREEMPTION_COUNT = "preemption_count"
    GPU_BLOCKS_FREE = "gpu_blocks_free"
    GPU_BLOCKS_NEEDED = "gpu_blocks_needed"
    HOST_RAM_UTIL = "host_ram_util"  # 0..1
    PCIE_TRAFFIC_GBPS = "pcie_traffic_gbps"
    SWAP_USED_GB = "swap_used_gb"
    LOG_LINES = "log_lines"  # list[str] of recent error lines
    KERNEL_ERROR = "kernel_error"  # "no kernel image" / "fp8 not supported"
    CPU_UTIL = "cpu_util"  # 0..1


# Short aliases keep the (many) predicate/patch signatures within line length.
_Cfg = Mapping[str, Any]
_Met = Mapping[str, Any]
_Patch = dict[str, Any]

# A config_patch takes (config, hardware, model, metrics) and returns the dict
# of keys to overlay onto config to remediate. Pure: no side effects.
PatchFn = Callable[
    [Mapping[str, Any], HardwareProfile, ModelProfile, Mapping[str, Any]],
    dict[str, Any],
]

# A predicate over the same inputs; True => the misconfig is present.
PredicateFn = Callable[
    [Mapping[str, Any], HardwareProfile, ModelProfile, Mapping[str, Any]],
    bool,
]


@dataclass(frozen=True)
class Remediation:
    """A proposed fix: a pure config patch + whether it needs a restart."""

    config_patch: PatchFn
    requires_restart: bool = True
    summary: str = ""


@dataclass(frozen=True)
class MisconfigRule:
    rule_id: str
    engine: str  # "vllm" | "llamacpp" | "any"
    signal: Signal
    severity: Severity
    predicate: PredicateFn
    remediation: Remediation
    description: str = ""

    def applies_to(self, engine: str) -> bool:
        return self.engine in ("any", engine)


@dataclass
class Finding:
    rule_id: str
    severity: Severity
    signal: Signal
    evidence: dict[str, Any]
    proposed_patch: dict[str, Any]
    requires_restart: bool = True
    description: str = ""


# ---------------------------------------------------------------------------
# Small helpers shared by predicates
# ---------------------------------------------------------------------------


def _get(metrics: Mapping[str, Any], signal: Signal, default: Any = None) -> Any:
    return metrics.get(signal.value, metrics.get(str(signal), default))


def _log_contains(metrics: Mapping[str, Any], *needles: str) -> bool:
    lines = _get(metrics, Signal.LOG_LINES, []) or []
    if isinstance(lines, str):
        lines = [lines]
    blob = "\n".join(str(x) for x in lines).lower()
    return any(n.lower() in blob for n in needles)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Rule a — gpu-mem-util too high -> peak OOM
# ---------------------------------------------------------------------------


def _pred_a(config: Mapping[str, Any], hw: HardwareProfile, model: ModelProfile, m: Mapping[str, Any]) -> bool:
    util = config.get("gpu_memory_utilization")
    oom = _num(_get(m, Signal.OOM_EVENTS, 0))
    preempt = _num(_get(m, Signal.PREEMPTION_COUNT, 0))
    mem = _num(_get(m, Signal.GPU_MEM_UTIL, 0.0))
    oom_logged = _log_contains(m, "cuda out of memory", "enginedeaderror", "out of memory")
    # Fire when util is set aggressively high AND we see OOM/preemption/near-full mem.
    too_high = isinstance(util, (int, float)) and float(util) >= 0.95
    pressure = oom > 0 or oom_logged or preempt > 0 or mem >= 0.98
    return bool(too_high and pressure)


def _patch_a(config: _Cfg, hw: HardwareProfile, model: ModelProfile, m: _Met) -> _Patch:
    util = _num(config.get("gpu_memory_utilization", 0.95), 0.95)
    new_util = max(0.80, round(util - 0.05, 3))
    patch: dict[str, Any] = {"gpu_memory_utilization": new_util}
    # Also cap max_num_seqs to relieve peak KV pressure.
    seqs = config.get("max_num_seqs")
    if isinstance(seqs, int) and seqs > 32:
        patch["max_num_seqs"] = max(32, seqs // 2)
    return patch


# ---------------------------------------------------------------------------
# Rule b — max-model-len exceeds KV budget
# ---------------------------------------------------------------------------


def _kv_pool_bytes(config: Mapping[str, Any], hw: HardwareProfile, model: ModelProfile) -> float:
    gmu = _num(config.get("gpu_memory_utilization", 0.9), 0.9)
    total = hw.total_vram_gb * 1e9
    # weights estimate via the dtype/quant in config (default bf16).
    quant = str(config.get("quantization") or config.get("dtype") or "bf16")
    from general_ludd.infra.deployment_optimizer import _quant_bytes_per_param  # local import

    try:
        bpp = _quant_bytes_per_param(quant)
    except ValueError:
        bpp = 2.0
    weights = model.params_b * 1e9 * bpp
    overhead = total * 0.05
    return (total * gmu) - weights - overhead


def _pred_b(config: Mapping[str, Any], hw: HardwareProfile, model: ModelProfile, m: Mapping[str, Any]) -> bool:
    # Signal-side: explicit "no available memory for KV cache" or blocks shortfall.
    log_hit = _log_contains(m, "no available memory for the kv cache", "no available memory for kv cache")
    free = _get(m, Signal.GPU_BLOCKS_FREE, None)
    need = _get(m, Signal.GPU_BLOCKS_NEEDED, None)
    blocks_short = free is not None and need is not None and _num(free) < _num(need)
    # Static-side (fail-closed): the configured max_model_len * max_num_seqs KV
    # cannot fit the computed KV pool.
    max_len = config.get("max_model_len")
    seqs = config.get("max_num_seqs", 1)
    static_short = False
    if isinstance(max_len, int) and max_len > 0:
        kv_dtype = "fp8" if config.get("kv_cache_dtype") == "fp8" else "fp16"
        need_bytes = kv_cache_bytes(model, max_len, int(seqs) if isinstance(seqs, int) else 1, kv_dtype)
        pool = _kv_pool_bytes(config, hw, model)
        static_short = need_bytes > pool > 0
    return bool(log_hit or blocks_short or static_short)


def _patch_b(config: _Cfg, hw: HardwareProfile, model: ModelProfile, m: _Met) -> _Patch:
    seqs = int(config.get("max_num_seqs", 1)) if isinstance(config.get("max_num_seqs", 1), int) else 1
    pool = _kv_pool_bytes(config, hw, model)
    # If KV is 16-bit, first try halving it via fp8 KV (on capable cards).
    using_fp8_kv = config.get("kv_cache_dtype") == "fp8"
    if not using_fp8_kv and hw.supports_fp8:
        return {"kv_cache_dtype": "fp8"}
    # Otherwise reduce max_model_len to what fits (round down to 256).
    kv_dtype = "fp8" if using_fp8_kv else "fp16"
    per_token = kv_cache_bytes(model, 1, max(seqs, 1), kv_dtype)
    fit_len = int(pool // per_token) if per_token > 0 and pool > 0 else 0
    fit_len = (fit_len // 256) * 256
    if fit_len >= 256:
        return {"max_model_len": fit_len}
    # Last resort, fail-closed: cut concurrency.
    return {"max_num_seqs": max(1, seqs // 2)}


# ---------------------------------------------------------------------------
# Rule c — TP != gpu count, or TP on non-NVLink
# ---------------------------------------------------------------------------


def _pred_c(config: Mapping[str, Any], hw: HardwareProfile, model: ModelProfile, m: Mapping[str, Any]) -> bool:
    tp = config.get("tensor_parallel_size", 1)
    if not isinstance(tp, int) or tp < 1:
        return True
    # TP on a non-NVLink box (PCIe-only / consumer) is a misconfig.
    if tp > 1 and not hw.has_nvlink:
        return True
    # TP must divide kv heads and not exceed gpu_count.
    if tp > hw.gpu_count:
        return True
    if model.num_kv_heads % tp != 0:
        return True
    log_hit = _log_contains(m, "world_size", "world size", "must be divisible", "head")
    return bool(log_hit and tp > 1 and model.num_kv_heads % tp != 0)


def _patch_c(config: _Cfg, hw: HardwareProfile, model: ModelProfile, m: _Met) -> _Patch:
    if not hw.has_nvlink:
        # PCIe-only -> TP=1 + pipeline parallel across the cards.
        return {
            "tensor_parallel_size": 1,
            "pipeline_parallel_size": max(1, hw.gpu_count),
        }
    # NVLink box: pick the largest valid divisor of kv heads <= gpu_count.
    for tp in range(hw.gpu_count, 0, -1):
        if model.num_kv_heads % tp == 0:
            return {"tensor_parallel_size": tp, "pipeline_parallel_size": 1}
    return {"tensor_parallel_size": 1, "pipeline_parallel_size": 1}


# ---------------------------------------------------------------------------
# Rule d — CPU offload / swap thrash
# ---------------------------------------------------------------------------


def _pred_d(config: Mapping[str, Any], hw: HardwareProfile, model: ModelProfile, m: Mapping[str, Any]) -> bool:
    offloading = (
        _num(config.get("cpu_offload_gb", 0)) > 0
        or _num(config.get("swap_space", 0)) > 0
    )
    tps = _num(_get(m, Signal.TOKENS_PER_SEC, 0.0))
    base = _num(_get(m, Signal.BASELINE_TOKENS_PER_SEC, 0.0))
    host_ram = _num(_get(m, Signal.HOST_RAM_UTIL, 0.0))
    swap = _num(_get(m, Signal.SWAP_USED_GB, 0.0))
    # Thrash = offload configured AND (tok/s collapsed vs baseline OR host RAM/swap pressure).
    collapsed = base > 0 and tps > 0 and tps < base * 0.5
    pressure = host_ram >= 0.9 or swap > 0
    return bool(offloading and (collapsed or pressure))


def _patch_d(config: _Cfg, hw: HardwareProfile, model: ModelProfile, m: _Met) -> _Patch:
    # Drop the offload; suggest a smaller quant that fits VRAM.
    patch: dict[str, Any] = {"cpu_offload_gb": 0, "swap_space": 0}
    # Recommend an aggressive-but-supported quant to fit on-GPU.
    if hw.supports_fp8:
        patch["quantization"] = "fp8"
        patch["dtype"] = "fp8"
    else:
        patch["quantization"] = "awq"
    return patch


# ---------------------------------------------------------------------------
# Rule e — quant/dtype unsupported on arch
# ---------------------------------------------------------------------------


def _uses_fp8(config: Mapping[str, Any]) -> bool:
    return any(
        str(config.get(k, "")).lower() == "fp8"
        for k in ("quantization", "dtype", "kv_cache_dtype")
    )


def _pred_e(config: Mapping[str, Any], hw: HardwareProfile, model: ModelProfile, m: Mapping[str, Any]) -> bool:
    kernel_err = _log_contains(m, "no kernel image", "fp8 not supported", "not supported on this gpu")
    explicit = bool(_get(m, Signal.KERNEL_ERROR))
    # Static-side fail-closed: fp8 requested on a card that cannot do fp8.
    static = _uses_fp8(config) and not hw.supports_fp8
    return bool(kernel_err or explicit or static)


def _patch_e(config: _Cfg, hw: HardwareProfile, model: ModelProfile, m: _Met) -> _Patch:
    # Map arch -> supported quant. Pre-Hopper/non-FP8 -> AWQ/GPTQ-Marlin/INT8.
    patch: dict[str, Any] = {}
    if not hw.supports_fp8:
        patch["quantization"] = "awq"
        patch["dtype"] = "bf16"
        if config.get("kv_cache_dtype") == "fp8":
            patch["kv_cache_dtype"] = "auto"
    return patch


# ---------------------------------------------------------------------------
# Rule f — llama.cpp -ngl exceeds VRAM
# ---------------------------------------------------------------------------


def _llamacpp_weight_fraction_fit(config: Mapping[str, Any], hw: HardwareProfile, model: ModelProfile) -> float:
    quant = str(config.get("gguf_quant") or "q4_k_m")
    from general_ludd.infra.deployment_optimizer import _quant_bytes_per_param

    try:
        bpp = _quant_bytes_per_param(quant)
    except ValueError:
        bpp = 0.5
    weights = model.params_b * 1e9 * bpp
    usable = hw.total_vram_gb * 0.85 * 1e9
    if weights <= 0:
        return 1.0
    return max(0.0, min(1.0, usable / weights))


def _pred_f(config: Mapping[str, Any], hw: HardwareProfile, model: ModelProfile, m: Mapping[str, Any]) -> bool:
    ngl = config.get("n_gpu_layers")
    if not isinstance(ngl, int):
        return False
    # -1 means "all layers"; a positive value over the fit fraction is the bug.
    frac_fit = _llamacpp_weight_fraction_fit(config, hw, model)
    max_fit_layers = int(model.num_layers * frac_fit)
    requested = model.num_layers if ngl < 0 else ngl
    over_capacity = requested > max_fit_layers
    mem = _num(_get(m, Signal.GPU_MEM_UTIL, 0.0))
    tps = _num(_get(m, Signal.TOKENS_PER_SEC, 0.0))
    base = _num(_get(m, Signal.BASELINE_TOKENS_PER_SEC, 0.0))
    cpu = _num(_get(m, Signal.CPU_UTIL, 0.0))
    # Static-side fires when requested layers exceed VRAM capacity (fail-closed).
    if over_capacity:
        return True
    # Signal-side: VRAM ~full + low tok/s + CPU busy.
    return bool(mem >= 0.98 and base > 0 and tps < base * 0.5 and cpu >= 0.8)


def _patch_f(config: _Cfg, hw: HardwareProfile, model: ModelProfile, m: _Met) -> _Patch:
    frac_fit = _llamacpp_weight_fraction_fit(config, hw, model)
    max_fit_layers = max(0, int(model.num_layers * frac_fit))
    patch: dict[str, Any] = {
        "n_gpu_layers": max_fit_layers,
        "flash_attn": True,
        "cache_type_k": "q8_0",
        "cache_type_v": "q8_0",
    }
    return patch


# ---------------------------------------------------------------------------
# Rule g — flash-attn / CUDA-graphs off on a capable card
# ---------------------------------------------------------------------------


def _pred_g(config: Mapping[str, Any], hw: HardwareProfile, model: ModelProfile, m: Mapping[str, Any]) -> bool:
    engine = str(config.get("engine", "")).lower()
    if engine == "llamacpp":
        # Missing -fa on a GPU card.
        return config.get("flash_attn") is not True and hw.total_vram_gb > 0
    # vLLM: enforce_eager True disables CUDA graphs on a capable card.
    return config.get("enforce_eager") is True


def _patch_g(config: _Cfg, hw: HardwareProfile, model: ModelProfile, m: _Met) -> _Patch:
    engine = str(config.get("engine", "")).lower()
    if engine == "llamacpp":
        return {"flash_attn": True}
    return {"enforce_eager": False}


# ---------------------------------------------------------------------------
# Default ruleset
# ---------------------------------------------------------------------------

DEFAULT_RULES: list[MisconfigRule] = [
    MisconfigRule(
        rule_id="a",
        engine="vllm",
        signal=Signal.GPU_MEM_UTIL,
        severity=Severity.FATAL,
        predicate=_pred_a,
        remediation=Remediation(_patch_a, summary="lower gpu-mem-util, cap max-num-seqs"),
        description="gpu-memory-utilization too high causing peak OOM",
    ),
    MisconfigRule(
        rule_id="b",
        engine="vllm",
        signal=Signal.GPU_BLOCKS_FREE,
        severity=Severity.FATAL,
        predicate=_pred_b,
        remediation=Remediation(_patch_b, summary="reduce max-model-len / fp8 KV / lower seqs"),
        description="max-model-len exceeds the KV-cache budget",
    ),
    MisconfigRule(
        rule_id="c",
        engine="vllm",
        signal=Signal.TOKENS_PER_SEC,
        severity=Severity.FATAL,
        predicate=_pred_c,
        remediation=Remediation(_patch_c, summary="valid TP divisor; PCIe -> TP=1 + pipeline"),
        description="tensor-parallel size invalid or TP over non-NVLink",
    ),
    MisconfigRule(
        rule_id="d",
        engine="vllm",
        signal=Signal.TOKENS_PER_SEC,
        severity=Severity.ERROR,
        predicate=_pred_d,
        remediation=Remediation(_patch_d, summary="drop CPU offload/swap; smaller quant"),
        description="CPU offload / swap thrash",
    ),
    MisconfigRule(
        rule_id="e",
        engine="vllm",
        signal=Signal.KERNEL_ERROR,
        severity=Severity.FATAL,
        predicate=_pred_e,
        remediation=Remediation(_patch_e, requires_restart=True, summary="map arch -> supported quant (AWQ/GPTQ/INT8)"),
        description="quant/dtype unsupported on this arch",
    ),
    MisconfigRule(
        rule_id="f",
        engine="llamacpp",
        signal=Signal.GPU_MEM_UTIL,
        severity=Severity.ERROR,
        predicate=_pred_f,
        remediation=Remediation(_patch_f, requires_restart=True, summary="reduce -ngl to fit; -fa; quantized KV"),
        description="llama.cpp -ngl exceeds VRAM",
    ),
    MisconfigRule(
        rule_id="g",
        engine="any",
        signal=Signal.TOKENS_PER_SEC,
        severity=Severity.WARNING,
        predicate=_pred_g,
        remediation=Remediation(_patch_g, requires_restart=True, summary="enable CUDA graphs / flash-attn"),
        description="flash-attn / CUDA-graphs disabled on a capable card",
    ),
]


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


def detect(
    config: Mapping[str, Any],
    hardware: HardwareProfile,
    model: ModelProfile,
    metrics: Mapping[str, Any] | None = None,
    *,
    rules: list[MisconfigRule] | None = None,
) -> list[Finding]:
    """Evaluate the ruleset; return a Finding for every rule that fires.

    ``metrics`` is the signal bag (keys are :class:`Signal` values). Missing
    signals are treated fail-closed: a rule with a dangerous static config still
    fires even with an empty metrics bag.
    """
    metrics = metrics or {}
    active = rules if rules is not None else DEFAULT_RULES
    engine = str(config.get("engine", "")).lower()
    findings: list[Finding] = []
    for rule in active:
        if not rule.applies_to(engine):
            continue
        try:
            fired = rule.predicate(config, hardware, model, metrics)
        except Exception:  # pragma: no cover - a broken predicate must not crash detect
            fired = False
        if not fired:
            continue
        try:
            patch = rule.remediation.config_patch(config, hardware, model, metrics)
        except Exception:  # pragma: no cover
            patch = {}
        findings.append(
            Finding(
                rule_id=rule.rule_id,
                severity=rule.severity,
                signal=rule.signal,
                evidence=_evidence(rule, config, hardware, model, metrics),
                proposed_patch=patch,
                requires_restart=rule.remediation.requires_restart,
                description=rule.description,
            )
        )
    return findings


def _evidence(
    rule: MisconfigRule,
    config: Mapping[str, Any],
    hardware: HardwareProfile,
    model: ModelProfile,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Structured, machine-readable evidence snapshot for a Finding."""
    ev: dict[str, Any] = {
        "rule": rule.rule_id,
        "engine": config.get("engine"),
        "gpu_type": hardware.gpu_type,
        "gpu_count": hardware.gpu_count,
        "has_nvlink": hardware.has_nvlink,
        "supports_fp8": hardware.supports_fp8,
        "model": model.name,
        "num_kv_heads": model.num_kv_heads,
    }
    # Attach the config knobs and signals most relevant to this rule.
    for key in (
        "gpu_memory_utilization",
        "max_model_len",
        "max_num_seqs",
        "tensor_parallel_size",
        "quantization",
        "dtype",
        "kv_cache_dtype",
        "n_gpu_layers",
        "enforce_eager",
        "flash_attn",
        "cpu_offload_gb",
        "swap_space",
    ):
        if key in config:
            ev[key] = config[key]
    for sig in (
        Signal.GPU_MEM_UTIL,
        Signal.TOKENS_PER_SEC,
        Signal.BASELINE_TOKENS_PER_SEC,
        Signal.OOM_EVENTS,
        Signal.PREEMPTION_COUNT,
        Signal.GPU_BLOCKS_FREE,
        Signal.GPU_BLOCKS_NEEDED,
        Signal.HOST_RAM_UTIL,
        Signal.SWAP_USED_GB,
        Signal.CPU_UTIL,
    ):
        val = _get(metrics, sig, None)
        if val is not None:
            ev[sig.value] = val
    return ev
