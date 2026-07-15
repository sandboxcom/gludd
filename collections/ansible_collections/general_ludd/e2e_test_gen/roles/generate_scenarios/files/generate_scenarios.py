#!/usr/bin/env python3
"""generate_scenarios — map ModuleSymbols JSON to E2E test scenarios.

Usage:
    python generate_scenarios.py --symbols-file <json> --output <json>
    python generate_scenarios.py --target-module <path.py> --output <json>

Reads module symbols from a JSON file (or analyzes a target module directly),
invokes ScenarioGenerator, and writes a scenarios JSON artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ensure_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent.parent.parent
    src_dir = str(repo_root / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate E2E test scenarios from module symbols"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbols-file", help="Path to module_symbols.json artifact")
    group.add_argument("--target-module", help="Path to Python source file to analyze")
    parser.add_argument("--output", required=True, help="Path for output scenarios.json")

    args = parser.parse_args()

    _ensure_path()

    if args.symbols_file:
        with open(args.symbols_file) as f:
            raw = json.load(f)
        mod_syms = _reconstruct_module_symbols(raw)
    else:
        from general_ludd.agents.test_generation.code_path_analyzer import CodePathAnalyzer

        analyzer = CodePathAnalyzer()
        mod_syms = analyzer.analyze(args.target_module)

    from general_ludd.agents.test_generation.scenario_generator import ScenarioGenerator

    gen = ScenarioGenerator()
    scenarios = gen.generate(mod_syms)

    output = {
        "module": mod_syms.name,
        "path": args.target_module or raw.get("path", ""),
        "scenarios": [
            {
                "name": s.name,
                "description": s.description,
                "steps": [
                    {
                        "action": st.action,
                        "target": st.target,
                        "expected_result": st.expected_result,
                        "assertions": st.assertions,
                    }
                    for st in s.steps
                ],
                "coverage_targets": s.coverage_targets,
            }
            for s in scenarios
        ],
        "scenario_count": len(scenarios),
        "coverage_targets": sorted(
            {t for s in scenarios for t in s.coverage_targets}
        ),
        "status": "completed",
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(json.dumps({"scenario_count": len(scenarios), "output": str(out_path)}))


def _reconstruct_module_symbols(raw: dict):
    """Reconstruct a ModuleSymbols namedtuple from a JSON dict."""
    from general_ludd.agents.test_generation.code_path_analyzer import (
        ClassSymbol,
        ModuleSymbols,
        Symbol,
    )

    functions = [Symbol(**f) for f in raw.get("functions", [])]
    classes = []
    for c in raw.get("classes", []):
        c_copy = dict(c)
        methods_raw = c_copy.pop("methods", [])
        methods = [Symbol(**m) for m in methods_raw]
        classes.append(ClassSymbol(**c_copy, methods=methods))
    return ModuleSymbols(
        name=raw.get("name", raw.get("module", "unknown")),
        functions=functions,
        classes=classes,
    )


if __name__ == "__main__":
    main()
