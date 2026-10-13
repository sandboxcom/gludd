"""Read-only native collection evidence for admission selection regressions."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TypedDict

import pytest
from _pytest.fixtures import FixtureManager


class MarkerSnapshot(TypedDict):
    name: str
    args: str
    kwargs: str


class NodeSnapshot(TypedDict):
    nodeid: str
    markers: list[MarkerSnapshot]


class FixtureSnapshot(TypedDict):
    module: str
    qualname: str
    scope: str
    baseid: str
    argnames: list[str]
    params: str


class Snapshot(TypedDict):
    kind: str
    worker: str
    pid: int
    nodes: list[NodeSnapshot]
    plugins: list[list[str]]
    conftests: list[str]
    fixtures: dict[str, list[FixtureSnapshot]]
    gate_evidence: bool
    release_ready: bool


class CollectorCall(TypedDict):
    worker: str
    file: str


def stable_repr(value: object) -> str:
    """Retain native marker values while omitting process-local addresses."""
    return re.sub(r" at 0x[0-9a-fA-F]+", "", repr(value))


def pytest_collect_file(file_path: Path) -> None:
    """Observe calls only; let all native collectors retain ownership."""
    root = os.environ.get("COLD_START_PROBE_ROOT")
    if root and file_path.is_relative_to(Path(root)):
        call: CollectorCall = {
            "worker": os.environ.get("PYTEST_XDIST_WORKER", "controller"),
            "file": str(file_path),
        }
        with Path(os.environ["COLD_START_PROBE_LOG"]).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(call) + "\n")


def pytest_collection_finish(session: pytest.Session) -> None:
    """Write complete diagnostics only when a regression explicitly requests it."""
    destination = os.environ.get("COLD_START_SNAPSHOT_ROOT")
    if not destination:
        return
    config = session.config
    manager = config.pluginmanager.get_plugin("funcmanage")
    fixtures: dict[str, list[FixtureSnapshot]] = {}
    if isinstance(manager, FixtureManager):
        for name, definitions in sorted(manager._arg2fixturedefs.items()):
            fixtures[name] = [
                {
                    "module": definition.func.__module__,
                    "qualname": definition.func.__qualname__,
                    "scope": definition.scope,
                    "baseid": definition.baseid,
                    "argnames": list(definition.argnames),
                    "params": stable_repr(definition.params),
                }
                for definition in definitions
            ]
    plugins = sorted(
        [
            str(getattr(plugin, "__name__", type(plugin).__module__ + "." + type(plugin).__qualname__)),
            distribution.project_name,
            distribution.version,
        ]
        for plugin, distribution in config.pluginmanager.list_plugin_distinfo()
    )
    nodes: list[NodeSnapshot] = [
        {
            "nodeid": item.nodeid,
            "markers": [
                {"name": marker.name, "args": stable_repr(marker.args), "kwargs": stable_repr(marker.kwargs)}
                for marker in item.iter_markers()
            ],
        }
        for item in session.items
    ]
    worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
    snapshot: Snapshot = {
        "kind": "diagnostic",
        "worker": worker,
        "pid": os.getpid(),
        "nodes": nodes,
        "plugins": plugins,
        "conftests": sorted(
            str(Path(module.__file__).resolve())
            for module in config.pluginmanager._conftest_plugins
            if module.__file__ is not None
        ),
        "fixtures": fixtures,
        "gate_evidence": False,
        "release_ready": False,
    }
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{worker}-{os.getpid()}.json").write_text(
        json.dumps(snapshot, indent=2) + "\n",
        encoding="utf-8",
    )
