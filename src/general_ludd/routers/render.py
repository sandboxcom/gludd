"""Playbook web renderer API.

Two endpoints:
  - ``GET /api/renderers``  — PSK-authed list of registered renderers
    (NOT in ``_PUBLIC_PATHS``; daemon middleware enforces the bearer PSK).
  - ``GET /render/<name>``  — public read; executes the named renderer
    playbook, renders its canonical ``RendererOutput`` to HTML via Jinja2,
    and returns the page.

Wiring: the daemon sets ``app.state._renderer_registry`` (a
:class:`RendererRegistry`) and ``app.state._renderer_executor`` (any object
with ``async def run(name) -> RendererOutput`` — the real
:class:`RendererExecutor` in production, a deterministic stub in tests) at
app creation. When either is missing the router fails closed with a clear
503 so a misconfigured deployment is observable rather than silent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from general_ludd.renderers.registry import RendererRegistry
from general_ludd.renderers.schema import RendererOutput

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "renderers" / "templates"
_TEMPLATE_NAME = "page.html.j2"

# Module-level Jinja2 environment: autoescape ON by default (select_autoescape
# enables it for .html/.j2). Only `| safe`-marked output bypasses escaping,
# which the template applies ONLY to raw_html section bodies.
_env = Environment(
    loader=FileSystemLoader(searchpath=str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(default=True, default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _get_registry(app: FastAPI) -> RendererRegistry | None:
    reg = getattr(app.state, "_renderer_registry", None)
    return reg if isinstance(reg, RendererRegistry) else None


def _get_executor(app: FastAPI) -> Any | None:
    return getattr(app.state, "_renderer_executor", None)


def render_output(output: RendererOutput) -> str:
    """Render a RendererOutput to an HTML string. Exposed for unit testing."""
    template = _env.get_template(_TEMPLATE_NAME)
    return template.render(output=output)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get(
        "/api/renderers",
        summary="List registered renderer playbooks",
        description="PSK-authed. Returns the catalog of discovered renderer playbooks.",
    )
    async def api_list_renderers() -> dict[str, Any]:
        registry = _get_registry(app)
        if registry is None:
            return {"renderers": [], "count": 0}
        return {
            "renderers": [r.model_dump() for r in registry.list_all()],
            "count": len(registry),
        }

    @app.get(
        "/render/{name}",
        response_class=HTMLResponse,
        summary="Render a renderer playbook to HTML",
        description="Public read. Executes the renderer and returns the rendered page.",
    )
    async def render_by_name(name: str) -> HTMLResponse:
        registry = _get_registry(app)
        executor = _get_executor(app)
        if registry is None or executor is None:
            return HTMLResponse(
                status_code=503,
                content="<html><body><h1>Renderer not configured</h1>"
                "<p>The renderer subsystem is not initialized on this daemon.</p>"
                "</body></html>",
            )
        if registry.get(name) is None:
            raise HTTPException(status_code=404, detail=f"renderer {name!r} not found")
        try:
            output = await executor.run(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("renderer %s failed: %s", name, exc, exc_info=True)
            return HTMLResponse(
                status_code=500,
                content="<html><body><h1>Renderer failed</h1>"
                f"<pre>{exc}</pre></body></html>",
            )
        if not isinstance(output, RendererOutput):
            # Duck-typed stubs may return non-RendererOutput objects; coerce
            # so the template always sees the canonical shape.
            try:
                output = RendererOutput.model_validate(
                    output if isinstance(output, dict) else output.model_dump()
                )
            except Exception as exc:
                logger.warning("renderer %s returned invalid output: %s", name, exc)
                return HTMLResponse(
                    status_code=500,
                    content="<html><body><h1>Renderer invalid output</h1>"
                    f"<pre>{exc}</pre></body></html>",
                )
        return HTMLResponse(content=render_output(output))
