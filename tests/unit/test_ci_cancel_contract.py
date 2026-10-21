"""Exercise emergency cancellation through production Make and its real bootstrap."""

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
FOUNDATION = Path(os.environ.get(
    "GLUDD_CI_CANCEL_FOUNDATION_SOURCE", str(ROOT / "make/00-foundation.mk"),
))
SOURCE = Path(os.environ.get(
    "GLUDD_CI_CANCEL_MAKE_SOURCE", str(ROOT / "make/50-development-and-guardrails.mk"),
))
_RECURSION_INPUTS = {
    "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS",
    "RUN", "CI_CANCEL_VALIDATE_ONLY",
}
_GH_RECORDER = """#!/INTERPRETER
import json
import os
import sys

arguments = sys.argv[1:]
record = {
    "argv": arguments,
    "cwd": os.getcwd(),
    "run": os.environ.get("RUN"),
    "mode": os.environ.get("CI_CANCEL_VALIDATE_ONLY"),
    "unrelated": os.environ.get("GLUDD_CANCEL_UNRELATED"),
}
with open(os.environ["GLUDD_CANCEL_TRACE"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\\n")
if arguments[:2] == ["run", "list"]:
    print(json.dumps([{
        "databaseId": 39587418003,
        "status": "queued",
        "jobs": [{"name": "active-" + str(index), "status": "in_progress"}
                 for index in range(5)],
    }]))
elif arguments[:2] == ["run", "cancel"]:
    status = int(os.environ.get("GLUDD_CANCEL_GH_EXIT", "0"))
    if status:
        print("native-gh-error:" + str(status), file=sys.stderr)
    else:
        print("native cancellation request accepted")
    sys.exit(status)
elif arguments == ["probe", "environment"]:
    print("unrelated target observed")
else:
    print("unexpected gh arguments", file=sys.stderr)
    sys.exit(42)
"""


def _run(
    tmp_path: Path, cancel_values: dict[str, str], cancel_environment_overrides: bool,
    *, cancel_target: str = "ci-cancel", cancel_exit_status: int = 0,
    cancel_environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir(exist_ok=True)
    gh = binary_directory / "gh"
    gh.write_text(_GH_RECORDER.replace("/INTERPRETER", sys.executable, 1), encoding="utf-8")
    gh.chmod(0o700)
    python = binary_directory / "python3"
    if not python.exists():
        python.symlink_to(sys.executable)
    for name in ("ledger.json", "retained-proof.log"):
        path = tmp_path / name
        if not path.exists():
            path.write_bytes(b"retained evidence\n")
    makefile = tmp_path / "cancel-targets.mk"
    makefile.write_text(
        f"include {FOUNDATION}\ninclude {SOURCE}\n\nunrelated-cancel-input-probe:\n"
        "\t@gh probe environment\n", encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items() if key not in _RECURSION_INPUTS
    }
    environment.update(
        PATH=str(binary_directory) + os.pathsep + environment.get("PATH", os.defpath),
        GLUDD_CANCEL_TRACE=str(tmp_path / "gh-calls.jsonl"),
        GLUDD_CANCEL_GH_EXIT=str(cancel_exit_status),
        GLUDD_CANCEL_UNRELATED="preserved unrelated input",
        GLUDD_CI_CANCEL_RUN="ambient-private-run",
        GLUDD_CI_CANCEL_VALIDATE_ONLY="0",
    )
    environment.update(cancel_environment or {})
    return subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if cancel_environment_overrides else []),
         "-f", str(makefile), cancel_target,
         *(f"{name}={value}" for name, value in cancel_values.items())],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        check=False, timeout=15,
    )


def _calls(tmp_path: Path) -> list[dict[str, object]]:
    trace = tmp_path / "gh-calls.jsonl"
    if not trace.exists():
        return []
    return [cast(dict[str, object], json.loads(line)) for line in trace.read_text().splitlines()]


def _assert_retained(tmp_path: Path) -> None:
    for name in ("ledger.json", "retained-proof.log"):
        assert (tmp_path / name).read_bytes() == b"retained evidence\n"
    for name in (
        "MAKE_TRIPWIRE", "SHELL_TRIPWIRE", "PRIVATE_ENV_TRIPWIRE", "PRIVATE_CLI_TRIPWIRE",
    ):
        assert not (tmp_path / name).exists(), name


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
@pytest.mark.parametrize("cancel_mode", [None, "1"], ids=["default", "explicit-plan"])
def test_default_and_explicit_plan_are_offline_and_preserve_proof(
    tmp_path: Path, cancel_environment_overrides: bool, cancel_mode: str | None,
) -> None:
    values = {"RUN": "39587418003"}
    if cancel_mode is not None:
        values["CI_CANCEL_VALIDATE_ONLY"] = cancel_mode
    result = _run(tmp_path, values, cancel_environment_overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(tmp_path) == []
    assert "CI_CANCEL_PLAN" in result.stdout
    assert "repo=sandboxcom/gludd run=39587418003 cancellation_requested=false" in result.stdout
    assert "cancelled" not in result.stdout.lower()
    _assert_retained(tmp_path)


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
@pytest.mark.parametrize("cancel_exit_status", [0, 7, 130, 143])
def test_apply_is_one_exact_native_request_and_preserves_status_and_stderr(
    tmp_path: Path, cancel_environment_overrides: bool, cancel_exit_status: int,
) -> None:
    result = _run(
        tmp_path, {"RUN": "39587418003", "CI_CANCEL_VALIDATE_ONLY": "0"},
        cancel_environment_overrides, cancel_exit_status=cancel_exit_status,
    )
    calls = _calls(tmp_path)
    assert len(calls) == 1
    assert calls[0]["argv"] == ["run", "cancel", "39587418003", "-R", "sandboxcom/gludd"]
    assert calls[0]["cwd"] == str(tmp_path)
    assert calls[0]["unrelated"] == "preserved unrelated input"
    if cancel_exit_status:
        assert result.returncode != 0
        assert f"native-gh-error:{cancel_exit_status}" in result.stderr
        assert f"Error {cancel_exit_status}" in result.stderr
        assert "accepted" not in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "native cancellation request accepted" in result.stdout
    assert calls[0]["run"] is None and calls[0]["mode"] is None
    assert "cancelled" not in result.stdout.lower()
    _assert_retained(tmp_path)


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
@pytest.mark.parametrize("cancel_run", [
    "", "0", "00", "01", "-1", "+1", "1.0", " 1", "1 ", "--help", "1\n2", "1\t2",
])
def test_noncanonical_run_refuses_without_a_native_request(
    tmp_path: Path, cancel_environment_overrides: bool, cancel_run: str,
) -> None:
    values = {"RUN": cancel_run, "CI_CANCEL_VALIDATE_ONLY": "0"}
    environment: dict[str, str] = {}
    if cancel_run.startswith(" "):
        # Native Make consumes leading assignment whitespace; environment preserves it.
        values.pop("RUN")
        environment["RUN"] = cancel_run
    result = _run(
        tmp_path, values, cancel_environment_overrides, cancel_environment=environment,
    )
    assert result.returncode != 0
    assert "RUN must be a positive canonical integer" in result.stderr
    assert _calls(tmp_path) == []
    _assert_retained(tmp_path)


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
@pytest.mark.parametrize("cancel_mode", ["", "2", "-1", "01", " 0", "0 "])
def test_nonliteral_mode_refuses_without_a_native_request(
    tmp_path: Path, cancel_environment_overrides: bool, cancel_mode: str,
) -> None:
    values = {"RUN": "39587418003", "CI_CANCEL_VALIDATE_ONLY": cancel_mode}
    environment: dict[str, str] = {}
    if cancel_mode.startswith(" "):
        values.pop("CI_CANCEL_VALIDATE_ONLY")
        environment["CI_CANCEL_VALIDATE_ONLY"] = cancel_mode
    result = _run(
        tmp_path, values, cancel_environment_overrides, cancel_environment=environment,
    )
    assert result.returncode != 0
    assert "CI_CANCEL_VALIDATE_ONLY must be 0 or 1" in result.stderr
    assert _calls(tmp_path) == []
    _assert_retained(tmp_path)


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
@pytest.mark.parametrize("cancel_origin", ["command-line", "environment"])
@pytest.mark.parametrize("cancel_field", ["RUN", "CI_CANCEL_VALIDATE_ONLY"])
@pytest.mark.parametrize("cancel_payload", [
    "$(shell touch MAKE_TRIPWIRE)",
    "$(file >MAKE_TRIPWIRE,expanded)",
    "`touch SHELL_TRIPWIRE`",
    "$(touch SHELL_TRIPWIRE)",
], ids=["make-shell", "make-file", "shell-backticks", "shell-substitution"])
def test_hostile_public_values_never_expand_or_reach_gh(
    tmp_path: Path, cancel_environment_overrides: bool, cancel_origin: str,
    cancel_field: str, cancel_payload: str,
) -> None:
    values = {"RUN": "39587418003", "CI_CANCEL_VALIDATE_ONLY": "0"}
    environment: dict[str, str] = {}
    if cancel_origin == "command-line":
        values[cancel_field] = cancel_payload
    else:
        values.pop(cancel_field)
        environment[cancel_field] = cancel_payload
    result = _run(
        tmp_path, values, cancel_environment_overrides, cancel_environment=environment,
    )
    _assert_retained(tmp_path)
    assert result.returncode != 0
    assert _calls(tmp_path) == []
    expected = ("RUN must be a positive canonical integer" if cancel_field == "RUN"
                else "CI_CANCEL_VALIDATE_ONLY must be 0 or 1")
    assert expected in result.stderr


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
@pytest.mark.parametrize("cancel_origin", ["command-line", "environment"])
@pytest.mark.parametrize("cancel_mode", ["0", "1"])
def test_private_transport_cannot_spoof_public_run_or_mode(
    tmp_path: Path, cancel_environment_overrides: bool, cancel_origin: str, cancel_mode: str,
) -> None:
    values = {"RUN": "39587418003", "CI_CANCEL_VALIDATE_ONLY": cancel_mode}
    private = {
        "GLUDD_CI_CANCEL_RUN": "$(shell touch PRIVATE_CLI_TRIPWIRE)",
        "GLUDD_CI_CANCEL_VALIDATE_ONLY": "$(shell touch PRIVATE_ENV_TRIPWIRE)",
    }
    environment: dict[str, str] = {}
    if cancel_origin == "command-line":
        values.update(private)
    else:
        environment.update(private)
    result = _run(
        tmp_path, values, cancel_environment_overrides, cancel_environment=environment,
    )
    _assert_retained(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(tmp_path)
    if cancel_mode == "0":
        assert len(calls) == 1
        assert calls[0]["argv"] == ["run", "cancel", "39587418003", "-R", "sandboxcom/gludd"]
    else:
        assert calls == []
        assert "cancellation_requested=false" in result.stdout


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
def test_each_explicit_invocation_observes_its_fresh_run_input(
    tmp_path: Path, cancel_environment_overrides: bool,
) -> None:
    for run_id in ("39587418003", "39587418004"):
        result = _run(
            tmp_path, {"RUN": run_id, "CI_CANCEL_VALIDATE_ONLY": "0"},
            cancel_environment_overrides,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert [entry["argv"] for entry in _calls(tmp_path)] == [
        ["run", "cancel", run_id, "-R", "sandboxcom/gludd"]
        for run_id in ("39587418003", "39587418004")
    ]
    _assert_retained(tmp_path)


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
def test_literal_transport_does_not_unexport_other_targets_public_inputs(
    tmp_path: Path, cancel_environment_overrides: bool,
) -> None:
    result = _run(
        tmp_path, {"RUN": "other-target-run", "CI_CANCEL_VALIDATE_ONLY": "other-target-mode"},
        cancel_environment_overrides, cancel_target="unrelated-cancel-input-probe",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(tmp_path)
    assert len(calls) == 1
    assert calls[0]["argv"] == ["probe", "environment"]
    assert calls[0]["run"] == "other-target-run"
    assert calls[0]["mode"] == "other-target-mode"
    _assert_retained(tmp_path)


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
def test_retired_bulk_alias_cannot_select_a_queued_workflow_with_five_active_jobs(
    tmp_path: Path, cancel_environment_overrides: bool,
) -> None:
    result = _run(
        tmp_path, {}, cancel_environment_overrides, cancel_target="ci-cancel-zombies-dev",
    )
    calls = _calls(tmp_path)
    assert [entry["argv"] for entry in calls
            if cast(list[str], entry["argv"])[:2] == ["run", "cancel"]] == []
    assert calls == []
    assert result.returncode != 0
    assert "No rule to make target" in result.stderr
    _assert_retained(tmp_path)


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
@pytest.mark.parametrize("cancel_field", ["RUN", "CI_CANCEL_VALIDATE_ONLY"])
def test_native_make_leading_assignment_whitespace_is_normalized_before_capture(
    tmp_path: Path, cancel_environment_overrides: bool, cancel_field: str,
) -> None:
    values = {"RUN": "39587418003", "CI_CANCEL_VALIDATE_ONLY": "0"}
    values[cancel_field] = " " + values[cancel_field]
    result = _run(tmp_path, values, cancel_environment_overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(tmp_path)
    assert len(calls) == 1
    assert calls[0]["argv"] == ["run", "cancel", "39587418003", "-R", "sandboxcom/gludd"]
    assert calls[0]["run"] is None and calls[0]["mode"] is None
    _assert_retained(tmp_path)


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
def test_absent_public_run_refuses_even_with_spoofed_private_environment(
    tmp_path: Path, cancel_environment_overrides: bool,
) -> None:
    result = _run(tmp_path, {"CI_CANCEL_VALIDATE_ONLY": "0"}, cancel_environment_overrides)
    assert result.returncode != 0
    assert "RUN must be a positive canonical integer" in result.stderr
    assert _calls(tmp_path) == []
    _assert_retained(tmp_path)


@pytest.mark.parametrize("cancel_environment_overrides", [False, True])
@pytest.mark.parametrize("cancel_field", ["RUN", "CI_CANCEL_VALIDATE_ONLY"])
def test_real_project_make_bootstrap_never_expands_hostile_cancel_inputs(
    tmp_path: Path, cancel_environment_overrides: bool, cancel_field: str,
) -> None:
    project_root = Path(os.environ.get("GLUDD_CI_CANCEL_PROJECT_ROOT", str(ROOT))).resolve()
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    gh = binary_directory / "gh"
    gh.write_text(_GH_RECORDER.replace("/INTERPRETER", sys.executable, 1), encoding="utf-8")
    gh.chmod(0o700)
    tripwire = tmp_path / "BOOTSTRAP_TRIPWIRE"
    values = {"RUN": "39587418003", "CI_CANCEL_VALIDATE_ONLY": "1"}
    values[cancel_field] = f"$(shell touch {shlex.quote(str(tripwire))})"
    environment = {
        key: value for key, value in os.environ.items()
        if key not in _RECURSION_INPUTS | {"MAKEFILES"}
    }
    environment.update(
        PATH=str(binary_directory) + os.pathsep + environment.get("PATH", os.defpath),
        GLUDD_CANCEL_TRACE=str(tmp_path / "gh-calls.jsonl"), GLUDD_CANCEL_GH_EXIT="0",
        GLUDD_CI_CANCEL_RUN="ambient-private-run", GLUDD_CI_CANCEL_VALIDATE_ONLY="0",
        UV_PROJECT_ENVIRONMENT=sys.prefix, VIRTUAL_ENV=sys.prefix,
        UV_NO_SYNC="1", UV_PYTHON=f"{sys.version_info.major}.{sys.version_info.minor}",
        UV_PYTHON_DOWNLOADS="never",
    )
    result = subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if cancel_environment_overrides else []),
         "-f", str(project_root / "Makefile"), "ci-cancel", "PYTHON=" + sys.executable,
         *(f"{name}={value}" for name, value in values.items())],
        cwd=project_root, env=environment, capture_output=True, text=True,
        check=False, timeout=15,
    )
    assert not tripwire.exists(), result.stdout + result.stderr
    assert _calls(tmp_path) == []
    assert result.returncode != 0
    diagnostic = ("RUN must be a positive canonical integer" if cancel_field == "RUN"
                  else "CI_CANCEL_VALIDATE_ONLY must be 0 or 1")
    assert diagnostic in result.stderr


def test_literal_capture_is_scoped_unique_and_before_the_first_eager_native_shell() -> None:
    foundation = FOUNDATION.read_text(encoding="utf-8")
    recipes = SOURCE.read_text(encoding="utf-8")
    capture = (
        "ifneq ($(filter ci-cancel,$(MAKECMDGOALS)),)\n"
        "CI_CANCEL_VALIDATE_ONLY ?= 1\n"
        "override GLUDD_CI_CANCEL_RUN := $(value RUN)\n"
        "override GLUDD_CI_CANCEL_VALIDATE_ONLY := $(value CI_CANCEL_VALIDATE_ONLY)\n"
        "export GLUDD_CI_CANCEL_RUN := $(value RUN)\n"
        "export GLUDD_CI_CANCEL_VALIDATE_ONLY := $(value CI_CANCEL_VALIDATE_ONLY)\n"
        "unexport RUN CI_CANCEL_VALIDATE_ONLY\n"
        "endif"
    )
    assert foundation.count(capture) == 1
    assert capture not in recipes
    assert foundation.index(capture) + len(capture) < foundation.index(":= $(shell")
    for declaration in capture.splitlines()[1:-1]:
        assert foundation.count(declaration) == 1
        assert declaration not in recipes
