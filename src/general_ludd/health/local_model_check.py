"""Local model health: model existence, llama.cpp readiness, memory usage."""

from __future__ import annotations

import logging
import os
from importlib import util as importlib_util
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL_CACHE = os.environ.get(
    "GLUDD_MODELS_DIR",
    os.path.expanduser("~/.cache/general-ludd/models"),
)


async def _async_import_module(name: str) -> bool:
    """Check if a Python module is importable (non-blocking via thread)."""
    import asyncio

    return await asyncio.to_thread(lambda: importlib_util.find_spec(name) is not None)


async def _has_local_gguf(cache_dir: str | None = None) -> bool:
    """True when at least one GGUF file exists under the model cache."""
    import asyncio

    def _scan() -> bool:
        root = Path(cache_dir or DEFAULT_MODEL_CACHE).expanduser().resolve()
        if not root.is_dir():
            return False
        return any(entry.is_file() and entry.stat().st_size > 0 for entry in root.rglob("*.gguf"))

    return await asyncio.to_thread(_scan)


async def _memory_pressure() -> dict[str, object]:
    """Report process and system memory pressure."""
    import asyncio

    def _probe() -> dict[str, object]:
        import psutil

        mem = psutil.virtual_memory()
        proc = psutil.Process(os.getpid()).memory_info()
        return {
            "system_total_mb": round(mem.total / (1024 * 1024), 1),
            "system_available_mb": round(mem.available / (1024 * 1024), 1),
            "system_used_pct": round(mem.percent, 1),
            "process_rss_mb": round(proc.rss / (1024 * 1024), 1),
        }

    return await asyncio.to_thread(_probe)


async def local_model_health_check() -> dict[str, object]:
    """Return local-model health facts suitable for the /healthz endpoint.

    Returns a dict with keys ``model_exists``, ``llama_cpp_available``,
    and ``memory`` — all probed without blocking the event loop.
    """
    import asyncio

    has_gguf, has_llama, memory = await asyncio.gather(
        _has_local_gguf(),
        _async_import_module("llama_cpp"),
        _memory_pressure(),
    )
    return {
        "model_exists": has_gguf,
        "llama_cpp_available": has_llama,
        "memory": memory,
    }
