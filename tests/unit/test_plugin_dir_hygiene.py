"""Guardrail: .opencode/plugin/ must contain ONLY valid opencode plugins.

opencode auto-discovers ``.ts`` files under ``.opencode/plugin/`` and calls
each module's default export as a plugin. If any ``.ts`` file in that
directory:

1. Has no ``export default`` (e.g. a pure constants module), OR
2. Has a default export that is not a function, OR
3. Has named ``export const`` / ``export function`` declarations (opencode's
   ``getLegacyPlugins()`` iterates ``Object.values(mod)`` and rejects any
   export that is not a function or ``{server: fn}``),

...opencode crashes at boot with::

    TypeError: undefined is not an object (evaluating 'N.event')

or::

    Plugin export is not a function

This test codifies the fix for the 2026-07-23 incident where
``_exports.ts`` companion files (added by commit 0e45db90 to hold named
constants for tests) landed directly inside ``.opencode/plugin/`` and
crashed opencode at every boot.

What this test proves
---------------------
- Every ``.ts`` file at the TOP LEVEL of ``.opencode/plugin/`` has an
  ``export default`` declaration.
- No ``.ts`` file at the top level has ``export const`` / ``export let`` /
  ``export var`` / ``export function`` (named exports crash the legacy
  loader).
- No ``_exports.ts`` files exist anywhere under ``.opencode/plugin/``.
- Every ``.ts`` file listed in ``opencode.json`` resolves to a file on disk
  whose Node dynamic-import default is a function.
- ``scripts/check_plugin_hooks.py`` exits 0 (no invalid hook names).

Run: make test TESTFILE=tests/unit/test_plugin_dir_hygiene.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
OPENCODE_JSON = ROOT / "opencode.json"
HOOK_CHECKER = ROOT / "scripts" / "check_plugin_hooks.py"

# opencode auto-discovers .ts files in .opencode/plugin/ at the TOP LEVEL.
# Subdirectories (impl/, test_exports/) are NOT auto-loaded (verified
# empirically: impl/*.ts have default exports and don't crash, and the
# test_exports/ files were removed entirely).
TOP_LEVEL_TS = sorted(PLUGIN_DIR.glob("*.ts")) if PLUGIN_DIR.is_dir() else []

# Patterns that indicate a named export (crashes opencode's legacy loader).
NAMED_EXPORT_RE = re.compile(
    r"^\s*export\s+(?!default\b|type\b)"
    r"(?:const|let|var|function|class|enum|interface)\s+\w+",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Structural tests (source-level, no Node required)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ts_file", TOP_LEVEL_TS, ids=lambda p: p.name)
def test_top_level_plugin_ts_has_export_default(ts_file: Path) -> None:
    """Every top-level .ts in .opencode/plugin/ must have ``export default``.

    Files without a default export crash opencode's auto-discovery loader.
    """
    content = ts_file.read_text()
    assert "export default" in content, (
        f"{ts_file.name} has no 'export default'. opencode auto-discovers "
        f"every .ts in .opencode/plugin/ and calls the default export as a "
        f"plugin. Files without a default export crash at boot."
    )


@pytest.mark.parametrize("ts_file", TOP_LEVEL_TS, ids=lambda p: p.name)
def test_top_level_plugin_ts_no_named_exports(ts_file: Path) -> None:
    """No top-level .ts in .opencode/plugin/ may have named exports.

    opencode's getLegacyPlugins() iterates ``Object.values(mod)`` and rejects
    any export that is not a function. ``export const X = 42`` crashes with
    "Plugin export is not a function".
    """
    content = ts_file.read_text()
    matches = NAMED_EXPORT_RE.findall(content)
    assert not matches, (
        f"{ts_file.name} has named exports (export const/let/var/function). "
        f"opencode's legacy loader rejects non-function exports. "
        f"Move named exports to a companion file OUTSIDE .opencode/plugin/. "
        f"Found {len(matches)} named export(s)."
    )


def test_no_exports_ts_files_anywhere_under_plugin_dir() -> None:
    """No ``_exports.ts`` files may exist anywhere under .opencode/plugin/.

    These companion files have only named exports (no default function) and
    crash opencode if auto-discovered — either at the top level or in a
    subdirectory if a future opencode version recurses.
    """
    if not PLUGIN_DIR.is_dir():
        pytest.skip(".opencode/plugin/ not present")
    exports_files = sorted(PLUGIN_DIR.rglob("*_exports.ts"))
    assert not exports_files, (
        "Companion _exports.ts files must live OUTSIDE .opencode/plugin/ "
        "(opencode auto-loads them and crashes on the missing default fn). "
        "Found: "
        + ", ".join(str(p.relative_to(ROOT)) for p in exports_files)
    )


def test_no_hot_reload_stub_in_plugin_dir() -> None:
    """The dead ``hot_reload.ts`` compatibility stub must not be in plugin dir.

    This file (deleted 2026-07-23) had no ``export default`` and was never
    imported — all plugins import from ``../lib/hot_reload.ts``. Its presence
    in ``.opencode/plugin/`` caused an opencode boot crash.
    """
    stub = PLUGIN_DIR / "hot_reload.ts"
    assert not stub.exists(), (
        f"{stub} is a dead stub with no export default. "
        f"All plugins import from ../lib/hot_reload.ts. Delete it."
    )


# ---------------------------------------------------------------------------
# opencode.json integration
# ---------------------------------------------------------------------------


def test_opencode_json_plugin_entries_resolve() -> None:
    """Every entry in opencode.json ``plugin`` array must resolve on disk."""
    cfg = json.loads(OPENCODE_JSON.read_text())
    plugins = cfg.get("plugin", [])
    assert plugins, "opencode.json has no plugin entries"
    missing = []
    for entry in plugins:
        path_str = entry if isinstance(entry, str) else entry[0]
        if not (ROOT / path_str).is_file():
            missing.append(path_str)
    assert not missing, (
        "opencode.json references plugin files that do not exist:\n"
        + "\n".join(f"  {m}" for m in missing)
    )


@pytest.mark.skipif(
    not PLUGIN_DIR.is_dir(), reason=".opencode/plugin/ not present"
)
def test_no_orphan_ts_files_in_plugin_dir() -> None:
    """Every top-level .ts in .opencode/plugin/ should be in opencode.json.

    Catches non-plugin files (like _exports.ts, hot_reload.ts stubs) that
    pollute the auto-discovery directory. Files in subdirectories (impl/) and
    the sibling ``plugins/`` directory are exempt.
    """
    cfg = json.loads(OPENCODE_JSON.read_text())
    config_basenames = {
        Path(entry if isinstance(entry, str) else entry[0]).name
        for entry in cfg.get("plugin", [])
    }
    orphans = [f.name for f in TOP_LEVEL_TS if f.name not in config_basenames]
    assert not orphans, (
        "Non-plugin .ts files found in .opencode/plugin/ (auto-loaded by "
        "opencode, will crash if they lack a proper default export):\n"
        + "\n".join(f"  {o}" for o in sorted(orphans))
    )


# ---------------------------------------------------------------------------
# Hook-name validator
# ---------------------------------------------------------------------------


def test_check_plugin_hooks_exits_zero() -> None:
    """``scripts/check_plugin_hooks.py`` must report no invalid hook names."""
    result = subprocess.run(
        [sys.executable, str(HOOK_CHECKER)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        "invalid hook names detected:\n" + result.stdout + "\n" + result.stderr
    )


# ---------------------------------------------------------------------------
# Node-level default-export check (runtime, catches transforms that
# strip export default via code-walking that source-grep misses)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    subprocess.run(
        ["node", "--version"], capture_output=True
    ).returncode != 0,
    reason="node not available",
)
def test_each_plugin_default_export_is_function() -> None:
    """Each plugin's Node dynamic-import default must be a function.

    This is the runtime equivalent of the source-level check — catches cases
    where ``export default`` is present in source but evaluates to a non-
    function at runtime (e.g. ``export default 42`` or re-export tricks).
    """
    cfg = json.loads(OPENCODE_JSON.read_text())
    plugins = cfg.get("plugin", [])
    node_script = (
        """
const plugins = __PLUGINS__;
let failures = [];
(async () => {
  for (const p of plugins) {
    try {
      const mod = await import(p);
      if (typeof mod.default !== 'function') {
        failures.push(p + ': default is ' + typeof mod.default);
      }
    } catch(e) {
      failures.push(p + ': IMPORT ERROR: ' + e.message);
    }
  }
  console.log(JSON.stringify(failures));
})();
"""
    ).replace("__PLUGINS__", json.dumps(plugins))

    result = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            node_script,
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=30,
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
    )
    output_lines = [
        line for line in result.stdout.strip().split("\n") if line.strip()
    ]
    failures = json.loads(output_lines[-1]) if output_lines else []
    assert not failures, (
        "Plugins whose default export is not a function "
        "(would crash opencode):\n"
        + "\n".join(f"  {f}" for f in failures)
    )
