"""RendererRegistry — discovers renderer playbooks under playbooks/renderers/.

A renderer playbook is any ``playbooks/renderers/<name>.yml`` file whose
top-level mapping contains ``renderer: true``. The registry catalogs each
discovered playbook as a :class:`RendererMeta` (name, description, path,
timeout) keyed by the file stem.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from general_ludd.renderers.schema import RendererMeta

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 30.0


def _resolve_default_playbooks_dir() -> Path:
    """Locate the repo ``playbooks/`` dir (mirrors runner._resolve_playbooks_root)."""
    here = Path(__file__).resolve()
    # src/general_ludd/renderers/registry.py -> repo root = 4 parents up
    candidate = here.parents[3] / "playbooks"
    if candidate.is_dir():
        return candidate
    cwd = Path.cwd() / "playbooks"
    return cwd if cwd.is_dir() else candidate


class RendererRegistry:
    """Catalogs renderer playbooks discovered under a playbooks dir."""

    def __init__(self, playbooks_dir: str | Path | None = None) -> None:
        self._playbooks_dir = Path(playbooks_dir) if playbooks_dir else _resolve_default_playbooks_dir()
        self._renderers: dict[str, RendererMeta] = {}
        self.discover()

    @property
    def playbooks_dir(self) -> Path:
        return self._playbooks_dir

    def discover(self) -> list[RendererMeta]:
        """Scan ``<playbooks_dir>/renderers/*.yml`` and catalog each renderer.

        A file is a renderer iff its parsed top-level mapping has a truthy
        ``renderer`` key. Non-renderer files (or unparseable ones) are skipped
        with a debug log — never raised, so a malformed sibling cannot break
        discovery for the rest.
        """
        renderers_dir = self._playbooks_dir / "renderers"
        found: dict[str, RendererMeta] = {}
        if renderers_dir.is_dir():
            for path in sorted(renderers_dir.glob("*.yml")):
                meta = self._parse_renderer_file(path)
                if meta is not None:
                    found[meta.name] = meta
        self._renderers = found
        return self.list_all()

    def _parse_renderer_file(self, path: Path) -> RendererMeta | None:
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
        except Exception as exc:
            logger.debug("renderer playbook %s unparseable: %s", path, exc)
            return None
        logger.warning("PARSE %s data_type=%s repr=%.200r", path, type(data).__name__, data)
        # A renderer playbook is a valid Ansible playbook (list of plays) whose
        # FIRST play carries `vars.renderer: true`. The marker lives in vars
        # (not the document root) because a YAML document cannot mix a mapping
        # with a sequence at the same level — a real playbook is a sequence.
        # An optional top-level mapping form (`{renderer: true, ...}`) is also
        # accepted for manifest-only files that don't themselves run.
        marker: Any = None
        description = ""
        timeout: Any = _DEFAULT_TIMEOUT_S
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                play_vars = first.get("vars", {}) or {}
                marker = play_vars.get("renderer")
                description = play_vars.get("renderer_description", "")
                timeout = play_vars.get("renderer_timeout_s", _DEFAULT_TIMEOUT_S)
        elif isinstance(data, dict):
            marker = data.get("renderer")
            description = data.get("description", "")
            timeout = data.get("renderer_timeout_s", _DEFAULT_TIMEOUT_S)
        if not marker:
            return None
        name = path.stem
        try:
            timeout_f = float(timeout)
        except (TypeError, ValueError):
            timeout_f = _DEFAULT_TIMEOUT_S
        return RendererMeta(
            name=name,
            description=str(description),
            playbook_path=str(path),
            timeout_s=timeout_f,
        )

    def get(self, name: str) -> RendererMeta | None:
        return self._renderers.get(name)

    def list_all(self) -> list[RendererMeta]:
        return list(self._renderers.values())

    def __iter__(self) -> Iterator[RendererMeta]:
        return iter(self._renderers.values())

    def __len__(self) -> int:
        return len(self._renderers)

    def __contains__(self, name: object) -> bool:
        return name in self._renderers
