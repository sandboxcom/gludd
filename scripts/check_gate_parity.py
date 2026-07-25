#!/usr/bin/env python3
"""
check_gate_parity.py

Verifies that local `gate-refresh` runs the SAME phases as the CI gate job in
`.github/workflows/build.yml`. Extracts CI phases from the workflow YAML and
local phases from the Makefile gate-refresh recipe. Exits non-zero if any CI
phase lacks a local equivalent.

Usage:
    python3 scripts/check_gate_parity.py [--ci WORKFLOW_PATH] [--makefile MAKEFILE_PATH]

Exit codes:
    0 — All CI gate phases have local equivalents.
    1 — Divergence detected (see stderr for missing phases).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Step-name → canonical phase name patterns.
# Each (regex, canonical_name) pair is tested against CI gate step names/runs.
STEP_PHASE_PATTERNS: list[tuple[str, str]] = [
    (r"hot.reload.*build", "hot-reload"),
    (r"hot.reload.*fresh", "verify-hot-reload"),
    (r"Enforcement.*runtime", "verify-enforcement"),
    (r"feature.claims", "verify-feature-claims"),
    (r"status.table", "check-status-table"),
]

# `make <target>` names that map to canonical gate phases.
MAKE_TARGET_TO_PHASE: dict[str, str] = {
    "lint": "lint",
    "typecheck": "typecheck",
    "test-count": "collect",
    "smoke": "smoke",
}


def _extract_ci_gate_section(text: str) -> str:
    """Return only the gate job block from a CI workflow YAML."""
    lines = text.splitlines()
    gate_lines: list[str] = []
    in_gate = False
    indent = ""
    for line in lines:
        if not in_gate:
            m = re.match(r"^(\s*)gate:", line)
            if m:
                in_gate = True
                indent = m.group(1)
            continue
        # A new key at the same indent as 'gate' means a sibling job
        m = re.match(rf"^{indent}([a-z])", line)
        # A sibling job at the gate indentation ends the gate block.
        if m and m.group(1) != "g" and not line.startswith(indent + " "):
            break
        gate_lines.append(line)
    return "\n".join(gate_lines)


def extract_ci_phases(workflow_path: Path) -> set[str]:
    """Extract gate job phase names from the CI workflow YAML."""
    text = workflow_path.read_text(encoding="utf-8")
    gate_section = _extract_ci_gate_section(text)

    phases: set[str] = set()

    # Phase 1: extract make targets from run blocks
    for match in re.finditer(r"\bmake\s+((?:[a-z][a-z0-9_-]*\s*)+)", gate_section, re.IGNORECASE):
        targets_str = match.group(1)
        for t in targets_str.split():
            t = t.strip().rstrip("\\")
            if t in MAKE_TARGET_TO_PHASE:
                phases.add(MAKE_TARGET_TO_PHASE[t])

    # Phase 2: detect phases from step names
    for pattern, phase_name in STEP_PHASE_PATTERNS:
        if re.search(pattern, gate_section, re.IGNORECASE):
            phases.add(phase_name)

    # Phase 3: detect explicit make targets used as standalone step cmds
    for target, phase in [
        ("hot-reload-plugins", "hot-reload"),
        ("check-hot-reload-fresh", "verify-hot-reload"),
        ("verify-enforcement", "verify-enforcement"),
        ("check-status-table", "check-status-table"),
    ]:
        if re.search(rf"\b{target}\b", gate_section):
            phases.add(phase)

    return phases


def extract_local_phases(makefile_path: Path) -> list[str]:
    """Extract phase names from `=== GATE PHASE:` markers in gate-refresh recipe."""
    text = makefile_path.read_text(encoding="utf-8")
    phases: list[str] = []

    in_target = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "gate-refresh:" or stripped.startswith("gate-refresh:"):
            in_target = True
            continue
        if in_target:
            # Recipe ends when we hit a non-indented, non-empty, non-comment line
            # that looks like a new target
            if (
                stripped
                and not line.startswith("\t")
                and not line.startswith("    ")
                and re.match(r"^[a-zA-Z_][a-zA-Z0-9_.-]*:", stripped)
            ):
                break
            m = re.search(r"=== GATE(?:-REFRESH)? PHASE:\s*(\S+)", line)
            if m:
                phases.append(m.group(1))

    return phases


def ci_to_local_phase_names(ci_phases: set[str]) -> set[str]:
    """Translate CI phase names to their equivalent local gate phase names."""
    ci_to_local = {"verify-enforcement": "hook-runtime"}
    return {ci_to_local.get(phase, phase) for phase in ci_phases}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check gate parity between CI and local.")
    parser.add_argument("--ci", type=Path, help="Path to CI workflow YAML")
    parser.add_argument("--makefile", type=Path, help="Path to Makefile")
    args = parser.parse_args()

    ci_path = args.ci or REPO / ".github" / "workflows" / "build.yml"
    makefile_path = args.makefile or REPO / "Makefile"

    if not ci_path.is_file():
        print(f"ERROR: CI workflow not found: {ci_path}", file=sys.stderr)
        return 2
    if not makefile_path.is_file():
        print(f"ERROR: Makefile not found: {makefile_path}", file=sys.stderr)
        return 2

    ci_phases = extract_ci_phases(ci_path)
    local_phases = extract_local_phases(makefile_path)
    local_set = set(local_phases)

    # CI-to-local name mapping: CI phase name → local phase name
    # Some phases have different names in local gate-refresh.
    ci_to_local: dict[str, str] = {"verify-enforcement": "hook-runtime"}

    # Some CI phases are intentionally not run locally (too expensive, CI-specific)
    known_exclusions: set[str] = set()

    missing: list[str] = []
    for ci_phase in sorted(ci_phases):
        if ci_phase in known_exclusions:
            continue
        local_name = ci_to_local.get(ci_phase, ci_phase)
        if local_name not in local_set:
            missing.append(ci_phase)

    if missing:
        print("GATE PARITY FAILURE:", file=sys.stderr)
        print("  CI phases missing from local gate-refresh:", file=sys.stderr)
        for phase in missing:
            print(f"    - {phase}", file=sys.stderr)
        print(f"\n  CI phases:  {sorted(ci_phases)}", file=sys.stderr)
        print(f"  Local phases: {local_phases}", file=sys.stderr)
        print(f"\n  CI→local map: {ci_to_local}", file=sys.stderr)
        return 1

    extra_local = local_set - ci_to_local_phase_names(ci_phases)
    msg = f"GATE PARITY: OK — {len(ci_phases)} CI phases, {len(local_phases)} local phases"
    if extra_local:
        msg += f" (local-only: {sorted(extra_local)})"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
