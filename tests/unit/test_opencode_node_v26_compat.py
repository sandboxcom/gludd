"""Factory→Hooks contract test for opencode plugins.

This is the test that was MISSING and allowed enforce-commit-lock.ts to ship
with a broken plugin pattern (api.tool.execute.before instead of returning
a Hooks object). It was caught by zero existing tests because:

  - test_all_plugin_files_load_with_strip_types: parse-time only, never calls factory
  - test_hook_runtime.py: bypasses factory, uses custom loading code

This test fills the gap: it calls plugin.default(input) the way opencode does
and verifies the return matches the Hooks contract from @opencode-ai/plugin 1.17.9.

When .opencode/ is absent, ALL tests skip — this preserves the operator workaround
of moving .opencode/ aside when plugins are broken.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_OPENCODE_DIR = Path(os.environ.get("OPENCODE_DIR", str(ROOT / ".opencode"))).resolve()
PLUGIN_DIR = _OPENCODE_DIR / "plugin"
PLUGINS_DIR = _OPENCODE_DIR / "plugins"

REQUIRE_RE = re.compile(r"\brequire\s*\(")

# Hook names from @opencode-ai/plugin 1.17.9 Hooks interface (index.d.ts)
# plus legacy names used by older plugins in this repo.
KNOWN_HOOK_NAMES = frozenset({
    "tool.execute.before",
    "tool.execute.after",
    "experimental.chat.system.transform",
    "experimental.text.complete",
    "experimental.chat.messages.transform",
    "experimental.provider.small_model",
    "experimental.session.compacting",
    "experimental.compaction.autocontinue",
    "event",
    "chat.message",
    "chat.params",
    "chat.headers",
    "permission.ask",
    "command.execute.before",
    "shell.env",
    "tool.definition",
    "dispose",
    "config",
    "tool",
    "auth",
    "provider",
    # Legacy names used by some plugins in this repo
    "text.complete",
    "session.idle",
})

# Minimal PluginInput stub. Plugins that access fields should fail-open
# (try/catch), so empty-ish values are safe for a contract test.
_PLUGIN_INPUT_TS = """({
  client: {},
  project: { id: "test-project" },
  directory: "/tmp",
  worktree: "/tmp",
  experimental_workspace: { register: () => {} },
  serverUrl: new URL("http://localhost:0"),
  $: {},
})
"""

_PLUGINS_PRESENT = _OPENCODE_DIR.is_dir() and (
    any(PLUGIN_DIR.glob("*.ts")) if PLUGIN_DIR.is_dir() else False
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_plugin_files() -> list[Path]:
    """Collect all .ts files from plugin/ and plugins/ directories."""
    files: list[Path] = []
    for d in (PLUGIN_DIR, PLUGINS_DIR):
        if d.is_dir():
            files.extend(sorted(f for f in d.glob("*.ts") if f.is_file()))
    return files


def _plugin_list_from_config() -> list[str]:
    """Read opencode.json and return the plugin path list."""
    cfg_path = ROOT / "opencode.json"
    if not cfg_path.exists():
        return []
    cfg = json.loads(cfg_path.read_text())
    return cfg.get("plugin", [])


def _run_node(ts_code: str, timeout: int = 30) -> tuple[int, str, str]:
    """Write TS to temp file, run with node --experimental-strip-types."""
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", dir="/tmp", prefix="contract_test_", delete=False
    ) as f:
        f.write(ts_code)
        tmp = f.name
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = "1"  # don't trigger enforcement side-effects
        result = subprocess.run(
            ["node", "--experimental-strip-types", tmp],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), env=env,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# Skip when .opencode/ is absent — preserves the operator workaround
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not _PLUGINS_PRESENT,
    reason=(
        f"no plugins found under {PLUGIN_DIR} "
        f"(set OPENCODE_DIR=... to test a different location; "
        f"this skip is intentional so operators can move .opencode/ aside)"
    ),
)


# ---------------------------------------------------------------------------
# Test 1: Factory contract — every plugin returns valid Hooks
# ---------------------------------------------------------------------------

class TestPluginFactoryContract:
    """Verify that every plugin listed in opencode.json, when called the way
    opencode calls it (default(input) → Hooks), returns a valid hooks object.

    This is the test that would have caught enforce-commit-lock.ts using the
    wrong api.tool.execute.before(fn) pattern instead of returning Hooks.
    """

    def test_every_plugin_factory_returns_hooks_object(self):
        plugins = _plugin_list_from_config()
        assert plugins, "opencode.json lists no plugins"

        errors: list[str] = []
        for plugin_path in plugins:
            abs_path = str((ROOT / plugin_path).resolve())

            # Build a node script that imports the plugin, calls its default
            # export with a realistic PluginInput, and verifies the return.
            ts_code = f"""
            const input = {_PLUGIN_INPUT_TS}
            try {{
              const mod = await import("{abs_path}")
              const factory = mod.default
              if (typeof factory !== "function") {{
                console.log(JSON.stringify({{ status: "fail",
                  error: "default is not a function (got " + typeof factory + ")" }}))
              }} else {{
                const hooks = await factory(input)
                if (hooks === null || hooks === undefined) {{
                  console.log(JSON.stringify({{ status: "fail",
                    error: "factory returned null/undefined" }}))
                }} else if (typeof hooks !== "object") {{
                  console.log(JSON.stringify({{ status: "fail",
                    error: "non-object: " + typeof hooks }}))
                }} else {{
                  const hookEntries = Object.entries(hooks).filter(([k, v]) => typeof v === "function")
                  console.log(JSON.stringify({{ status: "ok", hooks: hookEntries.map(([k]) => k) }}))
                }}
              }}
            }} catch (e) {{
              console.log(JSON.stringify({{ status: "fail", error: (e.message || String(e)).substring(0, 300) }}))
            }}
            """

            code, stdout, stderr = _run_node(ts_code)
            if code != 0:
                errors.append(f"{plugin_path}: node crashed (exit {code}): {stderr[:200]}")
                continue

            try:
                result = json.loads(stdout.split("\n")[-1] if "\n" in stdout else stdout)
            except (json.JSONDecodeError, IndexError):
                errors.append(f"{plugin_path}: could not parse output: {stdout[:200]}")
                continue

            if result["status"] != "ok":
                errors.append(f"{plugin_path}: {result['error']}")

        assert not errors, (
            f"{len(errors)} plugin(s) fail the factory→Hooks contract:\n"
            + "\n".join(f"  {e}" for e in errors)
            + "\n\nEach plugin's default export must be: "
            "(input: PluginInput) => Promise<Hooks> "
            "where Hooks is an object with function values."
        )

    def test_all_hook_keys_are_known(self):
        """Every hook key returned by a plugin must be a known hook name from
        the @opencode-ai/plugin Hooks interface. Unknown keys suggest a typo
        or a plugin using an outdated/invalid hook name.
        """
        plugins = _plugin_list_from_config()
        errors: list[str] = []

        for plugin_path in plugins:
            abs_path = str((ROOT / plugin_path).resolve())
            ts_code = f"""
            const input = {_PLUGIN_INPUT_TS}
            const mod = await import("{abs_path}")
            const hooks = await mod.default(input)
            const keys = Object.keys(hooks || {{}}).filter(k => typeof hooks[k] === "function")
            console.log(JSON.stringify(keys))
            """
            code, stdout, _ = _run_node(ts_code)
            if code != 0:
                continue  # factory failure is caught by the test above

            try:
                keys = json.loads(stdout.split("\n")[-1])
            except (json.JSONDecodeError, IndexError):
                continue

            unknown = [k for k in keys if k not in KNOWN_HOOK_NAMES]
            if unknown:
                errors.append(f"{plugin_path}: unknown hook names: {unknown}")

        assert not errors, (
            f"{len(errors)} plugin(s) return unknown hook names:\n"
            + "\n".join(f"  {e}" for e in errors)
            + f"\n\nKnown hooks: {sorted(KNOWN_HOOK_NAMES)}"
        )

    def test_hooks_do_not_throw_with_empty_inputs(self):
        """Each hook function must not throw when called with empty objects.
        Plugins should fail-open (try/catch) — a hook that throws on unexpected
        input will crash opencode at runtime.
        """
        plugins = _plugin_list_from_config()
        errors: list[str] = []

        for plugin_path in plugins:
            abs_path = str((ROOT / plugin_path).resolve())
            ts_code = f"""
            const input = {_PLUGIN_INPUT_TS}
            const mod = await import("{abs_path}")
            const hooks = await mod.default(input)
            if (!hooks) {{ console.log("[]"); return }}
            const results = []
            for (const [name, fn] of Object.entries(hooks)) {{
              if (typeof fn !== "function") continue
              try {{
                const out = await fn({{}}, {{}})
                results.push({{ name, ok: true }})
              }} catch (e) {{
                results.push({{ name, ok: false, error: (e.message || String(e)).substring(0, 200) }})
              }}
            }}
            console.log(JSON.stringify(results))
            """
            code, stdout, _ = _run_node(ts_code, timeout=45)
            if code != 0:
                continue  # factory failure caught above

            try:
                results = json.loads(stdout.split("\n")[-1])
            except (json.JSONDecodeError, IndexError):
                continue

            for r in results:
                if not r["ok"]:
                    errors.append(f"{plugin_path} hook '{r['name']}': {r['error']}")

        assert not errors, (
            f"{len(errors)} hook(s) throw on empty input (must fail-open):\n"
            + "\n".join(f"  {e}" for e in errors)
        )


# ---------------------------------------------------------------------------
# Test 2: Parse-time compatibility (existing, kept as first line of defense)
# ---------------------------------------------------------------------------

class TestNodeV26ParseCompat:
    """Verify plugin files parse cleanly under node --experimental-strip-types.
    This catches syntax errors but NOT runtime/factory errors — the factory
    contract test above covers that.
    """

    def test_all_plugin_files_parse(self):
        errors: list[str] = []
        for f in _collect_plugin_files():
            result = subprocess.run(
                ["node", "--experimental-strip-types", "--check", str(f)],
                cwd=str(ROOT),
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                rel = f.relative_to(ROOT)
                detail = result.stderr.strip().split("\n")[-1] if result.stderr else f"exit {result.returncode}"
                errors.append(f"{rel}: {detail}")

        assert not errors, (
            f"{len(errors)} file(s) fail Node v26 parse:\n"
            + "\n".join(errors)
        )

    def test_no_require_calls(self):
        """require() is not available in ESM context (Node v26)."""
        violations: list[str] = []
        for f in _collect_plugin_files():
            lines = f.read_text().split("\n")
            for i, line in enumerate(lines, 1):
                if REQUIRE_RE.search(line):
                    violations.append(f"{f.relative_to(ROOT)}:{i}")
        assert not violations, (
            f"{len(violations)} require() call(s) found (not available in ESM):\n"
            + "\n".join(f"  {v}" for v in violations)
        )
