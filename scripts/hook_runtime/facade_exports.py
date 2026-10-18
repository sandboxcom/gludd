"""One ordered export registry for the stable hook-runtime compatibility facade."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from types import FunctionType, ModuleType

from . import cases_core, cases_lifecycle, cases_safety, cases_scheduling
from .cases_lifecycle import (
    WATCHDOG_PATH,
    _enforce_make_bash_test,
    _fresh_session_state,
    _session_start_dispatch_then_bash,
)
from .fixtures import (
    _GLOBAL_RUNTIME_STATE_NAMES,
    _LEGACY_WORKSPACE_TEMP_NAMES,
    _OPENCODE_DIR,
    _PLUGINS_PRESENT,
    LIB_DIR,
    PLUGIN_DIR,
    ROOT,
    _clean_state_files,
    _cleanup_legacy_workspace_artifacts,
    _dirty_test_path,
    _hermetic_project_root,
    _remove_legacy_workspace_artifacts,
    _runtime_state_path,
    _runtime_state_root,
    _with_open_work,
)
from .runner import _factory_plugin_code, _pluginapi_code, _run_ts


def _case_exports(modules: Sequence[ModuleType]) -> dict[str, FunctionType]:
    """Collect owned definitions in source order and refuse ambiguous names."""
    exports: dict[str, FunctionType] = {}
    for module in modules:
        for name, value in vars(module).items():
            if (
                not name.startswith("test_")
                or not inspect.isfunction(value)
                or value.__module__ != module.__name__
            ):
                continue
            if name in exports:
                raise ValueError(f"duplicate hook-runtime case: {name}")
            exports[name] = value
    return exports


_CASE_EXPORTS = _case_exports((cases_core, cases_scheduling, cases_safety, cases_lifecycle))
globals().update(_CASE_EXPORTS)
__all__ = [
    "LIB_DIR",
    "PLUGIN_DIR",
    "ROOT",
    "WATCHDOG_PATH",
    "_GLOBAL_RUNTIME_STATE_NAMES",
    "_LEGACY_WORKSPACE_TEMP_NAMES",
    "_OPENCODE_DIR",
    "_PLUGINS_PRESENT",
    "_clean_state_files",
    "_cleanup_legacy_workspace_artifacts",
    "_dirty_test_path",
    "_enforce_make_bash_test",
    "_factory_plugin_code",
    "_fresh_session_state",
    "_hermetic_project_root",
    "_pluginapi_code",
    "_remove_legacy_workspace_artifacts",
    "_run_ts",
    "_runtime_state_path",
    "_runtime_state_root",
    "_session_start_dispatch_then_bash",
    "_with_open_work",
]
__all__.extend(_CASE_EXPORTS)
__all__.sort()
