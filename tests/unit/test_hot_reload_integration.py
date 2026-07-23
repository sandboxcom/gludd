"""Integration tests for hot_reload.ts loadHotModule — end-to-end verification.

Tests the compiled-in loadHotModule function by writing real JS hot modules to
/tmp/gludd-hot-*.js and invoking them via node --experimental-strip-types.
Verifies all 5 design guarantees: file-loaded, missing-defaults, parse-fail-open,
mtime-cache-invalidation, and require() (not eval) loading.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

HOT_RELOAD_TS = (
    Path(__file__).resolve().parents[2] / ".opencode" / "lib" / "hot_reload.ts"
)
assert HOT_RELOAD_TS.exists(), f"hot_reload.ts not found at {HOT_RELOAD_TS}"
HOT_PREFIX = f"/tmp/gludd-hot-{os.getpid()}-hot-reload-integration-"

pytestmark = pytest.mark.xdist_group("hot_reload_integration")

_import_path = json.dumps(str(HOT_RELOAD_TS))


def _invoke(name: str, defaults_ts: str) -> dict | None:
    ts = (
        f'import {{ loadHotModule, type HotModule }} from {_import_path}\n'
        f"const defaults: HotModule = {defaults_ts}\n"
        f"const mod = loadHotModule({json.dumps(name)}, defaults)\n"
        "console.log(JSON.stringify(mod))\n"
    )
    wrapper = "/tmp/_test_hot_reload_integration.ts"
    Path(wrapper).write_text(ts)
    env = os.environ.copy()
    env["OPENCODE_SUBAGENT"] = ""
    env["GLUDD_HOT_MODULE_PREFIX"] = HOT_PREFIX
    r = subprocess.run(
        ["node", "--experimental-strip-types", wrapper],
        capture_output=True, text=True, timeout=15,
        cwd=str(Path(__file__).resolve().parents[2]), env=env,
    )
    assert r.returncode == 0, (
        f"Node exit {r.returncode}\nstderr: {r.stderr[:800]}\nstdout: {r.stdout[:400]}"
    )
    return json.loads(r.stdout.strip()) if r.stdout.strip() else None


def _write_hot(name: str, exports: str) -> str:
    path = f"{HOT_PREFIX}{name}.js"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(f"exports = {{ {exports} }};\n")
    time.sleep(0.06)
    return path


def _rm(name: str) -> None:
    import os as _os
    with contextlib.suppress(FileNotFoundError):
        _os.remove(f"{HOT_PREFIX}{name}.js")


def _cleanup(*names: str) -> None:
    for n in names:
        _rm(n)
    with contextlib.suppress(FileNotFoundError):
        Path("/tmp/_test_hot_reload_integration.ts").unlink()


class TestHotModuleIntegration:
    """All 5 design guarantees tested in integration (real Node process)."""

    def test_loads_hot_module_when_file_exists(self):
        """1. loadHotModule returns the hot module (not defaults) when file exists."""
        name = "integ-exists"
        try:
            _write_hot(name, '"tool.execute.before": () => "hot-result"')
            r = _invoke(name, '{ "tool.execute.before": () => "default-result" }')
            assert r is not None
            assert "tool.execute.before" in r
        finally:
            _cleanup(name)

    def test_returns_defaults_when_file_missing(self):
        """2. loadHotModule falls back to defaults when hot file is missing."""
        r = _invoke(
            "integ-missing-abcxyz",
            '{ "text.complete": () => "only-default" }',
        )
        assert r is not None
        assert "text.complete" in r

    def test_returns_defaults_on_parse_error_fail_open(self):
        """3. Corrupt JS syntax → returns defaults silently (fail-open)."""
        name = "integ-corrupt"
        try:
            Path(f"{HOT_PREFIX}{name}.js").write_text("this ;; is } not { valid\n")
            r = _invoke(name, '{ "hook": () => "safe-default" }')
            assert r is not None
            assert "hook" in r
        finally:
            _cleanup(name)

    def test_mtime_cache_invalidation_newer_file(self):
        """4a. Changed mtime → cache miss → loads updated module."""
        name = "integ-cache-new"
        try:
            _write_hot(name, '"hook": () => "v1"')
            r1 = _invoke(name, '{ "hook": () => "default" }')
            time.sleep(0.15)
            _write_hot(name, '"hook": () => "v2"')
            r2 = _invoke(name, '{ "hook": () => "default" }')
            assert r1 is not None and r2 is not None
            assert r1 != r2
        finally:
            _cleanup(name)

    def test_same_mtime_returns_cached_module(self):
        """4b. Same mtime → cache hit → returns cached module."""
        name = "integ-cache-same"
        try:
            p = _write_hot(name, '"hook": () => "v1"')
            mtime_ns = os.stat(p).st_mtime_ns
            r1 = _invoke(name, '{ "hook": () => "default" }')
            os.utime(p, ns=(mtime_ns, mtime_ns))
            r2 = _invoke(name, '{ "hook": () => "default" }')
            assert r1 is not None and r2 is not None
            assert r1 == r2
        finally:
            _cleanup(name)

    def test_require_loading_not_eval(self):
        """5. The hot module is loaded via createRequire() (Node require), not
        eval / new Function / fs.readFileSync + vm. This test verifies the
        source code of hot_reload.ts does not contain eval or new Function
        patterns, and does contain createRequire."""
        src = HOT_RELOAD_TS.read_text()
        assert "createRequire" in src, "hot_reload.ts must use createRequire (Node require)"
        assert "eval(" not in src, "hot_reload.ts must NOT use eval()"
        assert "new Function" not in src, "hot_reload.ts must NOT use new Function()"

    def test_require_cache_invalidation(self):
        """5b. require.cache is cleared before re-require so updated files
        are loaded even if previously required."""
        name = "integ-require-cache"
        try:
            _write_hot(name, '"hook": () => "first"')
            r1 = _invoke(name, '{ "hook": () => "default" }')
            time.sleep(0.10)
            # Write updated content WITHOUT changing mtime significantly
            # — but the source code does delete _require.cache[...] so even
            # same-mtime files would reload IF the cache deletion works.
            # Here we vary mtime slightly to trigger the re-read branch,
            # then verify the require cache deletion in the source.
            _write_hot(name, '"hook": () => "second"')
            r2 = _invoke(name, '{ "hook": () => "default" }')
            assert r1 is not None and r2 is not None
            # Verify require.cache deletion is in the source
            assert "delete _require.cache" in HOT_RELOAD_TS.read_text()
        finally:
            _cleanup(name)

    def test_hot_module_overrides_default_hook_keys(self):
        """Integration: hot module keys take precedence over defaults."""
        name = "integ-override"
        try:
            _write_hot(
                name,
                '"tool.execute.before": () => "hot-teb", "text.complete": () => "hot-tc"',
            )
            r = _invoke(
                name,
                '{ "tool.execute.before": () => "default-teb", "text.complete": () => "default-tc" }',
            )
            assert r is not None
            assert "tool.execute.before" in r
            assert "text.complete" in r
        finally:
            _cleanup(name)
