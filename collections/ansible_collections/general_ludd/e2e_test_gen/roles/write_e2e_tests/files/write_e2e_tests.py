#!/usr/bin/env python3
"""write_e2e_tests — generate pytest test files from validated E2E scenarios.

Usage:
    python write_e2e_tests.py --scenarios-file <json> --output-dir <dir> [--manifest <file>]

Reads validated_scenarios.json, emits pytest test files using project fixtures
(TestClient, _run_cli, tmp_path) with AAA structure.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


def _sanitize_filename(name: str) -> str:
    return name.replace("/", "_").replace("<", "").replace(">", "").replace(" ", "_").replace(".", "_").strip("_")


def _to_assert_expr(raw: str) -> str | None:
    """Convert an assertion string to a valid Python expression, or None.

    ``"status == 201"`` -> ``"status == 201"``
    ``"result is not None"`` -> ``"result is not None"``
    ``"body has id"`` -> None (not a valid expression; emitted as a TODO comment)
    """
    raw = raw.strip()
    if raw.lower().startswith("assert "):
        raw = raw[7:].strip()
    if not raw:
        return None
    try:
        ast.parse(raw, mode="eval")
    except SyntaxError:
        return None
    return raw


def _fixture_sig_for_scenario(
    scenario_name: str,
) -> tuple[str, list[str]]:
    """Return ``(fixture_signature, imports_extra)`` for a scenario name.

    - crud_lifecycle / auth_flow → uses ``test_client`` (FastAPI TestClient).
    - daemon_restart → uses ``_run_cli`` (CLI runner fixture).
    - Others → uses ``tmp_path`` (standard pytest fixture).
    """
    api_scenarios = {"crud_lifecycle", "auth_flow", "timeout_handling", "concurrent_edits"}
    cli_scenarios = {"daemon_restart"}
    if scenario_name in api_scenarios:
        return ("test_client", ["from tests.conftest import test_client  # noqa: E402"])
    if scenario_name in cli_scenarios:
        return ("_run_cli", ["from tests.conftest import _run_cli  # noqa: E402"])
    return ("tmp_path", [])


def _emit_test_file(scenario: dict, output_dir: Path, prefix: str, module: str = "") -> dict:
    name = scenario["name"]
    filename = f"{prefix}{_sanitize_filename(name)}.py"
    filepath = output_dir / filename
    steps = scenario.get("steps", [])
    coverage_targets = scenario.get("coverage_targets", [])

    mod_stem = Path(module).stem if module else "target_module"

    fixture_sig, extra_imports = _fixture_sig_for_scenario(name)

    lines: list[str] = ["import pytest", ""]

    if coverage_targets:
        target_imports = ", ".join(coverage_targets)
        lines.append(f"# coverage target imports from {mod_stem}")
        lines.append(f"from {mod_stem} import {target_imports}")
        lines.append("")
    lines.extend(extra_imports)
    if extra_imports:
        lines.append("")
    lines.append("")

    def _emit_step(step: dict, idx: int) -> None:
        action_raw = step.get("action", "invoke")
        action = action_raw.lower().replace(" ", "_")
        target = _sanitize_filename(step.get("target", "unknown"))
        func_name = f"test_{_sanitize_filename(name)}_{action}_{target}_{idx}"
        exp = step.get("expected_result", "")
        assertions = step.get("assertions", [])

        lines.append(f"def {func_name}({fixture_sig}):")
        lines.append(f'    """{exp}"""')
        # AAA structure — Arrange / Act / Assert
        lines.append("    # Arrange")
        lines.append("    # Act")
        if coverage_targets:
            lines.append(f"    result = {coverage_targets[0]}()  # invoke coverage target")
        else:
            lines.append("    result = None  # no coverage target to invoke")
        lines.append("    # Assert")
        emitted_any = False
        for a in assertions:
            expr = _to_assert_expr(a)
            if expr is not None:
                lines.append(f"    assert {expr}")
                emitted_any = True
            else:
                lines.append(f"    # TODO: {a}  # not a valid Python expression")
        if not emitted_any:
            lines.append("    assert True  # no machine-checkable assertions in scenario")
        lines.append("")

    for idx, step in enumerate(steps):
        _emit_step(step, idx)

    content = "\n".join(lines)
    with open(filepath, "w") as fh:
        fh.write(content)

    return {"file": str(filepath), "scenario": name, "step_count": len(steps), "fixture": fixture_sig}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate pytest test files from validated E2E scenarios")
    parser.add_argument("--scenarios-file", required=True, help="Path to validated_scenarios.json")
    parser.add_argument("--output-dir", required=True, help="Directory for generated test files")
    parser.add_argument("--manifest", help="Path for generated_tests.json manifest")
    parser.add_argument("--test-file-prefix", default="test_e2e_generated_", help="Prefix for test file names")

    args = parser.parse_args()

    with open(args.scenarios_file) as f:
        data = json.load(f)

    valid = data.get("valid", data.get("scenarios", []))
    module_name = data.get("module", "")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"test_files": [], "scenario_count": len(valid)}
    for scenario in valid:
        info = _emit_test_file(scenario, output_dir, args.test_file_prefix, module=module_name)
        manifest["test_files"].append(info)

    manifest_path = Path(args.manifest) if args.manifest else output_dir / "generated_tests.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)

    print(json.dumps({"test_files": len(manifest["test_files"]), "manifest": str(manifest_path)}))


if __name__ == "__main__":
    main()
