#!/usr/bin/env python3
"""Simulate opencode startup: load every plugin from opencode.json, call its
default export, and verify each returns a valid plugin object with hooks.

This catches what node --experimental-strip-types --check <file> cannot:
- factory functions that throw
- plugins that return null/undefined instead of a hooks object
- imports that resolve at parse time but fail at execution time
- hot-reload module loading failures
- side effects (spawnGateRefresh, fs writes) that crash

Usage:
    make diag-opencode-startup
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_plugin_list() -> list[str]:
    cfg = json.loads((REPO / "opencode.json").read_text())
    return cfg.get("plugin", [])


def main() -> int:
    plugins = load_plugin_list()
    if not plugins:
        print("ERROR: no plugins listed in opencode.json")
        return 2

    # Build a node script that imports every plugin, calls its default export,
    # and verifies each returns a non-null object. This is what opencode does.
    lines = [
        "const results = [];",
    ]
    for p in plugins:
        abs_path = (REPO / p).resolve()
        lines.append(f"""
        {{
          try {{
            const mod = await import("{abs_path}");
            const factory = mod.default;
            if (typeof factory !== "function") {{
              results.push({{ plugin: "{p}", status: "FAIL",
                error: "default is not a function (got " + typeof factory + ")" }});
            }} else {{
              try {{
                const hooks = await factory({{}});
                if (hooks === null || hooks === undefined) {{
                  results.push({{ plugin: "{p}", status: "FAIL",
                    error: "factory returned null/undefined" }});
                }} else if (typeof hooks !== "object") {{
                  results.push({{ plugin: "{p}", status: "FAIL",
                    error: "factory returned non-object: " + typeof hooks }});
                }} else {{
                  const hookNames = Object.keys(hooks).filter(k => typeof hooks[k] === "function");
                  results.push({{ plugin: "{p}", status: "OK", hooks: hookNames }});
                }}
              }} catch (e) {{
                results.push({{ plugin: "{p}", status: "FAIL", error: "factory threw: " + (e.message || String(e)) }});
              }}
            }}
          }} catch (e) {{
            results.push({{ plugin: "{p}", status: "FAIL", error: "import failed: " + (e.message || String(e)) }});
          }}
        }}
        """)

    lines.append('console.log(JSON.stringify(results, null, 2));')
    script = "\n".join(lines)

    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO),
    )

    if proc.returncode != 0:
        print(f"Node process crashed (exit {proc.returncode}):")
        print(proc.stderr[-2000:])
        return 1

    try:
        results = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        print("Could not parse output:")
        print(proc.stdout[-2000:])
        print(proc.stderr[-2000:])
        return 1

    failures = [r for r in results if r["status"] != "OK"]
    for r in results:
        if r["status"] == "OK":
            print(f'  OK    {r["plugin"]:55s}  hooks: {", ".join(r.get("hooks", []))}')
        else:
            print(f'  FAIL  {r["plugin"]:55s}  {r["error"]}')

    print(f"\n=== {len(results) - len(failures)}/{len(results)} OK, {len(failures)} FAIL ===")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
