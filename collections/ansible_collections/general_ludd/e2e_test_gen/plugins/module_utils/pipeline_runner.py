"""Bounded, zero-downtime runner for packaged E2E generation scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

PipelineResult = dict[str, object]
CommandBuilder = Callable[[Path, Mapping[str, object], Path], list[str]]


class PipelineExecutionError(RuntimeError):
    """Raised when a packaged pipeline operation cannot publish a result."""


def _required_string(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _positive_int(arguments: Mapping[str, object], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _analyze(script: Path, arguments: Mapping[str, object], stage: Path) -> list[str]:
    return [
        sys.executable,
        str(script),
        "--target-module",
        _required_string(arguments, "target_module"),
        "--output",
        str(stage / "artifact.json"),
        "--ast-only",
    ]


def _generate(script: Path, arguments: Mapping[str, object], stage: Path) -> list[str]:
    command = [sys.executable, str(script)]
    symbols_file = _optional_string(arguments, "symbols_file")
    target_module = _optional_string(arguments, "target_module")
    if symbols_file:
        command.extend(["--symbols-file", symbols_file])
    elif target_module:
        command.extend(["--target-module", target_module])
    else:
        raise ValueError("generate requires symbols_file or target_module")
    return [*command, "--output", str(stage / "artifact.json")]


def _validate(script: Path, arguments: Mapping[str, object], stage: Path) -> list[str]:
    threshold = arguments.get("confidence_threshold", 0.4)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("confidence_threshold must be numeric")
    categories = arguments.get("research_categories", ["general", "it"])
    if not isinstance(categories, list) or not all(isinstance(value, str) for value in categories):
        raise ValueError("research_categories must be a list of strings")
    command = [
        sys.executable,
        str(script),
        "--scenarios-file",
        _required_string(arguments, "scenarios_file"),
        "--output",
        str(stage / "artifact.json"),
        "--confidence-threshold",
        str(float(threshold)),
        "--daemon-url",
        _required_string(arguments, "daemon_url"),
        "--research-categories",
        ",".join(categories),
        "--research-time-range",
        _required_string(arguments, "research_time_range"),
        "--max-results",
        str(_positive_int(arguments, "max_results", 10)),
    ]
    if arguments.get("mock") is True:
        command.append("--mock")
    return command


def _write_tests(script: Path, arguments: Mapping[str, object], stage: Path) -> list[str]:
    return [
        sys.executable,
        str(script),
        "--scenarios-file",
        _required_string(arguments, "scenarios_file"),
        "--output-dir",
        str(stage / "generated"),
        "--manifest",
        str(stage / "manifest.json"),
        "--test-file-prefix",
        _required_string(arguments, "test_file_prefix"),
    ]


def _verify_coverage(script: Path, arguments: Mapping[str, object], stage: Path) -> list[str]:
    command = [
        sys.executable,
        str(script),
        "--test-dir",
        _required_string(arguments, "test_dir"),
        "--source-module",
        _required_string(arguments, "source_module"),
        "--output",
        str(stage / "artifact.json"),
        "--threshold",
        str(_positive_int(arguments, "threshold", 85)),
        "--timeout",
        str(_positive_int(arguments, "pytest_timeout", 300)),
        "--test-file-prefix",
        _required_string(arguments, "test_file_prefix"),
    ]
    for argument_key, flag in (("scenarios_file", "--scenarios-file"), ("symbols_file", "--symbols-file")):
        value = _optional_string(arguments, argument_key)
        if value:
            command.extend([flag, value])
    return command


_SCRIPTS = {
    "analyze": ("analyze_code_paths", "analyze_code_paths.py", _analyze),
    "generate": ("generate_scenarios", "generate_scenarios.py", _generate),
    "validate": ("validate_scenarios", "validate_scenarios.py", _validate),
    "write_tests": ("write_e2e_tests", "write_e2e_tests.py", _write_tests),
    "verify_coverage": ("verify_coverage", "verify_coverage.py", _verify_coverage),
}


def _json_stdout(stdout: str) -> PipelineResult:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return cast(PipelineResult, value)
    return {"stdout": stdout.strip()}


def _publish_file(stage_path: Path, final_path: Path) -> None:
    if not stage_path.is_file():
        raise PipelineExecutionError(f"pipeline did not create staged artifact: {stage_path.name}")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(stage_path, final_path)


def _publish_generated(stage: Path, arguments: Mapping[str, object]) -> dict[str, object]:
    output_dir = Path(_required_string(arguments, "output_dir"))
    manifest_path = Path(_required_string(arguments, "manifest"))
    stage_generated = stage / "generated"
    stage_manifest = stage / "manifest.json"
    manifest = cast(dict[str, Any], json.loads(stage_manifest.read_text(encoding="utf-8")))
    for item in cast(list[dict[str, Any]], manifest.get("test_files", [])):
        staged_file = Path(str(item["file"]))
        final_file = output_dir / staged_file.relative_to(stage_generated)
        _publish_file(staged_file, final_file)
        item["file"] = str(final_file)
    stage_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _publish_file(stage_manifest, manifest_path)
    return cast(dict[str, object], manifest)


def run_pipeline(
    collection_root: Path,
    operation: str,
    arguments: Mapping[str, object],
) -> PipelineResult:
    """Run one packaged operation and publish only a successful staged result."""
    specification = _SCRIPTS.get(operation)
    if specification is None:
        raise ValueError(f"unsupported E2E pipeline operation: {operation}")
    role, filename, builder = specification
    script = collection_root / "roles" / role / "files" / filename
    if not script.is_file():
        raise PipelineExecutionError(f"packaged pipeline script is missing: {script}")
    timeout = _positive_int(arguments, "timeout", 360)
    environment = os.environ.copy()
    psk = _optional_string(arguments, "psk")
    if psk:
        environment["GLUDD_DAEMON_PSK"] = psk

    with tempfile.TemporaryDirectory(prefix="gludd-e2e-pipeline-") as temporary:
        stage = Path(temporary)
        command = builder(script, arguments, stage)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            env=environment,
            text=True,
            timeout=timeout,
        )
        cli_result = _json_stdout(completed.stdout)
        if completed.returncode != 0:
            message = str(cli_result.get("error") or completed.stderr.strip() or "unknown error")
            raise PipelineExecutionError(f"{operation} failed: {message}")

        if operation == "write_tests":
            artifact = _publish_generated(stage, arguments)
        else:
            final_path = Path(_required_string(arguments, "output"))
            stage_path = stage / "artifact.json"
            _publish_file(stage_path, final_path)
            artifact = cast(dict[str, object], json.loads(final_path.read_text(encoding="utf-8")))
        return {"operation": operation, "artifact": artifact, "cli": cli_result}


__all__ = ["PipelineExecutionError", "run_pipeline"]
