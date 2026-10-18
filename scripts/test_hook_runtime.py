#!/usr/bin/env python3
"""Functional test facade for the OpenCode plugin hook runtime suite.

The implementations live in :mod:`scripts.hook_runtime`; the ordered registry keeps
the historical ``scripts/test_hook_runtime.py::<case>`` node IDs and CLI stable.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from hook_runtime import facade_exports as _exports
from hook_runtime.facade_exports import (
    _GLOBAL_RUNTIME_STATE_NAMES,
    _LEGACY_WORKSPACE_TEMP_NAMES,
    _OPENCODE_DIR,
    _PLUGINS_PRESENT,
    LIB_DIR,
    PLUGIN_DIR,
    ROOT,
    WATCHDOG_PATH,
    _clean_state_files,
    _cleanup_legacy_workspace_artifacts,
    _dirty_test_path,
    _enforce_make_bash_test,
    _factory_plugin_code,
    _fresh_session_state,
    _hermetic_project_root,
    _pluginapi_code,
    _remove_legacy_workspace_artifacts,
    _run_ts,
    _runtime_state_path,
    _runtime_state_root,
    _session_start_dispatch_then_bash,
    _with_open_work,
)

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

pytestmark = pytest.mark.skipif(
    not _PLUGINS_PRESENT,
    reason=f"no plugins found under {PLUGIN_DIR} (set OPENCODE_DIR=... to test a different location)",
)

# Bind the canonical definitions directly so pytest keeps names, order and fixtures.
globals().update(_exports._CASE_EXPORTS)
__all__.extend(_exports._CASE_EXPORTS)

# Compatibility ports: tests and operators historically monkeypatch these
# module objects through the facade; runner.py observes the same shared objects.
_COMPAT_MODULE_PORTS = (os, subprocess)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
