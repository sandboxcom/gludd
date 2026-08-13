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

# Definition: `function _reportAlive` (old, inline) or `function reportAlive` (new, shared).
# Post E.5 refactor: plugins import reportAlive from shared.ts instead of defining locally.
_REPORT_ALIVE_DEF = re.compile(r"function\s+_?reportAlive\s*\(")

# Import from shared.ts: `import { ..., reportAlive, ... } from "../lib/shared.ts"`
_REPORT_ALIVE_IMPORT = re.compile(r'import\s+\{[^}]*\breportAlive\b[^}]*\}\s+from\s+"[^"]*shared\.ts"')

# Call: `reportAlive("plugin")` or `_reportAlive()` — parens present, optional await.
# Excludes the definition itself (which is `function reportAlive(...)`).
# Post E.5 refactor: calls pass string arg like `reportAlive("enforce-make")`.
_REPORT_ALIVE_CALL = re.compile(r"(?<!function\s)_?reportAlive\s*\(")


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


def _effective_source(plugin_path: Path) -> str:
    """Return the entrypoint plus any split runtime implementation."""
    entrypoint = plugin_path.read_text(encoding="utf-8")
    implementation_sources = []
    for relative in re.findall(
        r"""from\s+["'](\./impl/[^"']+)["']""",
        entrypoint,
    ):
        implementation_path = plugin_path.parent / relative
        if implementation_path.is_file():
            implementation_sources.append(
                implementation_path.read_text(encoding="utf-8")
            )
    return "\n".join([entrypoint, *implementation_sources])


@pytest.mark.parametrize(
    "plugin_name, plugin_path",
    PLUGINS,
    ids=[name for name, _ in PLUGINS],
)
def test_plugin_has_heartbeat(plugin_name: str, plugin_path: Path) -> None:
    """Each registered plugin must define OR import, call, and self-identify its heartbeat.

    Post E.5 refactor: plugins import `reportAlive` from shared.ts instead of
    defining `_reportAlive` locally. Both patterns are accepted.
    """
    assert plugin_path.exists(), f"plugin file referenced in opencode.json missing: {plugin_path}"
    src = _effective_source(plugin_path)

    # 1. Definition OR import present
    has_def = bool(_REPORT_ALIVE_DEF.search(src))
    has_import = bool(_REPORT_ALIVE_IMPORT.search(src))
    assert has_def or has_import, (
        f"{plugin_path.name}: must define `reportAlive()` or import it from "
        f"../lib/shared.ts. Every plugin must have a heartbeat probe."
    )

    # 2. Called somewhere: `reportAlive()` or `_reportAlive()` with parens.
    src_without_def = _REPORT_ALIVE_DEF.sub("", src)
    assert _REPORT_ALIVE_CALL.search(src_without_def), (
        f"{plugin_path.name}: `reportAlive()` is defined/imported but never invoked. "
        "The heartbeat must be called from at least one hook entry point "
        "(tool.execute.before / chat.response.transform / system.transform / etc)."
    )

    # 3. Self-identification: either the old key pattern OR new shared import
    #    (reportAlive("plugin-name") from shared.ts self-identifies).
    expected_key = f'alive["{plugin_name}"]'
    expected_call = f'reportAlive("{plugin_name}")'
    assert expected_key in src or expected_call in src, (
        f"{plugin_path.name}: must self-identify — expected `{expected_key}` "
        f"or `{expected_call}`."
    )


def test_all_registered_plugins_have_heartbeats() -> None:
    """Count assert — catches NEW plugins added without heartbeats.

    If a plugin is added to opencode.json without a `reportAlive` definition
    or import, the per-plugin case above fails — but this count assert is
    the structural backstop that fails LOUDLY with a diff of the missing plugins.
    """
    missing: list[str] = []
    for name, path in PLUGINS:
        if not path.exists():
            missing.append(f"{name} (file missing: {path})")
            continue
        src = _effective_source(path)
        has_def = bool(_REPORT_ALIVE_DEF.search(src))
        has_import = bool(_REPORT_ALIVE_IMPORT.search(src))
        if not has_def and not has_import:
            missing.append(name)
            continue
        src_without_def = _REPORT_ALIVE_DEF.sub("", src)
        if not _REPORT_ALIVE_CALL.search(src_without_def):
            missing.append(f"{name} (defined/imported but never called)")
            continue
        expected_key = f'alive["{name}"]'
        expected_call = f'reportAlive("{name}")'
        if expected_key not in src and expected_call not in src:
            missing.append(f"{name} (missing self-identifying alive key or call)")
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
