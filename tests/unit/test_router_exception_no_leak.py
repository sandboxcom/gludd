"""Regression guard: admin routers must not leak raw exception detail to clients.

Several endpoints previously returned `str(exc)` / `f"...{exc}"` / `{"error":
str(exc)}` in the HTTPException `detail`, exposing internal paths, DSNs, and
exception types to API callers. The fix logs the real error (with traceback) and
returns a static, non-leaking message while preserving the status code.

The handlers are nested inside `register(app, ...)` closures, which makes direct
invocation impractical, so this pins the contract at the source level: the leak
patterns must be absent and the generic messages present. It also imports each
module to catch syntax errors from the edits.
"""

from __future__ import annotations

import inspect
import re


def _module_source(modpath: str) -> str:
    import importlib

    mod = importlib.import_module(modpath)
    return inspect.getsource(mod)


def test_models_router_no_exc_leak() -> None:
    src = _module_source("general_ludd.routers.models")
    # No raw exception interpolated into a response detail.
    assert "detail=f\"model call failed: {exc}\"" not in src
    assert 'detail={"error": str(exc)}' not in src
    # Generic messages present + the error is logged for operators.
    assert 'detail="model call failed"' in src
    assert 'detail="workflow execution failed"' in src
    assert "logger.warning(\"model call failed: %s\", exc, exc_info=True)" in src


def test_compute_router_no_exc_leak() -> None:
    src = _module_source("general_ludd.routers.compute")
    assert "detail=str(exc)" not in src
    assert '"error": "compute deploy failed"' in src
    assert 'detail="compute destroy failed"' in src


def test_projects_router_no_exc_leak() -> None:
    src = _module_source("general_ludd.routers.projects")
    assert "detail=str(exc)" not in src
    # All three 422 handlers now return a static, user-actionable message.
    assert "invalid project request" in src
    assert "invalid weight for project" in src
    assert "invalid weights in rebalance request" in src


def test_no_str_exc_detail_pattern_remains() -> None:
    # Belt-and-suspenders: none of the three modules should contain the
    # `detail=str(exc)` or `detail=f"...{exc}"` leak shapes anywhere.
    leak_re = re.compile(r"detail=(?:str\(exc\)|f\"[^\"]*\{exc\}[^\"]*\")")
    for modpath in (
        "general_ludd.routers.models",
        "general_ludd.routers.compute",
        "general_ludd.routers.projects",
    ):
        src = _module_source(modpath)
        assert not leak_re.search(src), f"exception-detail leak pattern remains in {modpath}"
