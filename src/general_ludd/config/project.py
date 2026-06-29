"""Project-root resolution via the ``.gludd/`` marker directory.

Walks up from a start path looking for a parent that contains ``.gludd/``.
Returns the directory that *contains* ``.gludd/`` (i.e. the project root),
or ``None`` at the filesystem root.

This is the project-root counterpart to
:func:`general_ludd.config.project_dir.find_project_gludd_dir` (which returns
the ``.gludd/`` directory itself).
"""

from __future__ import annotations

from pathlib import Path


def find_project_root(start: Path | None = None) -> Path | None:
    """Return the first ancestor of *start* that contains a ``.gludd/`` dir.

    Args:
        start: Directory to begin the search from (default: ``Path.cwd()``).

    Returns:
        The directory *containing* ``.gludd/`` (the project root), or ``None``
        if no ancestor up to the filesystem root has a ``.gludd/`` directory.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / ".gludd").is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
