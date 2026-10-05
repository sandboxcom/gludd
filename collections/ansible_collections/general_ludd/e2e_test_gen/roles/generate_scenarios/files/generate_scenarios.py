#!/usr/bin/env python3
"""Generate deterministic E2E scenarios from packaged symbol metadata."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

_PATTERNS: dict[str, tuple[str, tuple[str, ...]]] = {
    "crud_lifecycle": (
        "Exercise a complete resource lifecycle.",
        ("create", "update", "delete", "remove", "add", "insert"),
    ),
    "auth_flow": (
        "Exercise authenticated and unauthenticated access.",
        ("auth", "login", "token", "logout", "session", "credentials"),
    ),
    "timeout_handling": (
        "Exercise bounded timeout and retry behavior.",
        ("timeout", "retry", "backoff", "deadline", "circuit"),
    ),
    "concurrent_edits": (
        "Exercise concurrent mutation without lost writes.",
        ("lock", "mutex", "atomic", "concurrent", "race", "transaction"),
    ),
    "daemon_restart": (
        "Exercise daemon restart and state recovery.",
        ("init", "startup", "shutdown", "restart", "reload", "bootstrap"),
    ),
}


def _analyze_target(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [
        {
            "name": node.name,
            "line_start": node.lineno,
            "line_end": node.end_lineno or node.lineno,
            "is_public": not node.name.startswith("_"),
        }
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    classes = [
        {
            "name": node.name,
            "line_start": node.lineno,
            "line_end": node.end_lineno or node.lineno,
            "is_public": not node.name.startswith("_"),
            "methods": [],
        }
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    ]
    return {"name": path.stem, "path": str(path), "functions": functions, "classes": classes}


def _load_symbols(symbols_file: str, target_module: str) -> dict[str, Any]:
    if symbols_file:
        raw = json.loads(Path(symbols_file).read_text(encoding="utf-8"))
        source = raw.get("source", raw)
        if not isinstance(source, dict):
            raise ValueError("symbols source must be an object")
        return {
            **source,
            "name": source.get("name", raw.get("module", "unknown")),
            "path": raw.get("path", source.get("path", "")),
        }
    return _analyze_target(Path(target_module))


def _steps(name: str, target: str) -> list[dict[str, object]]:
    if name == "crud_lifecycle":
        return [
            {
                "action": action,
                "target": f"/api/{target}",
                "expected_result": expected,
                "assertions": [assertion],
            }
            for action, expected, assertion in (
                ("POST", "201 Created", "response.status_code == 201"),
                ("GET", "200 OK", "response.status_code == 200"),
                ("DELETE", "204 No Content", "response.status_code == 204"),
            )
        ]
    return [
        {
            "action": "Invoke",
            "target": target,
            "expected_result": "operation completes within its contract",
            "assertions": ["result is not None"],
        }
    ]


def generate_scenarios(symbols: dict[str, Any]) -> dict[str, object]:
    """Map public top-level symbols to the stable scenario catalog."""
    public_names = [
        str(symbol.get("name", ""))
        for key in ("functions", "classes")
        for symbol in symbols.get(key, [])
        if isinstance(symbol, dict) and symbol.get("is_public") is True
    ]
    scenarios: list[dict[str, object]] = []
    coverage_targets: set[str] = set()
    for name, (description, keywords) in _PATTERNS.items():
        matches = [symbol for symbol in public_names if any(word in symbol.lower() for word in keywords)]
        if matches:
            coverage_targets.update(matches)
            scenarios.append(
                {
                    "name": name,
                    "description": description,
                    "steps": _steps(name, matches[0]),
                    "coverage_targets": matches,
                }
            )
    return {
        "module": str(symbols.get("name", "unknown")),
        "path": str(symbols.get("path", "")),
        "scenarios": scenarios,
        "scenario_count": len(scenarios),
        "coverage_targets": sorted(coverage_targets),
        "status": "completed",
    }


def main() -> None:
    """Run the standalone compatibility CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbols-file", default="")
    group.add_argument("--target-module", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = generate_scenarios(_load_symbols(args.symbols_file, args.target_module))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scenario_count": output["scenario_count"], "output": str(output_path)}))


if __name__ == "__main__":
    main()
