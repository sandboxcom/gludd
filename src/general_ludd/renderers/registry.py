"""RendererRegistry — discovers renderer playbooks (Phase 1).

Spec: docs/design/PLAYBOOK_WEB_RENDERER.md §3.2 + §6.

Discovery walks two directories for ``*.yml``:

  - ``bundled_dir``  — shipped example renderers (version-controlled).
  - ``operator_dir`` — operator-curated overrides; wins on name clash.

A file is a renderer iff its first play's ``vars:`` block contains
``renderer: true``. The renderer name is the file stem.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30
_DEFAULT_CACHE_TTL_SECONDS = 30
_VAR_TIMEOUT = "renderer_timeout_seconds"
_VAR_CACHE_TTL = "renderer_cache_ttl_seconds"
_VAR_DESCRIPTION = "renderer_description"
_VAR_ALLOW_RAW_HTML = "renderer_allow_raw_html"


@dataclass
class RendererSpec:
    """Catalog entry for one discovered renderer playbook (§6)."""

    name: str
    path: Path
    description: str = ""
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
    cache_ttl_seconds: int = _DEFAULT_CACHE_TTL_SECONDS
    allow_raw_html: bool = False
    schema_path: Path | None = None

    def model_dump(self) -> dict[str, object]:
        """Pydantic-style dump for routers that expect ``.model_dump()``."""
        d = asdict(self)
        d["path"] = str(self.path)
        d["schema_path"] = str(self.schema_path) if self.schema_path else None
        return d

    # Backward-compat properties for prior partial work that used RendererMeta.
    @property
    def playbook_path(self) -> str:
        return str(self.path)

    @property
    def timeout_s(self) -> float:
        return float(self.timeout_seconds)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_default_playbooks_dir() -> Path:
    """Locate the repo ``playbooks/`` dir for the zero-arg constructor case."""
    here = Path(__file__).resolve()
    candidate = here.parents[3] / "playbooks"
    if candidate.is_dir():
        return candidate
    cwd = Path.cwd() / "playbooks"
    return cwd if cwd.is_dir() else candidate


@dataclass
class RendererRegistry:
    """Discovers + catalogs renderer playbooks at daemon startup."""

    bundled_dir: Path | None = None
    operator_dir: Path | None = None
    _specs: dict[str, RendererSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Backward-compat: prior partial work constructed ``RendererRegistry()``
        # with no args and relied on a default playbooks dir. Preserve that.
        if self.bundled_dir is None:
            self.bundled_dir = _resolve_default_playbooks_dir()

    def discover(self) -> None:
        """Scan both dirs; operator entries override bundled on name clash."""
        found: dict[str, RendererSpec] = {}
        for d in (self.bundled_dir, self.operator_dir):
            if not d or not Path(d).is_dir():
                continue
            for yml in sorted(Path(d).glob("*.yml")):
                spec = self._parse(yml)
                if spec is not None:
                    found[spec.name] = spec
        self._specs = found
        with_schema = [n for n, s in self._specs.items() if s.schema_path is not None]
        if with_schema:
            logger.info(
                "renderer registry: %d renderer(s) have companion schemas: %s",
                len(with_schema),
                ", ".join(sorted(with_schema)),
            )
        else:
            logger.debug("renderer registry: no companion schemas discovered")

    @staticmethod
    def _parse(path: Path) -> RendererSpec | None:
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
        except Exception as exc:
            logger.debug("renderer playbook %s unparseable: %s", path, exc)
            return None
        if not isinstance(data, list) or not data:
            return None
        first = data[0]
        if not isinstance(first, dict):
            return None
        play_vars = first.get("vars") or {}
        if not isinstance(play_vars, dict) or not play_vars.get("renderer"):
            return None
        return RendererSpec(
            name=path.stem,
            path=path,
            description=str(play_vars.get(_VAR_DESCRIPTION, "")),
            timeout_seconds=_coerce_int(
                play_vars.get(_VAR_TIMEOUT), _DEFAULT_TIMEOUT_SECONDS
            ),
            cache_ttl_seconds=_coerce_int(
                play_vars.get(_VAR_CACHE_TTL), _DEFAULT_CACHE_TTL_SECONDS
            ),
            allow_raw_html=bool(play_vars.get(_VAR_ALLOW_RAW_HTML, False)),
            schema_path=_companion_schema(path),
        )

    def names(self) -> list[str]:
        return list(self._specs.keys())

    def get(self, name: str) -> RendererSpec | None:
        return self._specs.get(name)

    def metadata(self) -> list[dict[str, object]]:
        return [spec.model_dump() for spec in self._specs.values()]

    # Backward-compat with prior partial work that used list_all() / iteration.
    def list_all(self) -> list[RendererSpec]:
        return list(self._specs.values())

    def __iter__(self) -> Iterator[RendererSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, name: object) -> bool:
        return name in self._specs


def _companion_schema(playbook_path: Path) -> Path | None:
    """Return the ``<stem>.schema.json`` sibling if it exists (§3.3 mechanism b)."""
    candidate = playbook_path.with_suffix(".schema.json")
    return candidate if candidate.is_file() else None


# Backward-compat alias for prior partial work referencing RendererMeta.
RendererMeta = RendererSpec
