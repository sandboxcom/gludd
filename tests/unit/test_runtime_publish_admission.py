"""Native Make/observer routing contracts for batch publication runtime admission."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import makefile_layout
from scripts.check_make_target_contract import validate_contract

ROOT = Path(makefile_layout.__file__).resolve().parents[1]
RECOVERY_PATH = ROOT / "make/20-recovery-and-git.mk"
PIPELINES_PATH = ROOT / "make/95-collections-and-pipelines.mk"
GUARDRAILS_PATH = ROOT / "make/50-development-and-guardrails.mk"
CONTRACT_PATH = ROOT / "config/make_target_contract.json"
OWNER = "_pytest-runtime-compatibility-admission"
PUBLISH_OWNER = "_runtime-publish-admission"
HELP = "tests/unit/test_makefile_audit_deep.py::TestHelpNoDupTargets::test_no_duplicate_help_entries"


def _target(source: str, name: str) -> str:
    start = source.index(f"\n{name}:") + 1
    return source[start : source.index("\n\n", start)]


def _publish(
    tmp_path: Path,
    *,
    failure: str = "",
    forced: bool = False,
    count: int = 5,
    threshold: int = 5,
    push_status: int = 0,
    goal: str = "batch-push",
    validate_only: int = 0,
    runtime_state: str = "valid",
    gate_policy_context: str = "default",
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], Path]:
    """Run actual Make recipes and observer, replacing only native test/Git boundaries."""
    trace = tmp_path / "trace.jsonl"
    recorder = tmp_path / "boundary.py"
    recorder.write_text(
        "import json, os, pathlib, sys\n"
        f"trace = pathlib.Path({str(trace)!r})\n"
        "def record(kind, **values):\n"
        "    with trace.open('a') as stream:\n"
        "        stream.write(json.dumps(dict(kind=kind, **values)) + '\\n')\n"
        "names = ('GATE_TEST_PROFILE', 'GATE_EXACT_SHA_RESUME', 'GATE_MAX_FILES_PER_BATCH', 'GATE_BATCH_WORKERS')\n"
        "def context():\n"
        "    return dict(policy=[os.environ.get(name) for name in names], "
        "sentinel=os.environ.get('probe'), "
        "makeflags=os.environ.get('MAKEFLAGS'), makeoverrides=os.environ.get('MAKEOVERRIDES'))\n"
        "args = sys.argv[1:]\n"
        "if args[:3] == ['run', 'python', 'scripts/stream_command.py']:\n"
        "    record('observer', args=args[3:], **context())\n"
        f"    os.execv(sys.executable, [sys.executable, {str(ROOT / 'scripts/stream_command.py')!r}, *args[3:]])\n"
        "elif args[0] == 'test-files':\n"
        f"    phase = 'help' if args[1] == {HELP!r} else 'full'\n"
        "    record('runtime', phase=phase, files=args[1].split(), nproc=args[2], workers=args[3], flags=args[4], "
        "environment=os.environ.get('UV_PROJECT_ENVIRONMENT'), no_sync=os.environ.get('UV_NO_SYNC'), "
        "python=os.environ.get('UV_PYTHON'), **context())\n"
        f"    raise SystemExit(1 if phase == {failure!r} else 0)\n"
        "elif args[0] == 'record-verdict': record('verdict', **context())\n"
        "elif args[0] == 'verify-remote': record('verify-remote')\n"
        "else: raise AssertionError(args)\n",
        encoding="utf-8",
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake_git = binaries / "git"
    fake_git.write_text(
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        f"trace = pathlib.Path({str(trace)!r})\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['log', '--oneline']:\n"
        f"    print('\\n'.join('a' + str(i) + ' commit' for i in range({count})))\n"
        "elif args[:2] == ['branch', '--show-current']: print('gludd-test-runtime-publish')\n"
        "elif args[0] == 'rev-parse': print('a' * 40)\n"
        "elif args[0] == 'push':\n"
        "    with trace.open('a') as stream: stream.write(json.dumps(dict(kind='transport', args=args)) + '\\n')\n"
        f"    raise SystemExit({push_status})\n"
        "else: raise AssertionError(args)\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    python_metadata = binaries / "python3"
    python_metadata.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python_metadata.chmod(0o755)
    canonical = tmp_path / "fake-canonical-runtime" / "bin"
    canonical.mkdir(parents=True)
    runtime_python = canonical / "python"
    runtime_python.write_text(
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        f"trace = pathlib.Path({str(trace)!r})\n"
        "with trace.open('a') as stream: "
        "stream.write(json.dumps(dict(kind='runtime-identity', args=sys.argv[1:])) + '\\n')\n"
        f"raise SystemExit({0 if runtime_state == 'valid' else 17})\n",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)
    source = makefile_layout.compose_makefile(ROOT / "Makefile")
    policy_start = source.index("\nGATE_TEST_PROFILE ?=") + 1
    policy_end = source.index("\n.PHONY: gate-admission-config", policy_start)
    policy_defaults = source[policy_start:policy_end]
    recovery = RECOVERY_PATH.read_text(encoding="utf-8")
    publication = [
        _target(recovery, name)
        for name in (
            "batch-push",
            "force-batch-push",
            "force-push",
            "batch-push-nv",
            "master-force-push",
            "git-push-sandboxcom",
            "git-push-sandboxcom-nv",
            "push-dev-nv",
            "git-push-current-head-nv",
            "git-push-current-head-to-master-nv",
        )
    ]
    publication.append(_target(PIPELINES_PATH.read_text(encoding="utf-8"), "git-push-committed-head-nv"))
    publication.append(_target(GUARDRAILS_PATH.read_text(encoding="utf-8"), "development-force-push"))
    prerequisites = sorted(
        {
            "require-sandboxcom-ssh-key",
            "ci-busy-check",
            *(name for block in publication for name in block.splitlines()[0].partition(":")[2].split()),
        }
    )
    publish_owner = _target(recovery, PUBLISH_OWNER) if f"\n{PUBLISH_OWNER}:" in recovery else ""
    recipe = tmp_path / "Makefile"
    quoted_runner = f"{shlex.quote(sys.executable)} {shlex.quote(str(recorder))}"
    selected_environment = canonical.parent if runtime_state != "missing" else tmp_path / "absent-runtime"
    recipe.write_text(
        f"MAKE := make --no-print-directory -f {recipe}\nUV := {quoted_runner}\n"
        "probe := file-default\n"
        f"OBSERVED_ROOT := {tmp_path / 'observed'}\nWORK_ITEM_ID :=\nSSH_KEY := {tmp_path / 'absent-key'}\n"
        f"RUNTIME_PUBLISH_ENVIRONMENT := {selected_environment}\n"
        f"RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY := {validate_only}\n"
        + policy_defaults + "\n"
        + f".PHONY: batch-push run-watched {OWNER} {PUBLISH_OWNER} test-files _record-push-verdict "
        f"{' '.join(prerequisites)}\n"
        + "".join(f"{name}:\n\t@true\n\n" for name in prerequisites)
        + "\n\n".join(publication)
        + "\n\n"
        + publish_owner
        + "\n\n"
        + _target(source, "run-watched")
        + "\n\n"
        + _target(source, OWNER)
        + "\n\n"
        + f'test-files:\n\t@{quoted_runner} test-files "$(TESTFILES)" "$(NPROC)" '
        + '"$(GLUDD_XDIST_WORKERS)" "$(PYTEST_ARGS)"\n\n'
        + f"_record-push-verdict:\n\t@{quoted_runner} record-verdict\n\n"
        + f"verify-remote:\n\t@{quoted_runner} verify-remote\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "COMMIT_THRESHOLD": str(threshold),
        "GLUDD_FORCE_PUSH": "1" if forced else "0",
        "FORCE": "1" if forced else "0",
        "MAKEFLAGS": "",
        "MFLAGS": "",
        "MAKELEVEL": "0",
        "COUNT": str(count),
        "THRESHOLD": str(threshold),
    }
    names = (
        "GATE_TEST_PROFILE", "GATE_EXACT_SHA_RESUME", "GATE_MAX_FILES_PER_BATCH", "GATE_BATCH_WORKERS",
    )
    for name in names:
        environment.pop(name, None)
    command = [
        "make", "--no-print-directory", "-f", str(recipe), goal,
        "UV_PROJECT_ENVIRONMENT=.venv", "probe=kept",
    ]
    conflict = dict(zip(names, ("native", "1", "32", "2"), strict=True))
    if gate_policy_context == "environment":
        environment.update(conflict)
    elif gate_policy_context in {"command-line", "opaque-overrides"}:
        command.extend(f"{name}={value}" for name, value in conflict.items())
        if gate_policy_context == "opaque-overrides":
            command.append("MAKEOVERRIDES=probe=kept")
    else:
        assert gate_policy_context == "default"
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
    return result, rows, tmp_path / "observed"


def _assert_owned_once(rows: list[dict[str, object]], phases: list[tuple[str, str]]) -> None:
    observers = [row for row in rows if row["kind"] == "observer"]
    assert len(observers) == 1
    arguments = observers[0]["args"]
    assert isinstance(arguments, list)
    for name, value in (
        ("--label", "batch-push-runtime-admission"),
        ("--quiet-secs", "60"),
        ("--max-secs", "90"),
        ("--heartbeat-secs", "10"),
    ):
        assert arguments[arguments.index(name) + 1] == value
    assert arguments[-1] == OWNER
    identity = [row for row in rows if row["kind"] == "runtime-identity"]
    assert len(identity) == 1
    assert "sys.version_info[:2] == (3, 11)" in str(identity[0]["args"])
    assert "pytest_playwright" in str(identity[0]["args"])
    runtime = [row for row in rows if row["kind"] == "runtime"]
    assert [(row["phase"], row["workers"]) for row in runtime] == phases
    for row in runtime:
        workers = row["workers"]
        assert (row["nproc"], row["workers"]) == (workers, workers)
        assert "-W error" in str(row["flags"])
        assert f"--maxprocesses {workers}" in str(row["flags"])
        assert str(row["environment"]).endswith("fake-canonical-runtime")
        assert (row["no_sync"], row["python"]) == ("1", "3.11")


@pytest.mark.parametrize("failure", ("full", "help"))
@pytest.mark.parametrize("forced", (False, True))
def test_red_runtime_owner_blocks_transport_even_when_forced(
    tmp_path: Path,
    failure: str,
    forced: bool,
) -> None:
    result, rows, observed = _publish(tmp_path, failure=failure, forced=forced)
    assert result.returncode != 0, result.stdout + result.stderr
    _assert_owned_once(
        rows,
        [("full", "2")] if failure == "full" else [("full", "2"), ("help", "1")],
    )
    assert not any(row["kind"] in {"transport", "verdict"} for row in rows)
    assert "Runtime publication admission failed" in result.stdout + result.stderr
    assert list(observed.rglob("*.json")), "existing observer must retain failure evidence"


@pytest.mark.parametrize("forced", (False, True))
def test_green_runtime_owner_runs_once_before_normal_transport(tmp_path: Path, forced: bool) -> None:
    result, rows, observed = _publish(tmp_path, forced=forced, count=1 if forced else 5)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_owned_once(rows, [("full", "2"), ("help", "1"), ("full", "1")])
    assert [row["kind"] for row in rows] == [
        "runtime-identity",
        "observer",
        "runtime",
        "runtime",
        "runtime",
        "transport",
        "verdict",
    ]
    transport = next(row for row in rows if row["kind"] == "transport")
    transport_args = transport["args"]
    assert isinstance(transport_args, list)
    assert "--no-verify" not in transport_args
    assert list(observed.rglob("*.json")), "existing observer must retain success evidence"


@pytest.mark.parametrize(("count", "threshold", "successful"), ((1, 5, True), (5, 1, False)))
def test_nonpublication_does_not_run_runtime_or_transport(
    tmp_path: Path,
    count: int,
    threshold: int,
    successful: bool,
) -> None:
    result, rows, _ = _publish(tmp_path, count=count, threshold=threshold)
    assert (result.returncode == 0) is successful
    assert rows == []


def test_transport_failure_after_green_admission_never_records_success(tmp_path: Path) -> None:
    result, rows, _ = _publish(tmp_path, push_status=43)
    assert result.returncode != 0
    _assert_owned_once(rows, [("full", "2"), ("help", "1"), ("full", "1")])
    assert [row["kind"] for row in rows] == [
        "runtime-identity",
        "observer",
        "runtime",
        "runtime",
        "runtime",
        "transport",
    ]
    assert "success was not recorded" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "goal",
    (
        "force-batch-push",
        "force-push",
        "batch-push-nv",
        "master-force-push",
        "git-push-sandboxcom",
        "git-push-sandboxcom-nv",
        "push-dev-nv",
        "git-push-current-head-nv",
        "git-push-current-head-to-master-nv",
        "git-push-committed-head-nv",
        "development-force-push",
    ),
)
@pytest.mark.parametrize("failure", ("full", ""))
def test_legacy_and_forced_publication_cannot_skip_or_duplicate_runtime(
    tmp_path: Path,
    goal: str,
    failure: str,
) -> None:
    result, rows, _ = _publish(tmp_path, goal=goal, failure=failure, forced=True, validate_only=1)
    _assert_owned_once(
        rows,
        [("full", "2")] if failure else [("full", "2"), ("help", "1"), ("full", "1")],
    )
    assert (result.returncode == 0) is (not failure), result.stdout + result.stderr
    transports = [row for row in rows if row["kind"] == "transport"]
    assert len(transports) == (0 if failure else 1)


@pytest.mark.parametrize("runtime_state", ("missing", "wrong-version-or-plugin"))
def test_runtime_identity_failure_blocks_transport_without_environment_creation(
    tmp_path: Path,
    runtime_state: str,
) -> None:
    result, rows, _ = _publish(tmp_path, forced=True, runtime_state=runtime_state)
    assert result.returncode != 0
    assert not any(row["kind"] in {"observer", "runtime", "transport", "verdict"} for row in rows)
    assert not (tmp_path / "absent-runtime").exists()


def test_runtime_owner_plan_is_mutation_free_and_has_no_transport(tmp_path: Path) -> None:
    result, rows, observed = _publish(tmp_path, goal=PUBLISH_OWNER, validate_only=1, runtime_state="missing")
    assert result.returncode == 0, result.stdout + result.stderr
    assert rows == [] and not observed.exists()
    assert "max_seconds=90 quiet_seconds=60" in result.stdout
    assert "profile=release exact_sha_resume=0 max_files_per_batch=16 gate_workers=1" in result.stdout
    assert "release_ready=false gate_evidence=false" in result.stdout


def _assert_release_runtime_policy(rows: list[dict[str, object]]) -> None:
    observed = [row for row in rows if row["kind"] in {"observer", "runtime"}]
    assert len(observed) == 4
    for row in observed:
        assert row["policy"] == ["release", "0", "16", "1"], row
        assert row["sentinel"] == "kept", row
        assert "probe=kept" in str(row["makeflags"]), row


@pytest.mark.parametrize(
    "context", ("default", "environment", "command-line", "opaque-overrides"),
)
def test_publication_runtime_owns_release_policy_without_changing_caller(
    tmp_path: Path, context: str,
) -> None:
    result, rows, _ = _publish(tmp_path, gate_policy_context=context)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_owned_once(rows, [("full", "2"), ("help", "1"), ("full", "1")])
    _assert_release_runtime_policy(rows)
    verdict = next(row for row in rows if row["kind"] == "verdict")
    assert verdict["policy"] == ["native", "1", "32", "2"], verdict
    assert verdict["sentinel"] == "kept"
    owner = _target(RECOVERY_PATH.read_text(encoding="utf-8"), PUBLISH_OWNER)
    assert "MAKEFLAGS" not in owner
    assert "MAKEOVERRIDES" not in owner


def test_runtime_publish_owner_has_documented_mutation_free_contract(tmp_path: Path) -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    entry = next(entry for entry in contract["targets"] if entry["name"] == PUBLISH_OWNER)
    assert entry["not_for_direct_use"] is True
    assert "RUNTIME_PUBLISH_ADMISSION_VALIDATE_ONLY=1" in entry["behavior"]
    absent_environment = Path("/tmp") / "gludd-runtime-publish-absent"
    assert f"RUNTIME_PUBLISH_ENVIRONMENT={absent_environment}" in entry["behavior"]
    standalone = tmp_path / "Makefile"
    standalone.write_text(
        _target(RECOVERY_PATH.read_text(encoding="utf-8"), PUBLISH_OWNER) + "\n",
        encoding="utf-8",
    )
    assert validate_contract(standalone, {"targets": [entry]}) == []
