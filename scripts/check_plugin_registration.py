"""check_plugin_registration.py — AA056 enforcement.

Verify every enforce-*.ts file under .opencode/plugin/ is registered
in opencode.json's plugin array. Exit 0 on clean, exit 1 on violation.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
OPENCODE_JSON = ROOT / "opencode.json"


def _plugin_files() -> list[str]:
    """Return sorted list of enforce-*.ts filenames (excluding test files)."""
    files: list[str] = []
    for p in sorted(PLUGIN_DIR.glob("enforce-*.ts")):
        if ".test." in p.name:
            continue
        files.append(p.name)
    return files


def _registered_plugins() -> set[str]:
    """Return set of registered enforce-*.ts filenames from opencode.json."""
    if not OPENCODE_JSON.exists():
        print(f"ERROR: {OPENCODE_JSON} not found")
        sys.exit(1)
    cfg = json.loads(OPENCODE_JSON.read_text())
    plugin_list = cfg.get("plugin", [])
    registered: set[str] = set()
    for entry in plugin_list:
        if isinstance(entry, str):
            name = Path(entry).name
        elif isinstance(entry, dict):
            name = Path(entry.get("path", "")).name
        else:
            continue
        if name.startswith("enforce-") and name.endswith(".ts"):
            registered.add(name)
    return registered


def main() -> int:
    files = _plugin_files()
    registered = _registered_plugins()
    missing = [f for f in files if f not in registered]

    if missing:
        print(f"ERROR: {len(missing)} plugin(s) on disk but not registered in opencode.json:")
        for f in missing:
            print(f"  {f}")
        print(f"\nFix: add entries to opencode.json → plugin array")
        return 1

    extra = registered - set(files)
    if extra:
        print(f"WARNING: {len(extra)} plugin(s) registered in opencode.json but not on disk:")
        for f in sorted(extra):
            print(f"  {f}")
        # Warning only — registered-but-missing is less critical

    print(f"OK: {len(files)} enforce-*.ts files, all registered in opencode.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
