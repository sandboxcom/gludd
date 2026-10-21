"""Native Make/observer routing contracts for batch publication runtime admission."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
import yaml
from scripts import makefile_layout
from scripts.check_make_target_contract import validate_contract

ROOT = Path(makefile_layout.__file__).resolve().parents[1]
RECOVERY_PATH = ROOT / "make/20-recovery-and-git.mk"
WORKFLOW_PATH = ROOT / ".github/workflows/build.yml"
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
    manifest_status: int = 0,
    promotion_status: int = 0,
    pins_status: int = 0,
    gate_policy_context: str = "default",
    watched_trace_caller_mode: str | None = None,
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
        "sentinel=os.environ.get('probe'), venv=os.environ.get('VIRTUAL_ENV'), "
        "downloads=os.environ.get('UV_PYTHON_DOWNLOADS'), "
        "makeflags=os.environ.get('MAKEFLAGS'), makeoverrides=os.environ.get('MAKEOVERRIDES'), "
        "trace_path=os.environ.get('GLUDD_XDIST_TRACE_LOG'), "
        "trace_run_id=os.environ.get('GLUDD_XDIST_TRACE_RUN_ID'), "
        "trace_truncate=os.environ.get('GLUDD_XDIST_TRACE_TRUNCATE'))\n"
        "args = sys.argv[1:]\n"
        "if args[:3] == ['run', 'python', 'scripts/stream_command.py']:\n"
        "    record('observer', args=args[3:], environment=os.environ.get('UV_PROJECT_ENVIRONMENT'), "
        "no_sync=os.environ.get('UV_NO_SYNC'), python=os.environ.get('UV_PYTHON'), **context())\n"
        f"    os.execv(sys.executable, [sys.executable, {str(ROOT / 'scripts/stream_command.py')!r}, *args[3:]])\n"
        "elif args[:3] == ['run', 'python', 'scripts/ansible_runtime_artifacts.py']:\n"
        "    record('manifest', args=args[3:], environment=os.environ.get('UV_PROJECT_ENVIRONMENT'), "
        "venv=os.environ.get('VIRTUAL_ENV'), no_sync=os.environ.get('UV_NO_SYNC'), "
        "python=os.environ.get('UV_PYTHON'), downloads=os.environ.get('UV_PYTHON_DOWNLOADS'))\n"
        f"    raise SystemExit({manifest_status})\n"
        "elif args[:4] == ['run', 'python', '-m', 'scripts.check_gate_failure_promotions']:\n"
        "    record('promotions', args=args[4:], environment=os.environ.get('UV_PROJECT_ENVIRONMENT'), "
        "no_sync=os.environ.get('UV_NO_SYNC'), python=os.environ.get('UV_PYTHON'), **context())\n"
        f"    raise SystemExit({promotion_status})\n"
        "elif args[0] == 'test-files':\n"
        f"    phase = 'help' if args[1] == {HELP!r} else 'full'\n"
        "    if '::test_repository_manifest_seeds_each_promoted_failure_family' in args[1]: phase = 'pins'\n"
        "    record('prerequisite-pins' if phase == 'pins' else 'runtime', phase=phase, "
        "files=args[1].split(), nproc=args[2], workers=args[3], flags=args[4], "
        "environment=os.environ.get('UV_PROJECT_ENVIRONMENT'), no_sync=os.environ.get('UV_NO_SYNC'), "
        "python=os.environ.get('UV_PYTHON'), **context())\n"
        f"    raise SystemExit({pins_status} if phase == 'pins' else (1 if phase == {failure!r} else 0))\n"
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
        + _target(source, "check-ansible-base-image")
        + "\n\n"
        + _target(source, "check-gate-failure-promotions")
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
        "UV_PROJECT_ENVIRONMENT=.venv", "VIRTUAL_ENV=.venv", "UV_NO_SYNC=0",
        "UV_PYTHON=3.14", "UV_PYTHON_DOWNLOADS=automatic",
        "ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY=1", "probe=kept",
        "GATE_FAILURE_PROMOTION_MANIFEST=/tmp/gludd-untrusted-override.json",
    ]
    if watched_trace_caller_mode is not None:
        command.append("RUN_WATCHED_PYTEST_TRACE=" + watched_trace_caller_mode)
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
    assert len(observers) == 2
    _assert_prerequisite_once(rows)
    arguments = _observer_arguments(rows, "batch-push-runtime-admission")
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
    manifests = [row for row in rows if row["kind"] == "manifest"]
    assert len(manifests) == 1
    assert manifests[0]["args"] == ["check-base-image"]
    assert str(manifests[0]["environment"]).endswith("fake-canonical-runtime")
    assert manifests[0]["venv"] == manifests[0]["environment"]
    assert (manifests[0]["no_sync"], manifests[0]["python"], manifests[0]["downloads"]) == (
        "1", "3.11", "never",
    )
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
        "manifest",
        "observer",
        "promotions",
        "prerequisite-pins",
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
        "manifest",
        "observer",
        "promotions",
        "prerequisite-pins",
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
    assert "manifest_check=exact-pinned" in result.stdout
    assert "prerequisite_check=check-gate-failure-promotions" in result.stdout
    assert "prerequisite_max_seconds=90 prerequisite_quiet_seconds=60" in result.stdout
    assert "profile=release exact_sha_resume=0 max_files_per_batch=16 gate_workers=1" in result.stdout
    assert "release_ready=false gate_evidence=false" in result.stdout


def _assert_release_runtime_policy(rows: list[dict[str, object]]) -> None:
    observed = [row for row in rows if row["kind"] in {"observer", "runtime"}]
    assert len(observed) == 5
    for row in observed:
        assert row["policy"] == ["release", "0", "16", "1"], row
        assert row["sentinel"] == "kept", row
        assert "probe=kept" in str(row["makeflags"]), row


@pytest.mark.parametrize(
    "policy_context", ("default", "environment", "command-line", "opaque-overrides"),
)
def test_publication_runtime_owns_release_policy_without_changing_caller(
    tmp_path: Path, policy_context: str,
) -> None:
    result, rows, _ = _publish(tmp_path, gate_policy_context=policy_context)
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


@pytest.mark.parametrize("manifest_status", (1, 2, 124))
@pytest.mark.parametrize("forced", (False, True))
def test_unavailable_exact_manifest_stops_before_runtime_and_transport(
    tmp_path: Path, manifest_status: int, forced: bool,
) -> None:
    result, rows, observed = _publish(
        tmp_path, manifest_status=manifest_status, forced=forced,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert [row["kind"] for row in rows] == ["runtime-identity", "manifest"]
    assert rows[-1]["args"] == ["check-base-image"]
    assert not observed.exists(), "a failed prerequisite must not start expensive proof"
    assert "Pinned Ansible base manifest admission failed" in result.stdout + result.stderr
    assert "Git transport was not attempted" in result.stdout + result.stderr


SIX_PREREQUISITE_PINS = [
    "tests/unit/test_gate_failure_promotion.py::test_repository_manifest_seeds_each_promoted_failure_family",
    "tests/unit/test_gate_failure_promotion.py::test_make_wiring_and_target_contract_are_explicit",
    "tests/unit/test_runtime_admission_scheduling.py::test_runtime_admission_retains_entire_roster_and_fresh_help",
    "tests/unit/test_admission_cold_start_selection.py::test_real_admission_filters_only_never_selected_main_siblings",
    "tests/unit/test_runtime_admission_scheduling.py::test_native_git_metadata_query_count_executes_once_in_first_cohort",
    "tests/unit/test_gate_failure_promotion.py::test_async_canonical_profile_contract_fails_in_compatibility_admission_first",
]


def _observer_arguments(rows: list[dict[str, object]], label: str) -> list[str]:
    matches = [
        row["args"] for row in rows
        if row["kind"] == "observer" and isinstance(row["args"], list) and label in row["args"]
    ]
    assert len(matches) == 1, rows
    arguments = matches[0]
    assert isinstance(arguments, list) and all(isinstance(value, str) for value in arguments)
    return cast(list[str], arguments)


def _assert_prerequisite_once(
    rows: list[dict[str, object]], *, label: str = "batch-push-prerequisite-admission",
) -> None:
    arguments = _observer_arguments(rows, label)
    for name, value in (
        ("--label", label), ("--quiet-secs", "60"), ("--max-secs", "90"),
        ("--heartbeat-secs", "10"),
    ):
        assert arguments[arguments.index(name) + 1] == value
    assert arguments[-2:] == [
        "check-gate-failure-promotions",
        "GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json",
    ]
    promotions = [row for row in rows if row["kind"] == "promotions"]
    pins = [row for row in rows if row["kind"] == "prerequisite-pins"]
    assert len(promotions) == len(pins) == 1
    metadata = promotions[0]["args"]
    assert isinstance(metadata, list)
    assert metadata[:3] == ["--manifest", "config/gate_failure_promotions.json", "--repository-root"]
    assert len(metadata) == 4 and Path(str(metadata[3])).is_absolute()
    assert pins[0]["files"] == SIX_PREREQUISITE_PINS
    assert (pins[0]["nproc"], pins[0]["workers"]) == ("1", "1")
    assert shlex.split(str(pins[0]["flags"])) == [
        "-W", "error", "-q", "-n", "0", "--maxprocesses", "1", "--timeout=30",
    ]
    for row in rows:
        if row["kind"] in {"observer", "promotions", "prerequisite-pins", "runtime"}:
            assert row["environment"] == row["venv"]
            assert (row["no_sync"], row["python"], row["downloads"]) == ("1", "3.11", "never")
    run_ids = []
    for row in rows:
        if row["kind"] == "observer":
            argv = row["args"]
            assert isinstance(argv, list)
            run_ids.append(argv[argv.index("--run-id") + 1])
    assert len(set(run_ids)) == len(run_ids)


def _hosted_steps() -> list[dict[str, object]]:
    workflow = cast(dict[str, object], yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8")))
    jobs = cast(dict[str, object], workflow["jobs"])
    gate = cast(dict[str, object], jobs["gate"])
    return cast(list[dict[str, object]], gate["steps"])


def _hosted(
    tmp_path: Path, *, manifest_status: int = 0, promotion_status: int = 0,
    pins_status: int = 0, failure: str = "",
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], Path]:
    plan, rows, _ = _publish(
        tmp_path, goal=PUBLISH_OWNER, validate_only=1, manifest_status=manifest_status,
        promotion_status=promotion_status, pins_status=pins_status, failure=failure,
    )
    assert plan.returncode == 0 and rows == [], plan.stdout + plan.stderr
    steps = _hosted_steps()
    names = [
        "Verify pinned Ansible base manifest", "Canonical promotion prerequisite admission",
        "Known runtime and handoff regression admission",
    ]
    stages = [next(step for step in steps if step.get("name") == name) for name in names]
    recipe = tmp_path / "Makefile"
    chunks = ["\nhosted-probe:\n"]
    for index, stage in enumerate(stages):
        environment = cast(dict[str, str], stage.get("env", {}))
        bindings = []
        for name, value in environment.items():
            expanded = value.replace("${{ github.workspace }}", str(tmp_path))
            expanded = expanded.replace("${{ matrix.python-version }}", "3.11")
            bindings.append(f"{name}={shlex.quote(expanded)}")
        chunks.append(f"\t@$(MAKE) --no-print-directory _hosted_{index} {' '.join(bindings)}\n")
    chunks.append("\t@$(MAKE) --no-print-directory verify-remote\n")
    for index, stage in enumerate(stages):
        body = str(stage["run"]).rstrip()
        for expression, value in (
            ("${{ github.run_id }}", "4242"), ("${{ github.run_attempt }}", "3"),
            ("${{ matrix.python-version }}", "3.11"),
        ):
            body = body.replace(expression, value)
        body = body.replace("$", "$$").replace("make ", "$(MAKE) ")
        chunks.append(f"\n_hosted_{index}:\n\t@" + body.replace("\n", "\n\t") + "\n")
    recipe.write_text(recipe.read_text(encoding="utf-8") + "".join(chunks), encoding="utf-8")
    environment = dict(
        os.environ, UV_PROJECT_ENVIRONMENT=str(tmp_path / ".venv"),
        VIRTUAL_ENV=str(tmp_path / ".venv"), UV_NO_SYNC="1", UV_PYTHON="3.11",
        UV_PYTHON_DOWNLOADS="never", MAKEFLAGS="", MFLAGS="", MAKELEVEL="0",
    )
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(recipe), "hosted-probe"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=20,
    )
    trace = tmp_path / "trace.jsonl"
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
    return result, rows, tmp_path / ".gate-logs/observed"


@pytest.mark.parametrize("forced", (False, True))
@pytest.mark.parametrize("stage", ("manifest-parser", "six-pins"))
@pytest.mark.parametrize("native_status", (1, 2, 124))
def test_canonical_prerequisite_failure_blocks_runtime_and_transport(
    tmp_path: Path, forced: bool, stage: str, native_status: int,
) -> None:
    result, rows, observed = _publish(
        tmp_path, forced=forced,
        promotion_status=native_status if stage == "manifest-parser" else 0,
        pins_status=native_status if stage == "six-pins" else 0,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert [row["kind"] for row in rows] == [
        "runtime-identity", "manifest", "observer", "promotions",
        *(["prerequisite-pins"] if stage == "six-pins" else []),
    ]
    _observer_arguments(rows, "batch-push-prerequisite-admission")
    assert not any(row["kind"] in {"runtime", "transport", "verdict"} for row in rows)
    assert "Canonical promotion prerequisite failed" in result.stdout + result.stderr
    assert "runtime replay was not started; Git transport was not attempted" in result.stdout + result.stderr
    assert list((observed / "batch-push-prerequisite-admission").rglob("*.json"))
    assert not (observed / "batch-push-runtime-admission").exists()
    assert f"Error {native_status}" in result.stdout + result.stderr


@pytest.mark.parametrize("policy_context", ("default", "environment", "command-line", "opaque-overrides"))
def test_canonical_prerequisite_owns_environment_policy_and_stage_identity(
    tmp_path: Path, policy_context: str,
) -> None:
    result, rows, observed = _publish(tmp_path, gate_policy_context=policy_context)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_owned_once(rows, [("full", "2"), ("help", "1"), ("full", "1")])
    _assert_release_runtime_policy(rows)
    for row in rows:
        if row["kind"] in {"promotions", "prerequisite-pins"}:
            assert row["policy"] == ["release", "0", "16", "1"]
    for label in ("batch-push-prerequisite-admission", "batch-push-runtime-admission"):
        assert list((observed / label).rglob("*.json"))
    assert [row["kind"] for row in rows] == [
        "runtime-identity", "manifest", "observer", "promotions", "prerequisite-pins",
        "observer", "runtime", "runtime", "runtime", "transport", "verdict",
    ]


def test_hosted_canonical_prerequisite_has_exact_order_bounds_and_environment() -> None:
    steps = _hosted_steps()
    names = [step.get("name") for step in steps]
    image = names.index("Verify pinned Ansible base manifest")
    checker = names.index("Canonical promotion prerequisite admission")
    runtime = names.index("Known runtime and handoff regression admission")
    assert image + 1 == checker and checker + 1 == runtime
    stage = steps[checker]
    assert stage.get("if") is None and stage.get("continue-on-error") is None
    assert stage["env"] == {
        "UV_PROJECT_ENVIRONMENT": "${{ github.workspace }}/.venv",
        "VIRTUAL_ENV": "${{ github.workspace }}/.venv",
        "UV_NO_SYNC": "1", "UV_PYTHON": "${{ matrix.python-version }}",
        "UV_PYTHON_DOWNLOADS": "never", "GATE_TEST_PROFILE": "release",
        "GATE_EXACT_SHA_RESUME": "0", "GATE_MAX_FILES_PER_BATCH": "16",
        "GATE_BATCH_WORKERS": "1",
    }
    body = str(stage["run"])
    assert "check-gate-failure-promotions GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json" in body
    assert [
        token.partition("=")[2] for token in shlex.split(body)
        if token.partition("=")[0] == "OBSERVED_LABEL"
    ] == ["ci-gate-failure-promotions"]
    assert "STALL_SECS=60 MAX_SECS=90" in body
    assert "OBSERVED_HEARTBEAT_SECS=10" in body
    assert "OBSERVED_ROOT=.gate-logs/observed OBSERVED_RETAIN_RUNS=2" in body
    assert "LOG=.gate-logs/ci-gate-failure-promotions.log" in body
    assert "${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.python-version }}" in body
    assert "=== CI PHASE:" not in body


@pytest.mark.parametrize("stage", ("manifest", "manifest-parser", "six-pins", "runtime", ""))
def test_hosted_native_stages_stop_at_first_failure(
    tmp_path: Path, stage: str,
) -> None:
    result, rows, observed = _hosted(
        tmp_path, manifest_status=17 if stage == "manifest" else 0,
        promotion_status=17 if stage == "manifest-parser" else 0,
        pins_status=17 if stage == "six-pins" else 0,
        failure="full" if stage == "runtime" else "",
    )
    assert (result.returncode == 0) is (stage == ""), result.stdout + result.stderr
    expected = ["manifest"]
    if stage != "manifest":
        expected += ["observer", "promotions"]
        if stage != "manifest-parser":
            expected += ["prerequisite-pins"]
            if stage != "six-pins":
                expected += ["observer", "runtime"]
                if stage != "runtime":
                    expected += ["runtime", "runtime", "verify-remote"]
    assert [row["kind"] for row in rows] == expected
    if stage in {"", "runtime"}:
        _assert_prerequisite_once(rows, label="ci-gate-failure-promotions")
        arguments = _observer_arguments(rows, "ci-runtime-regression-admission")
        assert arguments[-1] == OWNER
        assert arguments[arguments.index("--max-secs") + 1] == "90"
        assert arguments[arguments.index("--quiet-secs") + 1] == "60"
        runtime = [row for row in rows if row["kind"] == "runtime"]
        assert [(row["nproc"], row["workers"]) for row in runtime] == (
            [("2", "2")] if stage == "runtime" else [("2", "2"), ("1", "1"), ("1", "1")]
        )
        assert list((observed / "ci-gate-failure-promotions").rglob("*.json"))
        for row in rows:
            if row["kind"] in {"promotions", "prerequisite-pins"}:
                assert row["policy"] == ["release", "0", "16", "1"]
    if stage:
        assert not any(row["kind"] == "verify-remote" for row in rows)


@pytest.mark.parametrize("watched_trace_origin", ["default", "environment", "command-line"])
def test_publication_observers_own_fresh_native_trace_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, watched_trace_origin: str,
) -> None:
    monkeypatch.delenv("RUN_WATCHED_PYTEST_TRACE", raising=False)
    if watched_trace_origin == "environment":
        monkeypatch.setenv("RUN_WATCHED_PYTEST_TRACE", "0")
    result, rows, observed = _publish(
        tmp_path, watched_trace_caller_mode="0" if watched_trace_origin == "command-line" else None,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_owned_once(rows, [("full", "2"), ("help", "1"), ("full", "1")])
    for label, child_kind in (
        ("batch-push-prerequisite-admission", "prerequisite-pins"),
        ("batch-push-runtime-admission", "runtime"),
    ):
        arguments = _observer_arguments(rows, label)
        assert arguments.count("--pytest-trace") == 1
        assert arguments.index("--pytest-trace") < arguments.index("--")
        status = json.loads((observed / label / "current.json").read_text(encoding="utf-8"))
        trace_path = status["trace_path"]
        assert isinstance(trace_path, str)
        assert Path(trace_path).is_file()
        assert Path(trace_path).parent == observed / label
        assert Path(trace_path).name == status["run_id"] + ".pytest.jsonl"
        children = [row for row in rows if row["kind"] == child_kind]
        assert len(children) == (3 if child_kind == "runtime" else 1)
        for child in children:
            assert child["trace_path"] == trace_path
            assert child["trace_run_id"] == status["run_id"]
            assert child["trace_truncate"] == "0"


@pytest.mark.parametrize("watched_trace_failure", ["full", "help"])
def test_failed_publication_observer_retains_its_owned_native_trace(
    tmp_path: Path, watched_trace_failure: str,
) -> None:
    result, rows, observed = _publish(
        tmp_path, failure=watched_trace_failure, watched_trace_caller_mode="0",
    )
    assert result.returncode != 0
    assert not any(row["kind"] in {"transport", "verdict", "verify-remote"} for row in rows)
    status = json.loads(
        (observed / "batch-push-runtime-admission" / "current.json").read_text(encoding="utf-8")
    )
    assert status["state"] == "failed"
    assert status["exit_code"] != 0
    trace_path = status["trace_path"]
    assert isinstance(trace_path, str)
    assert Path(trace_path).is_file()
    assert Path(trace_path).name == status["run_id"] + ".pytest.jsonl"
    assert Path(trace_path).parent == observed / "batch-push-runtime-admission"
    runtime = [row for row in rows if row["kind"] == "runtime"]
    assert len(runtime) == (1 if watched_trace_failure == "full" else 2)
    assert all(row["trace_path"] == trace_path for row in runtime)
