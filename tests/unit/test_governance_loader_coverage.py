"""Fail-closed branch coverage for the governance module loader."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import general_ludd.governance.loader as loader


def test_load_module_reuses_registered_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("general_ludd.governance_ext.cached")
    monkeypatch.setitem(sys.modules, module.__name__, module)

    assert loader._load_module("cached", Path("unused.py")) is module


def test_load_module_rejects_missing_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "general_ludd.governance.loader.importlib.util.spec_from_file_location",
        lambda *_args: SimpleNamespace(loader=None),
    )

    with pytest.raises(ImportError, match="Cannot load module spec"):
        loader._load_module("missing", Path("missing.py"))


def test_get_module_rejects_missing_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_module_utils_dir", lambda: Path("/definitely/missing"))

    with pytest.raises(FileNotFoundError, match="Governance module"):
        loader._get_module("missing")


def test_get_module_reuses_process_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("cached")
    monkeypatch.setitem(loader._GOVERNANCE_MODULE_UTILS_CACHE, "cached", module)

    assert loader._get_module("cached") is module
