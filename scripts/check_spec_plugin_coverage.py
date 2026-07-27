"""check_spec_plugin_coverage.py — AB018 enforcement.

Each enforcement plugin (enforce-*.ts) should have ≥5 behavioral specs
documenting what it prevents. Flags underdocumented plugins (<5 spec refs).
Also flags spec groups with 20+ entries but 0 plugin references.

Exit 0 if coverage meets threshold; exit 1 if gaps found.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

SPEC_RE = re.compile(r"^### (A[AB]\d{3}) — (.+)$", re.MULTILINE)
PLUGIN_RE = re.compile(r"enforce-([a-z0-9\-]+)\.ts")

MIN_SPECS_PER_PLUGIN = 5
MIN_PLUGIN_REFS_PER_GROUP = 1


def parse_specs(text: str) -> list[tuple[str, str]]:
    specs = []
    for m in SPEC_RE.finditer(text):
        start = m.end()
        next_m = SPEC_RE.search(text, start)
        end = next_m.start() if next_m else len(text)
        specs.append((m.group(1), text[start:end].strip()))
    return specs


def load_plugin_files() -> list[str]:
    if not PLUGIN_DIR.exists():
        return []
    return sorted(f.name for f in PLUGIN_DIR.glob("enforce-*.ts"))


def count_specs_per_plugin(specs: list[tuple[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _, body in specs:
        for m in PLUGIN_RE.finditer(body):
            pname = f"enforce-{m.group(1)}.ts"
            counts[pname] = counts.get(pname, 0) + 1
    return counts


def group_specs_by_prefix(
    specs: list[tuple[str, str]],
) -> dict[str, list[tuple[str, str]]]:
    groups: dict[str, list[tuple[str, str]]] = {}
    for spec_id, body in specs:
        prefix = spec_id[:2]
        groups.setdefault(prefix, []).append((spec_id, body))
    return groups


def main() -> int:
    if not SPECS_FILE.exists():
        return 0

    text = SPECS_FILE.read_text(encoding="utf-8")
    specs = parse_specs(text)
    plugins = load_plugin_files()
    plugin_counts = count_specs_per_plugin(specs)
    groups = group_specs_by_prefix(specs)

    violations: list[str] = []

    for plugin in plugins:
        count = plugin_counts.get(plugin, 0)
        if count < MIN_SPECS_PER_PLUGIN:
            violations.append(f"{plugin}: only {count} spec refs (need ≥{MIN_SPECS_PER_PLUGIN}) — UNDERDOCUMENTED")

    for prefix, group_specs in groups.items():
        if len(group_specs) >= 20:
            has_plugin_ref = any(PLUGIN_RE.search(body) for _, body in group_specs)
            if not has_plugin_ref:
                violations.append(
                    f"Group {prefix}: {len(group_specs)} specs but 0 plugin references — documentation gap"
                )

    if violations:
        print(f"AB018 SPEC-PLUGIN-COVERAGE: {len(violations)} gap(s):")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(
        f"AB018: {len(plugins)} plugins, {len(specs)} specs. All plugins have ≥{MIN_SPECS_PER_PLUGIN} spec refs. PASS"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
