#!/usr/bin/env python3
"""Derive stable, project-scoped paths for process/resource leases.

The orchestration scripts run from several working directories (including
temporary E2E workdirs).  A fixed ``/tmp/gludd-*.lock`` therefore causes
unrelated projects to reject one another and lets their scratch trees collide.
This module is deliberately dependency-free so shell launchers can ask for a
namespace/path without importing the application package.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_DEFAULT_RESOURCE_DIR = "gludd-resources"


def _validate_component(value: str, label: str) -> str:
    if value in {".", ".."} or not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"{label} must contain only safe path characters")
    return value


def project_root(start: Path | str | None = None) -> Path:
    """Return the canonical root used to scope local resources.

    ``GLUDD_PROJECT_ROOT`` is an explicit escape hatch for launchers started
    outside the checkout.  Otherwise the nearest ancestor containing either a
    Git metadata entry or ``pyproject.toml`` wins; when neither exists, the
    supplied/current directory remains a valid isolated project root.
    """

    configured = os.environ.get("GLUDD_PROJECT_ROOT", "").strip()
    candidate = Path(configured or start or Path.cwd()).expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    return candidate


def project_namespace(root: Path | str | None = None) -> str:
    """Return a path-safe, stable namespace for one project checkout."""

    override = (os.environ.get("GLUDD_PROJECT_NAMESPACE") or "").strip()
    if override:
        return _validate_component(override, "project namespace")

    resolved = project_root(root)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", resolved.name).strip("-._") or "project"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def resource_root(root: Path | str | None = None) -> Path:
    """Return the host scratch root containing all project namespaces."""

    configured = os.environ.get("GLUDD_RESOURCE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve() / project_namespace(root)
    tmp = Path(os.environ.get("TMPDIR", "/tmp")).expanduser()
    return tmp / _DEFAULT_RESOURCE_DIR / project_namespace(root)


def resource_path(resource: str, root: Path | str | None = None) -> Path:
    """Return a namespaced lease path for ``resource``.

    Resource names are intentionally single path components to prevent a
    caller from escaping the project namespace with ``../``.
    """

    _validate_component(resource, "resource name")
    suffix = "" if resource.endswith(".lock") else ".lock"
    return resource_root(root) / f"{resource}{suffix}"


def _main(argv: list[str]) -> int:
    if len(argv) not in {1, 2} or argv[0] not in {"namespace", "root", "path"}:
        print("usage: resource_arbiter.py namespace|root|path RESOURCE", file=sys.stderr)
        return 2
    command = argv[0]
    if command == "namespace":
        print(project_namespace(argv[1] if len(argv) == 2 else None))
    elif command == "root":
        print(resource_root(argv[1] if len(argv) == 2 else None))
    else:
        if len(argv) != 2:
            print("usage: resource_arbiter.py path RESOURCE", file=sys.stderr)
            return 2
        print(resource_path(argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
