"""Guardrail test: verify all opencode plugins pass the plugin loader check.

opencode's getLegacyPlugins() iterates Object.values(mod) and requires EVERY
export to be either:
  - a function (typeof === 'function'), OR
  - an object with a `server` key that is a function

If ANY export fails this check, opencode rejects the plugin with:
  "Plugin export is not a function"

This test simulates that check by importing each plugin via Node and verifying
all exports pass the function check. This is the test that SHOULD have existed
to catch the crash where named exports (const, regex, arrays) were added to
plugin files.

Run: make test TESTFILE=tests/unit/test_plugin_loader_compat.py
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestPluginLoaderCompat(unittest.TestCase):
    """Verify every plugin in opencode.json passes opencode's loader check."""

    def _get_plugin_list(self) -> list[str]:
        """Read opencode.json and extract the plugin list."""
        import json as _json

        config_path = ROOT / "opencode.json"
        with open(config_path) as f:
            config = _json.load(f)
        return config.get("plugin", [])

    def test_all_plugins_pass_loader_check(self) -> None:
        """Each plugin's Object.values(mod) must all be functions or {server: fn}.

        This is the exact check opencode's getLegacyPlugins() performs. If any
        plugin has a named export that is not a function (e.g. export const X = 42),
        opencode crashes with "Plugin export is not a function".
        """
        plugins = self._get_plugin_list()
        self.assertGreater(len(plugins), 10, "Expected 20+ plugins in opencode.json")

        # Node script that imports each plugin and checks all exports
        node_script = (
            """
const plugins = __PLUGINS__;
let failures = [];
let checked = 0;

async function check() {
  for (const p of plugins) {
    try {
      const mod = await import(p);
      for (const [key, value] of Object.entries(mod)) {
        const isFn = typeof value === 'function';
        const isServerObj = value && typeof value === 'object'
          && 'server' in value
          && typeof value.server === 'function';
        if (!isFn && !isServerObj) {
          failures.push(p + ': export "' + key + '" is ' + typeof value);
        }
      }
      checked++;
    } catch(e) {
      failures.push(p + ': IMPORT ERROR: ' + e.message);
    }
  }
  console.log(JSON.stringify({ checked, failures }));
}

check();
"""
        ).replace("__PLUGINS__", json.dumps(plugins))

        result = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", node_script],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=30,
            env={**os.environ, "NODE_NO_WARNINGS": "1"},
        )

        if result.returncode != 0:
            self.fail(f"Node script failed:\nstdout: {result.stdout}\nstderr: {result.stderr}")

        # Parse the JSON output from the last line
        output_lines = [
            line for line in result.stdout.strip().split("\n")
            if line.strip()
        ]
        data = json.loads(output_lines[-1])

        self.assertEqual(
            data["failures"], [],
            "Plugins with non-function exports (would crash opencode):\n" +
            "\n".join(f"  {f}" for f in data["failures"])
        )
        self.assertEqual(data["checked"], len(plugins),
                         f"Expected to check {len(plugins)} plugins, only checked {data['checked']}")

    def test_no_named_exports_in_plugin_files(self) -> None:
        """Plugin .ts files must not have `export ` lines except `export default`.

        This is a structural source-level check that catches the root cause before
        it reaches the runtime loader. Any `export const`, `export function`,
        `export let`, `export class` in a plugin file listed in opencode.json
        will cause the loader crash.
        """
        plugins = self._get_plugin_list()
        violations = []

        for plugin_path in plugins:
            if not plugin_path.endswith(".ts"):
                continue
            full_path = ROOT / plugin_path
            if not full_path.exists():
                continue
            content = full_path.read_text()
            for lineno, line in enumerate(content.split("\n"), 1):
                stripped = line.lstrip()
                # Skip export default, export type, and comments
                if (
                    stripped.startswith("export ")
                    and not stripped.startswith("export default")
                    and not stripped.startswith("export type")
                ):
                    violations.append(f"{plugin_path}:{lineno}: {stripped[:80]}")

        self.assertEqual(
            violations, [],
            f"Plugin files must only have `export default`. Named exports crash "
            f"opencode's loader. Found {len(violations)} violations:\n" +
            "\n".join(f"  {v}" for v in violations)
        )


if __name__ == "__main__":
    unittest.main()
