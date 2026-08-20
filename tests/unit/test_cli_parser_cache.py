"""Canonical behavioral coverage for the CLI parser graph cache."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

from pytest import MonkeyPatch

from general_ludd.cli_parser_cache import CommandGraphCache


def _owned_handler(module_name: str) -> Any:
    def _cmd_run() -> None:
        return None

    _cmd_run.__module__ = module_name
    return _cmd_run


def test_reuses_one_graph_while_owned_handlers_are_unchanged(
    monkeypatch: MonkeyPatch,
) -> None:
    module_name = "general_ludd.cli_cache_fixture"
    module = ModuleType(module_name)
    module._cmd_run = _owned_handler(module_name)
    monkeypatch.setitem(sys.modules, module_name, module)
    builds: list[object] = []

    def build() -> object:
        graph = object()
        builds.append(graph)
        return graph

    cache = CommandGraphCache(build, module_prefix=module_name)
    first = cache.get()

    assert cache.get() is first
    assert builds == [first]


def test_replaced_handler_graphs_are_never_retained(monkeypatch: MonkeyPatch) -> None:
    module_name = "general_ludd.cli_cache_fixture"
    module = ModuleType(module_name)
    module._cmd_run = lambda: None
    monkeypatch.setitem(sys.modules, module_name, module)
    builds: list[object] = []

    def build() -> object:
        graph = object()
        builds.append(graph)
        return graph

    cache = CommandGraphCache(build, module_prefix=module_name)

    assert cache.get() is not cache.get()
    assert len(builds) == 2
