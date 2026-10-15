"""Behavioral routing and fail-closed evidence contracts for the release gate."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import run_ci_shards_serial as serial
from scripts.gate_status_attestation import repository_state_id, verify_terminal_status
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
MAKE_EXECUTABLE = shutil.which("make") or "make"


def _make(*arguments: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [MAKE_EXECUTABLE, "--no-print-directory", *arguments], cwd=cwd, env=env,
        capture_output=True, text=True, check=False, timeout=20,
    )


@pytest.mark.parametrize("profile", ["", "unknown", "Release"])
def test_profile_enum_rejected_before_heavy_work(profile: str) -> None:
    result = _make(
        "gate-admission-config", f"GATE_TEST_PROFILE={profile}",
        "GATE_EXACT_SHA_RESUME=0", "GATE_MAX_FILES_PER_BATCH=16", "GATE_BATCH_WORKERS=1",
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "GATE_TEST_PROFILE must be native or release" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "override",
    [
        "PYTEST_CMD=true", "PYTEST_ARGS=-q", "PYTEST_ADDOPTS=-k none",
        "ALLOW_DIRTY_CI_REPLICA=1", "ALLOW_DIRTY_FOCUSED_REPRO=1",
        "DUAL_TRACK_LOCAL_VALIDATE_ONLY=1", "GATE_BATCH_WORKERS=2",
        "GATE_PREFLIGHT_TARGETS=_gate-preflight-fixture-pass-one", "MYPY_MAX=5",
    ],
)
def test_release_profile_rejects_diagnostic_overrides(override: str) -> None:
    result = _make(
        "gate-admission-config", "GATE_TEST_PROFILE=release", "GATE_EXACT_SHA_RESUME=0",
        "GATE_MAX_FILES_PER_BATCH=16", "GATE_BATCH_WORKERS=1", override,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "release profile" in result.stdout + result.stderr


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True,
        text=True, check=True, timeout=10,
    )
    return result.stdout.strip()


@pytest.fixture
def release_repo(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """Use the real shell driver and validator, with only the Make child faked."""
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for source in (ROOT / "scripts").iterdir():
        if source.is_file():
            destination = scripts / source.name
            if source.name == "run_gate.sh":
                destination.write_bytes(source.read_bytes())
            else:
                destination.symlink_to(source)
    (root / ".gitignore").write_text(".gate*\nresources*\n__pycache__/\n")
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "gate-contract@example.invalid")
    _git(root, "config", "user.name", "Gate Contract")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "gate fixture")
    sha = _git(root, "rev-parse", "HEAD")
    resources = root / "resources" / "gate-contract"
    resources.mkdir(parents=True)
    executable_dir = resources / "bin"
    executable_dir.mkdir()
    (executable_dir / "python3").symlink_to(sys.executable)
    fake_uv = executable_dir / "uv"
    fake_uv.write_text(f"#!{sys.executable}\nimport sys\nprint('unexpected native runner')\nsys.exit(59)\n")
    fake_uv.chmod(0o755)
    producer_python = Path(f"{resources}-toolchain") / "ci-shards" / "python-3.11" / "bin" / "python"
    producer_python.parent.mkdir(parents=True)
    producer_python.write_text(
        f"#!{sys.executable}\nimport os, sys\nos.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
    )
    producer_python.chmod(0o755)
    pairing = serial._attestation_pairing(list(serial.DEFAULT_SHARDS), pytest_args=["-W", "error"])
    # The fake producer models locked release evidence, not the interpreter running this test.
    policy = serial.release_execution_policy()
    pairing.update(execution_policy=policy, execution_policy_sha256=serial.canonical_json_sha256(policy))
    payload = {
        "schema_version": serial.ATTESTATION_SCHEMA_VERSION,
        "lane": "local", "status": "pass", "returncode": 0,
        "identity": {"head_sha": sha, "expected_sha": sha, "clean": True, "exact_sha": True, "queries_ok": True},
        "runner": "scripts/run_ci_shards_serial.py", "python": "3.11.14 (fixture)",
        "shards": list(serial.DEFAULT_SHARDS),
        "started_at": "2026-10-12T00:00:00Z", "completed_at": "2026-10-12T00:00:01Z",
        **pairing,
    }
    template = resources / "template.json"
    template.write_text(json.dumps(payload))
    fake_make = executable_dir / "make"
    fake_make.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "root = pathlib.Path(os.environ['FAKE_RESOURCE_DIR'])\n"
        "(root / 'make-arguments.json').write_text(json.dumps(sys.argv[1:]))\n"
        "rc = int(os.environ.get('FAKE_MAKE_RC', '0'))\n"
        "write = rc == 0 or os.environ.get('FAKE_WRITE_FAILURE_RECEIPT') == '1'\n"
        "if write and os.environ.get('FAKE_WRITE_RECEIPT', '1') == '1':\n"
        "    output = root / 'ci-shards' / 'attestation.json'\n"
        "    output.parent.mkdir(exist_ok=True)\n"
        "    output.write_bytes((root / 'template.json').read_bytes())\n"
        "sys.exit(rc)\n"
    )
    fake_make.chmod(0o755)
    environment = {
        **os.environ, "PATH": f"{executable_dir}{os.pathsep}{os.environ['PATH']}",
        "MAKE": str(fake_make), "GATE_TEST_PROFILE": "release", "GLUDD_GATE_BATCH_WORKERS": "1",
        "GLUDD_GATE_EXACT_SHA_RESUME": "0", "GLUDD_GATE_MAX_FILES_PER_BATCH": "16",
        "GLUDD_PROJECT_NAMESPACE": "gate-contract", "GLUDD_RESOURCE_ROOT": str(root / "resources"),
        "GLUDD_CANDIDATE_SHA": sha, "GLUDD_GATE_KEY_PATH": str(resources / "gate.key"),
        "GATE_STATUS_FILE": str(root / ".gate-status.next"), "GATE_FINAL_STATUS_FILE": str(root / ".gate-status"),
        "GATE_FAILED_FILE": str(root / ".gate-failed"), "GATE_TERMINALIZED_FILE": str(root / ".gate-terminalized"),
        "FAKE_RESOURCE_DIR": str(resources), "PYTEST_CMD": "", "PYTEST_ARGS": "", "PYTEST_ADDOPTS": "",
        "DUAL_TRACK_LOCAL_VALIDATE_ONLY": "0", "ALLOW_DIRTY_CI_REPLICA": "0", "ALLOW_DIRTY_FOCUSED_REPRO": "0",
        "MAKEFLAGS": "", "MFLAGS": "", "MAKELEVEL": "0",
    }
    return root, environment, template


def test_release_receipt_fixture_models_locked_policy_not_native_interpreter(
    release_repo: tuple[Path, dict[str, str], Path],
) -> None:
    _, _, template = release_repo
    payload = json.loads(template.read_text())
    policy = payload["execution_policy"]
    assert policy["python_version"] == "3.11"
    assert policy["python_implementation"] == "cpython"
    assert policy["pytest_args"] == ["-W", "error"]
    assert policy == serial.release_execution_policy()
    assert payload["execution_policy_sha256"] == serial.canonical_json_sha256(policy)


def _run_driver(root: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts" / "run_gate.sh")], cwd=root, env=environment,
        capture_output=True, text=True, check=False, timeout=20,
    )


@pytest.mark.parametrize("resume", ["0", "1"])
def test_real_driver_routes_to_canonical_producer_once(
    release_repo: tuple[Path, dict[str, str], Path], resume: str,
) -> None:
    root, environment, _ = release_repo
    environment["GLUDD_GATE_EXACT_SHA_RESUME"] = resume
    result = _run_driver(root, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = json.loads((Path(environment["FAKE_RESOURCE_DIR"]) / "make-arguments.json").read_text())
    assert arguments == [
        "--no-print-directory", "test-ci-dual-track-local", "DUAL_TRACK_LOCAL_VALIDATE_ONLY=0",
        "PYTEST_ARGS=", "ALLOW_DIRTY_CI_REPLICA=0", "ALLOW_DIRTY_FOCUSED_REPRO=0",
        "WORKERS_PER_SHARD=1", "MAX_FILES_PER_BATCH=16", f"DUAL_TRACK_RESUME={resume}",
        "DUAL_TRACK_WATCHDOG_OWNED_GATE=1",
    ]
    status = (root / ".gate-status.next").read_text()
    assert "test PASS 0 profile=release" in status
    assert environment["GLUDD_CANDIDATE_SHA"] in status
    assert not (root / ".gate-failed").exists()
    assert not list(Path(environment["FAKE_RESOURCE_DIR"]).glob("gate-*"))


@pytest.mark.parametrize("returncode", [7, 130, 143])
def test_failed_or_cancelled_make_child_never_becomes_pass(
    release_repo: tuple[Path, dict[str, str], Path], returncode: int,
) -> None:
    root, environment, _ = release_repo
    environment["FAKE_MAKE_RC"] = str(returncode)
    result = _run_driver(root, environment)
    assert result.returncode == returncode, result.stdout + result.stderr
    assert (root / ".gate-failed").exists()
    status = root / (".gate-status" if returncode in (130, 143) else ".gate-status.next")
    assert "test PASS" not in status.read_text()
    assert "test FAIL" in status.read_text()
    if returncode in (130, 143):
        key = bytes.fromhex(Path(environment["GLUDD_GATE_KEY_PATH"]).read_text().strip())
        assert verify_terminal_status(status, state_id=repository_state_id(root), key=key).ok
        assert (root / ".gate-terminalized").exists()


@pytest.mark.parametrize("invalid", ["native-policy", "wrong-python-version", "wrong-sha", "missing", "unchanged"])
def test_noncanonical_or_stale_receipt_is_not_gate_release_evidence(
    release_repo: tuple[Path, dict[str, str], Path], invalid: str,
) -> None:
    root, environment, template = release_repo
    payload = json.loads(template.read_text())
    if invalid == "native-policy":
        policy = serial.execution_policy(["-q"])
        payload.update(execution_policy=policy, execution_policy_sha256=serial.canonical_json_sha256(policy))
    elif invalid == "wrong-python-version":
        policy = serial.release_execution_policy()
        policy["python_version"] = "3.14"
        payload.update(execution_policy=policy, execution_policy_sha256=serial.canonical_json_sha256(policy))
    elif invalid == "wrong-sha":
        payload["identity"]["head_sha"] = "b" * 40
    else:
        environment["FAKE_WRITE_RECEIPT"] = "0"
        if invalid == "unchanged":
            receipt = Path(environment["FAKE_RESOURCE_DIR"]) / "ci-shards" / "attestation.json"
            receipt.parent.mkdir()
            receipt.write_text(json.dumps(payload))
    template.write_text(json.dumps(payload))
    result = _run_driver(root, environment)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "GATE-RELEASE-ATTESTATION-REJECTED" in result.stdout
    expected_error = {
        "native-policy": "does not use the release execution policy",
        "wrong-python-version": "does not use the release execution policy",
        "wrong-sha": "does not match candidate SHA",
        "missing": "unreadable attestation",
        "unchanged": "was not refreshed by this gate",
    }[invalid]
    assert expected_error in result.stdout
    assert "test PASS" not in (root / ".gate-status.next").read_text()
    assert (root / ".gate-failed").exists()


def test_actual_gate_recipe_forwards_profile_and_exact_candidate() -> None:
    source = compose_makefile(ROOT / "Makefile")
    gate = source.split("\ngate:", 1)[1].split("\n# gate-lite:", 1)[0]
    assert 'GATE_TEST_PROFILE="$(GATE_TEST_PROFILE)"' in gate
    assert 'GLUDD_CANDIDATE_SHA="$(GATE_CANDIDATE_SHA)"' in gate
    assert gate.index("gate-admission-config") < gate.index("_gate-run-lock-acquire")
    assert "_gate-release-state-check" in gate
    assert "export GATE_TEST_PROFILE" in source


def test_producer_pins_one_worker_and_keeps_watchdog_owner() -> None:
    source = compose_makefile(ROOT / "Makefile")
    producer = source.split("\ntest-ci-dual-track-local:", 1)[1].split("\ntest-ci-shard:", 1)[0]
    assert "--batch-workers 1" in producer
    assert "--watchdog-owned-gate" in producer
    assert "DUAL_TRACK_WATCHDOG_OWNED_GATE" in producer
    assert "@set -e;" in producer


def test_recent_release_failures_are_checked_before_full_proof() -> None:
    source = compose_makefile(ROOT / "Makefile")
    owner = source.split("\n_pytest-runtime-compatibility-admission:", 1)[1].split(
        "\n_ansible-role-variable-prefix:", 1,
    )[0]
    selected = next(line for line in owner.splitlines() if line.strip().startswith("TESTFILES="))
    selectors = selected.split('TESTFILES="', 1)[1].split('"', 1)[0].split()
    manifest = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    required = {
        "reviewed-focus-environment-naming": (
            "tests/unit/test_env_var_audit_deep.py::TestGLUDDNamingConvention::test_no_trailing_underscore"
        ),
        "reviewed-focus-environment-documentation": (
            "tests/unit/test_env_var_audit_deep.py::TestGLUDDDocumentation::test_config_reference_documents_live_vars"
        ),
        "reviewed-focus-required-run-identity": (
            "tests/unit/test_env_var_audit_deep.py::TestGLUDDDefaultConsistency::test_default_values_consistent"
        ),
        "backlog-public-help-contract": (
            "tests/unit/test_backlog_audit_target.py::test_backlog_target_is_public_registered_and_documented"
        ),
        "local-release-post-failure-opt-in": (
            "tests/unit/test_canonical_release_gate.py::"
            "test_actual_local_release_recipe_collects_later_failures_in_one_invocation"
        ),
    }
    families = {family["id"]: family for family in manifest["families"]}
    for family_id, node in required.items():
        assert node in selectors or node.split("::", 1)[0] in selectors
        assert families[family_id]["node"] == node
        assert families[family_id]["owner_target"] == "_pytest-runtime-compatibility-admission"
        assert families[family_id]["full_gate_phase"] == "test"


@pytest.mark.parametrize("returncode", [0, 143])
def test_actual_make_recipe_executes_release_driver_and_preserves_cancellation(
    release_repo: tuple[Path, dict[str, str], Path], returncode: int,
) -> None:
    root, environment, _ = release_repo
    environment["FAKE_MAKE_RC"] = str(returncode)
    source = compose_makefile(ROOT / "Makefile").splitlines()
    start = next(index for index, line in enumerate(source) if line.startswith('\t@GATE_TEST_PROFILE="'))
    end = next(index for index in range(start, len(source)) if source[index] == "\t}")
    recipe_file = Path(environment["FAKE_RESOURCE_DIR"]) / "recipe.mk"
    recipe_file.write_text(
        "GATE_TEST_PROFILE := release\n"
        f"GATE_CANDIDATE_SHA := {environment['GLUDD_CANDIDATE_SHA']}\n"
        "GATE_EXACT_SHA_RESUME := 0\nGATE_MAX_FILES_PER_BATCH := 16\nGATE_BATCH_WORKERS := 1\n"
        "GATE_RUN_LOCK :=\ncontract:\n" + "\n".join(source[start : end + 1]) + "\n"
    )
    result = _make("-f", str(recipe_file), "contract", cwd=root, env=environment)
    assert result.returncode == (0 if returncode == 0 else 2), result.stdout + result.stderr
    status = root / (".gate-status.next" if returncode == 0 else ".gate-status")
    content = status.read_text()
    assert ("test PASS 0 profile=release" if returncode == 0 else "test FAIL cancelled rc=143") in content
    if returncode == 143:
        assert "cancellation finalized and authenticated" in result.stdout


def test_dirty_release_driver_stops_before_child_or_gate_lock(
    release_repo: tuple[Path, dict[str, str], Path],
) -> None:
    root, environment, _ = release_repo
    (root / "uncommitted.py").write_text("dirty = True\n")
    result = _run_driver(root, environment)
    assert result.returncode == 2
    assert "same clean exact candidate SHA" in result.stderr
    assert not (Path(environment["FAKE_RESOURCE_DIR"]) / "make-arguments.json").exists()
    assert not (Path(environment["FAKE_RESOURCE_DIR"]) / "gate.lock").exists()


@pytest.mark.parametrize(
    ("variable", "value"),
    [("GATE_TEST_PROFILE", "unknown"), ("PYTEST_CMD", "true"), ("PYTEST_ARGS", "-q"),
     ("DUAL_TRACK_LOCAL_VALIDATE_ONLY", "1"), ("GLUDD_GATE_BATCH_WORKERS", "2")],
)
def test_release_driver_independently_rejects_diagnostic_policy_before_resources(
    release_repo: tuple[Path, dict[str, str], Path], variable: str, value: str,
) -> None:
    root, environment, _ = release_repo
    environment[variable] = value
    result = _run_driver(root, environment)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (Path(environment["FAKE_RESOURCE_DIR"]) / "make-arguments.json").exists()
    assert not (Path(environment["FAKE_RESOURCE_DIR"]) / "gate.lock").exists()


def test_fresh_canonical_cancellation_receipt_survives_make_exit_two(
    release_repo: tuple[Path, dict[str, str], Path],
) -> None:
    root, environment, template = release_repo
    payload = json.loads(template.read_text())
    payload.update(status="fail", returncode=143)
    template.write_text(json.dumps(payload))
    environment.update(FAKE_MAKE_RC="2", FAKE_WRITE_FAILURE_RECEIPT="1")
    result = _run_driver(root, environment)
    assert result.returncode == 143, result.stdout + result.stderr
    status = root / ".gate-status"
    assert "test FAIL cancelled rc=143" in status.read_text()
    assert (root / ".gate-terminalized").exists()


def test_actual_producer_recipe_aborts_before_runner_when_locked_sync_fails(
    release_repo: tuple[Path, dict[str, str], Path],
) -> None:
    root, environment, _ = release_repo
    resource_dir = Path(environment["FAKE_RESOURCE_DIR"])
    fake_uv = resource_dir / "bin" / "uv"
    fake_uv.write_text(
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        f"pathlib.Path({str(resource_dir / 'uv-call.json')!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "sys.exit(17 if 'scripts/dependency_profiles.py' in sys.argv else 0)\n"
    )
    source = compose_makefile(ROOT / "Makefile")
    producer = source.split("\ntest-ci-dual-track-local:", 1)[1].split("\ntest-ci-shard:", 1)[0]
    recipe_file = resource_dir / "producer.mk"
    recipe_file.write_text(
        f"UV := {fake_uv}\nPYTHON := {sys.executable}\n"
        "DUAL_TRACK_LOCAL_VALIDATE_ONLY := 0\nDUAL_TRACK_RESUME := 0\nMAX_FILES_PER_BATCH := 16\n"
        "_ci-replica-clean-tree:\n\t@:\ntest-ci-dual-track-local:" + producer
    )
    result = _make("-f", str(recipe_file), "test-ci-dual-track-local", cwd=root, env=environment)
    assert result.returncode == 2, result.stdout + result.stderr
    arguments = json.loads((resource_dir / "uv-call.json").read_text())
    assert "scripts/dependency_profiles.py" in arguments
    assert "scripts/run_ci_shards_serial.py" not in arguments


@pytest.mark.parametrize("validate_only", [False, True])
def test_actual_local_release_recipe_collects_later_failures_in_one_invocation(
    release_repo: tuple[Path, dict[str, str], Path], validate_only: bool,
) -> None:
    root, environment, _ = release_repo
    resource_dir = Path(environment["FAKE_RESOURCE_DIR"])
    calls = resource_dir / "uv-calls.jsonl"
    fake_uv = resource_dir / "bin" / "uv"
    fake_uv.write_text(
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        f"with pathlib.Path({str(calls)!r}).open('a') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    source = compose_makefile(ROOT / "Makefile")
    producer = source.split("\ntest-ci-dual-track-local:", 1)[1].split("\ntest-ci-shard:", 1)[0]
    recipe_file = resource_dir / "producer.mk"
    recipe_file.write_text(
        f"UV := {fake_uv}\nPYTHON := {sys.executable}\n"
        f"DUAL_TRACK_LOCAL_VALIDATE_ONLY := {int(validate_only)}\n"
        "DUAL_TRACK_RESUME := 0\nMAX_FILES_PER_BATCH := 16\n"
        "_ci-replica-clean-tree:\n\t@:\n"
        "DUAL_TRACK_WATCHDOG_OWNED_GATE := 1\ntest-ci-dual-track-local:" + producer
    )
    result = _make("-f", str(recipe_file), "test-ci-dual-track-local", cwd=root, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(invocations) == (1 if validate_only else 2)
    if not validate_only:
        assert "scripts/dependency_profiles.py" in invocations[0]
    arguments = invocations[-1]
    assert arguments.count("scripts/run_ci_shards_serial.py") == 1
    assert arguments.count("--diagnose-after-failure") == 1
    assert arguments.count("--require-release-policy") == 1
    assert "--collect-all-failures" not in arguments
    assert "--watchdog-owned-gate" in arguments
    assert ("--validate-only" in arguments) is validate_only
    assert arguments[arguments.index("--batch-workers") + 1] == "1"
    assert "--pytest-args=-W error " in arguments


def test_actual_background_recipe_forwards_the_selected_policy_without_launching(
    release_repo: tuple[Path, dict[str, str], Path],
) -> None:
    root, environment, _ = release_repo
    resource_dir = Path(environment["FAKE_RESOURCE_DIR"])
    fake_uv = resource_dir / "bin" / "uv"
    fake_uv.write_text(
        f"#!{sys.executable}\nimport json, os, pathlib\n"
        "names = ('GATE_TEST_PROFILE', 'GATE_EXACT_SHA_RESUME', 'GATE_MAX_FILES_PER_BATCH', "
        "'GATE_BATCH_WORKERS', 'GATE_CANDIDATE_SHA')\n"
        f"pathlib.Path({str(resource_dir / 'background-env.json')!r}).write_text("
        "json.dumps({name: os.environ[name] for name in names}))\n"
    )
    source = compose_makefile(ROOT / "Makefile")
    background = source.split("\ngate-background:", 1)[1].split("\n# Managed command runners", 1)[0]
    recipe_file = resource_dir / "background.mk"
    recipe_file.write_text(
        f"UV := {fake_uv}\n_GATE_REPOSITORY_ROOT := {root}\n_GATE_ENTRY_MAKEFILE := {recipe_file}\n"
        f"GATE_CANDIDATE_SHA := {environment['GLUDD_CANDIDATE_SHA']}\n"
        "GATE_TEST_PROFILE := release\nGATE_EXACT_SHA_RESUME := 0\nGATE_MAX_FILES_PER_BATCH := 16\n"
        "GATE_BATCH_WORKERS := 1\nGATE_BACKGROUND_VALIDATE_ONLY := 1\nGATE_TIMEOUT := 3600\n"
        "gate-admission-config:\n\t@:\ngate-background:" + background
    )
    result = _make("-f", str(recipe_file), "gate-background", cwd=root, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    forwarded = json.loads((resource_dir / "background-env.json").read_text())
    assert {name: forwarded[name] for name in (
        "GATE_TEST_PROFILE", "GATE_EXACT_SHA_RESUME", "GATE_MAX_FILES_PER_BATCH",
        "GATE_BATCH_WORKERS", "GATE_CANDIDATE_SHA",
    )} == {
        "GATE_TEST_PROFILE": "release", "GATE_EXACT_SHA_RESUME": "0", "GATE_MAX_FILES_PER_BATCH": "16",
        "GATE_BATCH_WORKERS": "1", "GATE_CANDIDATE_SHA": environment["GLUDD_CANDIDATE_SHA"],
    }
