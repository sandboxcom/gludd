"""Parity and release-gate tests for the local named CI shards."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from coverage import Coverage
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
EXPECTED_SHARDS = (
    "unit-1a1",
    "unit-1a2",
    "unit-1b",
    "unit-1d",
    "unit-2",
    "unit-3a",
    "unit-3b",
    "other",
)


def _load_script(name: str) -> Any:
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"gludd_{name}", SCRIPTS / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


def test_gate_owned_shard_commands_are_invisible_to_legacy_task_watchdogs(
    tmp_path: Path,
) -> None:
    """The runner and every pytest child carry the legacy exclusion marker."""
    module = _load_script("run_ci_shards_serial")

    batch = module._pytest_command(
        "unit-1b",
        ["tests/unit/test_example.py"],
        tmp_path,
        [],
        watchdog_owned_gate=True,
    )
    isolated = module._isolated_pytest_command([], watchdog_owned_gate=True)

    assert "watchdog-owned-gate" in " ".join(batch)
    assert "watchdog-owned-gate" in " ".join(isolated)


def test_local_shard_names_match_beta4_ci_matrix() -> None:
    module = _load_script("ci_named_shard_files")

    assert tuple(module.SHARDS) == EXPECTED_SHARDS


def test_workflow_matrix_delegates_shard_plans_to_canonical_registry() -> None:
    module = _load_script("ci_named_shard_files")
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text())
    include = workflow["jobs"]["test-shard"]["strategy"]["matrix"]["include"]
    workflow_shards = workflow["jobs"]["test-shard"]["strategy"]["matrix"]["shard"]

    assert tuple(workflow_shards) == tuple(module.SHARDS)
    assert all("testpaths" not in item and "exclude" not in item for item in include)


def test_local_shards_exclude_fresh_process_suites() -> None:
    module = _load_script("ci_named_shard_files")
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text())
    unit_1a1 = next(
        item for item in workflow["jobs"]["test-shard"]["strategy"]["matrix"]["include"] if item["shard"] == "unit-1a1"
    )

    assert "tests/unit/test_all_plugins_runtime.py" not in module.expand_shard("unit-1a1")
    assert "*/test_all_plugins_runtime.py" in module.SHARDS["unit-1a1"][1]
    assert "tests/unit/test_makefile_audit_deep.py" not in module.expand_shard("unit-2")
    assert "*/test_makefile_audit_deep.py" in module.SHARDS["unit-2"][1]
    assert module.ISOLATED_TESTS == (
        "tests/unit/test_all_plugins_runtime.py",
        "tests/unit/test_makefile_audit_deep.py",
    )
    assert tuple(str(unit_1a1["isolated_testpaths"]).split()) == module.ISOLATED_TESTS


def test_every_unit_test_file_has_exactly_one_execution_lane() -> None:
    module = _load_script("ci_named_shard_files")
    selected: dict[str, set[str]] = {}
    for shard in EXPECTED_SHARDS:
        files: set[str] = set()
        for token in module.expand_shard(shard):
            path = ROOT / token
            if path.is_dir():
                files.update(item.relative_to(ROOT).as_posix() for item in path.rglob("test_*.py"))
            else:
                files.add(token)
        selected[shard] = files
    selected["isolated"] = set(module.ISOLATED_TESTS)

    for path in sorted((ROOT / "tests" / "unit").rglob("test_*.py")):
        relative = path.relative_to(ROOT).as_posix()
        owners = [shard for shard, files in selected.items() if relative in files]
        assert len(owners) == 1, f"{relative} belongs to {owners}, expected exactly one shard"


def test_shard_slice_supports_inclusive_boundaries() -> None:
    module = _load_script("ci_named_shard_files")
    paths = ["a.py", "b.py", "c.py", "d.py"]

    assert module.slice_paths(paths, from_path="b.py", to_path="c.py") == [
        "b.py",
        "c.py",
    ]


def test_shard_slice_supports_exclusive_boundaries() -> None:
    module = _load_script("ci_named_shard_files")
    paths = ["a.py", "b.py", "c.py", "d.py"]

    assert module.slice_paths(paths, after_path="a.py", before_path="d.py") == [
        "b.py",
        "c.py",
    ]


def test_shard_slice_fails_closed_for_unknown_boundary() -> None:
    module = _load_script("ci_named_shard_files")

    with pytest.raises(SystemExit, match="not present"):
        module.slice_paths(["a.py"], from_path="missing.py")


def test_named_shard_expansion_fails_closed_for_unknown_shard() -> None:
    module = _load_script("ci_named_shard_files")

    with pytest.raises(SystemExit, match="valid shards"):
        module.expand_shard("retired-unit-3")


def test_named_shard_cli_supports_plain_and_shell_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("ci_named_shard_files")
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/unit/a file.py"])

    monkeypatch.setattr(sys, "argv", ["ci_named_shard_files.py", "--shard", "unit-3a"])
    assert module.main() == 0
    assert capsys.readouterr().out == "tests/unit/a file.py\n"

    monkeypatch.setattr(
        sys,
        "argv",
        ["ci_named_shard_files.py", "--shard", "unit-3a", "--shell"],
    )
    assert module.main() == 0
    assert capsys.readouterr().out == "'tests/unit/a file.py'\n"


def test_named_shard_cli_fails_closed_when_expansion_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("ci_named_shard_files")
    monkeypatch.setattr(module, "expand_shard", lambda _shard: [])
    monkeypatch.setattr(sys, "argv", ["ci_named_shard_files.py", "--shard", "unit-3a"])

    with pytest.raises(SystemExit, match="empty slice"):
        module.main()


def test_serial_gate_runner_is_fresh_process_and_coverage_complete() -> None:
    runner = SCRIPTS / "run_ci_shards_serial.py"
    assert runner.is_file()
    source = runner.read_text(encoding="utf-8")

    for token in (
        "MAX_FILES_PER_BATCH",
        "OWNED-PYTEST-RESULT",
        "start_new_session=True",
        "COVERAGE_FILE",
        "coverage combine",
        '"--cov"',
        "--cov-fail-under=0",
        "coverage report",
        "--fail-under=85",
        "audit_coverage.py",
        "--threshold=85",
        "--per-file-threshold=75",
    ):
        assert token in source


def test_bounded_pytest_batches_do_not_nest_xdist_controller_and_worker() -> None:
    """One owned batch process must not add an unnecessary execnet lifecycle."""
    module = _load_script("run_ci_shards_serial")

    command = module._pytest_command(
        "unit-1d",
        ["tests/unit/test_bundled_binaries.py"],
        Path("/tmp/gludd-owned-batch"),
        ["-W", "error"],
    )

    assert command[:3] == [sys.executable, "-m", "pytest"]
    assert "--cov" in command
    assert "-n" not in command
    assert "--dist" not in command
    assert "--max-worker-restart=0" not in command


def test_coverage_files_target_preserves_aggregate_and_per_file_floors() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    recipe = makefile.split("coverage-files:", 1)[1].split("gate-async:", 1)[0]

    assert '--threshold="$(COVERAGE_AGGREGATE_MIN)"' in recipe
    assert '--per-file-threshold="$(COVERAGE_PER_FILE_MIN)"' in recipe


def test_coverage_files_target_namespaces_ansible_temp_under_owned_basetemp() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    recipe = makefile.split("coverage-files:", 1)[1].split("gate-async:", 1)[0]

    assert 'mkdir -p "$$BT/ansible-local"' in recipe
    assert 'ANSIBLE_LOCAL_TEMP="$$BT/ansible-local"' in recipe


def test_coverage_files_accepts_serial_or_parallel_coverage_data() -> None:
    """Serial configs must not fail because no parallel fragments exist."""
    makefile = compose_makefile(ROOT / "Makefile")
    recipe = makefile.split("coverage-files:", 1)[1].split("gate-async:", 1)[0]

    fragment_probe = 'set -- "$$GLUDD_COVERAGE_DATA".*;'
    conditional_combine = 'if [ -e "$$1" ]; then'
    base_requirement = '[ -f "$$GLUDD_COVERAGE_DATA" ] || {'
    assert fragment_probe in recipe
    assert conditional_combine in recipe
    assert base_requirement in recipe
    assert recipe.index(fragment_probe) < recipe.index("coverage combine")
    assert recipe.index(base_requirement) < recipe.index("coverage report")


def test_local_and_hosted_named_shards_use_one_bounded_runner() -> None:
    """GHA and local release evidence must execute the same shard owner."""
    makefile = compose_makefile(ROOT / "Makefile")
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    local_recipe = makefile.split("test-ci-shard:", 1)[1].split("test-ci-shard-summary:", 1)[0]
    hosted_recipe = workflow.split("- name: Test (shard ${{ matrix.shard }}", 1)[1].split(
        "- name: Collect failure diagnostics", 1
    )[0]

    assert "scripts/run_ci_shards_serial.py" in local_recipe
    assert "scripts/run_ci_shards_serial.py" in hosted_recipe
    assert "scripts/adaptive_test.py" not in hosted_recipe
    assert "retry_test" not in hosted_recipe
    assert "${{ matrix.testpaths }}" not in hosted_recipe
    assert "${{ matrix.exclude }}" not in hosted_recipe
    assert "--max-files-per-batch" in hosted_recipe
    assert "--skip-isolated" in hosted_recipe
    assert "--skip-aggregate" in hosted_recipe


def test_hosted_shard_step_budget_covers_long_shards_and_cleanup() -> None:
    """Hosted execution must finish before the job's cleanup reserve begins."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text())
    job = workflow["jobs"]["test-shard"]
    test_step = next(step for step in job["steps"] if step.get("id") == "test-run")

    assert test_step["timeout-minutes"] >= 90
    assert test_step["timeout-minutes"] <= job["timeout-minutes"] - 15


def test_hosted_coverage_artifact_includes_hidden_coverage_database() -> None:
    """The shard artifact must contain the dot-prefixed coverage database."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text())
    steps = workflow["jobs"]["test-shard"]["steps"]
    upload = next(step for step in steps if str(step.get("name", "")).startswith("Upload coverage data"))

    assert upload["with"]["include-hidden-files"] is True


def test_local_and_hosted_shard_batches_share_safe_file_bound() -> None:
    """Hosted workers must not retain a 64-file process lifetime."""
    module = _load_script("run_ci_shards_serial")
    makefile = compose_makefile(ROOT / "Makefile")
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    local_recipe = makefile.split("test-ci-shard:", 1)[1].split("test-ci-shard-summary:", 1)[0]

    assert module.MAX_FILES_PER_BATCH == 16
    assert "$(or $(MAX_FILES_PER_BATCH),16)" in local_recipe
    assert "--max-files-per-batch 16" in workflow


def test_serial_runner_resource_paths_live_under_external_resource_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coverage and attestation state must not mutate the tested checkout."""
    module = _load_script("run_ci_shards_serial")
    resource_root = tmp_path / "resources"
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(resource_root))

    paths = module._resource_paths()

    expected_parent = module.project_resource_root(module.ROOT)
    assert paths.root == expected_parent / "ci-shards"
    assert paths.root.is_relative_to(resource_root)
    assert paths.coverage_shards.parent == paths.root
    assert paths.coverage_json.parent == paths.root
    assert paths.coverage_audit.parent == paths.root
    assert paths.attestation.parent == paths.root
    assert not str(paths.root).startswith(str(module.ROOT))


def test_repository_identity_fails_closed_on_dirty_or_wrong_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    def completed(stdout: str, returncode: int = 0) -> object:
        return type("Completed", (), {"stdout": stdout, "returncode": returncode})()

    responses = iter(
        [
            completed("abc123\n"),
            completed("feature\n"),
            completed(" M tracked.py\n"),
        ]
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: next(responses))

    identity = module._repository_identity(expected_sha="def456")

    assert identity["head_sha"] == "abc123"
    assert identity["expected_sha"] == "def456"
    assert identity["branch"] == "feature"
    assert identity["clean"] is False
    assert identity["exact_sha"] is False
    assert module._identity_is_release_eligible(identity) is False


def test_terminal_attestation_is_atomic_and_contains_exact_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / "attestations" / "unit-2.json"
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    identity = {
        "head_sha": "abc123",
        "expected_sha": "abc123",
        "branch": "feature",
        "clean": True,
        "exact_sha": True,
    }

    module._write_terminal_attestation(
        destination,
        identity=identity,
        shards=["unit-2"],
        returncode=0,
        started_at="2026-08-25T00:00:00Z",
        completed_at="2026-08-25T00:01:00Z",
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["lane"] == "local"
    assert payload["identity"] == identity
    assert payload["shards"] == ["unit-2"]
    assert payload["status"] == "pass"
    assert payload["returncode"] == 0
    plan = payload["shard_plans"]["unit-2"]
    assert plan["path_count"] == len(plan["paths"])
    assert plan["sha256"] == module.canonical_json_sha256(plan["paths"])
    assert payload["execution_policy"]["pytest_args"] == ["-W", "error"]
    assert payload["execution_policy_sha256"] == module.canonical_json_sha256(payload["execution_policy"])
    assert not destination.with_suffix(".json.tmp").exists()


def test_terminal_attestation_uses_precomputed_pairing_without_rescan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / "attestation.json"
    pairing = {
        "shard_plans": {"unit-2": {"paths": ["tests/unit/a.py"]}},
        "execution_policy": {"pytest_args": ["-W", "error"]},
        "execution_policy_sha256": "policy-digest",
    }
    monkeypatch.setattr(
        module,
        "_attestation_pairing",
        lambda *_args, **_kwargs: pytest.fail("terminal publication must not rescan the tested checkout"),
    )

    module._write_terminal_attestation(
        destination,
        identity={"head_sha": "abc123", "clean": True, "exact_sha": True},
        shards=["unit-2"],
        returncode=0,
        started_at="2026-08-26T00:00:00Z",
        completed_at="2026-08-26T00:01:00Z",
        pairing=pairing,
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["shard_plans"] == pairing["shard_plans"]
    assert payload["execution_policy_sha256"] == "policy-digest"


def test_terminal_attestation_identifies_hosted_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / "hosted.json"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    module._write_terminal_attestation(
        destination,
        identity={
            "head_sha": "abc123",
            "expected_sha": "abc123",
            "clean": True,
            "exact_sha": True,
        },
        shards=["unit-2"],
        returncode=0,
        started_at="2026-08-25T00:00:00Z",
        completed_at="2026-08-25T00:01:00Z",
    )

    assert json.loads(destination.read_text(encoding="utf-8"))["lane"] == "hosted"


def test_terminal_attestation_concurrent_publish_is_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parallel local/hosted writers must never share a temporary pathname."""
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / "shared.json"
    barrier = threading.Barrier(2)
    real_replace = module.os.replace

    def synchronized_replace(source: Path, target: Path) -> None:
        barrier.wait(timeout=2.0)
        real_replace(source, target)

    monkeypatch.setattr(module.os, "replace", synchronized_replace)

    def publish(returncode: int) -> None:
        module._write_terminal_attestation(
            destination,
            identity={"head_sha": "abc123", "clean": True, "exact_sha": True},
            shards=["unit-2"],
            returncode=returncode,
            started_at="2026-08-25T00:00:00Z",
            completed_at="2026-08-25T00:01:00Z",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish, returncode) for returncode in (0, 1)]
        for future in futures:
            future.result()

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["returncode"] in {0, 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_publishes_failed_attestation_when_runner_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / "failure.json"
    monkeypatch.setattr(
        module,
        "_repository_identity",
        lambda **_kwargs: {
            "head_sha": "abc123",
            "expected_sha": "abc123",
            "branch": "feature",
            "clean": True,
            "exact_sha": True,
        },
    )
    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2",
            f"--attestation-output={destination}",
        ],
    )

    assert module.main() == module.RUNNER_EXCEPTION_EXIT_CODE
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["status"] == "fail"
    assert payload["error"] == "RuntimeError: boom"


def test_cli_attests_exact_collected_failure_without_stale_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    explicit = tmp_path / "explicit-failure.json"
    stale_coverage = tmp_path / ".coverage.previous"
    stale_coverage.write_bytes(b"must-not-be-attested")
    resource_paths = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "default-failure.json",
    )
    identity = {
        "head_sha": "abc123",
        "expected_sha": "abc123",
        "branch": "feature",
        "clean": True,
        "exact_sha": True,
        "queries_ok": True,
    }
    pairing = {
        "shard_plans": {"unit-2": {"paths": ["tests/unit/a.py"]}},
        "execution_policy": {"pytest_args": []},
        "execution_policy_sha256": "policy-digest",
    }
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(module, "_resource_paths", lambda: resource_paths)
    monkeypatch.setattr(module, "_repository_identity", lambda **_kwargs: identity)
    monkeypatch.setattr(module, "_attestation_pairing", lambda *_args, **_kwargs: pairing)
    monkeypatch.setattr(module, "run", lambda *_args, **_kwargs: 6)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2",
            "--skip-isolated",
            "--skip-aggregate",
            f"--coverage-output={stale_coverage}",
            f"--attestation-output={explicit}",
        ],
    )

    assert module.main() == 6
    for destination in (resource_paths.attestation, explicit):
        payload = json.loads(destination.read_text(encoding="utf-8"))
        assert payload["status"] == "fail"
        assert payload["returncode"] == 6
        assert payload["lane"] == "hosted"
        assert payload["identity"] == identity
        assert payload["shards"] == ["unit-2"]
        assert payload["execution_policy_sha256"] == "policy-digest"
        assert "coverage" not in payload
        assert "error" not in payload


@pytest.mark.parametrize(
    ("initial_returncode", "expected_returncodes"),
    [
        (128 + signal.SIGINT, [128 + signal.SIGINT, 128 + signal.SIGINT]),
        (
            0,
            [
                0,
                0,
                128 + signal.SIGINT,
                128 + signal.SIGINT,
            ],
        ),
    ],
)
def test_cli_defers_repeated_cancellation_through_terminal_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial_returncode: int,
    expected_returncodes: list[int],
) -> None:
    module = _load_script("run_ci_shards_serial")
    explicit = tmp_path / "explicit.json"
    default = tmp_path / "default.json"
    resource_paths = module.ResourcePaths(
        root=tmp_path,
        coverage_shards=tmp_path / "coverage-fragments",
        coverage_json=tmp_path / "coverage.json",
        coverage_audit=tmp_path / "coverage-audit.json",
        attestation=default,
    )
    handlers: dict[signal.Signals, object] = {}
    writes: list[tuple[Path, int, dict[str, object]]] = []
    pairing = {
        "shard_plans": {"unit-2": {"paths": ["tests/unit/a.py"]}},
        "execution_policy": {"pytest_args": ["-W", "error"]},
        "execution_policy_sha256": "policy-digest",
    }

    def fake_getsignal(signum: signal.Signals) -> object:
        return handlers.get(signum, signal.SIG_DFL)

    def fake_signal(signum: signal.Signals, handler: object) -> object:
        previous = handlers.get(signum, signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    def publish(destination: Path, **kwargs: object) -> None:
        if not writes:
            handler = handlers.get(signal.SIGINT)
            assert callable(handler), "terminal publication must defer cancellation"
            handler(signal.SIGINT, None)
        returncode = kwargs["returncode"]
        seen_pairing = kwargs["pairing"]
        assert isinstance(returncode, int)
        assert isinstance(seen_pairing, dict)
        writes.append(
            (
                destination,
                returncode,
                seen_pairing,
            )
        )

    monkeypatch.setattr(module.signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(module.signal, "signal", fake_signal)
    monkeypatch.setattr(module, "_resource_paths", lambda: resource_paths)
    monkeypatch.setattr(module, "_attestation_pairing", lambda *_args, **_kwargs: pairing)
    monkeypatch.setattr(module, "_write_terminal_attestation", publish)
    monkeypatch.setattr(module, "run", lambda *_args, **_kwargs: initial_returncode)
    monkeypatch.setattr(
        module,
        "_repository_identity",
        lambda **_kwargs: {
            "head_sha": "abc123",
            "expected_sha": "abc123",
            "branch": "feature",
            "clean": True,
            "exact_sha": True,
            "queries_ok": True,
        },
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2",
            f"--attestation-output={explicit}",
        ],
    )

    assert module.main() == 128 + signal.SIGINT
    assert {destination for destination, _rc, _pairing in writes} == {
        default,
        explicit,
    }
    assert [rc for _destination, rc, _pairing in writes] == expected_returncodes
    assert all(seen == pairing for _destination, _rc, seen in writes)


def test_cli_rejects_non_release_eligible_identity_before_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / "identity-failure.json"
    resource_paths = module.ResourcePaths(
        root=tmp_path,
        coverage_shards=tmp_path / "coverage-fragments",
        coverage_json=tmp_path / "coverage.json",
        coverage_audit=tmp_path / "coverage-audit.json",
        attestation=tmp_path / "default-attestation.json",
    )
    monkeypatch.setattr(module, "_resource_paths", lambda: resource_paths)
    monkeypatch.setattr(
        module,
        "_repository_identity",
        lambda **_kwargs: {
            "head_sha": "abc123",
            "expected_sha": "def456",
            "branch": "feature",
            "clean": False,
            "exact_sha": False,
        },
    )
    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: pytest.fail("ineligible checkout must not run"),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2",
            f"--attestation-output={destination}",
        ],
    )

    assert module.main() == 2
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["status"] == "fail"
    assert payload["error"] == "repository identity is not release eligible"


def test_cli_allows_stable_dirty_identity_only_for_nonrelease_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A commit-preflight gate may test dirty content without blessing a release."""
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / "dirty-gate.json"
    resource_paths = module.ResourcePaths(
        root=tmp_path,
        coverage_shards=tmp_path / "coverage-fragments",
        coverage_json=tmp_path / "coverage.json",
        coverage_audit=tmp_path / "coverage-audit.json",
        attestation=tmp_path / "default-attestation.json",
    )
    identity = {
        "head_sha": "abc123",
        "expected_sha": "abc123",
        "branch": "feature",
        "clean": False,
        "exact_sha": True,
        "queries_ok": True,
    }
    calls: list[str] = []

    def fake_run(*_args: object, **_kwargs: object) -> int:
        calls.append("run")
        return 0

    states = iter(("candidate-state", "candidate-state"))
    monkeypatch.setattr(module, "_resource_paths", lambda: resource_paths)
    monkeypatch.setattr(module, "_repository_identity", lambda **_kwargs: identity)
    monkeypatch.setattr(module, "_worktree_state_id", lambda: next(states))
    monkeypatch.setattr(
        module,
        "run",
        fake_run,
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2",
            "--allow-dirty-worktree",
            f"--attestation-output={destination}",
        ],
    )

    assert module.main() == 0
    assert calls == ["run"]
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert payload["identity"]["clean"] is False
    assert payload["identity"]["worktree_state_id"] == "candidate-state"
    assert module._identity_is_release_eligible(payload["identity"]) is False


def test_dirty_gate_rejects_worktree_mutation_during_test_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / "mutated-dirty-gate.json"
    resource_paths = module.ResourcePaths(
        root=tmp_path,
        coverage_shards=tmp_path / "coverage-fragments",
        coverage_json=tmp_path / "coverage.json",
        coverage_audit=tmp_path / "coverage-audit.json",
        attestation=tmp_path / "default-attestation.json",
    )
    monkeypatch.setattr(module, "_resource_paths", lambda: resource_paths)
    monkeypatch.setattr(
        module,
        "_repository_identity",
        lambda **_kwargs: {
            "head_sha": "abc123",
            "expected_sha": "abc123",
            "branch": "feature",
            "clean": False,
            "exact_sha": True,
            "queries_ok": True,
        },
    )
    states = iter(("candidate-state", "mutated-state"))
    monkeypatch.setattr(module, "_worktree_state_id", lambda: next(states))
    monkeypatch.setattr(module, "run", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2",
            "--allow-dirty-worktree",
            f"--attestation-output={destination}",
        ],
    )

    assert module.main() == module.RUNNER_EXCEPTION_EXIT_CODE
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["status"] == "fail"
    assert payload["error"] == "repository state changed during dirty gate"


def test_release_policy_rejects_dirty_gate_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2",
            "--allow-dirty-worktree",
            "--require-release-policy",
            "--pytest-args=-W error",
        ],
    )

    assert module.main() == 2


def test_commit_preflight_gate_invokes_dirty_nonrelease_mode() -> None:
    source = (ROOT / "scripts" / "run_gate.sh").read_text(encoding="utf-8")

    assert (
        "run_ci_shards_serial.py --watchdog-owned-gate "
        "--pytest-args=-q --allow-dirty-worktree"
    ) in source


def test_serial_runner_rejects_nonpositive_batch_size() -> None:
    module = _load_script("run_ci_shards_serial")

    with pytest.raises(ValueError, match="max_files_per_batch must be positive"):
        module.run(["unit-2"], [], max_files_per_batch=0)


@pytest.mark.parametrize("batch_workers", (0, 3))
def test_serial_runner_rejects_workers_outside_two_slot_bound(
    batch_workers: int,
) -> None:
    module = _load_script("run_ci_shards_serial")

    with pytest.raises(ValueError, match=r"batch_workers must be in 1\.\.2"):
        module.run(["unit-2"], [], batch_workers=batch_workers)


def test_run_gate_delegates_to_serial_named_shards() -> None:
    source = (SCRIPTS / "run_gate.sh").read_text(encoding="utf-8")

    assert "run_ci_shards_serial.py" in source


def test_serial_pytest_command_uses_one_fail_closed_worker_and_isolated_basetemp(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")

    command = module._pytest_command("unit-2", ["tests/unit/test_alpha.py"], tmp_path, ["-q"])
    greenlet_command = module._pytest_command("unit-3b", ["tests/unit/test_zeta.py"], tmp_path, ["-q"])

    assert command[0] == sys.executable
    assert command[1:3] == ["-m", "pytest"]
    assert "tests/unit/test_alpha.py" in command
    assert "-n" not in command
    assert "--maxprocesses" not in command
    assert "--max-worker-restart=0" not in command
    assert "--cov" in command
    assert "--cov=general_ludd" not in command
    assert not any(item.startswith("--cov=") for item in command)
    coverage_config = module.ROOT / ".coveragerc-greenlet"
    assert f"--cov-config={coverage_config}" in command
    assert f"--cov-config={coverage_config}" in greenlet_command
    coverage = Coverage(config_file=str(coverage_config))
    assert coverage.get_option("run:branch") is True
    assert coverage.get_option("run:source") == [
        "src/general_ludd",
        "collections/ansible_collections/general_ludd/governance/plugins/module_utils",
    ]
    assert f"--basetemp={tmp_path / 'pytest'}" in command


def test_serial_pytest_command_uses_bounded_two_slot_loadfile_queue(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")

    command = module._pytest_command(
        "unit-2",
        ["tests/unit/test_alpha.py", "tests/unit/test_beta.py"],
        tmp_path,
        ["-q"],
        batch_workers=2,
    )

    worker_flag = command.index("-n")
    assert command[worker_flag + 1] == "2"
    assert "--dist=loadfile" in command
    assert "--max-worker-restart=0" in command
    assert "--maxprocesses" not in command


def test_serial_runner_strips_make_recursion_environment_from_owned_children() -> None:
    module = _load_script("run_ci_shards_serial")
    inherited = {
        "MAKEFLAGS": "-j8 --jobserver-auth=3,4 --no-print-directory",
        "MFLAGS": "-j8 --jobserver-auth=3,4",
        "MAKELEVEL": "2",
        "MAKEOVERRIDES": "PYTEST_ARGS=-q",
        "GNUMAKEFLAGS": "--output-sync=target",
        "GLUDD_SHARD_NAME": "unit-2:batch-030",
    }

    child = module._owned_test_environment(inherited)

    assert child == {"GLUDD_SHARD_NAME": "unit-2:batch-030"}
    assert "--jobserver-auth=3,4" in inherited["MAKEFLAGS"]


def test_serial_runner_uses_owned_socket_safe_tmpdir(
    tmp_path: Path,
) -> None:
    """Hosted checkout depth must not overflow multiprocessing AF_UNIX paths."""
    module = _load_script("run_ci_shards_serial")
    hosted_label = str(tmp_path / ("hosted-checkout-depth-" * 12))

    owned = module._owned_socket_safe_tmpdir(hosted_label)
    try:
        forkserver_socket = owned / "pymp-12345678" / "listener-12345678"
        assert owned.is_dir()
        assert re.fullmatch(r"gludd-[0-9a-f]{4}-[a-z0-9_]+", owned.name)
        assert len(str(forkserver_socket).encode()) < 108
    finally:
        module._cleanup_owned_tmpdir(owned)

    assert not owned.exists()


def test_owned_tmpdir_cleanup_defers_sigint_until_after_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    owned = module._owned_socket_safe_tmpdir("unit-3b-batch-024")
    handlers: dict[signal.Signals, object] = {}

    def fake_getsignal(signum: signal.Signals) -> object:
        return handlers.get(signum, signal.SIG_DFL)

    def fake_signal(signum: signal.Signals, handler: object) -> object:
        previous = handlers.get(signum, signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    def interrupting_rmtree(path: Path, **_kwargs: object) -> None:
        Path(path).rmdir()
        handler = handlers.get(signal.SIGINT)
        assert callable(handler), "cleanup must install a cancellation deferral handler"
        handler(signal.SIGINT, None)

    monkeypatch.setattr(module.signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(module.signal, "signal", fake_signal)
    monkeypatch.setattr(module.shutil, "rmtree", interrupting_rmtree)

    assert module._cleanup_owned_tmpdir(owned) == 128 + signal.SIGINT
    assert not owned.exists()


def test_owned_tmpdir_cleanup_is_idempotent() -> None:
    module = _load_script("run_ci_shards_serial")
    owned = module._owned_socket_safe_tmpdir("unit-3b-batch-024-repeat")

    assert module._cleanup_owned_tmpdir(owned) == 0
    assert module._cleanup_owned_tmpdir(owned) == 0


def test_serial_runner_propagates_deferred_cleanup_signal_as_rc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    workspace = tmp_path / "workspace"
    compact = tmp_path / "compact"

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        del dir
        path = workspace if prefix.startswith("gludd-gate-") else compact
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/unit/test_a.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(
        module,
        "_cleanup_owned_tmpdir",
        lambda _path: 128 + signal.SIGINT,
    )

    assert (
        module.run(
            ["unit-3b"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == 128 + signal.SIGINT
    )


def test_signal_deferral_is_safe_outside_the_main_thread() -> None:
    module = _load_script("run_ci_shards_serial")
    observed: list[list[int]] = []

    def worker() -> None:
        with module._defer_termination_signals() as deferred:
            observed.append(deferred)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert observed == [[]]


@pytest.mark.parametrize(
    ("cleanup_rc", "with_coverage_output", "coverage_rc", "expected_rc"),
    [(0, False, 0, 0), (9, False, 0, 9), (0, True, 0, 0), (0, True, 7, 7)],
)
def test_serial_runner_handles_success_and_noncancellation_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_rc: int,
    with_coverage_output: bool,
    coverage_rc: int,
    expected_rc: int,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    workspace = tmp_path / "workspace"
    compact = tmp_path / "compact"

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        del dir
        path = workspace if prefix.startswith("gludd-gate-") else compact
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/unit/test_a.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: cleanup_rc)
    monkeypatch.setattr(module, "_combine_coverage_output", lambda _path: coverage_rc)
    coverage_output = tmp_path / "combined-coverage" if with_coverage_output else None

    assert (
        module.run(
            ["unit-3b"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
            coverage_output=coverage_output,
        )
        == expected_rc
    )


def test_serial_runner_stops_the_whole_plan_after_one_interrupted_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    workspaces = tmp_path / "workspaces"
    compact_roots = tmp_path / "compact-roots"
    started: list[str] = []

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        del dir
        parent = workspaces if prefix.startswith("gludd-gate-") else compact_roots
        path = parent / prefix
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def interrupted_run(*_args: object, label: str, **_kwargs: object) -> int:
        started.append(label)
        return 128 + signal.SIGINT

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/unit/test_{shard}.py"])
    monkeypatch.setattr(module, "_run_owned_pytest", interrupted_run)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: False)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)

    assert (
        module.run(
            ["unit-1b", "unit-1d"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == 128 + signal.SIGINT
    )
    assert started == ["unit-1b:batch-001"]


def test_serial_runner_stops_before_batches_when_gate_owner_is_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    started: list[str] = []

    monkeypatch.setenv("GLUDD_GATE_OWNER_PID", "999999999")
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda shard: [f"tests/unit/test_{shard}.py"],
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        module,
        "_run_owned_pytest",
        lambda *_args, label, **_kwargs: started.append(label) or 0,
    )

    assert (
        module.run(
            ["unit-1b", "unit-1d"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
            watchdog_owned_gate=True,
        )
        == 128 + signal.SIGTERM
    )
    assert started == []
    output = capsys.readouterr().out
    assert "GATE-OWNER-DEATH" in output
    assert "planned=2 executed=0 resumed=0 not_started=2" in output


def test_serial_runner_stops_after_failed_batch_without_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    workspaces = tmp_path / "workspaces"
    compact_roots = tmp_path / "compact-roots"
    started: list[str] = []

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        del dir
        parent = workspaces if prefix.startswith("gludd-gate-") else compact_roots
        path = parent / prefix
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def failed_run(*_args: object, label: str, **_kwargs: object) -> int:
        started.append(label)
        return 1

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda shard: [f"tests/unit/test_{shard}.py"],
    )
    monkeypatch.setattr(module, "_run_owned_pytest", failed_run)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: False)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)

    assert (
        module.run(
            ["unit-1b", "unit-1d"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == 1
    )
    assert started == ["unit-1b:batch-001"]
    assert (
        "SHARD-COVERAGE-INTEGRITY-FAIL shard=unit-1b batch=1 rc=1; later-batches=not-started" in capsys.readouterr().out
    )


def test_serial_runner_stops_the_whole_plan_after_first_failed_batch_with_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    workspaces = tmp_path / "workspaces"
    compact_roots = tmp_path / "compact-roots"
    started: list[str] = []

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        del dir
        parent = workspaces if prefix.startswith("gludd-gate-") else compact_roots
        path = parent / prefix
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def failed_run(*_args: object, label: str, **_kwargs: object) -> int:
        started.append(label)
        return 1

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda shard: [f"tests/unit/test_{shard}.py"],
    )
    monkeypatch.setattr(module, "_run_owned_pytest", failed_run)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)

    assert (
        module.run(
            ["unit-1b", "unit-1d"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == 1
    )
    assert started == ["unit-1b:batch-001"]
    output = capsys.readouterr().out
    assert (
        "SHARD-FAIL shard=unit-1b batch=1 rc=1; later-batches=not-started"
        in output
    )
    assert "SERIAL-SHARD-FAILED shard=unit-1b rc=1; later-shards=not-started" in output


def test_serial_runner_skips_coverage_after_first_ordinary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []
    aggregate_calls = 0
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        return 1

    def aggregate() -> int:
        nonlocal aggregate_calls
        aggregate_calls += 1
        return 0

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)
    monkeypatch.setattr(module, "_aggregate_coverage", aggregate)

    result = module.run(
        ["unit-1a1", "unit-1a2"],
        [],
        run_isolated=False,
    )

    assert result == 1
    assert launched == ["unit-1a1:batch-001"]
    assert aggregate_calls == 0
    output = capsys.readouterr().out
    assert '"coverage:aggregate": "not-started"' in output
    assert '"unit-1a1:batch-001": 1' in output
    assert '"unit-1a1:batch-001:cleanup": 0' in output
    assert '"unit-1a1:batch-001:coverage": 0' in output
    assert "unit-1a2:batch-001" not in output


def test_serial_runner_stops_the_whole_plan_after_internal_runner_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    workspaces = tmp_path / "workspaces"
    compact_roots = tmp_path / "compact-roots"
    started: list[str] = []

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        del dir
        parent = workspaces if prefix.startswith("gludd-gate-") else compact_roots
        path = parent / prefix
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def failed_run(*_args: object, label: str, **_kwargs: object) -> int:
        started.append(label)
        return 3

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/unit/test_{shard}.py"])
    monkeypatch.setattr(module, "_run_owned_pytest", failed_run)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)

    assert (
        module.run(
            ["unit-2", "unit-3b"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == 3
    )
    assert started == ["unit-2:batch-001"]
    assert "SERIAL-SHARD-FAILED shard=unit-2 rc=3; later-shards=not-started" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [
        (0, False),
        (1, True),
        (2, True),
        (3, False),
        (4, False),
        (5, True),
        (6, True),
        (70, False),
        (73, False),
        (78, False),
        (124, False),
        (125, False),
        (128 + signal.SIGINT, False),
        (128 + signal.SIGTERM, False),
    ],
)
def test_serial_runner_classifies_collect_all_and_safety_results(
    returncode: int,
    expected: bool,
) -> None:
    module = _load_script("run_ci_shards_serial")

    assert module._is_collect_all_pytest_returncode(returncode) is expected


def test_serial_runner_reserves_xdist_and_test_name_socket_budget(
    tmp_path: Path,
) -> None:
    """The owned root must leave room for Darwin's 104-byte AF_UNIX limit."""
    module = _load_script("run_ci_shards_serial")
    hosted_label = str(tmp_path / ("hosted-checkout-depth-" * 12))

    owned = module._owned_socket_safe_tmpdir(hosted_label)
    try:
        socket_path = (
            owned.resolve() / "pytest" / "popen-gw0" / "test_release_issues_ctrl_alt_del_via_api0" / "fc-api.sock"
        )
        assert len(str(socket_path).encode()) < 104
    finally:
        module._cleanup_owned_tmpdir(owned)


def test_serial_partition_expands_directories_deduplicates_and_bounds_batches(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")
    suite = tmp_path / "tests" / "suite"
    nested = suite / "nested"
    nested.mkdir(parents=True)
    for relative in ("test_a.py", "test_b.py", "nested/test_c.py", "nested/sample_test.py"):
        path = suite / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_ok(): pass\n", encoding="utf-8")

    batches = module._partition_test_paths(
        ["tests/suite", "tests/suite/test_b.py", "tests/test_z.py"],
        max_files=2,
        root=tmp_path,
    )

    assert batches == [
        ["tests/suite/nested/sample_test.py", "tests/suite/nested/test_c.py"],
        ["tests/suite/test_a.py", "tests/suite/test_b.py"],
        ["tests/test_z.py"],
    ]
    assert all(1 <= len(batch) <= 2 for batch in batches)


def test_owned_pytest_runner_fails_closed_on_worker_death(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    class FinishedProcess:
        pid = 42420
        returncode = 0
        stdout = io.StringIO("[gw0] node down: Not properly terminated\n")

        def poll(self) -> int:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

    process = FinishedProcess()
    popen_kwargs: dict[str, object] = {}

    def fake_popen(_command: list[str], **kwargs: object) -> object:
        popen_kwargs.update(kwargs)
        return process

    terminated: list[object] = []
    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        module,
        "_terminate_owned_process",
        lambda owned, **_kwargs: terminated.append(owned),
    )

    rc = module._run_owned_pytest(
        ["pytest"],
        env={},
        label="unit-3:batch-1",
        heartbeat_seconds=1.0,
        no_progress_seconds=5.0,
    )

    assert rc == module.WORKER_DEATH_EXIT_CODE
    assert terminated == [process]
    assert popen_kwargs["start_new_session"] is True
    assert "WORKER-DEATH" in capsys.readouterr().out


@pytest.mark.parametrize(
    "line",
    [
        "[gw2] node down: Not properly terminated",
        "worker gw2 crashed and worker restarting disabled",
        "maximum crashed workers reached: 0",
        "===== xdist: maximum crashed workers reached: 0 =====",
    ],
)
def test_xdist_worker_death_parser_accepts_complete_control_lines(line: str) -> None:
    module = _load_script("run_ci_shards_serial")

    assert module._is_xdist_worker_death_line(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "tests/unit/test_adaptive_test.py::test_is_oom_exit_output_markers[[gw2] node down: Not properly terminated]",
        "payload=[gw2] node down: Not properly terminated",
        "stdout says worker gw2 crashed and worker restarting disabled",
        "assert 'maximum crashed workers reached: 0' in output",
    ],
)
def test_xdist_worker_death_parser_rejects_test_ids_payload_and_stdout(
    line: str,
) -> None:
    module = _load_script("run_ci_shards_serial")

    assert module._is_xdist_worker_death_line(line) is False


def test_owned_pytest_runner_ignores_marker_inside_parameterized_node_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    class FinishedProcess:
        pid = 42423
        returncode = 0
        stdout = io.StringIO(
            "tests/unit/test_adaptive_test.py::test_is_oom_exit_output_markers"
            "[[gw2] node down: Not properly terminated] PASSED\n"
        )

        def poll(self) -> int:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

    process = FinishedProcess()
    terminated: list[object] = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        module,
        "_terminate_owned_process",
        lambda owned, **_kwargs: terminated.append(owned),
    )
    monkeypatch.setattr(module, "_owned_process_group_alive", lambda _process: False)

    rc = module._run_owned_pytest(
        ["pytest"],
        env={},
        label="unit-3:batch-node-id",
        heartbeat_seconds=1.0,
        no_progress_seconds=5.0,
    )

    assert rc == 0
    assert terminated == []


def test_owned_cleanup_escalates_term_to_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    class RetainedProcess:
        pid = 42421

        @staticmethod
        def wait(timeout: float | None = None) -> int:
            return 0

    signals: list[signal.Signals] = []
    monkeypatch.setattr(module, "_owned_process_group_alive", lambda _process: True)
    monkeypatch.setattr(
        module,
        "_signal_owned_process_group",
        lambda _process, signum: signals.append(signum),
    )

    module._terminate_owned_process(RetainedProcess(), grace_seconds=0.0)

    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.parametrize("error_type", [ProcessLookupError, PermissionError])
def test_owned_cleanup_is_idempotent_when_group_is_gone_or_inaccessible(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[OSError],
) -> None:
    module = _load_script("run_ci_shards_serial")

    class ExitedProcess:
        pid = 42424
        returncode = 0
        wait_calls = 0

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            return self.returncode

    process = ExitedProcess()

    def missing_group(_pid: int, _signum: signal.Signals | int) -> None:
        raise error_type

    monkeypatch.setattr(module.os, "killpg", missing_group)

    module._terminate_owned_process(process, grace_seconds=0.0)
    module._terminate_owned_process(process, grace_seconds=0.0)

    assert process.wait_calls == 2


def test_owned_cleanup_does_not_signal_an_already_exited_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    class ExitedProcess:
        pid = 42425

        @staticmethod
        def wait(timeout: float | None = None) -> int:
            return 0

    signals: list[signal.Signals] = []
    monkeypatch.setattr(module, "_owned_process_group_alive", lambda _process: False)
    monkeypatch.setattr(
        module,
        "_signal_owned_process_group",
        lambda _process, signum: signals.append(signum),
    )

    module._terminate_owned_process(ExitedProcess(), grace_seconds=0.0)
    module._terminate_owned_process(ExitedProcess(), grace_seconds=0.0)

    assert signals == []


def test_owned_cleanup_contains_permission_race_during_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    class Process:
        pid = 42426

        @staticmethod
        def wait(timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr(module, "_owned_process_group_alive", lambda _process: True)

    def inaccessible(_process: object, _signum: signal.Signals) -> None:
        raise PermissionError

    monkeypatch.setattr(module, "_signal_owned_process_group", inaccessible)

    module._terminate_owned_process(Process(), grace_seconds=0.0)


def test_serial_runner_uses_unique_batches_and_stops_after_worker_death(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    workspace = tmp_path / "unit-3-workspace"
    batch_runs: list[tuple[str, str]] = []
    compact_index = 0

    def fake_run_owned(
        command: list[str],
        *,
        env: dict[str, str],
        label: str,
        **_kwargs: object,
    ) -> int:
        if "tests/unit/test_all_plugins_runtime.py" in command:
            return 0
        basetemp = next(arg for arg in command if arg.startswith("--basetemp="))
        batch_runs.append((basetemp, env["GLUDD_SHARD_STATE_DIR"]))
        return 0 if len(batch_runs) == 1 else module.WORKER_DEATH_EXIT_CODE

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal compact_index
        if prefix.startswith("gludd-gate-"):
            path = workspace
        else:
            compact_index += 1
            path = tmp_path / f"compact-{compact_index}"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: None)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda _shard: [f"tests/unit/test_{index}.py" for index in range(5)],
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(["unit-3"], [], max_files_per_batch=2)

    assert result == module.WORKER_DEATH_EXIT_CODE
    assert len(batch_runs) == 2, "worker death must not retry or launch later batches"
    assert len({basetemp for basetemp, _state in batch_runs}) == 2
    assert len({state for _basetemp, state in batch_runs}) == 2


def test_serial_runner_places_pytest_basetemp_under_compact_socket_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep tmp_path-created AF_UNIX endpoints below platform path limits."""
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    workspace = tmp_path / "intentionally-long-evidence-workspace-name"
    compact = tmp_path / "c"
    captured: dict[str, str] = {}

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        path = compact if re.fullmatch(r"gludd-[0-9a-f]{4}-", prefix) else workspace
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def fake_run_owned(
        command: list[str],
        *,
        env: dict[str, str],
        **_kwargs: object,
    ) -> int:
        if "tests/unit/test_all_plugins_runtime.py" not in command:
            captured["basetemp"] = next(
                argument.removeprefix("--basetemp=") for argument in command if argument.startswith("--basetemp=")
            )
            captured["tmpdir"] = env["TMPDIR"]
        return 0

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: None)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/unit/test_vm.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    assert module.run(["unit-3b"], [], run_isolated=False) == 0
    assert captured["tmpdir"] == str(compact)
    assert Path(captured["basetemp"]).is_relative_to(compact)


def test_serial_runner_uses_a_fresh_non_coverage_process_for_isolated_tests() -> None:
    module = _load_script("run_ci_shards_serial")

    command = module._isolated_pytest_command([])

    assert command == [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/test_all_plugins_runtime.py",
        "tests/unit/test_makefile_audit_deep.py",
        "-v",
    ]
    assert all(not argument.startswith("--cov") for argument in command)


def test_serial_runner_does_not_start_later_shards_after_test_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str) -> str:
        nonlocal temp_index
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def fake_run(command: list[str], *, env: dict[str, str] | None = None) -> int:
        return 0

    def fake_run_owned(command: list[str], **_kwargs: object) -> int:
        joined = " ".join(command)
        if "tests/unit/test_all_plugins_runtime.py" in command:
            return 0
        shard = "unit-1a1" if "unit-1a1.py" in joined else "unit-1a2"
        launched.append(shard)
        return 1 if shard == "unit-1a1" else 0

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: None)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(
        module,
        "_env_for_shard",
        lambda shard, basetemp: {"COVERAGE_FILE": str(basetemp / ".coverage")},
    )
    monkeypatch.setattr(module, "_run_command", fake_run)
    monkeypatch.setattr(module, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *args: True)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(["unit-1a1", "unit-1a2"], [])

    assert result == 1
    assert launched == ["unit-1a1"]


def test_serial_runner_does_not_start_later_batches_after_collection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def fake_run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        return 2 if label == "unit-1a1:batch-001" else 0

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: None)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda shard: [f"tests/{shard}-a.py", f"tests/{shard}-b.py"],
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(
        ["unit-1a1", "unit-1a2"],
        [],
        max_files_per_batch=1,
        run_isolated=False,
    )

    assert result == 2
    assert launched == ["unit-1a1:batch-001"]


def test_serial_runner_retains_first_failure_and_marks_rest_not_started(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    outcomes = {
        "unit-1a1:batch-001": 1,
        "unit-1a1:batch-002": 2,
        "unit-1a2:batch-001": 5,
        "unit-1a2:batch-002": 6,
    }
    launched: list[str] = []
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        return outcomes[label]

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda shard: [f"tests/{shard}-a.py", f"tests/{shard}-b.py"],
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)

    result = module.run(
        ["unit-1a1", "unit-1a2"],
        [],
        max_files_per_batch=1,
        run_isolated=False,
        aggregate_coverage=False,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert launched == ["unit-1a1:batch-001"]
    assert "failed=1" in output
    assert "'unit-1a1:batch-001': 1" in output
    assert "planned=4 executed=1 resumed=0 not_started=3" in output


def test_first_failure_rc_is_terminal_for_the_bounded_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        return 6

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda shard: [f"tests/{shard}-{index}.py" for index in range(3)],
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)

    result = module.run(
        ["unit-1a1", "unit-1a2"],
        [],
        max_files_per_batch=1,
        run_isolated=False,
        aggregate_coverage=False,
    )

    output = capsys.readouterr().out
    assert result == 6
    assert launched == ["unit-1a1:batch-001"]
    assert "'unit-1a1:batch-001': 6" in output
    assert "unit-1a1:batch-002" not in output
