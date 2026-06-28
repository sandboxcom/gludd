"""Project-local .gludd/ directory discovery + config overlay (Phase 1).

Walk UP from ``start`` (default: cwd) until we find an ancestor that contains
a ``.gludd/`` directory — git-root style.  A ``GLUDD_PROJECT_DIR`` env var
overrides the walk entirely, letting operators pin the directory explicitly.

Returns ``None`` when no ``.gludd/`` directory is found (not an error; the
daemon simply runs with the user-level config only).

See docs/design/PROJECT_LOCAL_GLUDD_DIR.md (request #17).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def find_project_gludd_dir(start: Path | None = None) -> Path | None:
    """Return the first ancestor of *start* that contains a ``.gludd/`` dir.

    Resolution order:
    1. ``GLUDD_PROJECT_DIR`` env var — if set, return that path directly
       (must point to the ``.gludd/`` directory itself, not its parent);
       returns ``None`` if it does not exist.
    2. Walk up from *start* (default: ``Path.cwd()``) to the filesystem root,
       returning the first directory that *contains* a ``.gludd/`` child.
    3. ``None`` — no ``.gludd/`` found anywhere in the ancestor chain.
    """
    env_override = os.environ.get("GLUDD_PROJECT_DIR")
    if env_override:
        p = Path(env_override)
        return p if p.is_dir() else None

    current = (start or Path.cwd()).resolve()
    while True:
        candidate = current / ".gludd"
        if candidate.is_dir():
            return candidate
        parent = current.parent
        if parent == current:
            # Reached the filesystem root without finding .gludd/
            return None
        current = parent


def project_config_path(project_gludd_dir: Path | None = None) -> Path | None:
    """Return ``<.gludd>/general-ludd.yml`` if it exists, else ``None``.

    Args:
        project_gludd_dir: The ``.gludd/`` directory returned by
            :func:`find_project_gludd_dir`.  If ``None`` the function returns
            ``None`` immediately (no crash).
    """
    if project_gludd_dir is None:
        return None
    p = project_gludd_dir / "general-ludd.yml"
    return p if p.exists() else None


def merge_config(user: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge *project* on top of *user* (project wins on conflicts).

    Merge semantics:
    - **Scalars / None**: project value replaces user value.
    - **Dicts**: recursively merged — project keys win on collisions.
    - **Lists** (``rules``, ``queues``, ``connectors``, …): project value
      *replaces* the user list entirely.  This is intentional — list fields
      are declarative (a project override is expected to be the full list, not
      an append).  Callers that need append semantics must do so explicitly
      before calling this function.

    Neither *user* nor *project* is mutated; a new dict is returned.
    """
    result: dict[str, Any] = dict(user)
    for key, proj_val in project.items():
        user_val = result.get(key)
        if isinstance(proj_val, dict) and isinstance(user_val, dict):
            result[key] = merge_config(user_val, proj_val)
        else:
            # Scalars and lists: project wins outright.
            result[key] = proj_val
    return result
