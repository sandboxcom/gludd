"""Self-test: validates the runtime test harness covers EVERY enforcement plugin.

Checks that `scripts/test_hook_runtime.py` actually loads and invokes hook
functions from each plugin, not just checks source-code patterns. This test
is the gate that would have caught the enforce-stop.ts gap (8 structural tests
that could never load the file).

Rules:
  1. Every .opencode/plugin/*.ts and .opencode/plugins/*.ts MUST have >=1
     runtime test in `scripts/test_hook_runtime.py`.
  2. Runtime tests MUST actually invoke hooks (load the plugin module and call
     `tool.execute.before` / `text.complete` / exported functions) — not just
     check source patterns like regex matching on the source file.
  3. shared.ts and hot_reload.ts are exempt (they are libraries, not plugins).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK_RUNTIME = ROOT / "scripts" / "test_hook_runtime.py"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"

EXEMPT = {
    "shared.ts",
    "hot_reload.ts",
}

# ---- source analysis helpers ----

def _all_plugin_files() -> set[str]:
    """Return basenames of all enforcement plugins."""
    plugins: set[str] = set()
    if PLUGIN_DIR.is_dir():
        plugins.update(f.name for f in PLUGIN_DIR.iterdir() if f.suffix == ".ts")
    if PLUGINS_DIR.is_dir():
        plugins.update(f.name for f in PLUGINS_DIR.iterdir() if f.suffix == ".ts")
    return plugins - EXEMPT


def _plugin_basenames_from_test_source() -> set[str]:
    """Extract plugin-enforce-XXX references from the runtime test source.

    Handles three patterns used in the harness:
      1. Direct import:  await import('{PLUGIN_DIR}/enforce-XXX.ts')
      2. Factory helper:  _factory_plugin_code("enforce-XXX.ts", ...)
      3. PluginAPI helper: _pluginapi_code("enforce-XXX.ts", ...)
    """
    source = HOOK_RUNTIME.read_text()
    imported = set(
        re.findall(r"await import\('[^']*/(enforce-\w[\w-]*\.ts)'\)", source)
    )
    # Catch helper-function patterns
    imported.update(
        re.findall(r"_factory_plugin_code\(\s*\"(enforce-\w[\w-]*\.ts)\"", source)
    )
    imported.update(
        re.findall(r"_pluginapi_code\(\s*\"(enforce-\w[\w-]*\.ts)\"", source)
    )
    # watchdog.ts lives in .opencode/plugins/ not .opencode/plugin/
    imported.update(
        re.findall(r"await import\('[^']*/(watchdog\.ts)'\)", source)
    )
    return imported


def _test_function_names() -> set[str]:
    """Return all test_* function names in the runtime test file."""
    source = HOOK_RUNTIME.read_text()
    tree = ast.parse(source)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


def _test_calls_real_hooks(test_name: str) -> bool:
    """Check if a test function invokes real plugin hooks (not source-pattern checks).

    A test qualifies if its body contains `_run_ts(code` or
    `console.log(JSON.stringify` — these indicate the test actually
    fires up node and executes plugin code, as opposed to only inspecting
    source file contents.
    """
    body_lines = _extract_test_body_lines(test_name)
    body = "\n".join(body_lines)

    real_hook_patterns = [
        r"_run_ts\(code",
        r"console\.log\(JSON\.stringify",
        r"await import\('",
        r"_pluginapi_code\(",
        r"_factory_plugin_code\(",
        r"_enforce_make_bash_test\(",
    ]

    return any(re.search(p, body) for p in real_hook_patterns)


def _plugin_to_test_map() -> dict[str, list[str]]:
    """Map each plugin filename to the test functions that exercise it.

    Uses section headers (comment lines containing a plugin .ts filename
    as a word boundary) to determine which plugin each test belongs to.

    Format examples seen in test_hook_runtime.py:
      # enforce-clean-tree.ts  ─  exports pure functions + PluginAPI hook
      # ── enforce-floor.ts  —  runtime tests: text.complete, ...
    """
    source = HOOK_RUNTIME.read_text()
    lines = source.split("\n")

    # Match any comment line containing a plugin filename as a distinct word
    plugin_basenames = "|".join(
        re.escape(p) for p in sorted(_all_plugin_files(), key=len, reverse=True)
    )
    section_header_re = re.compile(
        rf"^#.*\b({plugin_basenames})\b"
    )

    # Detect which plugin a test imports directly (fallback)
    import_re = re.compile(
        r"await import\('\{?PLUGIN_DIR\}?\s*/\s*(enforce-[\w-]+\.ts)'\)"
    )
    helper_re = re.compile(
        r"_(?:factory_plugin|pluginapi)_code\(\s*\"(enforce-[\w-]+\.ts)\""
    )

    current_plugin: str | None = None
    test_to_plugin: dict[str, str] = {}

    for line in lines:
        m = section_header_re.match(line)
        if m:
            current_plugin = m.group(1)
            continue
        tm = re.match(r"^def (test_\w+)", line)
        if tm and current_plugin:
            test_to_plugin[tm.group(1)] = current_plugin

    # Fallback: import-based detection for any unmatched tests
    for test_name in sorted(_test_function_names()):
        if test_name in test_to_plugin:
            continue
        test_lines = _extract_test_body_lines(test_name)
        test_body = "\n".join(test_lines)
        imp = import_re.search(test_body) or helper_re.search(test_body)
        if imp:
            test_to_plugin[test_name] = imp.group(1)

    plugin_base_to_test: dict[str, list[str]] = {}
    for plugin_file in _all_plugin_files():
        tests_for_plugin = [
            tn for tn, pf in test_to_plugin.items() if pf == plugin_file
        ]
        plugin_base_to_test[plugin_file] = sorted(tests_for_plugin)

    return plugin_base_to_test


def _extract_test_body_lines(test_name: str) -> list[str]:
    """Extract the body lines of a test function (robust to docstring deindents)."""
    source = HOOK_RUNTIME.read_text()
    lines = source.split("\n")
    result: list[str] = []
    in_test = False
    for line in lines:
        if line.startswith(f"def {test_name}"):
            in_test = True
            continue
        if not in_test:
            continue
        # Stop at the next top-level def or `if __name__`
        if re.match(r"^(def test_|if __name__)", line):
            break
        result.append(line)
    return result


# ---- structural pin tests ----

def test_runtime_test_file_exists():
    """scripts/test_hook_runtime.py must exist."""
    assert HOOK_RUNTIME.is_file(), f"Missing: {HOOK_RUNTIME}"


def test_all_plugins_have_runtime_coverage():
    """Every enforcement plugin must have >=1 runtime test that invokes hooks."""
    all_plugins = _all_plugin_files()
    imported = _plugin_basenames_from_test_source()
    uncovered = all_plugins - imported

    assert not uncovered, (
        f"Plugins with NO runtime test coverage in {HOOK_RUNTIME.name}:\n"
        + "\n".join(f"  - {p}" for p in sorted(uncovered))
        + "\n\nExempted: " + ", ".join(sorted(EXEMPT))
    )


def test_exempt_plugins_not_in_source():
    """Exempted files (shared.ts, hot_reload.ts) should NOT claim to be plugins."""
    imported = _plugin_basenames_from_test_source()
    for exempt_name in EXEMPT:
        assert exempt_name not in imported, (
            f"{exempt_name} is exempt but appears in runtime test imports. "
            f"Either remove the import or remove it from EXEMPT."
        )


def test_runtime_tests_invoke_hooks():
    """Every test_* function that references a plugin must invoke real hooks (not just source analysis)."""
    plugin_map = _plugin_to_test_map()

    # Collect tests that are "source-only" (don't invoke hooks)
    source_only_tests: list[str] = []
    for plugin_file, test_names in plugin_map.items():
        if plugin_file in EXEMPT:
            continue
        for test_name in test_names:
            if not _test_calls_real_hooks(test_name):
                source_only_tests.append(f"{plugin_file}: {test_name}")

    assert not source_only_tests, (
        "Runtime tests must invoke actual plugin hooks (not just check source patterns).\n"
        "Tests that never call _run_ts() / hook functions:\n"
        + "\n".join(f"  - {t}" for t in source_only_tests)
    )


def test_enforce_commit_lock_has_runtime_test():
    """enforce-commit-lock.ts is a plugin and must have runtime coverage."""
    imported = _plugin_basenames_from_test_source()
    assert "enforce-commit-lock.ts" in imported, (
        "enforce-commit-lock.ts has NO runtime tests in scripts/test_hook_runtime.py. "
        "Add at least one test that loads the plugin and invokes a hook."
    )


def test_watchdog_has_runtime_test():
    """watchdog.ts is a plugin and must have runtime coverage."""
    imported = _plugin_basenames_from_test_source()
    assert "watchdog.ts" in imported, (
        "watchdog.ts has NO runtime tests in scripts/test_hook_runtime.py. "
        "Add at least one test that loads the plugin and invokes a hook."
    )


def test_plugin_count_matches_expected():
    """Sanity check: known plugin count matches expectations."""
    all_plugins = _all_plugin_files()
    # 14 enforcement plugins + watchdog.ts = 15 total (exempt: shared.ts, hot_reload.ts)
    assert len(all_plugins) >= 14, (
        f"Expected >=14 enforcement plugins, found {len(all_plugins)}:\n"
        + "\n".join(f"  - {p}" for p in sorted(all_plugins))
    )


def test_all_imported_plugins_are_real():
    """Every plugin imported in the runtime test must exist in the plugin dirs."""
    imported = _plugin_basenames_from_test_source()
    all_plugins = _all_plugin_files()
    stale = imported - all_plugins - EXEMPT
    assert not stale, (
        "Runtime test imports plugins that don't exist on disk:\n"
        + "\n".join(f"  - {p}" for p in sorted(stale))
    )
