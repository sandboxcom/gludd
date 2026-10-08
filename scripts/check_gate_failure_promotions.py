#!/usr/bin/env python3
"""Validate owned deterministic failure nodes promoted into fast admission."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, cast

from jsonschema import Draft202012Validator

from scripts.makefile_layout import MakefileLayoutError, compose_makefile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "config" / "gate_failure_promotions.json"
MAX_MANIFEST_BYTES = 256_000
TARGET_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):(?:[^\n]*)$", re.MULTILINE)
MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "admission_target",
        "full_gate_target",
        "full_gate_required",
        "runtime_contract",
        "families",
    ],
    "properties": {
        "schema_version": {"const": 1},
        "admission_target": {"type": "string", "pattern": "^[A-Za-z0-9_.-]+$"},
        "full_gate_target": {"type": "string", "pattern": "^[A-Za-z0-9_.-]+$"},
        "full_gate_required": {"const": True},
        "runtime_contract": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "observer",
                "evidence_root",
                "label_prefix",
                "heartbeat_seconds",
                "max_retained_runs",
                "phases",
            ],
            "properties": {
                "observer": {"const": "scripts/stream_command.py"},
                "evidence_root": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9_./-]+$",
                },
                "label_prefix": {
                    "type": "string",
                    "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*-$",
                },
                "heartbeat_seconds": {"type": "integer", "minimum": 1},
                "max_retained_runs": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                },
                "phases": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "name",
                            "target",
                            "budget_class",
                            "max_seconds",
                            "quiet_seconds",
                        ],
                        "properties": {
                            "name": {
                                "type": "string",
                                "pattern": "^_?[a-z0-9]+(?:-[a-z0-9]+)*$",
                            },
                            "target": {
                                "type": "string",
                                "pattern": "^_?[A-Za-z0-9]+(?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$",
                            },
                            "budget_class": {
                                "enum": ["fast", "standard", "slow"]
                            },
                            "max_seconds": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 900,
                            },
                            "quiet_seconds": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 900,
                            },
                        },
                    },
                },
            },
        },
        "families": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "owner_target",
                    "node_kind",
                    "node",
                    "full_gate_phase",
                ],
                "properties": {
                    "id": {
                        "type": "string",
                        "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                    },
                    "owner_target": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9_.-]+$",
                    },
                    "node_kind": {
                        "enum": ["make_target", "pytest_node"],
                    },
                    "node": {"type": "string", "minLength": 1, "maxLength": 512},
                    "full_gate_phase": {"enum": ["preflights", "test"]},
                },
            },
        },
    },
}


def _schema_errors(payload: object) -> list[str]:
    errors: list[str] = []
    for error in sorted(
        Draft202012Validator(MANIFEST_SCHEMA).iter_errors(payload),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    ):
        location = ".".join(str(part) for part in error.absolute_path) or "manifest"
        errors.append(f"{location}: {error.message}")
    return errors


def _target_stanzas(makefile: str) -> dict[str, str]:
    matches = list(TARGET_RE.finditer(makefile))
    return {
        match.group(1): makefile[
            match.start() : matches[index + 1].start()
            if index + 1 < len(matches)
            else len(makefile)
        ]
        for index, match in enumerate(matches)
    }


def _preflight_targets(makefile: str) -> set[str]:
    lines = makefile.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("GATE_PREFLIGHT_TARGETS"):
            continue
        _, _, first = line.partition(":=")
        values = first.removesuffix("\\").split()
        while line.rstrip().endswith("\\") and index + 1 < len(lines):
            index += 1
            line = lines[index]
            values.extend(line.removesuffix("\\").split())
        return set(values)
    return set()


def _pytest_nodes(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return set()
    nodes: set[str] = set()

    def collect(body: list[ast.stmt], prefix: tuple[str, ...] = ()) -> None:
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                collect(statement.body, (*prefix, statement.name))
            elif isinstance(statement, (ast.AsyncFunctionDef, ast.FunctionDef)):
                nodes.add("::".join((*prefix, statement.name)))

    collect(tree.body)
    return nodes


def _pytest_node_exists(node: str, repository_root: Path) -> bool:
    file_name, separator, raw_node = node.partition("::")
    relative = PurePosixPath(file_name)
    if (
        not separator
        or not raw_node
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.suffix != ".py"
        or not relative.parts
        or relative.parts[0] != "tests"
    ):
        return False
    unparameterized = raw_node.split("[", maxsplit=1)[0]
    return unparameterized in _pytest_nodes(repository_root / relative)


def _runtime_contract_errors(
    runtime: dict[str, Any], *, admission: str, stanzas: dict[str, str]
) -> list[str]:
    """Return deterministic observer, budget, and phase-wiring violations."""
    errors: list[str] = []
    normalized_admission = " ".join(admission.replace("\\\n", " ").split())
    observer = cast(str, runtime["observer"])
    evidence_root = cast(str, runtime["evidence_root"])
    label_prefix = cast(str, runtime["label_prefix"])
    heartbeat_seconds = cast(int, runtime["heartbeat_seconds"])
    max_retained_runs = cast(int, runtime["max_retained_runs"])
    observer_fragments = (
        f"$(UV) run python {observer}",
        f'--root "{evidence_root}"',
        f'--label "{label_prefix}$$phase"',
        '--run-id "$$run_id"',
        f'--heartbeat-secs "{heartbeat_seconds}"',
        '--quiet-secs "$$quiet_seconds"',
        '--max-secs "$$max_seconds"',
        f'--retain-runs "{max_retained_runs}"',
        '"kind":"integration_admission_phase"',
        '"evidence_label":"%s%s"',
        "set -eu",
    )
    if any(fragment not in normalized_admission for fragment in observer_fragments):
        errors.append("observer contract is absent from integration admission")

    seen_names: set[str] = set()
    seen_targets: set[str] = set()
    phase_positions: list[int] = []
    has_slow_phase = False
    phases = cast(list[dict[str, Any]], runtime["phases"])
    for index, phase in enumerate(phases):
        label = f"runtime_contract.phases[{index}]"
        name = cast(str, phase["name"])
        target = cast(str, phase["target"])
        budget_class = cast(str, phase["budget_class"])
        max_seconds = cast(int, phase["max_seconds"])
        quiet_seconds = cast(int, phase["quiet_seconds"])
        if name in seen_names:
            errors.append(f"{label}: duplicate runtime phase name: {name}")
        seen_names.add(name)
        if target in seen_targets:
            errors.append(f"{label}: duplicate runtime phase target: {target}")
        seen_targets.add(target)
        if target not in stanzas:
            errors.append(f"{label}: runtime target is not defined: {target}")
        if quiet_seconds > max_seconds:
            errors.append(f"{label}: quiet_seconds must not exceed max_seconds")
        has_slow_phase = has_slow_phase or budget_class == "slow"
        marker = (
            f'run_phase "{name}" "{target}" "{budget_class}" '
            f'"{max_seconds}" "{quiet_seconds}" '
            f"$(MAKE) --no-print-directory {target}"
        )
        position = normalized_admission.find(marker)
        if position < 0:
            errors.append(
                f"{label}: runtime phase is not wired with its exact budget: {name}"
            )
        else:
            phase_positions.append(position)
    if not has_slow_phase:
        errors.append("runtime contract must classify at least one slow phase")
    if phase_positions != sorted(phase_positions):
        errors.append("runtime phases are not wired in manifest order")
    return errors


def validate_manifest(payload: object, *, repository_root: Path) -> list[str]:
    """Return every promotion-contract violation; only an empty list passes."""
    errors = _schema_errors(payload)
    if errors:
        return errors

    manifest = cast(dict[str, Any], payload)
    try:
        makefile = compose_makefile(repository_root / "Makefile")
    except (MakefileLayoutError, OSError) as exc:
        return [f"Makefile composition failed: {exc}"]

    stanzas = _target_stanzas(makefile)
    admission_target = cast(str, manifest["admission_target"])
    full_gate_target = cast(str, manifest["full_gate_target"])
    admission = stanzas.get(admission_target, "")
    full_gate = stanzas.get(full_gate_target, "")
    if not admission:
        errors.append(f"admission target is not defined: {admission_target}")
    if not full_gate:
        errors.append(f"full gate target is not defined: {full_gate_target}")
    if admission_target == full_gate_target:
        errors.append("admission target must remain separate from the full gate")
    if "$(MAKE) --no-print-directory check-gate-failure-promotions" not in admission:
        errors.append("integration admission does not invoke the promotion checker")
    if f"$(MAKE) --no-print-directory {full_gate_target}" in admission:
        errors.append("integration admission must not invoke the full gate")

    runtime = cast(dict[str, Any], manifest["runtime_contract"])
    errors.extend(
        _runtime_contract_errors(runtime, admission=admission, stanzas=stanzas)
    )

    preflights = _preflight_targets(makefile)
    seen_ids: set[str] = set()
    seen_nodes: set[str] = set()
    families = cast(list[dict[str, str]], manifest["families"])
    for index, family in enumerate(families):
        label = f"families[{index}]"
        family_id = family["id"]
        owner_target = family["owner_target"]
        node_kind = family["node_kind"]
        node = family["node"]
        full_gate_phase = family["full_gate_phase"]
        if family_id in seen_ids:
            errors.append(f"{label}: duplicate family id: {family_id}")
        seen_ids.add(family_id)
        if node in seen_nodes:
            errors.append(f"{label}: duplicate promoted node: {node}")
        seen_nodes.add(node)

        owner = stanzas.get(owner_target, "")
        if not owner:
            errors.append(f"{label}: owner target is not defined: {owner_target}")
        if f"$(MAKE) --no-print-directory {owner_target}" not in admission:
            errors.append(
                f"{label}: owner target is not wired into {admission_target}: "
                f"{owner_target}"
            )
        marker = f"=== GATE PHASE: {full_gate_phase} ==="
        if marker not in full_gate:
            errors.append(f"{label}: full gate phase is not defined: {full_gate_phase}")

        if node_kind == "make_target":
            if node != owner_target:
                errors.append(f"{label}: make node must equal its owner target")
            if owner_target not in preflights:
                errors.append(f"{label}: make owner is absent from gate preflights")
        else:
            if node not in owner:
                errors.append(f"{label}: pytest node is absent from its owner target")
            if not _pytest_node_exists(node, repository_root):
                errors.append(f"{label}: pytest node does not exist: {node}")
            if "scripts/run_gate.sh" not in full_gate:
                errors.append(f"{label}: full gate test runner is not wired")
    return errors


def _load_manifest(path: Path) -> object:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds the 256000-byte limit")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    repository_root = args.repository_root.resolve()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = repository_root / manifest_path
    try:
        payload = _load_manifest(manifest_path)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"gate-failure-promotions: ERROR cannot load manifest: {exc}", file=sys.stderr)
        return 1
    errors = validate_manifest(payload, repository_root=repository_root)
    if errors:
        for error in errors:
            print(f"gate-failure-promotions: ERROR {error}", file=sys.stderr)
        return 1
    manifest = cast(dict[str, Any], payload)
    family_count = len(manifest["families"])
    runtime = cast(dict[str, Any], manifest["runtime_contract"])
    runtime_phases = cast(list[dict[str, Any]], runtime["phases"])
    runtime_ceiling = sum(cast(int, phase["max_seconds"]) for phase in runtime_phases)
    print(
        "gate-failure-promotions: PASS "
        f"families={family_count} admission={manifest['admission_target']} "
        f"runtime_phases={len(runtime_phases)} "
        f"runtime_ceiling_seconds={runtime_ceiling} "
        f"full_gate={manifest['full_gate_target']} required=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
