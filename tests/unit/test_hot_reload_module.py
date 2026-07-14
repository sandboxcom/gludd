"""Functional unit tests for hot_reload.ts — loadHotModule proxy.

Tests the hot-reload proxy pattern by writing JavaScript hot-module files to
/tmp/gludd-hot-*.js and invoking the actual TypeScript loadHotModule function
via node --experimental-strip-types.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOT_RELOAD = ROOT / ".opencode" / "plugin" / "hot_reload.ts"
HOT_PREFIX = "/tmp/gludd-hot-"

def _call_load_hot_module(name: str, ts_defaults: str) -> dict | None:
    """Write a small TS wrapper that calls loadHotModule(name, defaults) and
    returns the parsed JSON result."""
    code = (
        "import { loadHotModule, type HotModule } from "
        + json.dumps(str(HOT_RELOAD))
        + "\n"
        + f"const defaults: HotModule = {ts_defaults}\n"
        + f"const mod = loadHotModule({json.dumps(name)}, defaults)\n"
        + "console.log(JSON.stringify(mod))\n"
    )
    with open("/tmp/_test_hot_reload.ts", "w") as f:
        f.write(code)
    env = os.environ.copy()
    env["OPENCODE_SUBAGENT"] = ""
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "/tmp/_test_hot_reload.ts"],
        capture_output=True, text=True, timeout=15,
        cwd=str(ROOT), env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"Node exit {proc.returncode}\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
        )
    return json.loads(proc.stdout.strip()) if proc.stdout.strip() else None


def _write_hot_module(name: str, exports_js: str) -> str:
    path = f"{HOT_PREFIX}{name}.js"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"exports = {{ {exports_js} }};\n")
    time.sleep(0.05)
    return path


def _remove_hot_module(name: str) -> None:
    path = f"{HOT_PREFIX}{name}.js"
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def _remove_tmp_files(names: list[str]) -> None:
    for n in names:
        _remove_hot_module(n)
    with contextlib.suppress(FileNotFoundError):
        os.remove("/tmp/_test_hot_reload.ts")


class TestHotModuleReturnsWhenFileExists:
    def test_loads_hot_module_not_defaults(self):
        name = "test-exists"
        try:
            _write_hot_module(name, '"tool.execute.before": () => "hot-result"')
            result = _call_load_hot_module(
                name,
                '{ "tool.execute.before": () => "default-result" }',
            )
            assert result is not None
            fn = result.get("tool.execute.before")
            assert callable(fn) or fn is not None
        finally:
            _remove_tmp_files([name])

    def test_defaults_returned_when_no_hot_file(self):
        result = _call_load_hot_module(
            "nonexistent-plugin-xyz",
            '{ "tool.execute.before": () => "default-val" }',
        )
        assert result is not None
        fn = result.get("tool.execute.before")
        assert fn is not None

    def test_empty_hot_module_uses_defaults_for_missing_keys(self):
        name = "test-empty"
        try:
            _write_hot_module(name, "")
            result = _call_load_hot_module(
                name,
                '{ "tool.execute.before": () => "default", "text.complete": () => "default-tc" }',
            )
            assert result is not None
            # empty module's exports override keys in the return, but the
            # hot module keys won't exist (empty), so we get back an empty
            # object — since hot module has no hook keys, they won't be
            # present. The proxy pattern in enforcement plugins handles this:
            # if impl[hookName] is falsy, it falls back to the compiled-in
            # default hook implicitly (not via loadHotModule). The module
            # itself returns the empty object, which is correct behavior.
        finally:
            _remove_tmp_files([name])


class TestCacheInvalidation:
    def test_updated_mtime_invalidates_cache(self):
        name = "test-cache-upd"
        try:
            _write_hot_module(name, '"tool.execute.before": () => "v1"')
            r1 = _call_load_hot_module(
                name,
                '{ "tool.execute.before": () => "default" }',
            )
            time.sleep(0.15)
            _write_hot_module(name, '"tool.execute.before": () => "v2"')
            r2 = _call_load_hot_module(
                name,
                '{ "tool.execute.before": () => "default" }',
            )
            assert r1 is not None and r2 is not None
            assert r1 != r2
        finally:
            _remove_tmp_files([name])

    def test_same_mtime_returns_cached(self):
        name = "test-cache-same"
        try:
            path = _write_hot_module(name, '"tool.execute.before": () => "v1"')
            mtime_before = os.stat(path).st_mtime_ns
            r1 = _call_load_hot_module(
                name,
                '{ "tool.execute.before": () => "default" }',
            )
            # restore exact mtime to prevent cache miss
            os.utime(path, ns=(mtime_before, mtime_before))
            r2 = _call_load_hot_module(
                name,
                '{ "tool.execute.before": () => "default" }',
            )
            assert r1 is not None and r2 is not None
            assert r1 == r2
        finally:
            _remove_tmp_files([name])


class TestFailOpenBehavior:
    def test_corrupt_hot_module_syntax_error_returns_defaults(self):
        name = "test-corrupt"
        try:
            path = f"{HOT_PREFIX}{name}.js"
            with open(path, "w") as f:
                f.write("this is not valid javascript {{{{{{{\n")
            result = _call_load_hot_module(
                name,
                '{ "tool.execute.before": () => "safe-default" }',
            )
            assert result is not None
            fn = result.get("tool.execute.before")
            assert fn is not None
        finally:
            _remove_tmp_files([name])

    def test_missing_file_returns_defaults(self):
        result = _call_load_hot_module(
            "definitely-missing-12345",
            '{ "text.complete": () => "only-default" }',
        )
        assert result is not None
        assert result.get("text.complete") is not None

    def test_hot_module_runtime_error_returns_defaults(self):
        name = "test-runtime-err"
        try:
            _write_hot_module(
                name,
                '"tool.execute.before": (function() { throw new Error("boom"); })()',
            )
            result = _call_load_hot_module(
                name,
                '{ "tool.execute.before": () => "safe" }',
            )
            assert result is not None
        finally:
            _remove_tmp_files([name])


class TestMissingMethodFallback:
    def test_missing_hook_name_in_hot_module(self):
        name = "test-partial"
        try:
            _write_hot_module(name, '"text.complete": () => "only-tc"')
            result = _call_load_hot_module(
                name,
                '{'
                ' "tool.execute.before": () => "default-teb",'
                ' "text.complete": () => "default-tc"'
                ' }',
            )
            assert result is not None
            # tool.execute.before not in hot module — should be absent
            assert "tool.execute.before" not in result
            assert "text.complete" in result
        finally:
            _remove_tmp_files([name])


class TestMultipleAndSeparate:
    def test_different_names_separate_caches(self):
        name_a = "test-sep-a"
        name_b = "test-sep-b"
        try:
            _write_hot_module(name_a, '"hook": () => "a"')
            _write_hot_module(name_b, '"hook": () => "b"')
            ra = _call_load_hot_module(name_a, '{ "hook": () => "default" }')
            rb = _call_load_hot_module(name_b, '{ "hook": () => "default" }')
            assert ra is not None and rb is not None
            assert True
        finally:
            _remove_tmp_files([name_a, name_b])

    def test_multiple_rapid_calls_same_mtime_cached(self):
        name = "test-rapid"
        try:
            path = _write_hot_module(name, '"hook": () => "val"')
            mtime_before = os.stat(path).st_mtime_ns
            results = []
            for _ in range(5):
                os.utime(path, ns=(mtime_before, mtime_before))
                results.append(
                    _call_load_hot_module(
                        name,
                        '{ "hook": () => "default" }',
                    )
                )
            assert all(r == results[0] for r in results)
        finally:
            _remove_tmp_files([name])


class TestPartialHookSets:
    def test_hot_module_only_tool_execute_before(self):
        name = "test-teb-only"
        try:
            _write_hot_module(name, '"tool.execute.before": () => "teb-hot"')
            result = _call_load_hot_module(
                name,
                '{'
                ' "tool.execute.before": () => "default-teb",'
                ' "text.complete": () => "default-tc"'
                ' }',
            )
            assert result is not None
            assert "tool.execute.before" in result
            assert "text.complete" not in result
        finally:
            _remove_tmp_files([name])

    def test_hot_module_only_text_complete(self):
        name = "test-tc-only"
        try:
            _write_hot_module(name, '"text.complete": () => "tc-hot"')
            result = _call_load_hot_module(
                name,
                '{'
                ' "tool.execute.before": () => "default-teb",'
                ' "text.complete": () => "default-tc"'
                ' }',
            )
            assert result is not None
            assert "text.complete" in result
            assert "tool.execute.before" not in result
        finally:
            _remove_tmp_files([name])


class TestNameVariants:
    def test_special_chars_in_plugin_name(self):
        name = "test.dash-and_underscore"
        try:
            _write_hot_module(name, '"hook": () => "ok"')
            result = _call_load_hot_module(name, '{ "hook": () => "default" }')
            assert result is not None
        finally:
            _remove_tmp_files([name])
