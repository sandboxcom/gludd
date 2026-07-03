"""Adapt the static GPU/hardware tables into a ``gpu_info`` dict.

Bridges :mod:`general_ludd.infra.deployment_optimizer` (which knows the
per-GPU VRAM / arch / FP8 / NVLink facts) to the ``gpu_info`` shape consumed
by :class:`general_ludd.infra.model_deploy_check.MisconfigDetector`.

The adapter is **fail-soft**: an unknown ``gpu_type`` (or any error resolving
the hardware profile) yields an empty dict ``{}`` so the detector degrades to
config-only rules rather than raising.
"""

from __future__ import annotations

import logging
from typing import Any

from general_ludd.infra import deployment_optimizer

logger = logging.getLogger(__name__)

# arch (lowercased) -> CUDA compute capability major.minor.
_CC_BY_ARCH: dict[str, float] = {
    "turing": 7.5,
    "ampere": 8.0,
    "ada": 8.9,
    "hopper": 9.0,
    "blackwell": 10.0,
}


def gpu_info_from_gpu_type(
    gpu_type: str,
    gpu_count: int = 1,
    *,
    instance: str | None = None,
) -> dict[str, Any]:
    """Return a ``gpu_info`` dict for ``gpu_type`` (fail-soft to ``{}``).

    Resolves the hardware profile via
    :func:`deployment_optimizer.hardware_profile_for` and reshapes it into the
    dict the :class:`MisconfigDetector` expects. An unknown ``gpu_type`` /
    ``instance`` (or any error) returns ``{}`` so the detector can still run its
    config-only rules.
    """
    try:
        hp = deployment_optimizer.hardware_profile_for(
            gpu_type, gpu_count, instance=instance
        )
    except Exception:
        logger.debug(
            "gpu_info_from_gpu_type: no hardware profile for gpu_type=%r "
            "gpu_count=%r instance=%r; degrading to config-only",
            gpu_type,
            gpu_count,
            instance,
        )
        return {}

    return {
        "vram_gb": hp.total_vram_gb,
        "gpu_count": hp.gpu_count,
        "arch": hp.arch,
        "supports_fp8": hp.supports_fp8,
        "has_nvlink": hp.has_nvlink,
        "compute_capability": _CC_BY_ARCH.get(hp.arch),
    }
