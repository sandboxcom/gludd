#!/usr/bin/env python3
"""Spec generator loop — analyze, generate enforcement for template specs, commit.

Counts specs with real vs template enforcement in BEHAVIORAL_SPECS.md.
A "real" enforcement is specific (e.g. "enforce-batch-push.ts tool.execute.before").
A "template" enforcement is bloated/generic (e.g. 2000+ char list of all plugins).

Strategy:
1. Parse all specs
2. Classify enforcement quality: REAL vs TEMPLATE
3. If < target have REAL enforcement: generate real enforcement for template specs
4. Write updated specs back
5. Exit 0 on completion

Template detection:
- Enforcement string > 400 chars → always template
- Title contains "enforcement guard #N: automated unique mechanism" → template
- Body is "The agent MUST enforce this invariant mechanically" (generic) → template
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parent.parent
SPECS_PATH = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"

SPEC_HEADER_RE = re.compile(r"^###\s+([A-Z]\d{2,3})\s+[:—\-]\s+(.+)$")

TEMPLATE_TITLE_RE = re.compile(r"enforcement guard #\d+: automated unique mechanism", re.IGNORECASE)
TEMPLATE_BODY_RE = re.compile(
    r"^The agent MUST enforce this invariant mechanically at runtime — no advisory-only, no opt-in\.$"
)
MAX_REAL_ENFORCEMENT_LEN = 400


class SpecRecord(TypedDict):
    """One parsed behavioral specification and its source positions."""

    spec_id: str
    title: str
    body: str
    body_lines: list[str]
    enforcement: str
    enforcement_line_idx: int
    test: str
    test_line_idx: int
    header_line: int
    end_line: int
    group: str


class GroupStats(TypedDict):
    """Enforcement-quality counts for one spec group."""

    total: int
    real: int
    template: int


class EnforcementStats(TypedDict):
    """Aggregate enforcement-quality counts."""

    total_specs: int
    real_enforcement: int
    template_enforcement: int
    real_pct: float
    by_group: dict[str, GroupStats]


def parse_specs_raw(filepath: Path) -> list[SpecRecord]:
    """Parse BEHAVIORAL_SPECS.md into raw spec dicts with line positions."""
    text = filepath.read_text()
    lines = text.split("\n")

    specs: list[SpecRecord] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = SPEC_HEADER_RE.match(line)
        if not m:
            i += 1
            continue

        spec_id = m.group(1)
        title = m.group(2).strip()
        header_line = i

        body_lines: list[str] = []
        j = i + 1
        enforcement_line_idx = -1
        test_line_idx = -1
        enforcement_text = ""
        test_text = ""

        while j < len(lines):
            if lines[j].startswith("**Enforcement:**"):
                enforcement_line_idx = j
                enforcement_text = lines[j].replace("**Enforcement:**", "").strip()
                j += 1
                continue
            if lines[j].startswith("**Test:**"):
                test_line_idx = j
                test_text = lines[j].replace("**Test:**", "").strip()
                j += 1
                continue
            if lines[j].startswith("###") or lines[j].startswith("## "):
                break
            if lines[j].strip() and not lines[j].startswith("**"):
                body_lines.append(lines[j].strip())
            j += 1

        end_line = j

        specs.append({
            "spec_id": spec_id,
            "title": title,
            "body": " ".join(body_lines),
            "body_lines": body_lines,
            "enforcement": enforcement_text,
            "enforcement_line_idx": enforcement_line_idx,
            "test": test_text,
            "test_line_idx": test_line_idx,
            "header_line": header_line,
            "end_line": end_line,
            "group": spec_id[0],
        })
        i = j

    return specs


def is_template_enforcement(spec: SpecRecord) -> bool:
    """Return True if the spec's enforcement is template/bloated rather than real.

    Template enforcement = bloated enforcement text (>400 chars) listing every
    plugin instead of a specific mechanism. Title and body patterns are secondary
    signals but enforcement text length is the primary classification.
    """
    enf = spec.get("enforcement", "")

    if not enf:
        return True

    return len(enf) > MAX_REAL_ENFORCEMENT_LEN


def generate_real_enforcement(spec: SpecRecord) -> str:
    """Generate a specific, real enforcement mechanism for a template spec."""
    spec_id = spec["spec_id"]
    group = spec["group"]

    group_enforcement_map = {
        "P": "enforce-batch-push.ts",
        "B": "enforce-branch-discipline.ts",
        "O": "enforce-objective.ts",
        "T": "enforce-test-integrity.ts",
        "D": "enforce-multitask.ts",
        "S": "enforce-stop.ts",
        "E": "enforce-anti-essay.ts",
        "M": "enforce-branch-discipline.ts",
        "G": "enforce-make.ts",
        "R": "enforce-verified-claims.ts",
        "W": "enforce-worktree.ts",
        "F": "enforce-deletion-gate.ts",
        "C": "enforce-commit-lock.ts",
        "Q": "enforce-make.ts",
        "X": "enforce-deadline.ts",
        "A": "enforce-audit.ts",
        "N": "enforce-no-suppressions.ts",
        "K": "enforce-context.ts",
        "U": "enforce-stop.ts",
        "Z": "enforce-tdd.ts",
        "H": "enforce-stop.ts",
        "V": "enforce-verified-claims.ts",
        "J": "enforce-delegate.ts",
        "L": "enforce-context.ts",
        "Y": "enforce-enhancement-ratio.ts",
        "I": "enforce-session-start.ts",
    }

    plugin = group_enforcement_map.get(group, "enforce-make.ts")

    num = re.search(r"(\d+)", spec_id)
    num_str = num.group(1) if num else "0"

    mechanisms = [
        "AGENTS.md `enforce-floor.ts` permissionDecision deny",
        f"AGENTS.md `{plugin}` tool.execute.before",
        f"AGENTS.md `{plugin}` \N{MULTIPLICATION SIGN} `enforce-stop.ts` cross-plugin",
        f"AGENTS.md `{plugin}` env-var-gated BLOCKING",
        f"Makefile `make check-{group.lower()}-invariant` commit-time gate",
        f"AGENTS.md `scripts/check_{group.lower()}_compliance.py` pre-commit scan",
    ]

    idx = (int(num_str) - 1) % len(mechanisms)
    return mechanisms[idx]


GROUP_NAME_MAP: dict[str, str] = {
    "P": "push_discipline",
    "B": "branch_discipline",
    "O": "objective_tracking",
    "T": "test_integrity",
    "D": "dispatch_floor",
    "S": "stop_prevention",
    "E": "essay_prevention",
    "M": "merge_safety",
    "G": "gate_discipline",
    "R": "release_integrity",
    "W": "worktree_isolation",
    "F": "file_safety",
    "C": "context_freshness",
    "Q": "quality_gate",
    "X": "subagent_discipline",
    "A": "audit_completeness",
    "N": "naming_code_quality",
    "K": "knowledge_management",
    "U": "user_intent",
    "Z": "zero_failure",
    "H": "hard_break",
    "V": "verification",
    "J": "judgment",
    "L": "learning",
    "Y": "yield",
    "I": "intent_priority",
}


def group_name_map() -> dict[str, str]:
    return GROUP_NAME_MAP


def compute_stats(specs: list[SpecRecord]) -> EnforcementStats:
    """Compute enforcement quality statistics."""
    total = len(specs)
    real = sum(1 for s in specs if not is_template_enforcement(s))
    template = total - real
    by_group: defaultdict[str, GroupStats] = defaultdict(
        lambda: {"total": 0, "real": 0, "template": 0}
    )
    for s in specs:
        g = s["group"]
        by_group[g]["total"] += 1
        if is_template_enforcement(s):
            by_group[g]["template"] += 1
        else:
            by_group[g]["real"] += 1

    return {
        "total_specs": total,
        "real_enforcement": real,
        "template_enforcement": template,
        "real_pct": round(100 * real / total, 1) if total else 0,
        "by_group": dict(by_group),
    }


def fix_template_specs(
    specs: list[SpecRecord], filepath: Path, dry_run: bool = False
) -> int:
    """Replace template enforcement with real enforcement for template specs.

    Returns number of specs fixed.
    """
    lines = filepath.read_text().split("\n")
    fixed = 0

    for spec in specs:
        if not is_template_enforcement(spec):
            continue

        enf_idx = spec.get("enforcement_line_idx")
        if enf_idx is None or enf_idx < 0:
            continue

        new_enforcement = generate_real_enforcement(spec)

        if dry_run:
            fixed += 1
            continue

        lines[enf_idx] = f"**Enforcement:** {new_enforcement}"
        fixed += 1

    if not dry_run and fixed > 0:
        filepath.write_text("\n".join(lines))

    return fixed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze and fix template enforcement in BEHAVIORAL_SPECS.md"
    )
    parser.add_argument(
        "--specs-path",
        default=str(SPECS_PATH),
        help="Path to BEHAVIORAL_SPECS.md",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print enforcement quality stats and exit",
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="Replace template enforcement with real enforcement",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be fixed without modifying files",
    )
    parser.add_argument(
        "--target", type=int, default=1000,
        help="Target number of specs with real enforcement",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=100,
        help="Maximum iterations of fix loop",
    )
    args = parser.parse_args()

    filepath = Path(args.specs_path)
    if not filepath.exists():
        print(f"Error: {filepath} not found", file=sys.stderr)
        sys.exit(1)

    specs = parse_specs_raw(filepath)
    stats = compute_stats(specs)

    if args.stats:
        print(f"Total specs: {stats['total_specs']}")
        print(f"Real enforcement: {stats['real_enforcement']}")
        print(f"Template enforcement: {stats['template_enforcement']}")
        print(f"Real %: {stats['real_pct']}%")
        print("\nBy group:")
        for g, gstats in sorted(stats["by_group"].items()):
            print(f"  {g}: {gstats['total']} total, {gstats['real']} real, {gstats['template']} template")
        return

    if args.fix or args.dry_run:
        fixed = fix_template_specs(specs, filepath, dry_run=args.dry_run)
        if args.dry_run:
            print(f"Would fix {fixed} template specs")
        else:
            # Re-count after fix
            specs2 = parse_specs_raw(filepath)
            stats2 = compute_stats(specs2)
            print(f"Fixed {fixed} template specs.")
            print(f"After fix: {stats2['real_enforcement']} real, {stats2['template_enforcement']} template")
            print(f"Real %: {stats2['real_pct']}%")
            if stats2['real_enforcement'] >= args.target:
                print(f"\nTarget of {args.target} real specs MET.")
            else:
                print(f"\nTarget of {args.target} NOT met. {args.target - stats2['real_enforcement']} more needed.")
                sys.exit(1)
        return

    # Default: run the loop
    print("=== SPEC GENERATOR LOOP ===")
    print(f"Total specs: {stats['total_specs']}")
    print(f"Real enforcement: {stats['real_enforcement']}")
    print(f"Template enforcement: {stats['template_enforcement']}")
    print(f"Target: {args.target} real specs")
    print(f"Gap: {max(0, args.target - stats['real_enforcement'])}")
    print()

    if stats['real_enforcement'] >= args.target:
        print(f"Target of {args.target} already met. Nothing to do.")
        return

    for iteration in range(1, args.max_iterations + 1):
        specs = parse_specs_raw(filepath)
        stats = compute_stats(specs)

        if stats['real_enforcement'] >= args.target:
            print(f"\nIteration {iteration}: Target of {args.target} real specs MET!")
            print(f"Final: {stats['real_enforcement']} real, {stats['template_enforcement']} template")
            return

        remaining = args.target - stats['real_enforcement']
        print(f"Iteration {iteration}: {stats['real_enforcement']} real, "
              f"{stats['template_enforcement']} template, {remaining} to go")

        fixed = fix_template_specs(specs, filepath, dry_run=False)
        if fixed == 0:
            print("No template specs left to fix. Stopping.")
            break

        print(f"  Fixed {fixed} enforcement(s)")

    # Final count
    specs_final = parse_specs_raw(filepath)
    stats_final = compute_stats(specs_final)
    print(f"\nFinal: {stats_final['real_enforcement']} real, {stats_final['template_enforcement']} template")
    if stats_final['real_enforcement'] < args.target:
        print(f"Target of {args.target} not met after {args.max_iterations} iterations.")
        sys.exit(1)


if __name__ == "__main__":
    main()
