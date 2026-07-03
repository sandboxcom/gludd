"""Pre-deploy misconfiguration precheck (SLICE B).

Bridges the request body + :class:`~general_ludd.infra.compute.ComputeConfig`
into the ``deployment`` dict shape consumed by
:class:`~general_ludd.infra.model_deploy_check.MisconfigDetector`, resolves the
matching ``gpu_info`` via
:func:`~general_ludd.infra.gpu_info_adapter.gpu_info_from_gpu_type`, and runs
the detector.

Both functions here are **pure** (no HTTP, no I/O) so they can be unit tested
in isolation. The router (:mod:`general_ludd.routers.compute`) calls
:func:`precheck` before spending on a deploy and again to enrich a failure
response.
"""

from __future__ import annotations

from typing import Any

from general_ludd.infra.compute import ComputeConfig
from general_ludd.infra.gpu_info_adapter import gpu_info_from_gpu_type
from general_ludd.infra.model_deploy_check import Finding, MisconfigDetector

# vLLM + llama.cpp serving knobs pulled from the request body. These are NOT
# part of ComputeConfig (which only knows engine/gpu/model_name) — they arrive
# on the raw request dict and drive the detector's per-engine rules.
_SERVING_KNOBS: tuple[str, ...] = (
    "gpu_memory_utilization",
    "max_model_len",
    "max_num_seqs",
    "tensor_parallel_size",
    "pipeline_parallel_size",
    "quantization",
    "dtype",
    "enforce_eager",
    "n_gpu_layers",
    "n_ctx",
    "n_parallel",
)


def build_deployment_dict(config: ComputeConfig, req: dict[str, Any]) -> dict[str, Any]:
    """Assemble the detector ``deployment`` dict.

    ``engine`` comes from ``config``; the serving knobs (and an optional
    ``model`` metadata sub-dict) are pulled from the raw request ``req``. Only
    keys actually present in ``req`` are copied through, so the detector sees
    exactly what the operator supplied.
    """
    req = req if isinstance(req, dict) else {}
    deployment: dict[str, Any] = {"engine": config.engine.value}
    for knob in _SERVING_KNOBS:
        if knob in req:
            deployment[knob] = req[knob]
    if req.get("model") is not None:
        deployment["model"] = req["model"]
    return deployment


def precheck(
    config: ComputeConfig, req: dict[str, Any]
) -> tuple[list[Finding], list[dict[str, Any]]]:
    """Run the misconfig detector for ``config`` + request ``req``.

    Returns ``(findings, remediations)`` where ``remediations[i]`` is the
    yaml-first config-patch dict for ``findings[i]``. Pure and never raises
    (the detector itself is crash-safe).
    """
    gpu_info = gpu_info_from_gpu_type(config.gpu_type.value, config.gpu_count)
    detector = MisconfigDetector()
    findings = detector.check(build_deployment_dict(config, req), gpu_info)
    remediations = [detector.remediate(f) for f in findings]
    return findings, remediations
