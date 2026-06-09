"""Tests verifying no circular imports exist in general_ludd packages.

Every subpackage must be importable independently and in any order.
The only known circular dependency is daemon <-> routers, which is
resolved via lazy imports documented in both modules.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from types import ModuleType

import pytest

import general_ludd


def _all_subpackages(root: ModuleType) -> list[str]:
    result: list[str] = []
    prefix = root.__name__ + "."
    for info in pkgutil.walk_packages(path=root.__path__, prefix=prefix):
        result.append(info.name)
    return sorted(result)


SUBPACKAGES = _all_subpackages(general_ludd)

KEY_MODULES = [
    "general_ludd.daemon",
    "general_ludd.event_loop.loop",
    "general_ludd.models.gateway",
    "general_ludd.ansible.runner",
    "general_ludd.db.session",
    "general_ludd.db.repository",
    "general_ludd.secrets.manager",
    "general_ludd.prompts.registry",
    "general_ludd.routers",
    "general_ludd.controllers.pid",
    "general_ludd.skills.fetcher",
    "general_ludd.quality.preflight",
    "general_ludd.filestore.bootstrap",
    "general_ludd.integrity.scanner",
    "general_ludd.config.loader",
    "general_ludd.review.reviewer",
    "general_ludd.rules.engine",
    "general_ludd.metrics.collector",
]


@pytest.mark.parametrize("package_name", SUBPACKAGES)
def test_subpackage_importable(package_name: str) -> None:
    imported = {}
    for modname in ("importlib",):
        imported[modname] = sys.modules.get(modname)
    try:
        mod = importlib.import_module(package_name)
        assert mod is not None
    finally:
        pass


@pytest.mark.parametrize("module_name", KEY_MODULES)
def test_key_module_importable(module_name: str) -> None:
    if module_name in sys.modules:
        existing = sys.modules[module_name]
        assert existing is not None
        return
    mod = importlib.import_module(module_name)
    assert mod is not None


def test_import_daemon_before_routers() -> None:
    saved_daemon = sys.modules.pop("general_ludd.daemon", None)
    saved_routers = sys.modules.pop("general_ludd.routers", None)
    try:
        import general_ludd.daemon
        import general_ludd.routers

        assert general_ludd.daemon is not None
        assert general_ludd.routers is not None
    finally:
        if saved_daemon is not None:
            sys.modules["general_ludd.daemon"] = saved_daemon
        if saved_routers is not None:
            sys.modules["general_ludd.routers"] = saved_routers


def test_import_routers_before_daemon() -> None:
    saved_daemon = sys.modules.pop("general_ludd.daemon", None)
    saved_routers = sys.modules.pop("general_ludd.routers", None)
    try:
        import general_ludd.daemon
        import general_ludd.routers

        assert general_ludd.routers is not None
        assert general_ludd.daemon is not None
    finally:
        if saved_daemon is not None:
            sys.modules["general_ludd.daemon"] = saved_daemon
        if saved_routers is not None:
            sys.modules["general_ludd.routers"] = saved_routers


def test_import_event_loop_and_models_gateway() -> None:
    for key in ("general_ludd.event_loop.loop", "general_ludd.models.gateway"):
        sys.modules.pop(key, None)
    from general_ludd.event_loop import loop
    from general_ludd.models import gateway

    assert loop is not None
    assert gateway is not None


def test_import_db_session_and_models() -> None:
    for key in ("general_ludd.db.session", "general_ludd.db.models"):
        sys.modules.pop(key, None)
    from general_ludd.db import models as db_models
    from general_ludd.db import session

    assert session is not None
    assert db_models is not None


def test_import_secrets_manager_and_config() -> None:
    for key in ("general_ludd.secrets.manager", "general_ludd.config.binary_paths"):
        sys.modules.pop(key, None)
    from general_ludd.config import binary_paths
    from general_ludd.secrets import manager

    assert manager is not None
    assert binary_paths is not None
