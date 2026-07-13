#!/usr/bin/env python3
"""Audit code branches without tests.

Scans .opencode/plugin/*.ts for exported hooks and checks whether corresponding
test files exist and test each hook. Also scans src/general_ludd/ for Python
modules without test files in tests/unit/ or tests/integration/.

Output: structured JSON to stdout with untested_plugins, untested_hooks,
and untested_python_modules.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
TESTS_UNIT = ROOT / "tests" / "unit"
TESTS_INTEGRATION = ROOT / "tests" / "integration"
SRC_DIR = ROOT / "src" / "general_ludd"

HOOK_KEY_RE = re.compile(
    r'"('
    r"tool\.execute\.before|"
    r"tool\.execute\.after|"
    r"text\.complete|"
    r"experimental\.chat\.system\.transform"
    r')"\s*:'
)

PY_ALLOWLIST_RE = re.compile(r".*/__init__\.py$")
TS_ALLOWLIST = {"shared.ts", "hot_reload.ts", "watchdog.ts"}


def _plugin_name(ts_file: Path) -> str:
    return ts_file.stem  # "enforce-make", "enforce-floor", etc.


def _test_name_variants(plugin_name: str) -> list[str]:
    """Generate test filename prefix variants from plugin name.

    enforce-make -> test_enforce_make, test_make
    enforce-floor -> test_enforce_floor, test_floor, test_enforcement_floor
    """
    parts = plugin_name.replace("-", "_")
    variants = [
        f"test_{parts}",
    ]
    if parts.startswith("enforce_"):
        base = parts[len("enforce_"):]
        variants.append(f"test_{base}")
        variants.append(f"test_enforcement_{base}")
        if base.endswith("s"):
            singular = base.rstrip("s")
            variants.append(f"test_{singular}")
            variants.append(f"test_enforcement_{singular}")
    elif parts.startswith("enforcement_"):
        base = parts[len("enforcement_"):]
        variants.append(f"test_{base}")
        if base.endswith("s"):
            singular = base.rstrip("s")
            variants.append(f"test_{singular}")
    return variants


def _find_test_files(variants: list[str]) -> list[Path]:
    found: list[Path] = []
    if not TESTS_UNIT.exists():
        return found
    for tf in TESTS_UNIT.iterdir():
        if not tf.is_file() or not tf.suffix == ".py":
            continue
        stem_lower = tf.stem.lower()
        for v in variants:
            v_lower = v.lower()
            # Prefix match: test_enforce_make matches test_enforce_make_plugin or test_enforce_make
            if stem_lower.startswith(v_lower + "_") or stem_lower == v_lower:
                found.append(tf)
                break
    return found


def _extract_hooks(ts_path: Path) -> list[str]:
    """Parse .ts file for exported hook keys used as impl object keys.

    Hooks are defined as string keys like '"tool.execute.before": async ...',
    then re-exported via `impl["tool.execute.before"]` in the default export.

    We collect unique hook names that appear both as key definitions AND as
    impl[...] lookups (the default export re-exposes them), or just as key
    definitions if the file doesn't use the impl pattern.
    """
    text = ts_path.read_text(encoding="utf-8")
    hooks: set[str] = set()

    # Find all hook key definitions
    for m in HOOK_KEY_RE.finditer(text):
        hooks.add(m.group(1))

    return sorted(hooks)


def _hook_tested_in_files(hook_name: str, test_files: list[Path]) -> bool:
    for tf in test_files:
        content = tf.read_text(encoding="utf-8")
        # Check for the hook name in quotes (matching TypeScript usage)
        if hook_name in content:
            return True
        # Check for common shorthand references
        short = _hook_short_name(hook_name)
        if short and short in content:
            return True
    return False


def _hook_short_name(hook_name: str) -> str | None:
    aliases = {
        "tool.execute.before": "tool.execute.before",
        "tool.execute.after": "tool.execute.after",
        "text.complete": "text.complete",
        "experimental.chat.system.transform": "system.transform",
    }
    return aliases.get(hook_name)


def _python_module_name(py_file: Path) -> str:
    """Derive a searchable module name from the .py path relative to SRC_DIR."""
    rel = py_file.relative_to(SRC_DIR)
    parts = list(rel.parts)
    parts[-1] = parts[-1].replace(".py", "")
    return ".".join(parts)


def _python_finds_test(py_file: Path) -> bool:
    mod_name = _python_module_name(py_file)
    stem = py_file.stem  # e.g., "daemon", "blocker_detector"

    # Check tests/unit/ and tests/integration/
    for test_dir in (TESTS_UNIT, TESTS_INTEGRATION):
        if not test_dir.exists():
            continue
        for tf in test_dir.iterdir():
            if not tf.is_file() or not tf.suffix == ".py":
                continue
            tname = tf.stem

            # Exact match: test_daemon.py matches daemon.py
            if tname == f"test_{stem}":
                return True

            # Prefix match: test_daemon_startup.py matches daemon.py
            if tname.startswith(f"test_{stem}_"):
                return True

            # Mod-path match: test_remediation_dispatcher.py matches remediation/dispatcher.py
            expected = f"test_{mod_name.replace('.', '_').replace('-', '_')}"
            if tname == expected:
                return True
            if tname.startswith(expected + "_"):
                return True

            # Also check if it matches the full relative path parts
            # e.g., src/general_ludd/foo/bar.py -> test_foo_bar.py
            parts = mod_name.split(".")
            if len(parts) >= 2:
                joined = "_".join(parts)
                if tname == f"test_{joined}":
                    return True
                if tname.startswith(f"test_{joined}_"):
                    return True

    return False


def main() -> None:
    untested_plugins: list[dict] = []
    untested_hooks: list[dict] = []
    untested_python_modules: list[str] = []

    # ── 1. Scan TypeScript plugins ──────────────────────────────────────────
    if PLUGIN_DIR.exists():
        for ts_file in sorted(PLUGIN_DIR.glob("*.ts")):
            pname = _plugin_name(ts_file)

            # Skip lib-like files
            if ts_file.name in TS_ALLOWLIST:
                continue

            hooks = _extract_hooks(ts_file)
            variants = _test_name_variants(pname)
            test_files = _find_test_files(variants)

            if not test_files:
                untested_plugins.append({
                    "plugin": ts_file.name,
                    "name": pname,
                    "exports": hooks,
                    "expected_test_variants": variants,
                })
                continue

            # Check each hook
            for hook in hooks:
                if not _hook_tested_in_files(hook, test_files):
                    untested_hooks.append({
                        "plugin": ts_file.name,
                        "hook": hook,
                        "test_files_found": [tf.name for tf in test_files],
                    })

    # ── 2. Scan Python source modules ───────────────────────────────────────
    if SRC_DIR.exists():
        for py_file in sorted(SRC_DIR.rglob("*.py")):
            if PY_ALLOWLIST_RE.search(str(py_file)):
                continue
            if not _python_finds_test(py_file):
                rel = str(py_file.relative_to(ROOT))
                untested_python_modules.append(rel)

    result = {
        "untested_plugins": untested_plugins,
        "untested_hooks": untested_hooks,
        "untested_python_modules": untested_python_modules,
    }

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")

    # Exit 0 on clean, 1 if anything untested
    if untested_plugins or untested_hooks or untested_python_modules:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
