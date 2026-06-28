"""RendererExecutor — runs a renderer playbook and reads its render.json artifact.

Contract (the only thing the router depends on)::

    async def run(name: str) -> RendererOutput

The concrete :class:`RendererExecutor` backs this with the live
``AnsibleRunnerAdapter`` (``app.state._runner``): it resolves the playbook
via :class:`RendererRegistry`, runs it in a worker thread
(:func:`asyncio.to_thread` so the event loop stays unblocked), reads
``render.json`` from the artifact dir, and validates it against
:class:`RendererOutput`.

Tests inject a duck-typed stand-in (any object with an ``async def run(name)``
method) on ``app.state._renderer_executor`` — see ``_StubExecutor`` in
``tests/integration/test_render_api.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Protocol

from general_ludd.renderers.registry import RendererRegistry
from general_ludd.renderers.schema import RendererOutput

logger = logging.getLogger(__name__)

_RENDER_ARTIFACT_NAME = "render.json"


class _ExecutorLike(Protocol):
    """Structural contract the router depends on."""

    async def run(self, name: str) -> RendererOutput: ...


def _read_render_json(artifact_dir: str | Path) -> dict[str, Any]:
    path = Path(artifact_dir) / _RENDER_ARTIFACT_NAME
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"render.json root must be an object, got {type(data).__name__}")
    return data


class RendererExecutor:
    """Ansible-backed implementation of the renderer-executor contract.

    Parameters
    ----------
    registry:
        :class:`RendererRegistry` used to resolve renderer name -> playbook
        path + timeout.
    runner:
        ``AnsibleRunnerAdapter`` (or duck-typed stand-in) whose
        ``run_playbook(playbook_name=..., extravars=..., env=..., timeout=...)``
        returns a result dict with at least ``status`` and ``rc`` keys. When
        ``None``, execution raises at call time (the router surfaces 500) —
        this keeps the wiring honest rather than silently no-op'ing.
    """

    def __init__(
        self,
        registry: RendererRegistry,
        runner: Any | None = None,
    ) -> None:
        self._registry = registry
        self._runner = runner

    async def run(self, name: str) -> RendererOutput:
        meta = self._registry.get(name)
        if meta is None:
            raise KeyError(f"renderer {name!r} is not registered")
        if self._runner is None:
            raise RuntimeError("no ansible runner wired into RendererExecutor")
        return await asyncio.to_thread(self._run_sync, meta.name, meta.playbook_path, meta.timeout_s)

    def _run_sync(self, name: str, playbook_path: str, timeout_s: float) -> RendererOutput:
        # Each renderer run gets its own artifact_dir under the system tmpdir.
        # The playbook writes render.json here; the executor reads it back.
        assert self._runner is not None, "RendererExecutor.run() must guard None"
        artifact_dir = Path(tempfile.mkdtemp(prefix=f"gludd-render-{name}-")) / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        extravars: dict[str, Any] = {
            "artifact_dir": str(artifact_dir),
            "renderer_timeout_s": timeout_s,
        }
        env: dict[str, str] = {}
        # Forward the operator PSK + daemon URL so the playbook's gludd_facts
        # module can reach the daemon (same convention as report_status role).
        psk = os.environ.get("GLUDD_PSK")
        if psk:
            env["GLUDD_PSK"] = psk
            extravars["psk"] = psk
        daemon_url = os.environ.get("GLUDD_DAEMON_URL")
        if daemon_url:
            extravars["daemon_url"] = daemon_url

        # Register the renderer playbook by absolute path so the runner can
        # resolve it even though it lives outside the default playbooks/ root.
        playbook_name = Path(playbook_path).name
        try:
            self._runner.register_playbook(playbook_name, playbook_path)
        except Exception:  # pragma: no cover - best-effort registration
            logger.debug("renderer playbook %s already registered", playbook_name)

        job_id = f"render-{name}-{uuid.uuid4().hex[:8]}"
        result = self._runner.run_playbook(
            playbook_name=playbook_name,
            extravars=extravars,
            env=env,
            timeout=timeout_s,
        )
        logger.debug("renderer %s job_id=%s status=%s", name, job_id, result.get("status"))
        status = str(result.get("status", "")).lower()
        if status not in ("successful", "ok", "completed") and result.get("rc", 1) != 0:
            raise RuntimeError(
                f"renderer playbook {name} failed: status={status} rc={result.get('rc')} "
                f"error={result.get('error', '')}"
            )
        data = _read_render_json(artifact_dir)
        return RendererOutput.model_validate(data)
