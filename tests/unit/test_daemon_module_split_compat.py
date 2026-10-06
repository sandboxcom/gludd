"""Compatibility contract for the daemon module split.

The public ``general_ludd.daemon`` module remains the process entrypoint and the
stable monkeypatch surface.  Implementation components must stay importable in
either order without importing the facade back from a lower layer.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DAEMON = ROOT / "src" / "general_ludd" / "daemon.py"
COMPONENTS = (
    "general_ludd.daemon_components.lifecycle",
    "general_ludd.daemon_components.ports",
)


@pytest.mark.parametrize("component", COMPONENTS)
def test_components_import_before_facade_without_cycle(component: str) -> None:
    """Lower-level components import independently and never import the facade."""
    probe = f"""
import importlib
import sys

component = {component!r}
imported = importlib.import_module(component)
assert "general_ludd.daemon" not in sys.modules
facade = importlib.import_module("general_ludd.daemon")
assert imported is sys.modules[component]
assert facade.create_daemon_app.__module__ == "general_ludd.daemon"
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_lifecycle_component_has_no_reverse_facade_import() -> None:
    """The lifecycle implementation receives ports instead of importing daemon."""
    lifecycle = ROOT / "src" / "general_ludd" / "daemon_components" / "lifecycle.py"
    tree = ast.parse(lifecycle.read_text(encoding="utf-8"), filename=str(lifecycle))

    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert "general_ludd.daemon" not in imported


def test_daemon_facade_preserves_entrypoint_and_lifespan_signatures() -> None:
    """External ASGI entrypoints and the tested private lifespan seam stay put."""
    from general_ludd import daemon

    assert daemon.create_daemon_app.__module__ == "general_ludd.daemon"
    assert daemon._lifespan.__module__ == "general_ludd.daemon"
    assert str(inspect.signature(daemon.create_daemon_app)) == (
        "(tick_interval: 'float | None' = None, log_level: 'str' = 'info', "
        "config_dir: 'str | None' = None, templates_dir: 'str | None' = None, "
        "playbooks_dir: 'str | None' = None, _db_path_override: 'str | None' = None, "
        "decision_codification: 'DecisionCodificationAdapter | None' = None) -> 'FastAPI'"
    )
    assert str(inspect.signature(daemon._lifespan)) == "(app: 'FastAPI') -> 'AsyncIterator[None]'"


def test_daemon_facade_is_below_repository_line_limit() -> None:
    """The compatibility facade leaves maintenance headroom below the hard cap."""
    assert len(DAEMON.read_text(encoding="utf-8").splitlines()) < 2500
