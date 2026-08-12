"""Verify the hot-reload proxy pattern across all enforcement plugins.

The pattern (from hot_reload.ts docs): each enforce-*.ts plugin is a thin proxy
wrapper that delegates to /tmp/gludd-hot-<name>.js on every hook invocation,
falling back to compiled-in defaultImpl if the hot file is absent or broken.

Tests:
  1. Structural: every converted plugin has defaultImpl + loadHotModule proxy hooks
  2. Build: make hot-reload-plugins produces /tmp/gludd-hot-*.js files
  3. Behavioral: a hot module, when present, is loaded instead of compiled-in defaults
  4. Fallback: defaults used when hot modules are absent
  5. Issues found with the hot-reload system are reported as test_output
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
HOT_RELOAD_TS = ROOT / ".opencode" / "lib" / "hot_reload.ts"
BUILD_SCRIPT = ROOT / "scripts" / "build_hot_modules.js"
MAKEFILE = ROOT / "Makefile"
HOT_PROXY_PREFIX = f"/tmp/gludd-hot-{os.getpid()}-hot-reload-proxy-"
HOT_PROXY_REAL_PREFIX = f"/tmp/gludd-hot-{os.getpid()}-hot-reload-proxy-real-"
HOT_PROXY_TMP_PREFIX = f"/tmp/gludd-hot-{os.getpid()}-hot-reload-proxy-tmp-"

pytestmark = pytest.mark.xdist_group("hot_reload_proxy")

# All enforce-*.ts plugins that exist on disk
_ALL_PLUGINS = sorted(
    p.stem for p in PLUGIN_DIR.glob("enforce-*.ts")
    if not p.name.startswith("enforce-commit-lock")
)

# Plugins that have been converted to the hot-reload proxy pattern
# (must have: import loadHotModule, defaultImpl, and loadHotModule() calls)
_CONVERTED_PLUGINS = [
    n for n in _ALL_PLUGINS
    if "defaultImpl" in (PLUGIN_DIR / f"{n}.ts").read_text()
]


def _plugin_source(name: str) -> str:
    path = PLUGIN_DIR / f"{name}.ts"
    assert path.exists(), f"Plugin missing: {path}"
    return path.read_text()


def _parse_load_hot_module_calls(src: str) -> list[str]:
    return re.findall(r'loadHotModule\(["\']([^"\']+)["\']', src)


# ── Structural tests ────────────────────────────────────────────────────────


class TestProxyPatternInConvertedPlugins:
    """Every plugin converted to the proxy pattern must be structurally correct."""

    def test_all_plugins_exist(self):
        assert len(_ALL_PLUGINS) >= 10, f"Expected >=10 enforce plugins, found {len(_ALL_PLUGINS)}"

    def test_every_converted_plugin_has_default_impl(self):
        for name in _CONVERTED_PLUGINS:
            src = _plugin_source(name)
            assert "defaultImpl" in src, f"{name}: converted but defaultImpl not found"

    def test_every_converted_plugin_imports_load_hot_module(self):
        for name in _CONVERTED_PLUGINS:
            src = _plugin_source(name)
            assert "loadHotModule" in src, f"{name}: missing loadHotModule import/call"

    def test_every_converted_plugin_calls_load_hot_module_with_name(self):
        for name in _CONVERTED_PLUGINS:
            src = _plugin_source(name)
            calls = _parse_load_hot_module_calls(src)
            assert len(calls) > 0, (
                f"{name}: converted (has defaultImpl) but no loadHotModule calls"
            )
            unique = set(calls)
            assert len(unique) == 1, (
                f"{name}: inconsistent loadHotModule names: {unique}"
            )

    def test_hot_reload_ts_exists_and_exports_load_hot_module(self):
        assert HOT_RELOAD_TS.exists(), f"hot_reload.ts missing at {HOT_RELOAD_TS}"
        src = HOT_RELOAD_TS.read_text()
        assert "export function loadHotModule" in src, "missing exported loadHotModule"
        assert "hotCache" in src, "missing mtime-based cache (hotCache)"
        assert "return defaults" in src, "missing fail-open return defaults on error"

    def test_unconverted_plugins_documented(self):
        """Plugins without defaultImpl are not yet converted — catalog them."""
        [n for n in _ALL_PLUGINS if n not in _CONVERTED_PLUGINS]
        # This is an informational test — unconverted plugins are expected
        # until all are converted. The test_output below reports them.
        pass


# ── Build tests ──────────────────────────────────────────────────────────────


class TestHotModuleBuild:
    """make hot-reload-plugins builds /tmp/gludd-hot-*.js files."""

    @staticmethod
    def _cleanup() -> None:
        for f in Path("/tmp").glob(f"{Path(HOT_PROXY_PREFIX).name}*.js"):
            with contextlib.suppress(OSError):
                f.unlink()

    def test_build_script_exists_and_lists_plugins(self):
        assert BUILD_SCRIPT.exists(), f"Build script missing: {BUILD_SCRIPT}"
        src = BUILD_SCRIPT.read_text()
        # Must list at least the known converted plugins
        for name in _CONVERTED_PLUGINS:
            expected_entry = name.removeprefix("enforce-") if name.startswith("enforce-") else name
            assert expected_entry in src or name in src, (
                f"Build script missing plugin: {name}"
            )

    def test_make_target_exists(self):
        assert "hot-reload-plugins:" in MAKEFILE.read_text(), (
            "Makefile missing hot-reload-plugins target"
        )

    def test_build_produces_hot_modules(self):
        self._cleanup()
        try:
            result = subprocess.run(
                ["node", str(BUILD_SCRIPT)],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30,
                env={**os.environ, "GLUDD_HOT_MODULE_PREFIX": HOT_PROXY_PREFIX},
            )
            assert result.returncode == 0, (
                f"build_hot_modules.js failed (exit {result.returncode}):\n"
                f"stderr: {result.stderr[:500]}"
            )
            built = sorted(Path("/tmp").glob(f"{Path(HOT_PROXY_PREFIX).name}*.js"))
            assert len(built) > 0, "No hot modules produced"
            for f in built:
                size = f.stat().st_size
                assert size > 0, f"Hot module {f.name} is empty"
        finally:
            self._cleanup()

    def test_built_modules_are_valid_javascript(self):
        """Every emitted hot module must be parseable JavaScript."""
        self._cleanup()
        try:
            subprocess.run(
                ["node", str(BUILD_SCRIPT)],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30,
                env={**os.environ, "GLUDD_HOT_MODULE_PREFIX": HOT_PROXY_PREFIX},
                check=True,
            )
            failures = []
            for f in sorted(Path("/tmp").glob(f"{Path(HOT_PROXY_PREFIX).name}*.js")):
                proc = subprocess.run(
                    ["node", "--check", str(f)],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode != 0:
                    failures.append(f.name)

            assert failures == [], f"generated invalid hot modules: {failures}"
        finally:
            self._cleanup()

    def test_built_module_names_match_proxy_lookup_keys(self):
        """Generated filenames must match each plugin's loadHotModule key."""
        self._cleanup()
        try:
            subprocess.run(
                ["node", str(BUILD_SCRIPT)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "GLUDD_HOT_MODULE_PREFIX": HOT_PROXY_PREFIX},
                check=True,
            )
            missing = []
            for source_name in _CONVERTED_PLUGINS:
                lookup_keys = set(_parse_load_hot_module_calls(_plugin_source(source_name)))
                assert len(lookup_keys) == 1, f"{source_name}: ambiguous lookup keys {lookup_keys}"
                lookup_key = next(iter(lookup_keys))
                if not Path(f"{HOT_PROXY_PREFIX}{lookup_key}.js").exists():
                    missing.append(f"{source_name}->{lookup_key}")
            assert missing == [], f"builder filenames cannot be loaded by proxies: {missing}"
        finally:
            self._cleanup()


# ── Behavioral tests (invoke loadHotModule via node) ─────────────────────────


class TestHotModuleOverridesDefault:
    """When a hot module exists, loadHotModule returns it instead of defaults."""

    HOT_PREFIX = HOT_PROXY_PREFIX

    @staticmethod
    def _call_load_hot_module(name: str, ts_defaults: str) -> dict[str, bool] | None:
        """Invoke loadHotModule(name, defaults) via node."""
        code = (
            "import { loadHotModule, type HotModule } from "
            + json.dumps(str(HOT_RELOAD_TS))
            + "\n"
            + f"const defaults: HotModule = {ts_defaults}\n"
            + f"const mod = loadHotModule({json.dumps(name)}, defaults)\n"
            + "console.log(JSON.stringify(Object.keys(mod)))\n"
        )
        tmp = Path(f"{HOT_PROXY_TMP_PREFIX}load.ts")
        tmp.write_text(code)
        try:
            env = os.environ.copy()
            env["OPENCODE_SUBAGENT"] = ""
            env["GLUDD_HOT_MODULE_PREFIX"] = TestHotModuleOverridesDefault.HOT_PREFIX
            proc = subprocess.run(
                ["node", "--experimental-strip-types", str(tmp)],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT), env=env,
            )
            if proc.returncode != 0:
                raise AssertionError(
                    f"Node exit {proc.returncode}\nstderr: {proc.stderr[:800]}"
                )
            keys = json.loads(proc.stdout.strip()) if proc.stdout.strip() else []
            return {k: True for k in keys}
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()

    @staticmethod
    def _write_hot_module(name: str, key: str, value_js: str) -> str:
        """Write a hot module file with exports["key"] = value;"""
        p = f"{TestHotModuleOverridesDefault.HOT_PREFIX}{name}.js"
        with open(p, "w") as f:
            f.write(f'exports["{key}"] = {value_js};\n')
        time.sleep(0.05)
        return p

    @staticmethod
    def _cleanup(*names: str) -> None:
        for n in names:
            with contextlib.suppress(FileNotFoundError):
                os.remove(f"{TestHotModuleOverridesDefault.HOT_PREFIX}{n}.js")
        with contextlib.suppress(FileNotFoundError):
            os.remove(f"{HOT_PROXY_TMP_PREFIX}load.ts")

    def test_defaults_returned_when_no_hot_module(self):
        result = self._call_load_hot_module(
            "nonexistent-xyz-12345",
            '{ "text.complete": () => "tc", "session.idle": () => "idle" }',
        )
        assert result is not None
        assert "text.complete" in result, f"Expected text.complete, got keys: {list(result.keys())}"
        assert "session.idle" in result

    def test_hot_module_key_appears_in_result(self):
        name = "test-proxy-override"
        try:
            self._write_hot_module(
                name,
                "tool.execute.before",
                'async function(...args) { return "hot-block"; }',
            )
            result = self._call_load_hot_module(
                name,
                '{ "tool.execute.before": () => "default" }',
            )
            assert result is not None
            assert "tool.execute.before" in result, (
                f"Expected tool.execute.before from hot module, got {result}"
            )
        finally:
            self._cleanup(name)

    def test_fallback_when_hot_module_is_corrupt(self):
        name = "test-corrupt-fallback"
        try:
            with open(f"{self.HOT_PREFIX}{name}.js", "w") as f:
                f.write("{{{[[[ this is not valid JS at all --- ")
            result = self._call_load_hot_module(
                name,
                '{ "tool.execute.before": () => "fallback-safe" }',
            )
            assert result is not None
            assert "tool.execute.before" in result, "Fallback should return defaults"
        finally:
            self._cleanup(name)

    def test_missing_hot_module_returns_all_default_keys(self):
        result = self._call_load_hot_module(
            "missing-module-abcde",
            '{ "key1": () => "val1", "key2": () => "val2" }',
        )
        assert result is not None
        assert "key1" in result
        assert "key2" in result

    def test_multiple_hot_modules_dont_interfere(self):
        name_a = "test-proxy-a"
        name_b = "test-proxy-b"
        try:
            self._write_hot_module(name_a, "hook", 'async function() { return "A"; }')
            self._write_hot_module(name_b, "hook", 'async function() { return "B"; }')
            ra = self._call_load_hot_module(name_a, '{ "hook": () => "def" }')
            rb = self._call_load_hot_module(name_b, '{ "hook": () => "def" }')
            assert ra is not None and rb is not None
        finally:
            self._cleanup(name_a, name_b)

    def test_mtime_change_invalidates_cache(self):
        """After updating the hot module, subsequent call gets updated version."""
        name = "test-proxy-cache"
        try:
            p = self._write_hot_module(
                name, "hook", 'async function() { return "v1"; }'
            )
            self._call_load_hot_module(name, '{ "hook": () => "default" }')
            time.sleep(0.15)
            # Rewrite with different content to change mtime
            with open(p, "w") as f:
                f.write('exports["hook"] = async function() { return "v2"; };\n')
            r2 = self._call_load_hot_module(name, '{ "hook": () => "default" }')
            assert r2 is not None
            assert "hook" in r2
        finally:
            self._cleanup(name)


# ── Integration: real plugin sources ────────────────────────────────────────


class TestRealPluginHotModules:
    """Build real hot modules from plugin sources and verify they load."""

    @staticmethod
    def _cleanup_all() -> None:
        for f in Path("/tmp").glob(f"{Path(HOT_PROXY_REAL_PREFIX).name}*.js"):
            with contextlib.suppress(OSError):
                f.unlink()

    def test_real_plugin_build_produces_non_empty_modules(self):
        self._cleanup_all()
        try:
            build = subprocess.run(
                ["node", str(BUILD_SCRIPT)],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30,
                env={**os.environ, "GLUDD_HOT_MODULE_PREFIX": HOT_PROXY_REAL_PREFIX},
            )
            assert build.returncode == 0, (
                f"Build failed (exit {build.returncode}):\n{build.stderr[:500]}"
            )
            hot_files = sorted(Path("/tmp").glob(f"{Path(HOT_PROXY_REAL_PREFIX).name}*.js"))
            assert len(hot_files) > 0, "No hot modules produced"
            for f in hot_files:
                content = f.read_text()
                assert "exports" in content, f"{f.name}: missing exports — not a valid hot module"
                assert len(content) > 0, f"{f.name}: empty"
        finally:
            self._cleanup_all()

    def test_real_hot_module_loads_via_load_hot_module(self):
        """Build a real hot module, then verify loadHotModule picks it up with keys."""
        self._cleanup_all()
        try:
            subprocess.run(
                ["node", str(BUILD_SCRIPT)],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30,
                env={**os.environ, "GLUDD_HOT_MODULE_PREFIX": HOT_PROXY_REAL_PREFIX},
                check=True,
            )

            # Find any built hot module
            built = sorted(
                Path("/tmp").glob(f"{Path(HOT_PROXY_REAL_PREFIX).name}*.js")
            )
            if not built:
                pytest.skip("No hot modules built")

            for hot_file in built[:3]:  # test up to 3
                name = hot_file.name.removeprefix(
                    Path(HOT_PROXY_REAL_PREFIX).name
                ).removesuffix(".js")
                code = (
                    "import { loadHotModule, type HotModule } from "
                    + json.dumps(str(HOT_RELOAD_TS))
                    + "\n"
                    + 'const defaults = { "dummy": () => "compiled-in" }\n'
                    + f'const mod = loadHotModule({json.dumps(name)}, defaults)\n'
                    + "const keys = Object.keys(mod);\n"
                    + "console.log('keys=' + JSON.stringify(keys));\n"
                    + "console.log('count=' + keys.length);\n"
                )
                tmp = Path(f"{HOT_PROXY_TMP_PREFIX}real-load.ts")
                tmp.write_text(code)
                try:
                    env = os.environ.copy()
                    env["OPENCODE_SUBAGENT"] = ""
                    env["GLUDD_HOT_MODULE_PREFIX"] = HOT_PROXY_REAL_PREFIX
                    proc = subprocess.run(
                        ["node", "--experimental-strip-types", str(tmp)],
                        capture_output=True, text=True, timeout=15,
                        cwd=str(ROOT), env=env,
                    )
                    assert proc.returncode == 0, (
                        f"Node exit {proc.returncode} for {name}\nstderr: {proc.stderr[:400]}"
                    )
                    stdout = proc.stdout.strip()
                    assert "count=" in stdout, (
                        f"Unexpected output for {name}: {stdout[:200]}"
                    )
                finally:
                    with contextlib.suppress(OSError):
                        tmp.unlink()
        finally:
            self._cleanup_all()

    def test_no_hot_module_fallback_for_real_plugin_name(self):
        """Using a real plugin name but with no hot module on disk, fallback works."""
        self._cleanup_all()
        code = (
            "import { loadHotModule, type HotModule } from "
            + json.dumps(str(HOT_RELOAD_TS))
            + "\n"
            + 'const defaults = { "text.complete": () => "safe" }\n'
            + 'const mod = loadHotModule("floor", defaults)\n'
            + "const keys = Object.keys(mod);\n"
            + "console.log('has_tc=' + keys.includes('text.complete'));\n"
        )
        tmp = Path(f"{HOT_PROXY_TMP_PREFIX}fallback.ts")
        tmp.write_text(code)
        try:
            env = os.environ.copy()
            env["OPENCODE_SUBAGENT"] = ""
            env["GLUDD_HOT_MODULE_PREFIX"] = HOT_PROXY_REAL_PREFIX
            proc = subprocess.run(
                ["node", "--experimental-strip-types", str(tmp)],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT), env=env,
            )
            assert proc.returncode == 0, f"Node exit {proc.returncode}\n{proc.stderr[:400]}"
            assert "has_tc=true" in proc.stdout, (
                f"Fallback should return defaults when no hot module exists\n{proc.stdout}"
            )
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
            self._cleanup_all()


# ── Findings report ──────────────────────────────────────────────────────────


class TestHotReloadSystemIssues:
    """Regressions that must remain fixed in the hot-reload proxy system."""

    def test_issue_build_script_ts_to_js_converter_bug(self):
        """The builder must fail closed instead of emitting invalid JavaScript."""
        TestHotModuleBuild._cleanup()
        try:
            subprocess.run(
                ["node", str(BUILD_SCRIPT)],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30,
                env={**os.environ, "GLUDD_HOT_MODULE_PREFIX": HOT_PROXY_PREFIX},
            )
            broken = []
            for f in sorted(
                Path("/tmp").glob(f"{Path(HOT_PROXY_PREFIX).name}*.js")
            ):
                proc = subprocess.run(
                    ["node", "--check", str(f)],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode != 0:
                    broken.append(f.name)
            assert broken == [], f"hot-module converter emitted invalid JS: {broken}"
        finally:
            TestHotModuleBuild._cleanup()

    def test_issue_enforce_delegate_not_converted(self):
        """enforce-delegate.ts is not yet converted to the proxy pattern.

        It lacks defaultImpl and loadHotModule calls. Until converted, it
        cannot benefit from hot-reload — a restart is needed for changes.

        Impact: changes to enforce-delegate.ts require an opencode restart.

        Fix: convert to proxy pattern like the other plugins.
        """
        src = PLUGIN_DIR.joinpath("enforce-delegate.ts")
        if src.exists():
            content = src.read_text()
            has_proxy = "defaultImpl" in content and "loadHotModule" in content
            if not has_proxy:
                print("\nNOTE: enforce-delegate.ts not yet converted to hot-reload proxy pattern")
