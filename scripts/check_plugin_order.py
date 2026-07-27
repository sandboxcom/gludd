"""check_plugin_order.py — AA077 enforcement.

Validate that enforcement plugins registered in opencode.json follow
a dependency-respecting load order:
  1. shared infrastructure / core enforcement
  2. behavioral enforcement
  3. advisory / auxiliary plugins

Exit 0 on clean, exit 1 on inspection failure (order violation).
This is a lint-style check — it reports issues but does not reorder.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENCODE_JSON = ROOT / "opencode.json"

# Canonical desired order — plugins in group 1 MUST load before group 2,
# group 2 before group 3. Within a group, order is advisory.
CORE_PLUGINS: set[str] = {
    "enforce-session-start.ts",
    "enforce-make.ts",
    "enforce-floor.ts",
    "enforce-delegate.ts",
    "enforce-multitask.ts",
    "enforce-floor-v2.ts",
    "enforce-stop.ts",
}

BEHAVIORAL_PLUGINS: set[str] = {
    "enforce-deadline.ts",
    "enforce-enhancement-ratio.ts",
    "enforce-clean-tree.ts",
    "enforce-commit-lock.ts",
    "enforce-verified-claims.ts",
    "enforce-no-suppressions.ts",
    "enforce-no-wait.ts",
    "enforce-deletion-gate.ts",
    "enforce-batch-push.ts",
    "enforce-depth.ts",
    "enforce-directives.ts",
    "enforce-tdd.ts",
    "enforce-objective.ts",
    "enforce-anti-essay.ts",
    "enforce-branch-discipline.ts",
    "enforce-test-integrity.ts",
    "enforce-worktree.ts",
    "enforce-no-ci-poll.ts",
    "enforce-release-deadline.ts",
    "enforce-task-tracking.ts",
    "enforce-deliverable.ts",
}

AUX_PLUGINS: set[str] = {
    "enforce-audit.ts",
    "enforce-context.ts",
}


def _plugin_order() -> list[str]:
    """Return list of enforce-*.ts filenames in registration order."""
    cfg = json.loads(OPENCODE_JSON.read_text())
    plugin_list = cfg.get("plugin", [])
    order: list[str] = []
    for entry in plugin_list:
        if isinstance(entry, str):
            name = Path(entry).name
        elif isinstance(entry, dict):
            name = Path(entry.get("path", "")).name
        else:
            continue
        if name.startswith("enforce-") and name.endswith(".ts"):
            order.append(name)
    return order


def _group(name: str) -> int:
    """Return group number: 1=core, 2=behavioral, 3=aux."""
    if name in CORE_PLUGINS:
        return 1
    if name in BEHAVIORAL_PLUGINS:
        return 2
    if name in AUX_PLUGINS:
        return 3
    return 2  # unknown → behavioral (treat as mid-priority)


def main() -> int:
    order = _plugin_order()
    violations: list[str] = []

    max_group_seen = 0
    for i, name in enumerate(order):
        g = _group(name)
        if g < max_group_seen:
            violations.append(
                f"  {name} (group {g}) registered after group-{max_group_seen} plugin"
                f" — should load earlier (position {i + 1})"
            )
        max_group_seen = max(max_group_seen, g)

    unclassified = [n for n in order if _group(n) not in (1, 2, 3)]
    if unclassified:
        print(f"WARNING: {len(unclassified)} plugins not in any group:")
        for n in unclassified:
            print(f"  {n}")

    if violations:
        print(f"ERROR: {len(violations)} plugin order violation(s):")
        for v in violations:
            print(v)
        print("\nFix: reorder opencode.json → plugin array so core plugins load first,")
        print("      then behavioral plugins, then advisory/aux plugins.")
        return 1

    print(f"OK: {len(order)} plugins in correct dependency order")
    return 0


if __name__ == "__main__":
    sys.exit(main())
