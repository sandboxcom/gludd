"""Deployment optimizer for vLLM / llama.cpp serving configs (#76).

Grounds in ``docs/MODEL_DEPLOYMENT.md``. Given a :class:`ModelProfile` and a
:class:`HardwareProfile` (mapped from the arch/cloud tables below), produces a
serving config dict for vLLM or llama.cpp that:

* picks ``tensor_parallel_size`` that respects NVLink (TP only across NVLinked
  GPUs; PCIe-only / consumer cards -> TP=1 + pipeline),
* sets ``gpu_memory_utilization``,
* fits ``max_model_len`` via the KV-cache budget formula,
* picks a quantization compatible with the arch (FP8 only when the card
  supports it) that fits in VRAM,
* sets dtype and flash-attn.

Workload-aware deployment (2026-07): the optimizer accepts a
:class:`WorkloadType` that tunes the config for the specific serving pattern
(batch inference, realtime API, fine-tuning, speculative decoding,
embedding generation). Each workload has a :class:`ModelDeploymentProfile`
that overrides context_length, max_tokens, batch_size, tensor_parallel,
gpu_memory, quantization, threads, and other knobs.

The KV-cache formula (the core sizing primitive) is exposed as
:func:`kv_cache_bytes` so it can be unit-tested in isolation.

Design bias: **fail-closed**. When in doubt the optimizer prefers the smaller,
safer knob (lower util, TP=1, more aggressive quant, shorter context) rather
than a config that might OOM or thrash.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TypedDict

__all__ = [
    "CLOUD_INSTANCE_TABLE",
    "GPU_TABLE",
    "WORKLOAD_PROFILES",
    "HardwareProfile",
    "ModelDeploymentProfile",
    "ModelProfile",
    "WorkloadType",
    "hardware_profile_for",
    "kv_cache_bytes",
    "profile_from_search",
    "recommend_config",
]


# ---------------------------------------------------------------------------
# Workload type enum
# ---------------------------------------------------------------------------


class WorkloadType(enum.StrEnum):
    BATCH_INFERENCE = "batch_inference"
    REALTIME_API = "realtime_api"
    FINE_TUNING = "fine_tuning"
    SPECULATIVE_DECODING = "speculative_decoding"
    EMBEDDING_GENERATION = "embedding_generation"


# ---------------------------------------------------------------------------
# Model deployment profile — workload-specific overrides
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelDeploymentProfile:
    """Workload-specific overrides for a model serving config.

    Fields set to ``None`` mean "use the optimizer's default / computed value".
    Only the fields relevant to the workload are set; the rest flow through
    from the base ``recommend_config`` output.
    """

    context_length: int | None = None
    max_tokens: int | None = None
    batch_size: int | None = None
    tensor_parallel: int | None = None
    gpu_memory_utilization: float | None = None
    quantization: str | None = None
    threads: int | None = None
    max_num_seqs: int | None = None
    enforce_eager: bool | None = None
    enable_prefix_caching: bool | None = None
    enable_chunked_prefill: bool | None = None
    kv_cache_dtype: str | None = None

    def apply(self, config: dict[str, object]) -> dict[str, object]:
        """Merge workload-specific overrides into a base config dict.

        Only overrides non-None fields; all other keys pass through unchanged.
        """
        merged = dict(config)
        if self.context_length is not None:
            merged["max_model_len"] = self.context_length
        if self.max_tokens is not None:
            merged["max_tokens"] = self.max_tokens
        if self.batch_size is not None:
            merged["batch_size"] = self.batch_size
        if self.tensor_parallel is not None:
            merged["tensor_parallel_size"] = self.tensor_parallel
        if self.gpu_memory_utilization is not None:
            merged["gpu_memory_utilization"] = self.gpu_memory_utilization
        if self.quantization is not None:
            merged["quantization"] = self.quantization
        if self.threads is not None:
            merged["threads"] = self.threads
        if self.max_num_seqs is not None:
            merged["max_num_seqs"] = self.max_num_seqs
        if self.enforce_eager is not None:
            merged["enforce_eager"] = self.enforce_eager
        if self.enable_prefix_caching is not None:
            merged["enable_prefix_caching"] = self.enable_prefix_caching
        if self.enable_chunked_prefill is not None:
            merged["enable_chunked_prefill"] = self.enable_chunked_prefill
        if self.kv_cache_dtype is not None:
            merged["kv_cache_dtype"] = self.kv_cache_dtype
        return merged


# Workload → profile mapping. Each profile overrides knobs that differ from
# the optimizer's default serving config for that workload pattern.
# ``None`` fields flow through from the base recommend_config output.
WORKLOAD_PROFILES: dict[WorkloadType, ModelDeploymentProfile] = {
    WorkloadType.BATCH_INFERENCE: ModelDeploymentProfile(
        batch_size=128,
        max_num_seqs=256,
        enable_prefix_caching=True,
        enable_chunked_prefill=True,
    ),
    WorkloadType.REALTIME_API: ModelDeploymentProfile(
        batch_size=8,
        max_num_seqs=64,
        enable_prefix_caching=True,
        enable_chunked_prefill=False,
        enforce_eager=False,
    ),
    WorkloadType.FINE_TUNING: ModelDeploymentProfile(
        context_length=8192,
        batch_size=4,
        max_num_seqs=8,
        gpu_memory_utilization=0.85,
        enforce_eager=True,
        enable_prefix_caching=False,
        enable_chunked_prefill=False,
    ),
    WorkloadType.SPECULATIVE_DECODING: ModelDeploymentProfile(
        batch_size=1,
        max_num_seqs=32,
        enforce_eager=True,
        enable_prefix_caching=True,
    ),
    WorkloadType.EMBEDDING_GENERATION: ModelDeploymentProfile(
        context_length=512,
        batch_size=256,
        max_num_seqs=512,
        gpu_memory_utilization=0.95,
        enable_prefix_caching=False,
        enable_chunked_prefill=False,
    ),
}


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HardwareProfile:
    """A GPU box the model will be served on.

    ``has_nvlink`` gates tensor-parallelism: TP all-reduce is interconnect-bound,
    so it is only worthwhile across NVLinked GPUs. ``supports_fp8`` gates the FP8
    quant/dtype path (native only on Ada/Hopper, see the arch table).
    """

    gpu_type: str
    gpu_count: int = 1
    vram_gb: float = 0.0  # per-GPU VRAM
    has_nvlink: bool = False
    supports_fp8: bool = False
    hbm_bw_gbps: float = 0.0  # decode is HBM-bandwidth-bound
    arch: str = ""

    @property
    def total_vram_gb(self) -> float:
        return self.vram_gb * self.gpu_count


@dataclass(frozen=True)
class ModelProfile:
    """Architecture facts needed to size weights + KV cache.

    ``num_kv_heads`` is the GQA KV-head count (often 8), NOT the attention head
    count — using the attention head count overestimates the KV cache.
    ``head_dim = hidden_size / num_attention_heads``.
    """

    name: str
    num_layers: int
    num_kv_heads: int
    head_dim: int
    params_b: float  # parameter count, in billions
    is_moe: bool = False
    native_quant: str | None = None  # e.g. "fp8" for DeepSeek-V3
    # active params for MoE throughput accounting; defaults to params_b.
    active_params_b: float | None = None

    def weights_bytes(self, quant: str) -> float:
        """Approximate weight footprint (bytes) at a given quantization.

        For MoE, VRAM must hold ALL experts (params_b), even though throughput
        scales with active params.
        """
        return self.params_b * 1e9 * _quant_bytes_per_param(quant)


# ---------------------------------------------------------------------------
# Quant / dtype helpers
# ---------------------------------------------------------------------------

# Bytes per parameter for weight storage at each quantization.
_QUANT_BYTES_PER_PARAM: dict[str, float] = {
    "fp16": 2.0,
    "bf16": 2.0,
    "fp8": 1.0,
    "int8": 1.0,
    "awq": 0.5,  # 4-bit
    "gptq": 0.5,  # 4-bit
    "q8_0": 1.0,
    "q6_k": 0.75,
    "q5_k_m": 0.625,
    "q4_k_m": 0.5,
}

# dtype bytes for KV-cache entries.
_KV_DTYPE_BYTES: dict[str, int] = {
    "fp16": 2,
    "bf16": 2,
    "fp8": 1,
    "int8": 1,
    "q8_0": 1,
}


def _quant_bytes_per_param(quant: str) -> float:
    try:
        return _QUANT_BYTES_PER_PARAM[quant.lower()]
    except KeyError as exc:  # fail-closed: unknown quant is an error
        raise ValueError(f"unknown quantization {quant!r}") from exc


def _kv_dtype_bytes(dtype: str) -> int:
    try:
        return _KV_DTYPE_BYTES[dtype.lower()]
    except KeyError as exc:  # fail-closed
        raise ValueError(f"unknown kv-cache dtype {dtype!r}") from exc


# ---------------------------------------------------------------------------
# Static GPU / cloud tables (from docs/MODEL_DEPLOYMENT.md arch + cloud sections)
# ---------------------------------------------------------------------------

class _GpuSpec(TypedDict):
    vram_gb: float
    has_nvlink: bool
    supports_fp8: bool
    hbm_bw_gbps: float
    arch: str


class _InstanceSpec(TypedDict):
    gpu_type: str
    gpu_count: int
    has_nvlink: bool


# gpu_type -> spec. Keys match general_ludd.infra.compute.GPUType values.
GPU_TABLE: dict[str, _GpuSpec] = {
    # Hopper — NVSwitch, native FP8.
    "h200": {"vram_gb": 141.0, "has_nvlink": True, "supports_fp8": True, "hbm_bw_gbps": 4800.0, "arch": "hopper"},
    "h100": {"vram_gb": 80.0, "has_nvlink": True, "supports_fp8": True, "hbm_bw_gbps": 3350.0, "arch": "hopper"},
    # Ampere DC — NVSwitch/NVLink, no FP8.
    "a100_80": {"vram_gb": 80.0, "has_nvlink": True, "supports_fp8": False, "hbm_bw_gbps": 2039.0, "arch": "ampere"},
    "a100_40": {"vram_gb": 40.0, "has_nvlink": True, "supports_fp8": False, "hbm_bw_gbps": 1555.0, "arch": "ampere"},
    "a40": {"vram_gb": 48.0, "has_nvlink": True, "supports_fp8": False, "hbm_bw_gbps": 696.0, "arch": "ampere"},
    "a10": {"vram_gb": 24.0, "has_nvlink": False, "supports_fp8": False, "hbm_bw_gbps": 600.0, "arch": "ampere"},
    "a10g": {"vram_gb": 24.0, "has_nvlink": False, "supports_fp8": False, "hbm_bw_gbps": 600.0, "arch": "ampere"},
    "t4": {"vram_gb": 16.0, "has_nvlink": False, "supports_fp8": False, "hbm_bw_gbps": 320.0, "arch": "turing"},
    # Ada — PCIe only (no NVLink), FP8 supported.
    "l40s": {"vram_gb": 48.0, "has_nvlink": False, "supports_fp8": True, "hbm_bw_gbps": 864.0, "arch": "ada"},
    "l4": {"vram_gb": 24.0, "has_nvlink": False, "supports_fp8": True, "hbm_bw_gbps": 300.0, "arch": "ada"},
    "rtx_6000_ada": {"vram_gb": 48.0, "has_nvlink": False, "supports_fp8": True, "hbm_bw_gbps": 960.0, "arch": "ada"},
    # Consumer — no NVLink, P2P off; 4090 has FP8, 3090 does not.
    "rtx_4090": {"vram_gb": 24.0, "has_nvlink": False, "supports_fp8": True, "hbm_bw_gbps": 1008.0, "arch": "ada"},
    "rtx_3090": {"vram_gb": 24.0, "has_nvlink": False, "supports_fp8": False, "hbm_bw_gbps": 936.0, "arch": "ampere"},
}

# cloud instance -> (gpu_type, gpu_count, has_nvlink). From the cloud section.
# has_nvlink here reflects the *box topology* (overrides the per-card default
# when a vendor wires NVLink/NVSwitch across the cards in the instance).
CLOUD_INSTANCE_TABLE: dict[str, _InstanceSpec] = {
    # AWS
    "p5": {"gpu_type": "h100", "gpu_count": 8, "has_nvlink": True},
    "p4d": {"gpu_type": "a100_40", "gpu_count": 8, "has_nvlink": True},
    "g6e": {"gpu_type": "l40s", "gpu_count": 1, "has_nvlink": False},
    "g5": {"gpu_type": "a10g", "gpu_count": 1, "has_nvlink": False},
    "g6": {"gpu_type": "l4", "gpu_count": 1, "has_nvlink": False},
    # GCP
    "a3": {"gpu_type": "h100", "gpu_count": 8, "has_nvlink": True},
    "a2": {"gpu_type": "a100_40", "gpu_count": 8, "has_nvlink": True},
    "g2": {"gpu_type": "l4", "gpu_count": 1, "has_nvlink": False},
    # Azure
    "ndv5": {"gpu_type": "h100", "gpu_count": 8, "has_nvlink": True},
    "ndv4": {"gpu_type": "a100_80", "gpu_count": 8, "has_nvlink": True},
    "ncads": {"gpu_type": "l40s", "gpu_count": 1, "has_nvlink": False},
}


def hardware_profile_for(
    gpu_type: str,
    gpu_count: int = 1,
    *,
    has_nvlink: bool | None = None,
    instance: str | None = None,
) -> HardwareProfile:
    """Build a :class:`HardwareProfile` from the static tables.

    If ``instance`` is given it is resolved against :data:`CLOUD_INSTANCE_TABLE`
    first (it supplies gpu_type, count, and box-level NVLink topology). An
    explicit ``has_nvlink`` overrides everything. Unknown GPUs fail closed.
    """
    if instance is not None:
        row = CLOUD_INSTANCE_TABLE.get(instance.lower())
        if row is None:
            raise ValueError(f"unknown cloud instance {instance!r}")
        gpu_type = row["gpu_type"]
        gpu_count = row["gpu_count"]
        if has_nvlink is None:
            has_nvlink = row["has_nvlink"]

    spec = GPU_TABLE.get(gpu_type.lower())
    if spec is None:
        raise ValueError(f"unknown gpu_type {gpu_type!r}")

    nvlink = spec["has_nvlink"] if has_nvlink is None else bool(has_nvlink)
    # Fail-closed: a single GPU can never run NVLink TP regardless of topology.
    if gpu_count < 2:
        nvlink = False

    return HardwareProfile(
        gpu_type=gpu_type.lower(),
        gpu_count=gpu_count,
        vram_gb=spec["vram_gb"],
        has_nvlink=nvlink,
        supports_fp8=spec["supports_fp8"],
        hbm_bw_gbps=spec["hbm_bw_gbps"],
        arch=spec["arch"],
    )


# ---------------------------------------------------------------------------
# KV-cache formula
# ---------------------------------------------------------------------------


def kv_cache_bytes(
    model: ModelProfile,
    max_len: int,
    max_seqs: int,
    dtype: str = "fp16",
) -> int:
    """KV-cache footprint in bytes.

    ``KV_bytes = 2 * num_layers * max_model_len * max_num_seqs
                   * num_kv_heads * head_dim * dtype_bytes``

    The leading ``2`` is K+V. ``num_kv_heads`` is the GQA KV-head count.
    """
    if max_len <= 0 or max_seqs <= 0:
        raise ValueError("max_len and max_seqs must be positive")
    dtype_bytes = _kv_dtype_bytes(dtype)
    total = (
        2
        * model.num_layers
        * max_len
        * max_seqs
        * model.num_kv_heads
        * model.head_dim
        * dtype_bytes
    )
    return int(total)


# ---------------------------------------------------------------------------
# Quant selection
# ---------------------------------------------------------------------------

# Candidate quants in *descending* quality order. The optimizer walks this and
# picks the highest-quality quant whose weights fit, subject to arch support.
_VLLM_QUANT_LADDER: tuple[str, ...] = ("bf16", "fp8", "int8", "awq")
_LLAMACPP_QUANT_LADDER: tuple[str, ...] = ("q8_0", "q6_k", "q5_k_m", "q4_k_m")


def _quant_supported_on_arch(quant: str, hardware: HardwareProfile) -> bool:
    """Whether a quant/dtype can actually run on this card.

    FP8 weights/dtype need native FP8 (Ada/Hopper). AWQ/GPTQ/INT8 (Marlin
    kernels) run on pre-Hopper. bf16/fp16 run everywhere.
    """
    q = quant.lower()
    if q == "fp8":
        return hardware.supports_fp8
    return True


def _select_quant_dtype(
    model: ModelProfile,
    hardware: HardwareProfile,
    engine: str,
    gpu_memory_utilization: float,
    *,
    allow_partial: bool = False,
) -> tuple[str, str]:
    """Pick (quantization, dtype) that fits weights into usable VRAM.

    Fail-closed: walks from highest quality to most aggressive; if even the most
    aggressive quant does not fit, raises rather than returning an OOM config.

    ``allow_partial`` (llama.cpp): if nothing fits fully, return the most
    aggressive supported quant so the caller can size a *partial* GPU offload
    (``-ngl`` < num_layers) rather than failing outright.
    """
    # Native-quant models (e.g. DeepSeek-V3 FP8) are pinned to their native quant
    # when the arch supports it.
    if model.native_quant and _quant_supported_on_arch(model.native_quant, hardware):
        ladder: tuple[str, ...] = (model.native_quant, *(
            _LLAMACPP_QUANT_LADDER if engine == "llamacpp" else _VLLM_QUANT_LADDER
        ))
    elif engine == "llamacpp":
        ladder = _LLAMACPP_QUANT_LADDER
    else:
        ladder = _VLLM_QUANT_LADDER

    # Reserve VRAM for KV + activation overhead: only weights below the usable
    # pool (leaving headroom for at least a minimal KV pool) are acceptable.
    usable = hardware.total_vram_gb * gpu_memory_utilization * 1e9
    # Keep ~15% of the usable pool for KV/activations at minimum (fail-closed).
    weight_budget = usable * 0.85

    most_aggressive: str | None = None
    for quant in ladder:
        if not _quant_supported_on_arch(quant, hardware):
            continue
        most_aggressive = quant  # last supported quant in the (descending) ladder
        if model.weights_bytes(quant) <= weight_budget:
            dtype = _dtype_for_quant(quant, hardware)
            return quant, dtype

    if allow_partial and most_aggressive is not None:
        # Nothing fits fully; hand back the smallest quant for a partial offload.
        return most_aggressive, _dtype_for_quant(most_aggressive, hardware)

    raise ValueError(
        f"model {model.name!r} ({model.params_b}B) does not fit on "
        f"{hardware.gpu_count}x{hardware.gpu_type} "
        f"({hardware.total_vram_gb:.0f}GB) at any supported quant"
    )


def _dtype_for_quant(quant: str, hardware: HardwareProfile) -> str:
    q = quant.lower()
    if q == "fp8":
        return "fp8"
    if q in ("bf16", "fp16"):
        # Prefer bf16 on Ampere+; everything we model is Ampere or newer.
        return "bf16"
    # Quantized weights still compute through a 16-bit compute dtype.
    return "bf16"


# ---------------------------------------------------------------------------
# Tensor-parallel selection
# ---------------------------------------------------------------------------


def _divides_heads(tp: int, model: ModelProfile) -> bool:
    """TP must evenly divide the KV-head count (and stay >= 1)."""
    if tp < 1:
        return False
    return model.num_kv_heads % tp == 0


def _select_tensor_parallel(model: ModelProfile, hardware: HardwareProfile) -> int:
    """Largest valid TP size.

    Fail-closed rules from the doc:
    * TP only across NVLinked GPUs — PCIe-only / consumer cards => TP=1
      (use pipeline parallelism instead).
    * TP must divide num_kv_heads.
    * TP <= gpu_count.
    """
    if not hardware.has_nvlink or hardware.gpu_count < 2:
        return 1
    # Walk down from gpu_count to the first divisor of kv heads.
    for tp in range(hardware.gpu_count, 0, -1):
        if tp <= hardware.gpu_count and _divides_heads(tp, model):
            return tp
    return 1


# ---------------------------------------------------------------------------
# max_model_len fitting
# ---------------------------------------------------------------------------


def _fit_max_model_len_with_weights(
    model: ModelProfile,
    hardware: HardwareProfile,
    weights_bytes: float,
    gpu_memory_utilization: float,
    max_num_seqs: int,
    kv_dtype: str,
    desired_max_len: int,
) -> int:
    """Largest max_model_len whose KV cache fits the pool, given on-GPU weights.

    KV_pool = gpu_mem_util * total_VRAM - weights - overhead. The KV cache for
    ``max_num_seqs`` concurrent sequences at the chosen length must fit in that
    pool, else requests preempt/queue (rule b). We shrink fail-closed.
    """
    total_vram_bytes = hardware.total_vram_gb * 1e9
    pool = total_vram_bytes * gpu_memory_utilization
    # ~5% activation/cudagraph overhead, fail-closed.
    overhead = total_vram_bytes * 0.05
    kv_pool = pool - weights_bytes - overhead
    if kv_pool <= 0:
        return 0

    # bytes per token of KV across all layers/seqs at length 1.
    per_token = kv_cache_bytes(model, max_len=1, max_seqs=max_num_seqs, dtype=kv_dtype)
    if per_token <= 0:
        return 0
    fit_len = int(kv_pool // per_token)
    capped = min(desired_max_len, fit_len)
    # Round down to a sane multiple of 256 (and never below 0).
    capped = (capped // 256) * 256
    return max(capped, 0)


def _fit_max_model_len(
    model: ModelProfile,
    hardware: HardwareProfile,
    quant: str,
    gpu_memory_utilization: float,
    max_num_seqs: int,
    kv_dtype: str,
    desired_max_len: int,
    tensor_parallel_size: int,
) -> int:
    """Convenience wrapper that derives weights from ``quant`` (full offload)."""
    return _fit_max_model_len_with_weights(
        model,
        hardware,
        model.weights_bytes(quant),
        gpu_memory_utilization,
        max_num_seqs,
        kv_dtype,
        desired_max_len,
    )


# ---------------------------------------------------------------------------
# Public: recommend_config
# ---------------------------------------------------------------------------


@dataclass
class _RecommendDefaults:
    gpu_memory_utilization: float = 0.90
    max_num_seqs: int = 256
    desired_max_len: int = 32768
    kv_cache_dtype: str = "auto"  # "auto" => match weight dtype unless fp8 KV


def recommend_config(
    model: ModelProfile,
    hardware: HardwareProfile,
    engine: str,
    *,
    gpu_memory_utilization: float | None = None,
    max_num_seqs: int | None = None,
    desired_max_len: int | None = None,
    workload_type: WorkloadType | None = None,
) -> dict[str, object]:
    """Recommend a serving config dict for ``engine`` in {vllm, llamacpp}.

    Returns a dict carrying the chosen knobs. Fail-closed throughout: TP defaults
    to 1 unless NVLink truly supports it, FP8 only when ``supports_fp8``, and the
    quant/length are shrunk until they fit VRAM.

    When ``workload_type`` is given, the appropriate :class:`ModelDeploymentProfile`
    overrides are merged on top of the base config (batch_size, max_num_seqs,
    context_length, gpu_memory_utilization, etc.).
    """
    engine = engine.lower()
    if engine not in ("vllm", "llamacpp"):
        raise ValueError(f"unsupported engine {engine!r}")

    if workload_type is not None and workload_type not in WorkloadType:
        raise ValueError(f"unknown workload_type {workload_type!r}")

    d = _RecommendDefaults()

    # Workload profile overrides the defaults BEFORE the base config is built.
    profile = WORKLOAD_PROFILES.get(workload_type) if workload_type is not None else None

    # Track which knobs were explicitly passed by the caller — they must win
    # over the workload profile even during the post-hoc apply() merge.
    explicit_keys: set[str] = set()
    if gpu_memory_utilization is not None:
        explicit_keys.add("gpu_memory_utilization")
    if max_num_seqs is not None:
        explicit_keys.add("max_num_seqs")
    if desired_max_len is not None:
        explicit_keys.add("context_length")

    gmu = (profile.gpu_memory_utilization if profile and profile.gpu_memory_utilization is not None
           else d.gpu_memory_utilization)
    gmu = gpu_memory_utilization if gpu_memory_utilization is not None else gmu
    if not (0.0 < gmu <= 0.95):
        raise ValueError("gpu_memory_utilization must be in (0, 0.95]")
    seqs = (profile.max_num_seqs if profile and profile.max_num_seqs is not None
            else d.max_num_seqs)
    seqs = max_num_seqs if max_num_seqs is not None else seqs
    want_len = (profile.context_length if profile and profile.context_length is not None
                else d.desired_max_len)
    want_len = desired_max_len if desired_max_len is not None else want_len

    # llama.cpp can partially offload, so it may proceed with a quant that does
    # not fully fit; vLLM must fully fit (KV is pre-allocated).
    quant, dtype = _select_quant_dtype(
        model, hardware, engine, gmu, allow_partial=(engine == "llamacpp")
    )

    # Workload-specific quantization override (e.g. embedding may prefer int8).
    if profile and profile.quantization is not None and "quantization" not in explicit_keys:
        quant = profile.quantization
        dtype = _dtype_for_quant(quant, hardware)

    # KV dtype: use fp8 KV only when the card supports fp8; else 16-bit.
    kv_dtype = "fp8" if hardware.supports_fp8 else "fp16"
    if profile and profile.kv_cache_dtype is not None and "kv_cache_dtype" not in explicit_keys:
        kv_dtype = profile.kv_cache_dtype

    if engine == "vllm":
        base = _recommend_vllm(model, hardware, quant, dtype, kv_dtype, gmu, seqs, want_len)
    else:
        base = _recommend_llamacpp(model, hardware, quant, dtype, kv_dtype, gmu, seqs, want_len)

    if profile is not None:
        merged = profile.apply(base)
        for key in explicit_keys:
            if key == "gpu_memory_utilization":
                merged["gpu_memory_utilization"] = gmu
            elif key == "max_num_seqs":
                merged["max_num_seqs"] = seqs
            elif key == "context_length":
                merged["max_model_len"] = want_len
        return merged
    return base


def _recommend_vllm(
    model: ModelProfile,
    hardware: HardwareProfile,
    quant: str,
    dtype: str,
    kv_dtype: str,
    gmu: float,
    seqs: int,
    want_len: int,
) -> dict[str, object]:
    tp = _select_tensor_parallel(model, hardware)
    # Single-GPU llamacpp-style quant names aren't valid for vLLM; bf16 => None.
    vllm_quant = None if quant in ("bf16", "fp16") else quant
    max_len = _fit_max_model_len(
        model, hardware, quant, gmu, seqs, kv_dtype, want_len, tp
    )
    if max_len < 256:
        raise ValueError(
            f"cannot fit any usable context for {model.name!r} on "
            f"{hardware.gpu_count}x{hardware.gpu_type}; reduce max_num_seqs or use a smaller quant"
        )

    config: dict[str, object] = {
        "engine": "vllm",
        "model": model.name,
        "tensor_parallel_size": tp,
        "pipeline_parallel_size": 1 if (hardware.has_nvlink or hardware.gpu_count == 1) else hardware.gpu_count,
        "gpu_memory_utilization": round(gmu, 3),
        "max_model_len": max_len,
        "max_num_seqs": seqs,
        "dtype": dtype,
        "quantization": vllm_quant,
        "kv_cache_dtype": "fp8" if kv_dtype == "fp8" else "auto",
        # Enable CUDA graphs (do NOT enforce eager) on capable cards -> rule g.
        "enforce_eager": False,
        "enable_chunked_prefill": True,
        "enable_prefix_caching": True,
    }
    return config


def _recommend_llamacpp(
    model: ModelProfile,
    hardware: HardwareProfile,
    quant: str,
    dtype: str,
    kv_dtype: str,
    gmu: float,
    seqs: int,
    want_len: int,
) -> dict[str, object]:
    # llama.cpp is single-context; size for a small concurrency.
    seqs = min(seqs, 4)
    # n_gpu_layers: offload all layers when weights fit VRAM, else partial-offload
    # the fraction that fits (the rest runs on CPU).
    usable = hardware.total_vram_gb * gmu * 1e9
    weights = model.weights_bytes(quant)
    if weights <= usable * 0.85:
        n_gpu_layers = -1  # all layers on GPU
        on_gpu_weight_bytes = weights
    else:
        frac = max(0.0, min(1.0, (usable * 0.85) / weights))
        n_gpu_layers = int(model.num_layers * frac)
        on_gpu_weight_bytes = weights * frac

    # Size context against the VRAM left after the on-GPU weight fraction.
    max_len = _fit_max_model_len_with_weights(
        model, hardware, on_gpu_weight_bytes, gmu, seqs, kv_dtype, want_len
    )
    if max_len < 256:
        raise ValueError(
            f"cannot fit any usable context for {model.name!r} on {hardware.gpu_type} via llama.cpp"
        )

    config: dict[str, object] = {
        "engine": "llamacpp",
        "model": model.name,
        "gguf_quant": quant if quant.startswith("q") else "q4_k_m",
        "n_gpu_layers": n_gpu_layers,
        "n_ctx": max_len,
        "flash_attn": True,  # -fa, rule g
        "cache_type_k": "q8_0" if kv_dtype in ("fp8", "q8_0") else "f16",
        "cache_type_v": "q8_0" if kv_dtype in ("fp8", "q8_0") else "f16",
        "split_mode": "layer",
        "tensor_parallel_size": 1,  # llama.cpp has no NVLink TP
        "n_threads": 0,  # 0 => let llama.cpp pick
    }
    return config


# ---------------------------------------------------------------------------
# SearX-backed dynamic model profile lookup
# ---------------------------------------------------------------------------


def profile_from_search(query: str, searx_url: str | None = None) -> ModelDeploymentProfile | None:
    """Build a :class:`ModelDeploymentProfile` for any model via SearXNG search.

    Searches HuggingFace for the model and extracts quantization info to
    construct a deployment profile. Falls back to ``None`` when the search
    engine is unreachable or no results are found.
    """
    from general_ludd.infra.model_search import ModelIndex, SearXModelSearch

    searcher = SearXModelSearch(base_url=searx_url)
    index = ModelIndex()

    cached = index.search(query)
    if cached:
        result = cached[0]
    else:
        results = searcher.search_models(query, source="huggingface")
        if not results:
            return None
        result = results[0]
        index.put(result)

    quant = None
    quants_lower = [q.lower() for q in result.quantizations_available]
    if "fp8" in quants_lower:
        quant = "fp8"
    elif "q4_k_m" in quants_lower:
        quant = "q4_k_m"

    return ModelDeploymentProfile(quantization=quant)
