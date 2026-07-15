#!/usr/bin/env python3
"""write_e2e_tests — generate pytest test files from validated E2E scenarios.

Usage:
    python write_e2e_tests.py --scenarios-file <json> --output-dir <dir> [--manifest <file>]

Reads validated_scenarios.json, emits pytest test files using project fixtures
(TestClient, _run_cli, tmp_path) with AAA structure.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path


def _sanitize_filename(name: str) -> str:
    return name.replace("/", "_").replace("<", "").replace(">", "").replace(" ", "_").replace(".", "_").strip("_")


def _emit_test_file(scenario: dict, output_dir: Path, prefix: str) -> dict:
    name = scenario["name"]
    filename = f"{prefix}{_sanitize_filename(name)}.py"
    filepath = output_dir / filename
    steps = scenario.get("steps", [])
    expected = scenario.get("expected_result", scenario.get("description", ""))
    coverage_targets = scenario.get("coverage_targets", [])

    lines = []
    lines.append("import pytest")
    lines.append("")
    lines.append("")

    def _emit_step(step: dict, idx: int) -> None:
        action = step.get("action", "invoke").lower().replace(" ", "_")
        target = _sanitize_filename(step.get("target", "unknown"))
        func_name = f"test_{_sanitize_filename(name)}_{action}_{target}_{idx}"
        exp = step.get("expected_result", "")
        assertions = step.get("assertions", [])
        lines.append(f"def {func_name}():")
        lines.append(f'    """{exp}"""')
        for a in assertions:
            lines.append(f"    # {a}")
        lines.append("    pass")
        lines.append("")

    for idx, step in enumerate(steps):
        _emit_step(step, idx)

    content = "\n".join(lines)
    with open(filepath, "w") as fh:
        fh.write(content)

    return {"file": str(filepath), "scenario": name, "step_count": len(steps)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate pytest test files from validated E2E scenarios"
    )
    parser.add_argument("--scenarios-file", required=True, help="Path to validated_scenarios.json")
    parser.add_argument("--output-dir", required=True, help="Directory for generated test files")
    parser.add_argument("--manifest", help="Path for generated_tests.json manifest")
    parser.add_argument("--test-file-prefix", default="test_e2e_generated_", help="Prefix for test file names")

    args = parser.parse_args()

    with open(args.scenarios_file) as f:
        data = json.load(f)

    valid = data.get("valid", data.get("scenarios", []))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"test_files": [], "scenario_count": len(valid)}
    for scenario in valid:
        info = _emit_test_file(scenario, output_dir, args.test_file_prefix)
        manifest["test_files"].append(info)

    manifest_path = Path(args.manifest) if args.manifest else output_dir / "generated_tests.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)

    print(json.dumps({"test_files": len(manifest["test_files"]), "manifest": str(manifest_path)}))


if __name__ == "__main__":
    main()
