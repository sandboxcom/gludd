"""Deprecated: use :mod:`general_ludd.renderers.runner` instead.

The previous ``RendererExecutor`` class was superseded by the spec-compliant
``run_renderer`` async function in :mod:`general_ludd.renderers.runner`. This
module remains as a thin re-export so older imports do not break at runtime.
"""

from __future__ import annotations

from general_ludd.renderers.runner import (
    RendererFailure,
    RendererTimeout,
    run_renderer,
)

__all__ = ["RendererFailure", "RendererTimeout", "run_renderer"]
