"""Heartbeat coverage for every registered opencode plugin.

Each plugin registered in opencode.json must define and invoke a
`_reportAlive()` heartbeat probe so the watchdog (and any other observer)
can confirm the plugin's hook entry point actually fires. A plugin that
loads but never reports alive is structurally indistinguishable from a
plugin whose hook was silently defanged — this test catches both omissions
on NEW plugins and regressions on existing ones.

The test reads plugin source files directly (no execution). It is
parametrized over the plugins registered in opencode.json, so adding a
new plugin without a heartbeat fails BOTH the per-plugin case AND the
count assert.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
OPENCODE_JSON = REPO_ROOT / "opencode.json"

# Definition: `function _reportAlive` (optionally `async function`).
_REPORT_ALIVE_DEF = re.compile(r"function\s+_reportAlive\s*\(")

# Call: `_reportAlive()` — parens present, optional leading `await`.
# Excludes the definition itself (which is `function _reportAlive(...)`).
_REPORT_ALIVE_CALL = re.compile(r"(?<!function\s)_reportAlive\s*\(\s*\)")


def _registered_plugins() -> list[tuple[str, Path]]:
    """Return (name, absolute_path) for every plugin referenced in opencode.json.

    `name` is the basename without the `.ts` extension; it is the key the
    plugin writes into the alive map (e.g. enforce-stop.ts → "enforce-stop").
    """
    cfg = json.loads(OPENCODE_JSON.read_text())
    out: list[tuple[str, Path]] = []
    for rel in cfg.get("plugin", []):
        # NOTE: do NOT use lstrip("./") — it treats "./" as a character SET
        # {'.', '/'} and strips the leading '.' off ".opencode", resolving
        # to opencode/plugin/x.ts (wrong) instead of .opencode/plugin/x.ts.
        rel_path = rel[2:] if rel.startswith("./") else rel
        path = (REPO_ROOT / rel_path).resolve()
        name = Path(rel_path).stem
        out.append((name, path))
    return out


PLUGINS = _registered_plugins()


@pytest.mark.parametrize(
    "plugin_name, plugin_path",
    PLUGINS,
    ids=[name for name, _ in PLUGINS],
)
def test_plugin_has_heartbeat(plugin_name: str, plugin_path: Path) -> None:
    """Each registered plugin must define, call, and self-identify its heartbeat."""
    assert plugin_path.exists(), f"plugin file referenced in opencode.json missing: {plugin_path}"
    src = plugin_path.read_text()

    # 1. Definition present: `function _reportAlive(...)`
    assert _REPORT_ALIVE_DEF.search(src), (
        f"{plugin_path.name}: missing `function _reportAlive(...)` definition. "
        "Every plugin must define a heartbeat probe so the watchdog can confirm "
        "the hook entry point fires."
    )

    # 2. Called somewhere (not just defined): `_reportAlive()` with parens.
    #    Strip the definition line so we don't false-positive on the signature.
    src_without_def = _REPORT_ALIVE_DEF.sub("", src)
    assert _REPORT_ALIVE_CALL.search(src_without_def), (
        f"{plugin_path.name}: `_reportAlive()` is defined but never invoked. "
        "The heartbeat must be called from at least one hook entry point "
        "(tool.execute.before / chat.response.transform / system.transform / etc)."
    )

    # 3. The plugin's name appears as the JSON key in the alive write
    #    (e.g. `alive["enforce-stop"]`), so the heartbeat is self-identifying
    #    rather than masquerading as another plugin.
    expected_key = f'alive["{plugin_name}"]'
    assert expected_key in src, (
        f"{plugin_path.name}: alive-map write must use the plugin's own name as key — "
        f"expected `{expected_key}` (the plugin must self-identify)."
    )


def test_all_registered_plugins_have_heartbeats() -> None:
    """Count assert — catches NEW plugins added without heartbeats.

    If a plugin is added to opencode.json without a `_reportAlive` definition,
    the per-plugin case above fails — but this count assert is the structural
    backstop that fails LOUDLY with a diff of the missing plugins.
    """
    missing: list[str] = []
    for name, path in PLUGINS:
        if not path.exists():
            missing.append(f"{name} (file missing: {path})")
            continue
        src = path.read_text()
        if not _REPORT_ALIVE_DEF.search(src):
            missing.append(name)
            continue
        src_without_def = _REPORT_ALIVE_DEF.sub("", src)
        if not _REPORT_ALIVE_CALL.search(src_without_def):
            missing.append(f"{name} (defined but never called)")
            continue
        if f'alive["{name}"]' not in src:
            missing.append(f"{name} (missing self-identifying alive key)")
            continue

    assert not missing, (
        f"{len(missing)}/{len(PLUGINS)} registered plugins lack a heartbeat: "
        f"{missing}. Every plugin in opencode.json must define, call, and "
        f"self-identify a `_reportAlive()` probe."
    )


def test_plugin_count_matches_opencode_json() -> None:
    """Belt-and-braces: the parametrized list is non-empty and matches config.

    Guards against a future refactor that breaks the plugin-list loader
    silently (returning [] would make every parametrized case a no-op).
    """
    cfg = json.loads(OPENCODE_JSON.read_text())
    registered = cfg.get("plugin", [])
    assert len(PLUGINS) == len(registered), (
        f"plugin loader returned {len(PLUGINS)} plugins but opencode.json "
        f"registers {len(registered)}. Loader is out of sync."
    )
    assert len(PLUGINS) > 0, "no plugins registered in opencode.json"
