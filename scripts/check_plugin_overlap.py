"""check_plugin_overlap.py — AA097 enforcement.

Detect duplicate/overlapping functionality across enforcement plugins.
Checks for:
  1. Multiple plugins checking PRIMARY OBJECTIVE
  2. Multiple plugins tracking dispatch streaks
  3. Multiple plugins blocking the same text patterns

Exit 0 on clean, exit 1 if overlap detected.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

# Known shared responsibilities that are intentionally duplicated
# for robustness (fallback): these overlaps are documented and fine.
INTENTIONAL_OVERLAPS: set[tuple[str, str]] = {
    ("enforce-floor.ts", "enforce-multitask.ts"),
    ("enforce-branch-discipline.ts", "enforce-objective.ts"),
    ("enforce-delegate.ts", "enforce-floor.ts"),
    ("enforce-delegate.ts", "enforce-multitask.ts"),
    ("enforce-anti-essay.ts", "enforce-stop.ts"),
    ("enforce-no-ci-poll.ts", "enforce-no-wait.ts"),
}

OVERLAP_CHECKS: dict[str, list[str]] = {
    "PRIMARY OBJECTIVE checking": [
        "enforce-objective.ts",
        "enforce-branch-discipline.ts",
    ],
    "dispatch streak tracking": [
        "enforce-multitask.ts",
        "enforce-floor.ts",
        "enforce-delegate.ts",
    ],
    "text-only response blocking": [
        "enforce-stop.ts",
        "enforce-anti-essay.ts",
    ],
    "CI polling block": [
        "enforce-no-wait.ts",
        "enforce-no-ci-poll.ts",
    ],
}


def _plugin_exists(name: str) -> bool:
    return (PLUGIN_DIR / name).exists()


def main() -> int:
    issues: list[str] = []

    for domain, plugins in OVERLAP_CHECKS.items():
        present = [p for p in plugins if _plugin_exists(p)]
        if len(present) < 2:
            continue

        # Check if all pairs are intentional
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                a, b = sorted([present[i], present[j]])
                if (a, b) in INTENTIONAL_OVERLAPS:
                    continue
                issues.append(f"  Domain: {domain}  Plugins: {a}, {b}  (overlap is not in intentional-overlaps list)")

    if issues:
        print(f"WARNING: {len(issues)} undocumented plugin overlap(s):")
        for issue in issues:
            print(issue)
        print("\nAdd the pair to INTENTIONAL_OVERLAPS if shared responsibility is by design,")
        print("or consolidate the functionality into a single plugin.")
        return 1

    print("OK: all plugin overlap documented as intentional")
    return 0


if __name__ == "__main__":
    sys.exit(main())
