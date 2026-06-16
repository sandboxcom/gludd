"""Static model-deployment misconfiguration detector (#76).

Self-contained, stdlib-only. Operates on a passed-in ``deployment`` config
dict plus an optional ``gpu_info`` dict (or an injected provider callable),
so it performs **no** network or hardware access of its own.

The ruleset implements the ``MisconfigDetector`` spec in
``docs/MODEL_DEPLOYMENT.md`` (rules a-g) and adds the sensible superset the
issue calls for (rules h-l): llama.cpp ``n_gpu_layers == 0`` on a GPU box,
``n_ctx`` vs VRAM, dtype vs compute-capability, missing parallelism on a
multi-GPU box, and batch/parallel slots vs memory.

Design invariants:

* ``check`` NEVER raises. Malformed input is surfaced as a ``critical``
  :class:`Finding` rather than an exception.
* Every finding carries a concrete ``remediation`` string.
* ``remediate`` returns a yaml-first config-patch dict.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

Severity = str  # 'info' | 'warn' | 'critical'
Engine = str  # 'vllm' | 'llamacpp' | 'any'

# --- Tunable thresholds (documented so remediation strings can cite them) ---
GPU_MEM_UTIL_MAX = 0.95  # above this, peak allocations risk CUDA OOM
GPU_MEM_UTIL_MIN = 0.50  # below this on a dedicated GPU, KV pool is wasted
KV_DTYPE_BYTES = 2  # fp16/bf16 KV; fp8 KV halves this
WEIGHTS_BYTES_PER_PARAM_FP16 = 2.0
GIB = 1024.0**3

# Architectures (lowercased) that natively support FP8 tensor cores.
_FP8_ARCHES = {"hopper", "ada", "blackwell"}
# bf16 needs Ampere (sm_80) or newer.
_BF16_MIN_CC = 8.0


@dataclass
class Finding:
    """A single detected (mis)configuration signal.

    ``severity`` is one of ``info`` | ``warn`` | ``critical``; ``engine`` is
    ``vllm`` | ``llamacpp`` | ``any``. ``evidence`` carries the concrete
    numbers behind the call so a human (or the remediate loop) can audit it.
    """

    rule_id: str
    severity: Severity
    engine: Engine
    message: str
    remediation: str
    evidence: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Safe coercion helpers — never raise, return a sentinel on bad input.
# ---------------------------------------------------------------------------


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class MisconfigDetector:
    """Detect static deployment misconfigs and propose remediations.

    Parameters
    ----------
    gpu_info_provider:
        Optional zero-arg callable returning a ``gpu_info`` dict. Used by
        :meth:`check` only when no explicit ``gpu_info`` is passed. Any
        exception it raises is contained (the detector degrades to
        config-only rules) rather than propagated.
    """

    def __init__(self, gpu_info_provider: Callable[[], dict[str, Any]] | None = None) -> None:
        self._gpu_info_provider = gpu_info_provider

    # -- public API ---------------------------------------------------------

    def check(
        self, deployment: dict[str, Any], gpu_info: dict[str, Any] | None = None
    ) -> list[Finding]:
        """Return all findings for ``deployment``. Never raises."""
        try:
            return self._check(deployment, gpu_info)
        except Exception as exc:  # pragma: no cover - defensive backstop
            logger.exception("MisconfigDetector.check crashed; returning critical finding")
            return [
                Finding(
                    rule_id="internal-error",
                    severity="critical",
                    engine="any",
                    message=f"detector crashed on input: {exc!r}",
                    remediation="report this config to the gludd maintainers; "
                    "validate the deployment dict against the documented schema",
                    evidence={"error": repr(exc)},
                )
            ]

    def remediate(self, finding: Finding) -> dict[str, Any]:
        """Return a yaml-first config-patch dict for ``finding``.

        Shape::

            {"rule_id", "format": "yaml", "config_patch": {...},
             "requires_restart": bool, "notes": str}

        Unknown rule ids yield an empty (but well-formed) patch.
        """
        builder = _REMEDIATIONS.get(finding.rule_id)
        if builder is None:
            return {
                "rule_id": finding.rule_id,
                "format": "yaml",
                "config_patch": {},
                "requires_restart": True,
                "notes": finding.remediation or "no automated patch available",
            }
        try:
            patch, requires_restart = builder(finding)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("remediate builder for %s failed", finding.rule_id)
            return {
                "rule_id": finding.rule_id,
                "format": "yaml",
                "config_patch": {},
                "requires_restart": True,
                "notes": f"remediation builder failed: {exc!r}",
            }
        return {
            "rule_id": finding.rule_id,
            "format": "yaml",
            "config_patch": patch,
            "requires_restart": requires_restart,
            "notes": finding.remediation,
        }

    # -- internals ----------------------------------------------------------

    def _resolve_gpu_info(self, gpu_info: dict[str, Any] | None) -> dict[str, Any]:
        if gpu_info is not None:
            return _as_dict(gpu_info)
        if self._gpu_info_provider is not None:
            try:
                return _as_dict(self._gpu_info_provider())
            except Exception:
                logger.exception("gpu_info_provider raised; degrading to config-only checks")
                return {}
        return {}

    def _check(self, deployment: Any, gpu_info: dict[str, Any] | None) -> list[Finding]:
        if not isinstance(deployment, dict):
            return [
                Finding(
                    rule_id="malformed-deployment",
                    severity="critical",
                    engine="any",
                    message=f"deployment must be a dict, got {type(deployment).__name__}",
                    remediation="pass a deployment config dict; see docs/MODEL_DEPLOYMENT.md "
                    "for the expected schema",
                    evidence={"received_type": type(deployment).__name__},
                )
            ]

        gpu = self._resolve_gpu_info(gpu_info)
        engine = _as_str(deployment.get("engine")).lower()
        findings: list[Finding] = []

        if engine not in {"vllm", "llamacpp"}:
            findings.append(
                Finding(
                    rule_id="unknown-engine",
                    severity="critical",
                    engine="any",
                    message=f"unsupported or missing engine: {deployment.get('engine')!r}",
                    remediation="set engine to one of 'vllm' or 'llamacpp'",
                    evidence={"engine": deployment.get("engine")},
                )
            )
            return findings

        model = _as_dict(deployment.get("model"))
        if deployment.get("model") is not None and not isinstance(deployment.get("model"), dict):
            findings.append(
                Finding(
                    rule_id="malformed-model",
                    severity="critical",
                    engine=engine,
                    message="'model' must be a dict of model metadata",
                    remediation="pass model as a dict (num_layers, num_kv_heads, head_dim, "
                    "params_b, ...)",
                    evidence={"received_type": type(deployment.get("model")).__name__},
                )
            )

        if engine == "vllm":
            findings.extend(self._check_vllm(deployment, model, gpu))
        else:
            findings.extend(self._check_llamacpp(deployment, model, gpu))
        return findings

    # -- vLLM rules ---------------------------------------------------------

    def _check_vllm(
        self, dep: dict[str, Any], model: dict[str, Any], gpu: dict[str, Any]
    ) -> list[Finding]:
        findings: list[Finding] = []
        vram = _as_float(gpu.get("vram_gb"))
        gpu_count = _as_int(gpu.get("gpu_count")) or 0
        util = _as_float(dep.get("gpu_memory_utilization"))
        tp = _as_int(dep.get("tensor_parallel_size")) or 1
        pp = _as_int(dep.get("pipeline_parallel_size")) or 1
        max_len = _as_int(dep.get("max_model_len"))
        max_seqs = _as_int(dep.get("max_num_seqs")) or 1
        quant = _as_str(dep.get("quantization")).lower()
        dtype = _as_str(dep.get("dtype")).lower()
        arch = _as_str(gpu.get("arch")).lower()
        cc = _as_float(gpu.get("compute_capability"))
        supports_fp8 = bool(gpu.get("supports_fp8")) or arch in _FP8_ARCHES
        has_nvlink = bool(gpu.get("has_nvlink"))
        enforce_eager = bool(dep.get("enforce_eager"))

        # Rule a: gpu_memory_utilization too high / too low.
        if util is not None:
            if util > GPU_MEM_UTIL_MAX:
                findings.append(
                    Finding(
                        rule_id="a",
                        severity="critical",
                        engine="vllm",
                        message=f"gpu_memory_utilization={util} risks CUDA OOM at peak "
                        f"(> {GPU_MEM_UTIL_MAX})",
                        remediation=f"lower gpu_memory_utilization toward {GPU_MEM_UTIL_MAX:.2f} "
                        "(step 0.05, keep >= 0.80) and/or cap max_num_seqs",
                        evidence={"gpu_memory_utilization": util, "max": GPU_MEM_UTIL_MAX},
                    )
                )
            elif util < GPU_MEM_UTIL_MIN:
                findings.append(
                    Finding(
                        rule_id="a",
                        severity="warn",
                        engine="vllm",
                        message=f"gpu_memory_utilization={util} under-uses the GPU "
                        f"(< {GPU_MEM_UTIL_MIN}); KV pool is starved",
                        remediation="raise gpu_memory_utilization toward 0.90 to grow the "
                        "KV-cache pool (watch for OOM)",
                        evidence={"gpu_memory_utilization": util, "min": GPU_MEM_UTIL_MIN},
                    )
                )

        # Rule b: max_model_len * max_num_seqs exceeds the KV-cache budget.
        if max_len is not None and max_len > 0 and vram and util is not None:
            kv_need = _kv_cache_bytes(model, max_len, max_seqs, KV_DTYPE_BYTES)
            weights = _weights_bytes(model, quant)
            pool = util * vram * GIB - weights
            if kv_need is not None and pool > 0 and kv_need > pool:
                findings.append(
                    Finding(
                        rule_id="b",
                        severity="critical",
                        engine="vllm",
                        message="max_model_len * max_num_seqs exceeds the KV-cache budget "
                        f"(need ~{kv_need / GIB:.1f}GiB, pool ~{pool / GIB:.1f}GiB)",
                        remediation="reduce max_model_len or max_num_seqs, or set "
                        "kv_cache_dtype: fp8 to halve KV bytes",
                        evidence={
                            "kv_need_gib": round(kv_need / GIB, 2),
                            "kv_pool_gib": round(pool / GIB, 2),
                            "max_model_len": max_len,
                            "max_num_seqs": max_seqs,
                        },
                    )
                )

        # Rule c: tensor_parallel_size invalid.
        if gpu_count:
            if tp > gpu_count:
                findings.append(
                    Finding(
                        rule_id="c",
                        severity="critical",
                        engine="vllm",
                        message=f"tensor_parallel_size={tp} exceeds gpu_count={gpu_count}",
                        remediation=f"set tensor_parallel_size to a divisor of {gpu_count} "
                        "(<= gpu_count)",
                        evidence={"tensor_parallel_size": tp, "gpu_count": gpu_count},
                    )
                )
            elif tp > 1 and gpu_count % tp != 0:
                findings.append(
                    Finding(
                        rule_id="c",
                        severity="critical",
                        engine="vllm",
                        message=f"tensor_parallel_size={tp} does not divide gpu_count={gpu_count}",
                        remediation=f"choose a tensor_parallel_size that divides {gpu_count} "
                        "(e.g. 1, 2, 4, 8)",
                        evidence={"tensor_parallel_size": tp, "gpu_count": gpu_count},
                    )
                )
            elif tp > 1 and not has_nvlink:
                findings.append(
                    Finding(
                        rule_id="c",
                        severity="warn",
                        engine="vllm",
                        message=f"tensor_parallel_size={tp} on a non-NVLink box: all-reduce over "
                        "PCIe can be slower than a single GPU",
                        remediation="set tensor_parallel_size=1 and use pipeline_parallel_size "
                        "(PCIe-tolerant) instead",
                        evidence={"tensor_parallel_size": tp, "has_nvlink": has_nvlink},
                    )
                )

        # Rule e: quant/dtype unsupported on arch (fp8 pre-Hopper/Ada).
        if quant in {"fp8", "float8"} and not supports_fp8:
            findings.append(
                Finding(
                    rule_id="e",
                    severity="critical",
                    engine="vllm",
                    message=f"fp8 quantization requested but arch {arch or '?'} lacks native FP8",
                    remediation="use AWQ / GPTQ-Marlin / INT8 on pre-Hopper/Ada hardware",
                    evidence={"quantization": quant, "arch": arch, "supports_fp8": supports_fp8},
                )
            )

        # Rule g: CUDA graphs disabled (enforce_eager) on a capable card.
        if enforce_eager and (cc is None or cc >= _BF16_MIN_CC):
            findings.append(
                Finding(
                    rule_id="g",
                    severity="warn",
                    engine="vllm",
                    message="enforce_eager disables CUDA graphs on a capable card (lower tok/s)",
                    remediation="remove enforce_eager to re-enable CUDA graphs",
                    evidence={"enforce_eager": True, "compute_capability": cc},
                )
            )

        # Rule j: dtype vs compute capability (bf16 needs sm_80+).
        if dtype in {"bf16", "bfloat16"} and cc is not None and cc < _BF16_MIN_CC:
            findings.append(
                Finding(
                    rule_id="j",
                    severity="critical",
                    engine="vllm",
                    message=f"dtype bf16 requires compute capability >= {_BF16_MIN_CC}, "
                    f"card is sm_{cc}",
                    remediation="use dtype: fp16 on pre-Ampere (sm < 8.0) hardware",
                    evidence={"dtype": dtype, "compute_capability": cc},
                )
            )

        # Rule k: missing parallelism on a multi-GPU box.
        if gpu_count >= 2 and tp == 1 and pp == 1:
            findings.append(
                Finding(
                    rule_id="k",
                    severity="warn",
                    engine="vllm",
                    message=f"{gpu_count} GPUs present but tensor/pipeline parallel both = 1; "
                    "extra GPUs are idle",
                    remediation="set tensor_parallel_size (NVLink) or pipeline_parallel_size "
                    "(PCIe) to use all GPUs",
                    evidence={"gpu_count": gpu_count, "tp": tp, "pp": pp},
                )
            )

        # Rule l: batch/parallel slots vs memory (absurd max_num_seqs).
        if vram and max_seqs > 0:
            seqs_per_gib = max_seqs / max(vram, 1.0)
            if seqs_per_gib > 64.0:  # ~heuristic: >64 seqs/GiB cannot fit KV
                findings.append(
                    Finding(
                        rule_id="l",
                        severity="warn",
                        engine="vllm",
                        message=f"max_num_seqs={max_seqs} is implausibly high for {vram:.0f}GiB "
                        "VRAM; requests will queue/preempt",
                        remediation="lower max_num_seqs to match the KV-cache budget",
                        evidence={"max_num_seqs": max_seqs, "vram_gb": vram},
                    )
                )

        return findings

    # -- llama.cpp rules ----------------------------------------------------

    def _check_llamacpp(
        self, dep: dict[str, Any], model: dict[str, Any], gpu: dict[str, Any]
    ) -> list[Finding]:
        findings: list[Finding] = []
        vram = _as_float(gpu.get("vram_gb"))
        gpu_count = _as_int(gpu.get("gpu_count")) or 0
        ngl = _as_int(dep.get("n_gpu_layers"))
        n_ctx = _as_int(dep.get("n_ctx"))
        n_parallel = _as_int(dep.get("n_parallel")) or 1
        num_layers = _as_int(model.get("num_layers"))

        if dep.get("n_gpu_layers") is not None and ngl is None:
            findings.append(
                Finding(
                    rule_id="malformed-n_gpu_layers",
                    severity="critical",
                    engine="llamacpp",
                    message="n_gpu_layers must be an integer",
                    remediation="set n_gpu_layers to an int (0..num_layers, or -1 for all)",
                    evidence={"n_gpu_layers": dep.get("n_gpu_layers")},
                )
            )

        # Rule f: n_gpu_layers exceeds the model's layer count.
        if ngl is not None and num_layers is not None and ngl > num_layers + 1:
            findings.append(
                Finding(
                    rule_id="f",
                    severity="warn",
                    engine="llamacpp",
                    message=f"n_gpu_layers={ngl} exceeds model layers ({num_layers}); "
                    "excess is ignored / may thrash",
                    remediation=f"set n_gpu_layers to {num_layers + 1} (all layers + output) "
                    "or -1",
                    evidence={"n_gpu_layers": ngl, "num_layers": num_layers},
                )
            )

        # Rule h: n_gpu_layers == 0 while a GPU is present (wasted GPU).
        if ngl == 0 and gpu_count >= 1 and vram and vram > 0:
            findings.append(
                Finding(
                    rule_id="h",
                    severity="warn",
                    engine="llamacpp",
                    message="n_gpu_layers=0 on a GPU box runs fully on CPU; the GPU is idle",
                    remediation="raise n_gpu_layers to offload layers to the GPU (or -1 for all)",
                    evidence={"n_gpu_layers": 0, "gpu_count": gpu_count, "vram_gb": vram},
                )
            )

        # Rule i: n_ctx * n_parallel too big for VRAM.
        if n_ctx is not None and n_ctx > 0 and vram and vram > 0:
            kv_need = _kv_cache_bytes(model, n_ctx, n_parallel, KV_DTYPE_BYTES)
            weights = _weights_bytes(model, _as_str(dep.get("quantization")).lower())
            pool = vram * GIB - weights
            if kv_need is not None and pool > 0 and kv_need > pool:
                findings.append(
                    Finding(
                        rule_id="i",
                        severity="critical",
                        engine="llamacpp",
                        message=f"n_ctx={n_ctx} x n_parallel={n_parallel} exceeds VRAM KV budget "
                        f"(need ~{kv_need / GIB:.1f}GiB, free ~{pool / GIB:.1f}GiB)",
                        remediation="lower n_ctx or n_parallel, enable flash_attn, or use "
                        "quantized KV (cache-type q8_0)",
                        evidence={
                            "n_ctx": n_ctx,
                            "n_parallel": n_parallel,
                            "kv_need_gib": round(kv_need / GIB, 2),
                            "free_gib": round(pool / GIB, 2),
                        },
                    )
                )

        return findings


# ---------------------------------------------------------------------------
# Memory math (best-effort; returns None when metadata is missing).
# ---------------------------------------------------------------------------


def _weights_bytes(model: dict[str, Any], quant: str) -> float:
    params_b = _as_float(model.get("params_b")) or 0.0
    bytes_per_param = WEIGHTS_BYTES_PER_PARAM_FP16
    if quant in {"fp8", "float8", "int8", "gptq", "awq"}:
        bytes_per_param = 1.0
    if quant in {"q4_k_m", "q4_0", "q4_k_s", "int4", "4bit"} or "q4" in quant:
        bytes_per_param = 0.5
    return params_b * 1e9 * bytes_per_param


def _kv_cache_bytes(
    model: dict[str, Any], seq_len: int, num_seqs: int, dtype_bytes: int
) -> float | None:
    num_layers = _as_int(model.get("num_layers"))
    num_kv_heads = _as_int(model.get("num_kv_heads")) or 8
    head_dim = _as_int(model.get("head_dim")) or 128
    if num_layers is None:
        # Without layer info we cannot estimate KV; skip the rule rather than guess.
        return None
    # KV_bytes ≈ 2 (K+V) · layers · seq · seqs · kv_heads · head_dim · dtype_bytes
    return float(2 * num_layers * seq_len * max(num_seqs, 1) * num_kv_heads * head_dim * dtype_bytes)


# ---------------------------------------------------------------------------
# Remediation patch builders: rule_id -> (config_patch, requires_restart).
# ---------------------------------------------------------------------------


def _patch_a(f: Finding) -> tuple[dict[str, Any], bool]:
    util = _as_float(f.evidence.get("gpu_memory_utilization"))
    if util is not None and util > GPU_MEM_UTIL_MAX:
        return {"gpu_memory_utilization": round(max(util - 0.05, 0.80), 2)}, True
    return {"gpu_memory_utilization": 0.90}, True


def _patch_b(f: Finding) -> tuple[dict[str, Any], bool]:
    return {"kv_cache_dtype": "fp8", "max_model_len": 8192}, True


def _patch_c(f: Finding) -> tuple[dict[str, Any], bool]:
    gpu_count = _as_int(f.evidence.get("gpu_count"))
    if gpu_count and "has_nvlink" in f.evidence:
        return {"tensor_parallel_size": 1, "pipeline_parallel_size": gpu_count}, True
    return {"tensor_parallel_size": 1}, True


def _patch_e(f: Finding) -> tuple[dict[str, Any], bool]:
    return {"quantization": "awq"}, True


def _patch_g(f: Finding) -> tuple[dict[str, Any], bool]:
    return {"enforce_eager": False}, True


def _patch_f(f: Finding) -> tuple[dict[str, Any], bool]:
    num_layers = _as_int(f.evidence.get("num_layers"))
    return {"n_gpu_layers": (num_layers + 1) if num_layers is not None else -1}, True


def _patch_h(f: Finding) -> tuple[dict[str, Any], bool]:
    return {"n_gpu_layers": -1}, True


def _patch_i(f: Finding) -> tuple[dict[str, Any], bool]:
    return {"n_ctx": 4096, "flash_attn": True, "cache_type_k": "q8_0", "cache_type_v": "q8_0"}, True


def _patch_j(f: Finding) -> tuple[dict[str, Any], bool]:
    return {"dtype": "fp16"}, True


def _patch_k(f: Finding) -> tuple[dict[str, Any], bool]:
    gpu_count = _as_int(f.evidence.get("gpu_count")) or 2
    return {"tensor_parallel_size": gpu_count}, True


def _patch_l(f: Finding) -> tuple[dict[str, Any], bool]:
    return {"max_num_seqs": 64}, True


_REMEDIATIONS: dict[str, Callable[[Finding], tuple[dict[str, Any], bool]]] = {
    "a": _patch_a,
    "b": _patch_b,
    "c": _patch_c,
    "e": _patch_e,
    "f": _patch_f,
    "g": _patch_g,
    "h": _patch_h,
    "i": _patch_i,
    "j": _patch_j,
    "k": _patch_k,
    "l": _patch_l,
}
