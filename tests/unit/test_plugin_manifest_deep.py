"""Deep plugin manifest verification: registry completeness, hook validity,
hot-reload integrity, allowlist paths, import resolution, and duplicate detection.

Run: make test TESTFILE=tests/unit/test_plugin_manifest_deep.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"
LIB_DIR = ROOT / ".opencode" / "lib"
OPENCODE_JSON = ROOT / "opencode.json"

_CONFIG = json.loads(OPENCODE_JSON.read_text())
_REGISTERED = _CONFIG.get("plugin", [])
_REGISTERED_NAMES: set[str] = set()
_REGISTERED_PATHS: list[Path] = []
for entry in _REGISTERED:
    p = ROOT / entry
    _REGISTERED_NAMES.add(p.name)
    _REGISTERED_PATHS.append(p)

_TOP_LEVEL_PLUGIN_TS = sorted(PLUGIN_DIR.glob("*.ts")) if PLUGIN_DIR.is_dir() else []
_TOP_LEVEL_PLUGINS_TS = sorted(PLUGINS_DIR.glob("*.ts")) if PLUGINS_DIR.is_dir() else []

VALID_HOOKS = {
    "dispose",
    "event",
    "config",
    "tool",
    "auth",
    "provider",
    "chat.message",
    "chat.params",
    "chat.headers",
    "permission.ask",
    "command.execute.before",
    "tool.execute.before",
    "tool.execute.after",
    "shell.env",
    "experimental.chat.messages.transform",
    "experimental.chat.system.transform",
    "experimental.provider.small_model",
    "experimental.session.compacting",
    "experimental.compaction.autocontinue",
    "experimental.text.complete",
    "tool.definition",
}

HOOK_KEY_RE = re.compile(r"[\"']([a-z][a-z0-9_.]+)[\"']\s*:\s*(?:async\s*)?\(")

LOAD_HM_RE = re.compile(r'loadHotModule\(\s*["\']([\w-]+)["\']')

NAMED_CONST_RE = re.compile(
    r"^\s*export\s+(?:const|let|var)\s+",
    re.MULTILINE,
)

EXPORT_DEFAULT_RE = re.compile(r"export\s+default")

SUBAGENT_GUARD_RE = re.compile(
    r"OPENCODE_SUBAGENT|isSubagent\(",
)

ALLOWLIST_ARR_RE = re.compile(
    r"(?:ALLOWLIST_PATHS|ALLOWLIST_PATTERNS)\s*[:=]\s*(\[[\s\S]*?\])",
    re.MULTILINE,
)

HOT_MODULE_STUB = re.compile(r"loadHotModule\(.*defaultImpl\)")

NODE_AVAILABLE = subprocess.run(["node", "--version"], capture_output=True).returncode == 0


def _load_ts_file(path: Path) -> str:
    return path.read_text()


def _load_hot_reload_contract(path: Path) -> str:
    """Read a plugin's hot-reload implementation, following a thin facade."""
    content = _load_ts_file(path)
    if "defaultImpl" in content:
        return content
    match = re.search(
        r'import\s+\w+\s+from\s+["\'](\./impl/[A-Za-z0-9_-]+\.ts)["\']',
        content,
    )
    if match:
        implementation = (path.parent / match.group(1)).resolve()
        if implementation.is_file():
            return _load_ts_file(implementation)
    return content


def _compute_hotmodule_exclusion_zones(content: str) -> list[tuple[int, int]]:
    """Return byte ranges for HotModule blocks that should be excluded from
    duplicate-hook checking (they are fallback implementations, not registrations)."""
    zones: list[tuple[int, int]] = []
    for m in re.finditer(
        r"\b(?:const|let|var)\s+\w+\s*:\s*HotModule\s*=\s*\{",
        content,
    ):
        start = m.end() - 1
        depth = 1
        i = start + 1
        while i < len(content) and depth > 0:
            ch = content[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        zones.append((m.start(), i))
    return zones


def _extract_hook_keys(content: str) -> set[str]:
    keys: set[str] = set()
    zones = _compute_hotmodule_exclusion_zones(content)

    def _in_zone(pos: int) -> bool:
        return any(s <= pos <= e for s, e in zones)

    for m in HOOK_KEY_RE.finditer(content):
        key = m.group(1)
        if "." not in key and key not in ("dispose", "event", "config", "tool", "auth", "provider"):
            continue
        if _in_zone(m.start()):
            continue
        keys.add(key)
    return keys


# ---------------------------------------------------------------------------
# 1. Registry completeness
# ---------------------------------------------------------------------------


class TestRegistryCompleteness:
    def test_all_opencode_json_plugins_exist_on_disk(self) -> None:
        missing = [str(p) for p in _REGISTERED_PATHS if not p.is_file()]
        assert not missing, f"opencode.json references nonexistent files: {missing}"

    def test_no_orphan_ts_files_in_plugin_dir(self) -> None:
        orphans = [f.name for f in _TOP_LEVEL_PLUGIN_TS if f.name not in _REGISTERED_NAMES]
        assert not orphans, "Non-registered .ts files in .opencode/plugin/ (auto-loaded by opencode): " + ", ".join(
            orphans
        )

    def test_no_orphan_ts_files_in_plugins_dir(self) -> None:
        orphans = [f.name for f in _TOP_LEVEL_PLUGINS_TS if f.name not in _REGISTERED_NAMES]
        assert not orphans, "Non-registered .ts files in .opencode/plugins/ (auto-loaded by opencode): " + ", ".join(
            orphans
        )

    def test_registry_count_exceeds_threshold(self) -> None:
        assert len(_REGISTERED_PATHS) >= 20, f"Only {len(_REGISTERED_PATHS)} plugins registered; expected >= 20"


# ---------------------------------------------------------------------------
# 2. Export shape
# ---------------------------------------------------------------------------


class TestExportShape:
    @pytest.mark.parametrize("plugin_path", _REGISTERED_PATHS, ids=lambda p: p.name)
    def test_has_export_default(self, plugin_path: Path) -> None:
        content = _load_hot_reload_contract(plugin_path)
        assert EXPORT_DEFAULT_RE.search(content), f"{plugin_path.name}: missing 'export default'"

    @pytest.mark.parametrize("plugin_path", _REGISTERED_PATHS, ids=lambda p: p.name)
    def test_no_named_const_exports(self, plugin_path: Path) -> None:
        content = _load_hot_reload_contract(plugin_path)
        for match in NAMED_CONST_RE.finditer(content):
            line_no = content[: match.start()].count("\n") + 1
            line = content.split("\n")[line_no - 1].strip()[:80]
            rhs = line.split("=", 1)[-1].strip()
            if "=>" in rhs or "function" in rhs:
                continue
            pytest.fail(
                f"{plugin_path.name}:{line_no} named const export would crash "
                f"opencode's getLegacyPlugins() loader: {line}"
            )

    @pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available")
    def test_all_default_exports_are_functions(self) -> None:
        paths = [str(ROOT / e) for e in _REGISTERED]
        script = (
            "const plugins = __PLUGINS__;\n"
            "let failures = [];\n"
            "(async () => {\n"
            "  for (const p of plugins) {\n"
            "    try {\n"
            "      const mod = await import(p);\n"
            "      if (typeof mod.default !== 'function') {\n"
            "        failures.push(p + ': default is ' + typeof mod.default);\n"
            "      }\n"
            "    } catch(e) {\n"
            "      failures.push(p + ': IMPORT ERROR: ' + e.message);\n"
            "    }\n"
            "  }\n"
            "  console.log(JSON.stringify(failures));\n"
            "})();"
        ).replace("__PLUGINS__", json.dumps(paths))
        result = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=30,
            env={**os.environ, "NODE_NO_WARNINGS": "1", "OPENCODE_SUBAGENT": "1"},
        )
        output_lines = [line for line in result.stdout.strip().split("\n") if line.strip()]
        failures = json.loads(output_lines[-1]) if output_lines else []
        assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# 3. Hook name validity
# ---------------------------------------------------------------------------


class TestHookValidity:
    @pytest.mark.parametrize("plugin_path", _REGISTERED_PATHS, ids=lambda p: p.name)
    def test_all_hook_names_valid(self, plugin_path: Path) -> None:
        content = _load_ts_file(plugin_path)
        hooks = _extract_hook_keys(content)
        invalid = hooks - VALID_HOOKS
        if invalid:
            invalid_str = ", ".join(sorted(invalid))
            pytest.fail(f"{plugin_path.name}: invalid hook names: {invalid_str}. Valid hooks: {sorted(VALID_HOOKS)}")

    def test_no_duplicate_hooks_within_single_plugin(self) -> None:
        violations: list[str] = []
        for plugin_path in _REGISTERED_PATHS:
            content = _load_hot_reload_contract(plugin_path)
            zones = _compute_hotmodule_exclusion_zones(content)
            seen: dict[str, list[int]] = {}

            for m in HOOK_KEY_RE.finditer(content):
                key = m.group(1)
                if key not in VALID_HOOKS:
                    continue
                # Skip matches inside HotModule fallback blocks.
                if any(s <= m.start() <= e for s, e in zones):
                    continue
                line_no = content[: m.start()].count("\n") + 1
                seen.setdefault(key, []).append(line_no)

            for hook, lines in seen.items():
                if len(lines) > 1:
                    violations.append(f"{plugin_path.name}: hook '{hook}' defined {len(lines)} times at lines {lines}")

        assert not violations, "\n".join(violations)


# ---------------------------------------------------------------------------
# 4. Hot-reload integrity
# ---------------------------------------------------------------------------

# Plugins that handle hot-reload differently -- they use the impl/ submodule
# pattern (enforce-make.ts, enforce-stop.ts) or a custom registration approach
# (enforce-commit-lock.ts) or are simple (watchdog.ts).
_HOT_RELOAD_EXEMPT_PLUGINS = frozenset(
    {
        "enforce-make.ts",
        "enforce-stop.ts",
        "enforce-commit-lock.ts",
        "watchdog.ts",
    }
)


def _hot_module_names_per_plugin() -> dict[str, set[str]]:
    """Return {plugin_name: {hot_module_name, ...}} -- unique per plugin."""
    result: dict[str, set[str]] = {}
    for plugin_path in _REGISTERED_PATHS:
        content = _load_hot_reload_contract(plugin_path)
        names: set[str] = set()
        for m in LOAD_HM_RE.finditer(content):
            names.add(m.group(1))
        if names:
            result[plugin_path.name] = names
    return result


def _hot_module_names_global() -> dict[str, set[str]]:
    """Return {hot_module_name: {plugin_name, ...}} -- unique hot-mod names."""
    result: dict[str, set[str]] = {}
    for plugin_path in _REGISTERED_PATHS:
        content = _load_hot_reload_contract(plugin_path)
        for m in LOAD_HM_RE.finditer(content):
            name = m.group(1)
            result.setdefault(name, set()).add(plugin_path.name)
    return result


class TestHotReloadIntegrity:
    def test_all_hot_module_names_unique_across_plugins(self) -> None:
        global_names = _hot_module_names_global()
        dupes = {k: v for k, v in global_names.items() if len(v) > 1}
        assert not dupes, "Hot module names must be unique across plugins. Duplicates: " + "; ".join(
            f"'{k}' used by {sorted(v)}" for k, v in dupes.items()
        )

    def test_all_registered_plugins_use_hot_reload_or_are_exempt(self) -> None:
        no_proxy: list[str] = []
        for plugin_path in _REGISTERED_PATHS:
            if plugin_path.name in _HOT_RELOAD_EXEMPT_PLUGINS:
                continue
            content = _load_hot_reload_contract(plugin_path)
            if not HOT_MODULE_STUB.search(content):
                no_proxy.append(plugin_path.name)
        assert not no_proxy, "Plugins without hot-reload proxy (loadHotModule(..defaultImpl)): " + ", ".join(no_proxy)

    def test_hot_reload_modules_build_from_a_clean_state(self, tmp_path: Path) -> None:
        """Every registered hot module must build without relying on global /tmp."""
        global_names = _hot_module_names_global()
        prefix = tmp_path / "gludd-hot-"
        env = {**os.environ, "GLUDD_HOT_MODULE_PREFIX": str(prefix)}
        result = subprocess.run(
            ["node", "scripts/build_hot_modules.js"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        missing = [
            name
            for name in sorted(global_names)
            if not (tmp_path / f"gludd-hot-{name}.js").is_file()
        ]
        assert not missing, f"Hot-reload build omitted registered modules: {missing}"


# ---------------------------------------------------------------------------
# 5. Allowlist path validation
# ---------------------------------------------------------------------------


class TestAllowlistPaths:
    def test_enforce_no_suppressions_allowlist_paths_exist(self) -> None:
        path = PLUGIN_DIR / "enforce-no-suppressions.ts"
        content = _load_ts_file(path)
        m = re.search(r"ALLOWLIST_PATHS\s*=\s*\[([^\]]*)\]", content, re.DOTALL)
        assert m is not None, "Could not find ALLOWLIST_PATHS in enforce-no-suppressions.ts"
        raw = m.group(1)
        entries = re.findall(r'"([^"]+)"', raw)
        missing = [e for e in entries if not (ROOT / e).is_file()]
        assert not missing, f"ALLOWLIST_PATHS in enforce-no-suppressions.ts reference missing files: {missing}"

    def test_enforce_test_integrity_allowlist_paths_exist(self) -> None:
        path = PLUGIN_DIR / "enforce-test-integrity.ts"
        content = _load_ts_file(path)
        m = re.search(r"ALLOWLIST_PATHS\s*=\s*Object\.freeze\(\[([^\]]*)\]\)", content, re.DOTALL)
        assert m is not None, "Could not find ALLOWLIST_PATHS in enforce-test-integrity.ts"
        raw = m.group(1)
        entries = re.findall(r'"([^"]+)"', raw)
        missing = [e for e in entries if not (ROOT / e).is_file()]
        assert not missing, f"ALLOWLIST_PATHS in enforce-test-integrity.ts reference missing files: {missing}"


# ---------------------------------------------------------------------------
# 6. Subagent guard presence
# ---------------------------------------------------------------------------

# Watchdog uses the `event` hook rather than `tool.execute.before`, so it doesn't
# need a subagent guard. Depth is the sole tool-hook exception: it must observe
# delegated dispatches to enforce the recursion boundary. Dedicated depth and
# framework-contract tests pin that exception to dispatch tools only.
_SUBAGENT_GUARD_EXEMPT = frozenset({"enforce-depth.ts", "watchdog.ts"})


class TestSubagentGuard:
    @pytest.mark.parametrize("plugin_path", _REGISTERED_PATHS, ids=lambda p: p.name)
    def test_has_subagent_guard(self, plugin_path: Path) -> None:
        if plugin_path.name in _SUBAGENT_GUARD_EXEMPT:
            pytest.skip(f"{plugin_path.name} exempt: uses event hook, not tool.execute.before")
        content = _load_ts_file(plugin_path)
        assert SUBAGENT_GUARD_RE.search(content), (
            f"{plugin_path.name}: missing subagent guard (OPENCODE_SUBAGENT or isSubagent())"
        )


# ---------------------------------------------------------------------------
# 7. Import resolution (Node)
# ---------------------------------------------------------------------------


class TestImportResolution:
    @pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available")
    @pytest.mark.parametrize("plugin_path", _REGISTERED_PATHS, ids=lambda p: p.name)
    def test_each_plugin_imports_cleanly(self, plugin_path: Path) -> None:
        script = (
            'import("__PATH__")\n'
            '  .then(m => console.log("OK " + (typeof m.default)))\n'
            '  .catch(e => console.log("FAIL " + e.message));'
        ).replace("__PATH__", str(plugin_path))
        result = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=15,
            env={**os.environ, "NODE_NO_WARNINGS": "1", "OPENCODE_SUBAGENT": "1"},
        )
        assert result.stdout.strip().startswith("OK "), (
            f"{plugin_path.name}: import failed: {result.stdout.strip()}\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# 8. Watchdog plugin
# ---------------------------------------------------------------------------


class TestWatchdogPlugin:
    def test_watchdog_is_registered(self) -> None:
        assert "watchdog.ts" in _REGISTERED_NAMES, "watchdog.ts not in opencode.json plugin array"

    def test_watchdog_has_export_default(self) -> None:
        content = _load_ts_file(PLUGINS_DIR / "watchdog.ts")
        assert EXPORT_DEFAULT_RE.search(content), "watchdog.ts: missing 'export default'"

    def test_watchdog_registers_event_hook(self) -> None:
        content = _load_ts_file(PLUGINS_DIR / "watchdog.ts")
        assert '"event"' in content, "watchdog.ts: missing 'event' hook"


# ---------------------------------------------------------------------------
# 9. impl/ directory integrity
# ---------------------------------------------------------------------------


class TestImplDirectory:
    def test_impl_files_not_in_opencode_json(self) -> None:
        impl_dir = PLUGIN_DIR / "impl"
        if not impl_dir.is_dir():
            pytest.skip("impl/ directory not present")
        registered_impl = [p for p in _REGISTERED if "impl/" in p]
        assert not registered_impl, (
            f"impl/ files must not be in opencode.json (loaded as submodules): {registered_impl}"
        )

    def test_impl_files_exist(self) -> None:
        impl_dir = PLUGIN_DIR / "impl"
        assert impl_dir.is_dir(), "impl/ directory missing"
        expected = {"enforce_make_impl.ts", "enforce_stop_impl.ts"}
        actual = {f.name for f in impl_dir.glob("*.ts")}
        missing = expected - actual
        assert not missing, f"Expected impl files missing: {missing}"


# ---------------------------------------------------------------------------
# 10. Library reference integrity
# ---------------------------------------------------------------------------


class TestLibraryReferences:
    def test_hot_reload_ts_referenced_by_plugins(self) -> None:
        refs: list[str] = []
        for plugin_path in _REGISTERED_PATHS:
            content = _load_ts_file(plugin_path)
            if "hot_reload.ts" in content:
                refs.append(plugin_path.name)
        assert len(refs) > 0, "No plugins reference hot_reload.ts"

    def test_shared_ts_referenced_by_plugins(self) -> None:
        refs: list[str] = []
        for plugin_path in _REGISTERED_PATHS:
            content = _load_ts_file(plugin_path)
            if "shared.ts" in content:
                refs.append(plugin_path.name)
        assert len(refs) > 0, "No plugins reference shared.ts"

    def test_plugin_test_exports_not_in_plugin_dir(self) -> None:
        assert (LIB_DIR / "plugin_test_exports.ts").is_file(), (
            "plugin_test_exports.ts must be in .opencode/lib/, NOT .opencode/plugin/"
        )
        assert not (PLUGIN_DIR / "plugin_test_exports.ts").exists(), (
            "plugin_test_exports.ts found in .opencode/plugin/ -- move it to .opencode/lib/"
        )
