#!/usr/bin/env python3
"""Validate the documented, variable-aware Make target contract.

The contract is intentionally data-driven so agent prompts and behavioral tests
can use one source of truth for target names, variables, and safe examples.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

TARGET_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):")


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("targets"), list):
        raise ValueError("contract must contain a targets list")
    return payload


def _stanzas(makefile: str) -> dict[str, str]:
    lines = makefile.splitlines()
    result: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in [*lines, ""]:
        match = TARGET_RE.match(line)
        if match:
            if current is not None:
                result[current] = "\n".join(body)
            current = match.group(1)
            body = [line]
        elif current is not None:
            if line.startswith(("\t", " ")) or not line.strip():
                body.append(line)
            else:
                result[current] = "\n".join(body)
                current = None
                body = []
    return result


def _help_lines(makefile: str) -> list[str]:
    return [line for line in makefile.splitlines() if "@echo" in line and "  " in line]


def validate_contract(makefile_path: Path, contract: dict[str, Any]) -> list[str]:
    makefile = makefile_path.read_text(encoding="utf-8")
    stanzas = _stanzas(makefile)
    help_lines = _help_lines(makefile)
    errors: list[str] = []
    seen: set[str] = set()
    for entry in contract["targets"]:
        if not isinstance(entry, dict):
            errors.append("target entry must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            errors.append("target entry is missing a name")
            continue
        if name in seen:
            errors.append(f"{name}: duplicated in contract")
        seen.add(name)
        stanza = stanzas.get(name)
        if stanza is None:
            errors.append(f"{name}: target is missing from Makefile")
            continue
        matching_help = [line for line in help_lines if name in line]
        if not matching_help and not name.startswith("_"):
            # Internal targets (underscore-prefixed) must NOT appear in
            # `make help` (test_makefile_targets_deep pins that convention);
            # only agent-facing targets require a help entry.
            errors.append(f"{name}: target is missing from make help")
        variables = entry.get("make_variables", [])
        if not isinstance(variables, list):
            errors.append(f"{name}: make_variables must be a list")
            variables = []
        behavior = entry.get("behavior")
        if not isinstance(behavior, str) or not behavior.startswith(f"make {name}"):
            errors.append(f"{name}: behavior must start with 'make {name}'")
        for variable in variables:
            if not isinstance(variable, str):
                errors.append(f"{name}: variable names must be strings")
                continue
            references = (f"$({variable})", f"${{{variable}}}", f"{variable}=", variable)
            if not any(reference in stanza for reference in references):
                errors.append(f"{name}: Makefile does not reference {variable}")
            if isinstance(behavior, str) and variable not in behavior:
                errors.append(f"{name}: behavior does not demonstrate {variable}")
        for environment_variable in entry.get("environment_variables", []):
            if not isinstance(environment_variable, str) or (
                isinstance(behavior, str) and environment_variable not in behavior
            ):
                errors.append(f"{name}: behavior does not demonstrate {environment_variable}")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    root = Path(__file__).resolve().parent.parent
    makefile = root / "Makefile"
    contract_path = root / "config/make_target_contract.json"
    if args:
        contract_path = Path(args[0])
    try:
        errors = validate_contract(makefile, load_contract(contract_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"make-target-contract: ERROR: {exc}")
        return 1
    if errors:
        print("make-target-contract: FAIL")
        print("\n".join(f"  - {error}" for error in errors))
        return 1
    print(f"make-target-contract: PASS ({len(load_contract(contract_path)['targets'])} targets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
