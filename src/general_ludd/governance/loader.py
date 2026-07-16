"""Loader for the governance collection's ``module_utils`` knowledge modules.

The data modules live inside the ansible collection at::

    collections/ansible_collections/general_ludd/governance/plugins/module_utils/

This loader dynamically imports them by file path using ``importlib`` so
they are accessible from the application Python layer without requiring the
collection path to be on ``sys.path``. Loaded modules are cached in a
process-level dict so repeated lookups incur no file I/O.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_GOVERNANCE_MODULE_UTILS_CACHE: dict[str, ModuleType] = {}


def _project_root() -> Path:
    """Return the project root (parent of the ``src/`` directory)."""
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent


def _module_utils_dir() -> Path:
    """Return the path to the governance collection's module_utils directory."""
    return (
        _project_root()
        / "collections"
        / "ansible_collections"
        / "general_ludd"
        / "governance"
        / "plugins"
        / "module_utils"
    )


def _load_module(name: str, file_path: Path) -> ModuleType:
    """Import a Python module from ``file_path`` and register it in ``sys.modules``.

    Uses ``importlib.util.spec_from_file_location`` for explicit file-path
    loading. The module is cached in both the process-level dict and
    ``sys.modules`` so subsequent loads return the same object.
    """
    full_name = f"general_ludd.governance_ext.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def _get_module(name: str) -> ModuleType:
    """Return the cached governance module ``name``, loading it if needed."""
    if name in _GOVERNANCE_MODULE_UTILS_CACHE:
        return _GOVERNANCE_MODULE_UTILS_CACHE[name]
    module_path = _module_utils_dir() / f"{name}.py"
    if not module_path.is_file():
        raise FileNotFoundError(f"Governance module '{name}' not found at {module_path}")
    module = _load_module(name, module_path)
    _GOVERNANCE_MODULE_UTILS_CACHE[name] = module
    return module


def get_borders() -> ModuleType:
    """Return the ``borders`` knowledge module."""
    return _get_module("borders")


def get_governing_bodies() -> ModuleType:
    """Return the ``governing_bodies`` knowledge module."""
    return _get_module("governing_bodies")


def get_conflicts_treaties() -> ModuleType:
    """Return the ``conflicts_treaties`` knowledge module."""
    return _get_module("conflicts_treaties")


def get_tax_currency() -> ModuleType:
    """Return the ``tax_currency`` knowledge module."""
    return _get_module("tax_currency")


def get_civic_services() -> ModuleType:
    """Return the ``civic_services`` knowledge module."""
    return _get_module("civic_services")


def get_elections_voting() -> ModuleType:
    """Return the ``elections_voting`` knowledge module."""
    return _get_module("elections_voting")


def get_international_relations() -> ModuleType:
    """Return the ``international_relations`` knowledge module."""
    return _get_module("international_relations")


def get_legal_systems() -> ModuleType:
    """Return the ``legal_systems`` knowledge module."""
    return _get_module("legal_systems")


def get_public_finance() -> ModuleType:
    """Return the ``public_finance`` knowledge module."""
    return _get_module("public_finance")


def clear_cache() -> None:
    """Clear the module cache (useful for tests)."""
    for name in list(_GOVERNANCE_MODULE_UTILS_CACHE):
        full_name = f"general_ludd.governance_ext.{name}"
        sys.modules.pop(full_name, None)
    _GOVERNANCE_MODULE_UTILS_CACHE.clear()
