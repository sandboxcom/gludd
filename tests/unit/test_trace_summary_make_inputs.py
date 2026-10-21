"""Native trace-summary Make inputs preserve existing diagnostics and inert data."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ("LOG", "RUN_ID", "TRACE_SUMMARY_TIMINGS", "TRACE_SUMMARY_MEMORY")
PRIVATE = {
    "LOG": "GLUDD_TRACE_SUMMARY_LOG", "RUN_ID": "GLUDD_TRACE_SUMMARY_RUN_ID",
    "TRACE_SUMMARY_TIMINGS": "GLUDD_TRACE_SUMMARY_TIMINGS",
    "TRACE_SUMMARY_MEMORY": "GLUDD_TRACE_SUMMARY_MEMORY",
}
RECURSION = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS"}
RECORDER = """import json, os, sys
from pathlib import Path
public = ("LOG", "RUN_ID", "TRACE_SUMMARY_TIMINGS", "TRACE_SUMMARY_MEMORY")
with Path(os.environ["GLUDD_TRACE_INPUT_CALLS"]).open("x", encoding="utf-8") as stream:
    json.dump({"argv": sys.argv[1:], "public": {key: os.environ.get(key) for key in public},
               "unrelated": os.environ.get("GLUDD_TRACE_INPUT_UNRELATED")}, stream)
status = int(os.environ["GLUDD_TRACE_INPUT_EXIT"])
if status:
    print("native-summary-error:" + str(status), file=sys.stderr)
sys.exit(status)
"""


def _run(
    tmp_path: Path, trace_values: dict[str, str], *, trace_environment_overrides: bool,
    trace_private_origin: str = "environment", trace_exit_status: int = 0,
    trace_unrelated: bool = False, trace_alias_context: str = "",
    trace_target_values: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    recorder = tmp_path / "recorder.py"
    recorder.write_text(RECORDER, encoding="utf-8")
    removed = RECURSION | set(PUBLIC) | set(PRIVATE.values())
    environment = {key: value for key, value in os.environ.items() if key not in removed}
    environment.update(
        GLUDD_TRACE_INPUT_CALLS=str(tmp_path / "calls.json"),
        GLUDD_TRACE_INPUT_EXIT=str(trace_exit_status),
        GLUDD_TRACE_INPUT_UNRELATED="preserved unrelated input",
    )
    private = {key: "ambient-private-value" for key in PRIVATE.values()}
    if trace_private_origin == "command-line":
        private = {key: "$(shell touch " + str(tmp_path / "PRIVATE_TRIPWIRE") + ")"
                   for key in PRIVATE.values()}
    else:
        environment.update(private)
    command = ["make", "--no-print-directory"]
    if trace_environment_overrides:
        command.append("-e")
    if trace_unrelated:
        probe = tmp_path / "unrelated.mk"
        inheritance = "".join(
            f"trace-input-export-parent: override {key} := {value}\n"
            for key, value in (trace_target_values or {}).items()
        )
        if trace_target_values:
            inheritance += "trace-input-export-parent: trace-input-unrelated-probe\n"
        probe.write_text(
            f"include {ROOT / 'make/00-foundation.mk'}\n" + inheritance
            + "trace-input-unrelated-probe:\n\t@$(UV) probe\n", encoding="utf-8",
        )
        command.extend(["-f", str(probe)])
    elif trace_alias_context:
        probe = tmp_path / "alias.mk"
        target = ("test-xdist-trace-summary" if trace_alias_context == "direct-context"
                  else "trace-input-alias")
        assignments = "".join(
            f"{target}: override {key} := {value}\n"
            for key, value in (trace_target_values or {}).items()
        )
        probe.write_text(
            f"include {ROOT / 'Makefile'}\n" + assignments
            + "trace-input-alias: test-xdist-trace-summary\n", encoding="utf-8",
        )
        command.extend(["-f", str(probe)])
    goal = "test-xdist-trace-summary"
    if trace_unrelated:
        goal = ("trace-input-export-parent" if trace_target_values
                else "trace-input-unrelated-probe")
    elif trace_alias_context and trace_alias_context != "direct-context":
        goal = "trace-input-alias"
    command.extend([
        goal,
        "UV=" + shlex.join([sys.executable, str(recorder)]),
        *(f"{key}={value}" for key, value in trace_values.items()),
    ])
    if trace_private_origin == "command-line":
        command.extend(f"{key}={value}" for key, value in private.items())
    if trace_alias_context == "mixed":
        command.append("test-xdist-trace-summary")
    return subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True,
        check=False, timeout=15,
    )


def _call(tmp_path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads((tmp_path / "calls.json").read_text()))


def _values(tmp_path: Path) -> dict[str, str]:
    return {"LOG": str(tmp_path / "trace with spaces.jsonl"), "RUN_ID": "selected-run",
            "TRACE_SUMMARY_TIMINGS": "0", "TRACE_SUMMARY_MEMORY": "0"}


def _assert_call(tmp_path: Path, values: dict[str, str]) -> None:
    expected = ["run", "python", "scripts/summarize_xdist_trace.py", "--run-id", values["RUN_ID"]]
    if values["TRACE_SUMMARY_TIMINGS"] == "1":
        expected.append("--include-timings")
    if values.get("TRACE_SUMMARY_MEMORY", "0") == "1":
        expected.append("--include-memory")
    expected.extend(["--", values["LOG"]])
    call = _call(tmp_path)
    assert call["argv"] == expected
    assert call["public"] == dict.fromkeys(PUBLIC)
    assert call["unrelated"] == "preserved unrelated input"
    assert not (tmp_path / "PRIVATE_TRIPWIRE").exists()


@pytest.mark.parametrize("trace_environment_overrides", [False, True])
@pytest.mark.parametrize("trace_timings", ["0", "1"])
@pytest.mark.parametrize("trace_memory", ["0", "1"])
def test_summary_forwards_both_existing_views_as_exact_native_arguments(
    tmp_path: Path, trace_environment_overrides: bool, trace_timings: str, trace_memory: str,
) -> None:
    values = {**_values(tmp_path), "TRACE_SUMMARY_TIMINGS": trace_timings,
              "TRACE_SUMMARY_MEMORY": trace_memory}
    result = _run(tmp_path, values, trace_environment_overrides=trace_environment_overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_call(tmp_path, values)


@pytest.mark.parametrize("trace_environment_overrides", [False, True])
def test_summary_default_memory_keeps_the_original_compact_projection(
    tmp_path: Path, trace_environment_overrides: bool,
) -> None:
    values = _values(tmp_path)
    values.pop("TRACE_SUMMARY_MEMORY")
    result = _run(tmp_path, values, trace_environment_overrides=trace_environment_overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_call(tmp_path, values)


@pytest.mark.parametrize("trace_environment_overrides", [False, True])
@pytest.mark.parametrize("trace_memory", ["", "2", "01", "-1"])
def test_summary_refuses_invalid_memory_before_any_native_child(
    tmp_path: Path, trace_environment_overrides: bool, trace_memory: str,
) -> None:
    values = {**_values(tmp_path), "TRACE_SUMMARY_MEMORY": trace_memory}
    result = _run(tmp_path, values, trace_environment_overrides=trace_environment_overrides)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "TRACE_SUMMARY_MEMORY must be 0 or 1" in result.stderr
    assert not (tmp_path / "calls.json").exists()


@pytest.mark.parametrize("trace_environment_overrides", [False, True])
@pytest.mark.parametrize("trace_field", PUBLIC)
@pytest.mark.parametrize("trace_payload", ["make-function", "shell-substitution", "backticks"])
def test_summary_public_inputs_never_execute_make_or_shell_data(
    tmp_path: Path, trace_environment_overrides: bool, trace_field: str, trace_payload: str,
) -> None:
    marker = tmp_path / "PUBLIC_TRIPWIRE"
    payload = {
        "make-function": "$(shell touch " + str(marker) + ")",
        "shell-substitution": "$(touch " + str(marker) + ")",
        "backticks": "`touch " + str(marker) + "`",
    }[trace_payload]
    values = {**_values(tmp_path), trace_field: payload}
    result = _run(tmp_path, values, trace_environment_overrides=trace_environment_overrides)
    assert not marker.exists(), "literal diagnostic input executed a command"
    if trace_field in {"TRACE_SUMMARY_TIMINGS", "TRACE_SUMMARY_MEMORY"}:
        assert result.returncode == 2, result.stdout + result.stderr
        assert trace_field + " must be 0 or 1" in result.stderr
        assert not (tmp_path / "calls.json").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_call(tmp_path, values)


@pytest.mark.parametrize("trace_environment_overrides", [False, True])
@pytest.mark.parametrize("trace_private_origin", ["command-line", "environment"])
def test_summary_private_carriers_cannot_replace_public_inputs(
    tmp_path: Path, trace_environment_overrides: bool, trace_private_origin: str,
) -> None:
    values = {**_values(tmp_path), "TRACE_SUMMARY_TIMINGS": "1", "TRACE_SUMMARY_MEMORY": "1"}
    result = _run(
        tmp_path, values, trace_environment_overrides=trace_environment_overrides,
        trace_private_origin=trace_private_origin,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_call(tmp_path, values)


@pytest.mark.parametrize("trace_environment_overrides", [False, True])
@pytest.mark.parametrize("trace_exit_status", [7, 124, 143])
def test_summary_retains_native_child_failure_and_stderr_status(
    tmp_path: Path, trace_environment_overrides: bool, trace_exit_status: int,
) -> None:
    result = _run(
        tmp_path, _values(tmp_path), trace_environment_overrides=trace_environment_overrides,
        trace_exit_status=trace_exit_status,
    )
    assert result.returncode == 2
    assert f"Error {trace_exit_status}" in result.stderr
    assert f"native-summary-error:{trace_exit_status}" in result.stderr
    assert (tmp_path / "calls.json").exists()


@pytest.mark.parametrize("trace_environment_overrides", [False, True])
def test_summary_capture_does_not_change_inputs_for_unrelated_goals(
    tmp_path: Path, trace_environment_overrides: bool,
) -> None:
    values = _values(tmp_path)
    result = _run(
        tmp_path, values, trace_environment_overrides=trace_environment_overrides,
        trace_unrelated=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    call = _call(tmp_path)
    assert call["argv"] == ["probe"]
    assert call["public"] == values


@pytest.mark.parametrize("trace_environment_overrides", [False, True])
@pytest.mark.parametrize("trace_alias_context", ["ordinary", "inherited", "mixed", "direct-context"])
def test_summary_keeps_native_prerequisite_and_target_specific_inputs(
    tmp_path: Path, trace_environment_overrides: bool, trace_alias_context: str,
) -> None:
    values = _values(tmp_path)
    target_values = None
    if trace_alias_context == "ordinary":
        values.pop("TRACE_SUMMARY_TIMINGS")
        values.pop("TRACE_SUMMARY_MEMORY")
        expected_values = values
    else:
        target_values = {
            "LOG": str(tmp_path / "target trace with spaces.jsonl"),
            "RUN_ID": "target-selected-run", "TRACE_SUMMARY_TIMINGS": "1",
            "TRACE_SUMMARY_MEMORY": "1",
        }
        expected_values = target_values
    result = _run(
        tmp_path, values, trace_environment_overrides=trace_environment_overrides,
        trace_alias_context=trace_alias_context, trace_target_values=target_values,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = ["run", "python", "scripts/summarize_xdist_trace.py",
                "--run-id", expected_values["RUN_ID"]]
    if trace_alias_context != "ordinary":
        expected.extend(["--include-timings", "--include-memory"])
    expected.extend(["--", expected_values["LOG"]])
    call = _call(tmp_path)
    assert call["argv"] == expected
    expected_public: object = {key: expected_values.get(key) for key in PUBLIC}
    if trace_alias_context in {"mixed", "direct-context"}:
        expected_public = dict.fromkeys(PUBLIC)
    elif trace_alias_context == "inherited":
        # Compare native Make export semantics instead of assuming a Make version.
        control_root = tmp_path / "native-export-control"
        control_root.mkdir()
        control = _run(
            control_root, values, trace_environment_overrides=trace_environment_overrides,
            trace_unrelated=True, trace_target_values=target_values,
        )
        assert control.returncode == 0, control.stdout + control.stderr
        assert _call(control_root)["argv"] == ["probe"]
        expected_public = _call(control_root)["public"]
    assert call["public"] == expected_public
    assert call["unrelated"] == "preserved unrelated input"
    assert not (tmp_path / "PRIVATE_TRIPWIRE").exists()
