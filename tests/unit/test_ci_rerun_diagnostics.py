"""Native guarded Make reruns preserve ledger ownership and opt-in diagnostics."""

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
FLAGS = ("CI_RERUN_DEBUG", "CI_RERUN_FAILED_ONLY")
RECURSION = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS", "MAKEFILES"}
RECORDER = r'''#!/INTERPRETER
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["GLUDD_RERUN_TRACE"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"argv": args, "unrelated": os.environ.get("GLUDD_RERUN_UNRELATED"),
                             "public": {name: os.environ.get(name)
                                        for name in ("CI_RERUN_DEBUG", "CI_RERUN_FAILED_ONLY")}}) + "\n")
if args[:2] == ["run", "view"]:
    status = int(os.environ.get("GLUDD_RERUN_OBSERVE_STATUS", "0"))
    if status:
        print("native observe refused", file=sys.stderr)
        raise SystemExit(status)
    print(json.dumps({"attempt": 1, "databaseId": 123, "headSha": "a" * 40,
                      "headBranch": "development", "status": "completed",
                      "conclusion": "failure",
                      "url": "https://github.com/sandboxcom/gludd/actions/runs/123",
                      "workflowName": "Build and Release"}))
elif args[:1] == ["api"]:
    print(json.dumps([{"total_count": 1, "jobs": [{
        "id": 7, "run_id": 123, "run_attempt": 1, "head_sha": "a" * 40,
        "run_url": "https://api.github.com/repos/sandboxcom/gludd/actions/runs/123",
        "url": "https://api.github.com/repos/sandboxcom/gludd/actions/jobs/7",
        "html_url": "https://github.com/sandboxcom/gludd/actions/runs/123/job/7",
        "name": "gate (3.12)", "status": "completed", "conclusion": "failure",
        "started_at": None, "completed_at": None, "steps": [],
    }]}]))
elif args == ["run", "rerun", "--help"]:
    print("native gh run rerun flags: --debug --failed")
elif args[:2] == ["run", "rerun"]:
    status = int(os.environ.get("GLUDD_RERUN_STATUS", "0"))
    if status:
        print("native rerun refusal:" + str(status), file=sys.stderr)
    else:
        print("native rerun request accepted")
    raise SystemExit(status)
elif args == ["probe", "environment"]:
    print("unrelated probe")
else:
    raise SystemExit("unexpected native gh argv: " + repr(args))
'''


def _calls(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "gh-calls.jsonl"
    if not path.exists():
        return []
    return [cast(dict[str, object], json.loads(line)) for line in path.read_text().splitlines()]


def _run(
    tmp_path: Path, values: dict[str, str], *, inherited: dict[str, str] | None = None,
    overrides: bool = False, target: str = "ci-rerun", request_status: int = 0,
    observe_status: int = 0,
) -> subprocess.CompletedProcess[str]:
    binaries = tmp_path / "gludd-bin"
    binaries.mkdir(exist_ok=True)
    gh = binaries / "gh"
    gh.write_text(RECORDER.replace("/INTERPRETER", sys.executable, 1), encoding="utf-8")
    gh.chmod(0o700)
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    ledger_script = scripts / "ci_failure_ledger.py"
    if not ledger_script.exists():
        ledger_script.symlink_to(ROOT / "scripts/ci_failure_ledger.py")
    makefile = tmp_path / "gludd-rerun.mk"
    makefile.write_text(
        f"include {ROOT / 'make/00-foundation.mk'}\n"
        f"include {ROOT / 'make/30-ci-and-release.mk'}\n"
        "unrelated-rerun-probe:\n\t@gh probe environment\n",
        encoding="utf-8",
    )
    environment = {key: value for key, value in os.environ.items()
                   if key not in RECURSION | set(FLAGS)}
    environment.update(
        PATH=str(binaries) + os.pathsep + environment.get("PATH", os.defpath),
        GLUDD_RERUN_TRACE=str(tmp_path / "gh-calls.jsonl"),
        GLUDD_RERUN_STATUS=str(request_status),
        GLUDD_RERUN_OBSERVE_STATUS=str(observe_status),
        GLUDD_RERUN_UNRELATED="retained carrier",
        UV_PROJECT_ENVIRONMENT=sys.prefix, VIRTUAL_ENV=sys.prefix,
        UV_NO_SYNC="1", UV_PYTHON=f"{sys.version_info.major}.{sys.version_info.minor}",
        UV_PYTHON_DOWNLOADS="never",
    )
    environment.update(inherited or {})
    arguments = {
        "RUN": "123", "CI_FAILURE_REPOSITORY": "sandboxcom/gludd",
        "CI_FAILURE_LEDGER": str(tmp_path / "ledger.json"),
        "CI_RERUN_ALLOW_UNCHANGED": "1", "CI_RERUN_REASON": "diagnostic native debug evidence",
        "CI_FAILURE_VALIDATE_ONLY": "0", "PYTHON": sys.executable,
        **values,
    }
    return subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if overrides else []),
         "-f", str(makefile), target, *(f"{key}={value}" for key, value in arguments.items())],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False, timeout=20,
    )


@pytest.mark.parametrize("overrides", [False, True])
@pytest.mark.parametrize("origin", ["command-line", "environment"])
@pytest.mark.parametrize("debug,failed_only", [("0", "0"), ("1", "0"), ("0", "1"), ("1", "1")])
def test_native_rerun_flags_follow_real_observation_and_guard(
    tmp_path: Path, overrides: bool, origin: str, debug: str, failed_only: str,
) -> None:
    selected = dict(zip(FLAGS, (debug, failed_only), strict=True))
    result = _run(
        tmp_path, selected if origin == "command-line" else {},
        inherited=selected if origin == "environment" else {}, overrides=overrides,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(tmp_path)
    expected = ["run", "rerun", "-R", "sandboxcom/gludd", "123"]
    if debug == "1":
        expected.append("--debug")
    if failed_only == "1":
        expected.append("--failed")
    assert [cast(list[str], call["argv"])[:2] for call in calls] == [
        ["run", "view"], ["api", "repos/sandboxcom/gludd/actions/runs/123/attempts/1/jobs?per_page=50"],
        ["run", "rerun"],
    ]
    assert calls[-1]["argv"] == expected
    assert calls[-1]["unrelated"] == "retained carrier"
    assert calls[-1]["public"] == dict.fromkeys(FLAGS)
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert ledger["runs"]["123"]["sha"] == "a" * 40
    assert len(ledger["families"]) == 1
    assert "outcome=requested" in result.stderr
    assert "run=123" in result.stderr and "diagnostic native debug evidence" in result.stderr
    assert "gate_evidence=false" in result.stderr and "release_ready=false" in result.stderr


@pytest.mark.parametrize("overrides", [False, True])
def test_default_native_rerun_argv_is_unchanged(tmp_path: Path, overrides: bool) -> None:
    result = _run(tmp_path, {}, overrides=overrides)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(tmp_path)[-1]["argv"] == ["run", "rerun", "-R", "sandboxcom/gludd", "123"]


@pytest.mark.parametrize("overrides", [False, True])
def test_plan_is_offline_without_ledger_or_request(tmp_path: Path, overrides: bool) -> None:
    result = _run(
        tmp_path, {"CI_FAILURE_VALIDATE_ONLY": "1", "CI_RERUN_DEBUG": "1",
                   "CI_RERUN_FAILED_ONLY": "1"}, overrides=overrides,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(tmp_path) == []
    assert not (tmp_path / "ledger.json").exists()
    assert "CI_RERUN_VALIDATE_ONLY_PASS" in result.stdout
    assert "debug=1 failed_only=1 rerun_requested=false" in result.stdout
    assert "outcome=planned" in result.stderr


@pytest.mark.parametrize("overrides", [False, True])
@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("invalid", ["", "2", "true", "01", "0 1", "1 ", " 1"])
def test_nonliteral_flag_refuses_before_observation(
    tmp_path: Path, overrides: bool, flag: str, invalid: str,
) -> None:
    result = _run(tmp_path, {}, inherited={flag: invalid}, overrides=overrides)
    assert result.returncode != 0
    assert flag + " must be literal 0 or 1" in result.stderr
    assert _calls(tmp_path) == []
    assert not (tmp_path / "ledger.json").exists()


@pytest.mark.parametrize("overrides", [False, True])
@pytest.mark.parametrize("guard_values", [
    {"CI_RERUN_ALLOW_UNCHANGED": "0"}, {"CI_RERUN_REASON": ""},
])
def test_diagnostics_preserve_real_ledger_rerun_refusals(
    tmp_path: Path, overrides: bool, guard_values: dict[str, str],
) -> None:
    result = _run(
        tmp_path, {"CI_RERUN_DEBUG": "1", "CI_RERUN_FAILED_ONLY": "1", **guard_values},
        overrides=overrides,
    )
    assert result.returncode != 0
    assert len(_calls(tmp_path)) == 2
    assert "CI_FAILURE_RERUN_GUARD_BLOCKED" in result.stderr
    assert "outcome=requested" not in result.stderr


@pytest.mark.parametrize("status", [7, 130, 143])
def test_native_request_failure_preserves_status_and_reconciliation(
    tmp_path: Path, status: int,
) -> None:
    result = _run(
        tmp_path, {"CI_RERUN_DEBUG": "1", "CI_RERUN_FAILED_ONLY": "1"},
        request_status=status,
    )
    assert result.returncode != 0
    assert f"native rerun refusal:{status}" in result.stderr
    assert f"native_status={status}" in result.stderr
    assert "write_outcome=unknown" in result.stderr
    assert "reconcile" in result.stderr
    assert "outcome=requested" not in result.stderr


def test_native_observation_failure_never_requests_rerun(tmp_path: Path) -> None:
    result = _run(tmp_path, {"CI_RERUN_DEBUG": "1"}, observe_status=9)
    assert result.returncode != 0
    assert len(_calls(tmp_path)) == 1
    assert "native observe refused" in result.stderr


@pytest.mark.parametrize("overrides", [False, True])
def test_unrelated_goals_keep_public_inputs_and_carriers(tmp_path: Path, overrides: bool) -> None:
    result = _run(
        tmp_path, {"CI_RERUN_DEBUG": "unrelated-debug", "CI_RERUN_FAILED_ONLY": "unrelated-failed"},
        overrides=overrides, target="unrelated-rerun-probe",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(tmp_path) == [{
        "argv": ["probe", "environment"], "unrelated": "retained carrier",
        "public": dict(zip(FLAGS, ("unrelated-debug", "unrelated-failed"), strict=True)),
    }]


@pytest.mark.parametrize("overrides", [False, True])
@pytest.mark.parametrize("origin", ["command-line", "environment"])
@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("payload_form", ["make-shell", "backticks"])
def test_owned_evaluation_witness_precedes_guarded_refusal(
    tmp_path: Path, overrides: bool, origin: str, flag: str, payload_form: str,
) -> None:
    marker = tmp_path / "owned witness"
    command = "touch " + shlex.quote(str(marker))
    payload = "$(shell " + command + ")" if payload_form == "make-shell" else "`" + command + "`"
    witness = tmp_path / "unguarded-native.mk"
    witness.write_text("witness:\n\t@printf '%s' \"$(" + flag + ")\" >/dev/null\n", encoding="utf-8")
    environment = {key: value for key, value in os.environ.items()
                   if key not in RECURSION | set(FLAGS)}
    if origin == "environment":
        environment[flag] = payload
    control = subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if overrides else []),
         "-f", str(witness), "witness",
         *([flag + "=" + payload] if origin == "command-line" else [])],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False, timeout=10,
    )
    assert control.returncode == 0, control.stdout + control.stderr
    assert marker.exists(), "the same native input/origin must demonstrate evaluation"
    marker.unlink()

    result = _run(
        tmp_path, {flag: payload} if origin == "command-line" else {},
        inherited={flag: payload} if origin == "environment" else {}, overrides=overrides,
    )

    assert result.returncode != 0
    assert flag + " must be literal 0 or 1" in result.stderr
    assert not marker.exists()
    assert _calls(tmp_path) == []
    assert not (tmp_path / "ledger.json").exists()


@pytest.mark.parametrize("overrides", [False, True])
def test_private_carriers_cannot_override_public_diagnostic_flags(
    tmp_path: Path, overrides: bool,
) -> None:
    result = _run(
        tmp_path, {"CI_RERUN_DEBUG": "1", "CI_RERUN_FAILED_ONLY": "0",
                   "GLUDD_CI_RERUN_DEBUG": "0", "GLUDD_CI_RERUN_FAILED_ONLY": "1"},
        overrides=overrides,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(tmp_path)[-1]["argv"] == [
        "run", "rerun", "-R", "sandboxcom/gludd", "123", "--debug",
    ]


def test_rerun_help_is_a_native_read_only_cli_call(tmp_path: Path) -> None:
    result = _run(tmp_path, {}, target="ci-rerun-help")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call["argv"] for call in _calls(tmp_path)] == [["run", "rerun", "--help"]]
    assert "--debug" in result.stdout and "--failed" in result.stdout
    assert not (tmp_path / "ledger.json").exists()
