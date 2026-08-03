"""Hardware inventory API: GET /admin/hardware/inventory, /admin/hardware/model-fit.

Exposes the live HardwareInventory from app.state._hardware_inventory (populated
at daemon startup by HardwareSurvey.survey()) and answers model-to-hardware
fit queries.
"""

from __future__ import annotations

import logging
from typing import Annotated, cast

from fastapi import FastAPI, HTTPException, Query

from general_ludd.hardware.survey import HardwareInventory

logger = logging.getLogger(__name__)

_MODEL_VRAM_GB: dict[str, float] = {
    "llama3-8b": 6.0,
    "llama3-70b": 40.0,
    "mistral-7b": 6.0,
    "mixtral-8x7b": 48.0,
    "phi3-mini": 4.0,
    "phi3-medium": 12.0,
    "gemma2-9b": 8.0,
    "gemma2-27b": 24.0,
    "codestral-22b": 18.0,
    "deepseek-coder-7b": 6.0,
    "deepseek-coder-33b": 24.0,
    "qwen2-7b": 6.0,
    "qwen2-72b": 40.0,
    "starcoder2-15b": 12.0,
}


def can_run_model(inventory: HardwareInventory, model: str) -> dict[str, object]:
    required_vram = _MODEL_VRAM_GB.get(model.lower(), 4.0)
    gpu_vram = max((g.vram_gb for g in inventory.gpus), default=0.0)
    extra_ram = max(0.0, inventory.total_ram_gb - 2.0)

    if gpu_vram >= required_vram:
        return {
            "model": model,
            "can_run": True,
            "reason": f"GPU VRAM {gpu_vram:.1f}GB meets {required_vram:.1f}GB requirement",
            "required_vram_gb": required_vram,
            "available_gpu_vram_gb": gpu_vram,
        }
    if extra_ram >= required_vram:
        return {
            "model": model,
            "can_run": True,
            "reason": f"CPU offload: {extra_ram:.1f}GB RAM available meets {required_vram:.1f}GB requirement",
            "required_vram_gb": required_vram,
            "available_gpu_vram_gb": gpu_vram,
            "cpu_offload": True,
        }
    return {
        "model": model,
        "can_run": False,
        "reason": f"Need {required_vram:.1f}GB VRAM; GPU has {gpu_vram:.1f}GB, system RAM usable: {extra_ram:.1f}GB",
        "required_vram_gb": required_vram,
        "available_gpu_vram_gb": gpu_vram,
    }


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.get(
        "/admin/hardware/inventory",
        summary="Full hardware inventory",
        description=(
            "Returns the HardwareInventory snapshot captured at daemon startup: "
            "GPU list (name, VRAM, backend), total system RAM, free disk, and CPU cores."
        ),
    )
    async def admin_hardware_inventory() -> dict[str, object]:
        inventory = getattr(app.state, "_hardware_inventory", None)
        if inventory is None:
            raise HTTPException(status_code=503, detail="Hardware inventory not yet available")
        return cast(dict[str, object], inventory.to_dict())

    @app.get(
        "/admin/hardware/model-fit",
        summary="Check if a model fits on this hardware",
        description="Returns whether the named model can run given the surveyed hardware.",
    )
    async def admin_hardware_model_fit(
        model: Annotated[str, Query(max_length=64)],
    ) -> dict[str, object]:
        inventory: HardwareInventory | None = getattr(app.state, "_hardware_inventory", None)
        if inventory is None:
            raise HTTPException(status_code=503, detail="Hardware inventory not yet available")
        return can_run_model(inventory, model)
