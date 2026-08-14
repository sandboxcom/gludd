"""Phase FW — Framework and Tooling plugin property verification.

Structural tests verifying that every enforcement plugin in ``.opencode/plugin/``
has the framework-level invariants codified in AGENTS.md and TASKS.md Phase FW.
These properties already exist in the source — this module pins them so a
regression that strips one is caught at gate time.

What this proves (per TASKS.md Phase FW)
----------------------------------------
- FW.1  — every top-level plugin .ts has ``export default``
- FW.2  — every plugin uses the hot-reload proxy (``loadHotModule``)
- FW.3  — every plugin guards subagent context via ``isSubagent()``
- FW.4  — every plugin wraps hooks in fail-open try/catch
- FW.5  — every plugin honors a ``GLUDD_*_ENFORCE=0`` disable env var
- FW.6  — every plugin reports liveness via ``reportAlive()``
- FW.7/8 — bash-metachar blocking lives in enforce-make.ts (impl)
- FW.9  — opencode.json allows workspace tools and denies unlisted external paths
- FW.10 — crash-recovery state file handling in enforce-session-start.ts
- FW.11 — Makefile defines ``verify-plugin-manifest`` target
- FW.12 — shared.ts consolidates isSubagent/reportAlive/isDisengaged/getProjectRoot
- FW.13 — Makefile defines ``check-node-v26-compat`` target
- FW.14 — plugin test exports live OUTSIDE .opencode/plugin/ (in lib/)
- FW.15 — behavioral test runner scripts/test_plugin_behavior.py exists

Additionally pins the user-requested invariants:
- Makefile defines ``check-plugin-hook-invoke`` target

Tests are STRUCTURAL (grep/read source) — they do not invoke the hooks.
Runtime invocation is covered by ``make check-plugin-hook-invoke`` and the
``.test.node.mjs`` runtime suites.

Run: make test-specific TESTFILE='tests/unit/test_framework_plugin_properties'
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
IMPL_DIR = PLUGIN_DIR / "impl"
LIB_DIR = ROOT / ".opencode" / "lib"
OPENCODE_JSON = ROOT / "opencode.json"
MAKEFILE = ROOT / "Makefile"
SHARED_TS = LIB_DIR / "shared.ts"
HOT_RELOAD_TS = LIB_DIR / "hot_reload.ts"
PLUGIN_TEST_EXPORTS = LIB_DIR / "plugin_test_exports.ts"
BEHAVIORAL_RUNNER = ROOT / "scripts" / "test_plugin_behavior.py"

# Top-level plugin .ts files (the ones opencode auto-discovers).
# Excludes: ``impl/`` subdirectory, ``*.test.node.mjs`` runtime suites.
TOP_LEVEL_PLUGINS: list[Path] = (
    sorted(p for p in PLUGIN_DIR.glob("enforce-*.ts")) if PLUGIN_DIR.is_dir() else []
)


def _effective_source(plugin_path: Path) -> str:
    """Return plugin source + impl source if the plugin delegates to impl/.

    Two plugins (enforce-make.ts, enforce-stop.ts) are thin wrappers that
    delegate to ``impl/enforce_make_impl.ts`` / ``impl/enforce_stop_impl.ts``.
    Properties like reportAlive / loadHotModule / _ENFORCE live in the impl
    file, not the wrapper. Treat the impl file as part of the plugin.
    """
    plugin_path.stem.split("_")  # enforce-make -> ["enforce-make"]
    stem = plugin_path.stem  # "enforce-make"
    # Convention: impl file is impl/<stem>_impl.ts or impl/enforce_<X>_impl.ts
    candidates = [
        IMPL_DIR / f"{stem}_impl.ts",
        IMPL_DIR / f"{stem.replace('-', '_')}_impl.ts",
    ]
    text = plugin_path.read_text()
    for cand in candidates:
        if cand.is_file():
            text += "\n" + cand.read_text()
            break
    return text


# ---------------------------------------------------------------------------
# FW.1 — every plugin has export default (no named exports)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plugin", TOP_LEVEL_PLUGINS, ids=lambda p: p.name)
def test_fw1_plugin_has_export_default(plugin: Path) -> None:
    """FW.1: every top-level plugin .ts has ``export default``.

    Files without a default export crash opencode's auto-discovery loader.
    """
    assert "export default" in plugin.read_text(), (
        f"{plugin.name}: missing 'export default'. opencode's auto-discovery "
        f"calls the default export as a plugin — its absence crashes boot."
    )


# ---------------------------------------------------------------------------
# FW.2 — hot-reload proxy pattern (loadHotModule)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plugin", TOP_LEVEL_PLUGINS, ids=lambda p: p.name)
def test_fw2_plugin_uses_loadHotModule(plugin: Path) -> None:
    """FW.2: every plugin uses the hot-reload proxy via ``loadHotModule``.

    Without the proxy, committed plugin changes require an opencode restart.
    """
    source = _effective_source(plugin)
    assert "loadHotModule" in source, (
        f"{plugin.name}: does not call loadHotModule. Every enforcement "
        f"plugin must use the hot-reload proxy pattern (hot_reload.ts)."
    )


def test_fw2_hot_reload_lib_exists() -> None:
    """FW.2: the hot-reload proxy utility itself exists and exports the helper."""
    assert HOT_RELOAD_TS.is_file(), "missing .opencode/lib/hot_reload.ts"
    text = HOT_RELOAD_TS.read_text()
    assert "export function loadHotModule" in text, (
        "hot_reload.ts must export loadHotModule"
    )
    assert "HotModule" in text, "hot_reload.ts must export the HotModule type"


# ---------------------------------------------------------------------------
# FW.3 — subagent guard (isSubagent)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plugin", TOP_LEVEL_PLUGINS, ids=lambda p: p.name)
def test_fw3_plugin_uses_isSubagent_guard(plugin: Path) -> None:
    """FW.3: every plugin checks ``isSubagent()`` and skips enforcement.

    Subagent contexts must never have enforcement fired inside them — the
    orchestrator manages enforcement, not the subagent.
    """
    source = _effective_source(plugin)
    assert "isSubagent" in source, (
        f"{plugin.name}: missing isSubagent() guard. Plugins MUST NOT fire "
        f"in subagent context (see AGENTS.md 'Subagent Enforcement Isolation')."
    )


def test_fw3_shared_ts_exports_isSubagent() -> None:
    """FW.3: ``isSubagent`` is defined once in shared.ts, not duplicated."""
    assert SHARED_TS.is_file(), "missing .opencode/lib/shared.ts"
    text = SHARED_TS.read_text()
    assert re.search(r"export function isSubagent\b", text), (
        "shared.ts must export isSubagent() — single source of truth"
    )
    # No plugin should redefine its own _isSubagent / isSubagent (the old
    # per-plugin copy-paste pattern from before the E.5 refactor).
    for plugin in TOP_LEVEL_PLUGINS:
        source = plugin.read_text()
        assert not re.search(r"function\s+isSubagent\b", source), (
            f"{plugin.name}: defines a local isSubagent() — use the shared "
            f"import from lib/shared.ts instead (E.5 consolidation)."
        )


# ---------------------------------------------------------------------------
# FW.4 — fail-open try/catch patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plugin", TOP_LEVEL_PLUGINS, ids=lambda p: p.name)
def test_fw4_plugin_has_fail_open_try_catch(plugin: Path) -> None:
    """FW.4: every plugin has at least one try/catch block (fail-open pattern).

    A throw inside a plugin hook must NEVER wedge the editor — every hook is
    wrapped in try/catch that returns ``undefined`` (allow) on exception.
    """
    source = _effective_source(plugin)
    assert "try" in source and "catch" in source, (
        f"{plugin.name}: no try/catch found. Every plugin hook must be "
        f"fail-open (AGENTS.md: 'any exception must allow the operation')."
    )


def test_fw4_shared_ts_fail_open_comments() -> None:
    """FW.4: shared.ts documents the fail-open contract in its helpers."""
    text = SHARED_TS.read_text()
    # readJsonFile / writeJsonFile / reportAlive all swallow exceptions.
    assert "fail-open" in text.lower() or "/* fail-open" in text, (
        "shared.ts should document fail-open semantics on its helpers"
    )


# ---------------------------------------------------------------------------
# FW.5 — env var disable pattern (GLUDD_*_ENFORCE=0)
# ---------------------------------------------------------------------------


# Disable-env-pattern regex. Accepts all three forms used in the codebase:
#   (a) ``process.env.GLUDD_X_ENFORCE === "0"``  (early return)
#   (b) ``process.env.GLUDD_X_ENFORCE !== "0"``  (compute ENFORCE flag)
#   (c) ``(process.env.GLUDD_X_ENFORCE || "1") !== "0"``  (default-on flag)
# The optional ``|| "1"`` segment handles form (c).
ENVAR_DISABLE_RE = re.compile(
    r'GLUDD_[A-Z0-9_]*ENFORCE\s*(?:\|\|\s*"1"\s*)?\)?\s*(?:===\s*"0"|!==\s*"0")'
)

# Plugins that are intentionally hard-coded on because disabling them would
# bypass the quality gate they protect.
HARD_ON_PLUGINS: frozenset[str] = frozenset({
    "enforce-no-suppressions.ts",
})


@pytest.mark.parametrize("plugin", TOP_LEVEL_PLUGINS, ids=lambda p: p.name)
def test_fw5_plugin_has_env_var_disable(plugin: Path) -> None:
    """FW.5: every plugin honors ``GLUDD_<NAME>_ENFORCE=0`` to disable.

    Operators must be able to disable any single plugin without restarting
    opencode or editing source code. Accepts any of the three idiomatic
    forms (early-return, flag-compute, default-on-flag).
    """
    source = _effective_source(plugin)
    if plugin.name in HARD_ON_PLUGINS:
        assert "no environment-variable bypass" in source
        assert not ENVAR_DISABLE_RE.search(source)
        return
    assert ENVAR_DISABLE_RE.search(source), (
        f"{plugin.name}: no GLUDD_*_ENFORCE=0 env-var disable found. "
        f"Every plugin must be individually disableable."
    )


# ---------------------------------------------------------------------------
# FW.6 — plugin heartbeat (reportAlive)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plugin", TOP_LEVEL_PLUGINS, ids=lambda p: p.name)
def test_fw6_plugin_calls_reportAlive(plugin: Path) -> None:
    """FW.6: every plugin calls ``reportAlive()`` on hook entry.

    The watchdog uses ``/tmp/gludd-plugin-alive.json`` to detect dead plugins;
    a plugin that does not write its heartbeat cannot be distinguished from
    a crashed one.
    """
    source = _effective_source(plugin)
    assert "reportAlive" in source, (
        f"{plugin.name}: does not call reportAlive(). The watchdog cannot "
        f"detect liveness without a heartbeat (AGENTS.md FW.6)."
    )


def test_fw6_shared_ts_exports_reportAlive() -> None:
    """FW.6: ``reportAlive`` is defined once in shared.ts."""
    text = SHARED_TS.read_text()
    assert re.search(r"export function reportAlive\b", text), (
        "shared.ts must export reportAlive() — single source of truth"
    )
    assert "/tmp/gludd-plugin-alive" in text or "ALIVE_PATH" in text, (
        "shared.ts must write heartbeats to the canonical alive path"
    )


# ---------------------------------------------------------------------------
# FW.7/FW.8 — bash-metachar blocking in enforce-make.ts (impl)
# ---------------------------------------------------------------------------


def test_fw7_fw8_metachar_blocking_exists() -> None:
    """FW.7/FW.8: enforce-make impl blocks shell metacharacters in bash.

    AGENTS.md codifies a fixed list of forbidden shell metacharacters
    (``|`` ``;`` ``&&`` ``||`` ``$()`` backticks ``>`` ``<`` ``2>&1`` ``{}``
    ``!`` ``\\``). The impl must define a metachar policy and emit a deny
    message containing the matched chars when one is found.
    """
    impl = IMPL_DIR / "enforce_make_impl.ts"
    assert impl.is_file(), "missing impl/enforce_make_impl.ts"
    text = impl.read_text()
    assert "BASH_METACHAR_POLICY" in text, (
        "enforce_make_impl.ts must define BASH_METACHAR_POLICY"
    )
    assert "Shell metacharacter(s) forbidden" in text, (
        "enforce_make_impl.ts must emit a metachar-forbidden deny message"
    )
    # Spot-check at least the canonical forbidden chars from AGENTS.md.
    for char_literal in ("|", ";", "&&", "||", "$(", "`", ">", "<", "!", "\\"):
        assert char_literal in text, (
            f"enforce_make_impl.ts: missing forbidden-char '{char_literal}' "
            f"in metachar policy"
        )


# ---------------------------------------------------------------------------
# FW.9 — workspace path restriction in opencode.json
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", ["global", "build"], ids=lambda s: s)
def test_fw9_opencode_json_restricts_external_paths(scope: str) -> None:
    """FW.9: workspace tools work while unlisted external paths are denied.

    OpenCode grants file tools for the workspace through the direct
    ``read``/``edit``/``glob``/``grep`` permissions.  Path allowlisting belongs
    under ``external_directory``; putting workspace globs on every file tool is
    not part of the supported schema and previously broke the live TUI.
    """
    cfg = json.loads(OPENCODE_JSON.read_text())
    perm = dict(cfg["permission"])
    if scope == "build":
        perm.update(cfg.get("agent", {}).get("build", {}).get("permission", {}))

    assert perm["read"] == {
        "*": "allow",
        "*.env": "deny",
        "*.env.*": "deny",
        "*.env.example": "allow",
    }
    assert perm["edit"] == "allow"
    assert perm["glob"] == "allow"
    assert perm["grep"] == "allow"
    assert "write" not in perm, "OpenCode routes writes through the edit permission"

    external = perm["external_directory"]
    assert external.get("*") == "deny"
    assert external.get("/tmp/**") == "allow"
    allowed_external = {
        "*",
        "/tmp/**",
        "/private/tmp/**",
        "/private/var/folders/**",
        "/Users/shawnwilson/.config/opencode/**",
        "/Users/shawnwilson/.local/share/opencode/**",
        "/Users/shawnwilson/.cache/**",
    }
    assert set(external) <= allowed_external, (
        f"permission.external_directory has an unreviewed path: "
        f"{sorted(set(external) - allowed_external)}"
    )


def test_fw9_opencode_json_bash_make_only() -> None:
    """FW.9 (companion): bash is hard-denied except for ``make *``."""
    cfg = json.loads(OPENCODE_JSON.read_text())
    bash = cfg.get("permission", {}).get("bash", {})
    assert bash.get("*") == "deny", (
        "permission.bash must deny everything by default"
    )
    assert bash.get("make *") == "allow", (
        "permission.bash must allow only `make <target>` commands"
    )


# ---------------------------------------------------------------------------
# FW.10 — crash recovery state file handling (enforce-session-start.ts)
# ---------------------------------------------------------------------------


def test_fw10_crash_recovery_state_handling() -> None:
    """FW.10: enforce-session-start.ts detects stale state via PID + age.

    A prior crashed session leaves a stale ``/tmp/gludd-session-start.json``
    that would gate a fresh session incorrectly. The plugin must:
      (a) check stored PID against ``process.pid`` (PID mismatch → reset),
      (b) check state age against a staleness threshold (STALE_MS),
      (c) write atomically via tmp-file + ``renameSync`` (no partial reads).
    """
    plugin = PLUGIN_DIR / "enforce-session-start.ts"
    assert plugin.is_file(), "missing enforce-session-start.ts"
    text = plugin.read_text()
    assert "pidMismatch" in text, (
        "enforce-session-start.ts: must compute pidMismatch for crash recovery"
    )
    assert "STALE_MS" in text, (
        "enforce-session-start.ts: must define STALE_MS age threshold"
    )
    assert "renameSync" in text, (
        "enforce-session-start.ts: must use atomic renameSync on saveState"
    )
    assert "storedPid !== 0" in text or "storedPid !== 0 &&" in text, (
        "enforce-session-start.ts: PID mismatch must exclude zero PIDs "
        "(test fixtures / hand-written state must NOT trigger reset)"
    )


# ---------------------------------------------------------------------------
# FW.11 — Makefile defines verify-plugin-manifest target
# ---------------------------------------------------------------------------


def test_fw11_makefile_has_verify_plugin_manifest() -> None:
    """FW.11: ``make verify-plugin-manifest`` target exists.

    Catches orphan plugins (in opencode.json but not on disk) and missing
    plugins (on disk but not in opencode.json).
    """
    text = MAKEFILE.read_text()
    assert re.search(r"^verify-plugin-manifest\s*:", text, re.MULTILINE), (
        "Makefile missing 'verify-plugin-manifest:' target"
    )


# ---------------------------------------------------------------------------
# FW.12 — shared.ts consolidation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "helper",
    ["isSubagent", "reportAlive", "isDisengaged", "getProjectRoot"],
    ids=lambda h: h,
)
def test_fw12_shared_ts_consolidates_helpers(helper: str) -> None:
    """FW.12: shared.ts is the single source for the common plugin helpers.

    Before the E.5 refactor, each plugin copy-pasted its own ``_isSubagent``,
    disengage-check, project-root walk, etc. shared.ts eliminates that
    duplication — every helper must be defined here.
    """
    text = SHARED_TS.read_text()
    assert re.search(rf"export function {helper}\b", text), (
        f"shared.ts: missing export function {helper}() (FW.12 consolidation)"
    )


def test_fw12_no_duplicated_helpers_in_plugins() -> None:
    """FW.12 (enforcement): plugins must NOT redefine consolidated helpers.

    A plugin that defines its own ``getProjectRoot`` or ``isDisengaged`` has
    reverted to the pre-refactor copy-paste pattern.
    """
    forbidden = ["getProjectRoot", "isDisengaged"]
    violations: list[str] = []
    for plugin in TOP_LEVEL_PLUGINS:
        text = plugin.read_text()
        for fn in forbidden:
            if re.search(rf"function\s+{fn}\b", text):
                violations.append(f"{plugin.name}: defines local {fn}()")
    assert not violations, (
        "Plugins must import consolidated helpers from shared.ts, not "
        "redefine them:\n" + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# FW.13 — Makefile defines check-node-v26-compat target
# ---------------------------------------------------------------------------


def test_fw13_makefile_has_check_node_v26_compat() -> None:
    """FW.13: ``make check-node-v26-compat`` target exists.

    All plugin .ts files must parse under Node v26 ``--experimental-strip-types``.
    The check forbids enums, namespaces, and nested try-in-catch patterns.
    """
    text = MAKEFILE.read_text()
    assert re.search(r"^check-node-v26-compat\s*:", text, re.MULTILINE), (
        "Makefile missing 'check-node-v26-compat:' target"
    )


# ---------------------------------------------------------------------------
# FW.14 — plugin test exports live OUTSIDE .opencode/plugin/
# ---------------------------------------------------------------------------


def test_fw14_plugin_test_exports_outside_plugin_dir() -> None:
    """FW.14: helper exports live in ``lib/plugin_test_exports.ts``.

    Files in ``.opencode/plugin/`` are auto-discovered by opencode's loader;
    a companion file with named exports crashes the boot (2026-07-23 incident).
    The canonical home is ``.opencode/lib/``.
    """
    assert PLUGIN_TEST_EXPORTS.is_file(), (
        "missing .opencode/lib/plugin_test_exports.ts (FW.14 canonical home)"
    )
    # No _exports.ts files anywhere under .opencode/plugin/.
    if PLUGIN_DIR.is_dir():
        leaks = sorted(PLUGIN_DIR.rglob("*_exports.ts"))
        assert not leaks, (
            "Companion _exports.ts files must live OUTSIDE .opencode/plugin/ "
            "(auto-discovery crashes on missing default fn). Found: "
            + ", ".join(str(p.relative_to(ROOT)) for p in leaks)
        )


# ---------------------------------------------------------------------------
# FW.15 — behavioral plugin test runner exists
# ---------------------------------------------------------------------------


def test_fw15_behavioral_test_runner_exists() -> None:
    """FW.15: ``scripts/test_plugin_behavior.py`` exists.

    Structural tests (this file) prove source-shape; behavioral tests prove
    runtime hook behavior with real inputs. Both layers are required.
    """
    assert BEHAVIORAL_RUNNER.is_file(), (
        "missing scripts/test_plugin_behavior.py (FW.15 behavioral layer)"
    )


# ---------------------------------------------------------------------------
# User-requested: check-plugin-hook-invoke target exists
# ---------------------------------------------------------------------------


def test_makefile_has_check_plugin_hook_invoke() -> None:
    """``make check-plugin-hook-invoke`` target exists.

    The definitive ReferenceError check — actually invokes every plugin hook
    function with null-safe inputs. Catches the class of bug where a function
    is called but never imported (2026-07-24 incident).
    """
    text = MAKEFILE.read_text()
    assert re.search(r"^check-plugin-hook-invoke\s*:", text, re.MULTILINE), (
        "Makefile missing 'check-plugin-hook-invoke:' target"
    )


# ---------------------------------------------------------------------------
# Cross-cutting: opencode.json plugin list is non-empty + all resolve
# ---------------------------------------------------------------------------


def test_opencode_json_plugin_list_resolves() -> None:
    """Every entry in opencode.json ``plugin`` array resolves to a file.

    Catches orphan references (plugin removed from disk but still listed).
    """
    cfg = json.loads(OPENCODE_JSON.read_text())
    plugins = cfg.get("plugin", [])
    assert plugins, "opencode.json has no plugin entries"
    missing = [p for p in plugins if not (ROOT / p).is_file()]
    assert not missing, (
        "opencode.json references plugin files that do not exist:\n"
        + "\n".join(f"  {m}" for m in missing)
    )


def test_top_level_plugin_count() -> None:
    """Sanity: at least 20 top-level enforcement plugins are present.

    A future refactor that accidentally moves plugins into a subdirectory
    (and out of auto-discovery) would silently drop enforcement.
    """
    assert len(TOP_LEVEL_PLUGINS) >= 20, (
        f"Expected >=20 top-level plugins in .opencode/plugin/, "
        f"found {len(TOP_LEVEL_PLUGINS)}. Did the directory layout change?"
    )
