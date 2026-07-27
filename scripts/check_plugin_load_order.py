#!/usr/bin/env python3
"""AB028 — validate enforcement plugin load order in opencode.json.

Parses import statements in each .opencode/plugin/enforce-*.ts file, builds a
dependency graph, and verifies that the opencode.json `plugins` array order
satisfies topological sort. A plugin must appear AFTER all its imports.

Exit non-zero on dependency violations.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
OPENCODE_JSON = ROOT / "opencode.json"

IMPORT_RE = re.compile(r"import\s+.*?\s+from\s+['\"](\.\.?/[^'\"]+)['\"]")


def extract_imports(file_path: Path) -> list[str]:
    if not file_path.exists():
        return []
    content = file_path.read_text()
    imports = []
    for m in IMPORT_RE.finditer(content):
        raw = m.group(1)
        resolved = (file_path.parent / raw).resolve()
        imports.append(resolved.name)
    return imports


def get_registered_plugins() -> list[str]:
    if not OPENCODE_JSON.exists():
        return []
    with open(OPENCODE_JSON) as f:
        config = json.load(f)
    plugins = config.get("plugins", [])
    return [Path(p).name for p in plugins if isinstance(p, str)]


def build_dep_graph() -> tuple[dict[str, list[str]], list[str]]:
    plugin_files = sorted(PLUGIN_DIR.glob("enforce-*.ts"))
    deps: dict[str, list[str]] = {}

    for pf in plugin_files:
        name = pf.name
        imported = extract_imports(pf)
        shared_imports = [i for i in imported if i in {p.name for p in plugin_files} or i == "shared.ts"]
        deps[name] = shared_imports

    return deps, [p.name for p in plugin_files]


def check_order(deps: dict[str, list[str]], order: list[str]) -> list[str]:
    violations: list[str] = []
    plugin_positions = {name: i for i, name in enumerate(order) if name in deps}

    for plugin_name, imports in sorted(deps.items()):
        if plugin_name not in plugin_positions:
            continue
        plugin_pos = plugin_positions[plugin_name]
        for imported in imports:
            if imported not in plugin_positions:
                continue
            import_pos = plugin_positions[imported]
            if import_pos > plugin_pos:
                violations.append(
                    f"  {plugin_name} imports {imported} but loads BEFORE it (position {plugin_pos} < {import_pos})"
                )

    return violations


def main() -> int:
    deps, plugin_names = build_dep_graph()
    order = get_registered_plugins()

    if not order:
        print("check-plugin-load-order: no plugins found in opencode.json")
        return 1

    violations = check_order(deps, order)
    if violations:
        print(f"check-plugin-load-order: {len(violations)} dependency order violation(s):")
        for v in violations:
            print(v)
        return 1

    print(f"check-plugin-load-order: {len(order)} plugins in valid dependency order")
    return 0


if __name__ == "__main__":
    sys.exit(main())
