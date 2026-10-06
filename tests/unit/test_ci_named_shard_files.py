"""Parity and release-gate tests for the local named CI shards."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import shutil
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from coverage import Coverage, CoverageData

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
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("coverage-files:", 1)[1].split("gate-async:", 1)[0]

    assert '--threshold="$(COVERAGE_AGGREGATE_MIN)"' in recipe
    assert '--per-file-threshold="$(COVERAGE_PER_FILE_MIN)"' in recipe


def test_coverage_files_target_namespaces_ansible_temp_under_owned_basetemp() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("coverage-files:", 1)[1].split("gate-async:", 1)[0]

    assert 'mkdir -p "$$BT/ansible-local"' in recipe
    assert 'ANSIBLE_LOCAL_TEMP="$$BT/ansible-local"' in recipe


def test_local_and_hosted_named_shards_use_one_bounded_runner() -> None:
    """GHA and local release evidence must execute the same shard owner."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
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
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
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


def test_serial_runner_holds_shared_uv_cache_lease_while_shards_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sibling cleanup cannot reclaim uv state beneath an active shard."""
    module = _load_script("run_ci_shards_serial")
    resource_paths = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    cache_root = tmp_path / "shared-uv-cache"
    events: list[str] = []

    @contextlib.contextmanager
    def lease(path: Path, *, owner_root: Path):
        assert path == cache_root.resolve()
        assert owner_root == module.ROOT
        events.append("lease-enter")
        try:
            yield type("Lease", (), {"acquired": True})()
        finally:
            events.append("lease-exit")

    monkeypatch.setenv("UV_CACHE_DIR", str(cache_root))
    monkeypatch.setattr(module, "_resource_paths", lambda: resource_paths)
    monkeypatch.setattr(module, "shared_uv_cache_lease", lease)
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
        "_attestation_pairing",
        lambda *_args, **_kwargs: {
            "shard_plans": {"unit-2": {"paths": ["tests/unit/a.py"]}},
            "execution_policy": {"pytest_args": []},
            "execution_policy_sha256": "policy-digest",
        },
    )
    monkeypatch.setattr(
        module,
        "run",
        lambda *_args, **_kwargs: events.append("run") or 9,
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        ["run_ci_shards_serial.py", "--shards=unit-2"],
    )

    assert module.main() == 9
    assert events == ["lease-enter", "run", "lease-exit"]


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


def test_serial_runner_collects_independent_failures_with_coverage(
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
    assert started == ["unit-1b:batch-001", "unit-1d:batch-001"]
    output = capsys.readouterr().out
    assert output.count("later-shards=continuing") == 2


def test_serial_runner_runs_coverage_after_collecting_ordinary_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    outcomes = iter((1, 5))
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
        return next(outcomes)

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

    assert result == 5
    assert launched == ["unit-1a1:batch-001", "unit-1a2:batch-001"]
    assert aggregate_calls == 1
    output = capsys.readouterr().out
    assert '"coverage:aggregate": 0' in output
    assert '"unit-1a1:batch-001": 1' in output
    assert '"unit-1a1:batch-001:cleanup": 0' in output
    assert '"unit-1a1:batch-001:coverage": 0' in output
    assert '"unit-1a2:batch-001": 5' in output
    assert '"unit-1a2:batch-001:cleanup": 0' in output
    assert '"unit-1a2:batch-001:coverage": 0' in output


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


def test_serial_runner_collects_later_shards_after_test_failure(
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
    assert launched == ["unit-1a1", "unit-1a2"]


def test_serial_runner_collects_later_batches_after_collection_failure(
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
    assert launched == [
        "unit-1a1:batch-001",
        "unit-1a1:batch-002",
        "unit-1a2:batch-001",
        "unit-1a2:batch-002",
    ]


def test_serial_runner_retains_every_failing_batch_and_shard(
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
    assert result == 6
    assert launched == list(outcomes)
    assert "failed=4" in output
    for phase, returncode in outcomes.items():
        assert f"'{phase}': {returncode}" in output


def test_terminal_safety_rc_overrides_higher_collected_failure_rc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    outcomes = iter((6, 3, 0))
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
        return next(outcomes)

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
    assert result == 3
    assert launched == ["unit-1a1:batch-001", "unit-1a1:batch-002"]
    assert "'unit-1a1:batch-001': 6" in output
    assert "'unit-1a1:batch-002': 3" in output


@pytest.mark.parametrize(
    ("scenario", "expected_rc", "expected_batches", "expected_phase"),
    [
        ("disk", 73, 1, "unit-1a1:batch-002"),
        ("interpreter", 78, 1, "unit-1a1:batch-002"),
        ("worker", 70, 2, "unit-1a1:batch-002"),
        ("worker-cleanup", 70, 2, "unit-1a1:batch-002"),
        ("no-progress", 124, 2, "unit-1a1:batch-002"),
        ("cancellation", 130, 2, "unit-1a1:batch-002"),
        ("cleanup", 9, 2, "unit-1a1:cleanup"),
    ],
)
def test_collected_failure_never_masks_later_terminal_safety_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
    expected_rc: int,
    expected_batches: int,
    expected_phase: str,
) -> None:
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    module.COVERAGE_SHARDS = resources.coverage_shards
    module.COVERAGE_JSON = resources.coverage_json
    module.COVERAGE_AUDIT = resources.coverage_audit
    launched: list[str] = []
    disk_checks = 0
    interpreter_checks = 0
    cleanup_calls = 0
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def disk_available(*_args: object, **_kwargs: object) -> bool:
        nonlocal disk_checks
        disk_checks += 1
        return scenario != "disk" or disk_checks != 2

    def interpreter_unchanged(*_args: object, **_kwargs: object) -> bool:
        nonlocal interpreter_checks
        interpreter_checks += 1
        return scenario != "interpreter" or interpreter_checks != 3

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        if len(launched) == 1:
            return 6
        return int(
            {
                "worker": module.WORKER_DEATH_EXIT_CODE,
                "worker-cleanup": module.WORKER_DEATH_EXIT_CODE,
                "no-progress": module.NO_PROGRESS_EXIT_CODE,
                "cancellation": 128 + signal.SIGINT,
            }.get(scenario, 0)
        )

    def cleanup(_path: Path) -> int:
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 2 and scenario in {
            "cleanup",
            "cancellation",
            "worker-cleanup",
        }:
            return 9
        return 0

    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda shard: [f"tests/{shard}-{index}.py" for index in range(3)],
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_disk_headroom_available", disk_available)
    monkeypatch.setattr(module, "_interpreter_is_unchanged", interpreter_unchanged)
    monkeypatch.setattr(module, "_run_owned_pytest", run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", cleanup)

    result = module.run(
        ["unit-1a1", "unit-1a2"],
        [],
        max_files_per_batch=1,
        run_isolated=False,
        aggregate_coverage=False,
    )

    output = capsys.readouterr().out
    assert result == expected_rc
    assert launched == [f"unit-1a1:batch-{index:03d}" for index in range(1, expected_batches + 1)]
    assert "'unit-1a1:batch-001': 6" in output
    assert f"'{expected_phase}': {expected_rc}" in output
    if scenario in {"cancellation", "worker-cleanup"}:
        assert "'unit-1a1:cleanup': 9" in output


def test_failed_batch_without_coverage_fragment_stops_before_later_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []
    saved: list[int] = []
    aggregate_called = False
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
        return 2 if label.endswith("batch-001") else 0

    def save_coverage(_shard: str, batch: int, *_args: object) -> bool:
        saved.append(batch)
        return batch != 1

    def aggregate() -> int:
        nonlocal aggregate_called
        aggregate_called = True
        return 0

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda _shard: ["tests/a.py", "tests/b.py"],
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", save_coverage)
    monkeypatch.setattr(module, "_aggregate_coverage", aggregate)

    result = module.run(
        ["unit-1a1"],
        [],
        max_files_per_batch=1,
        run_isolated=False,
    )

    assert result == 2
    assert launched == ["unit-1a1:batch-001"]
    assert saved == [1]
    assert aggregate_called is False


def test_serial_runner_collects_shards_after_isolated_test_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []

    def fake_run_owned(command: list[str], *, label: str, **_kwargs: object) -> int:
        del command
        launched.append(label)
        return 1 if label == "isolated" else 0

    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: None)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(["unit-1a1", "unit-1a2"], [])

    assert result == 1
    assert launched == ["isolated", "unit-1a1:batch-001", "unit-1a2:batch-001"]


def test_serial_runner_stops_on_empty_shard_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []
    identity = {
        "implementation": "cpython",
        "version": "3.11.14",
        "executable": "/opt/python/3.11/bin/python3.11",
    }

    monkeypatch.setattr(module, "_interpreter_identity", lambda: identity)
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda shard: [] if shard == "unit-1a1" else ["tests/later.py"],
    )

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        return 0

    monkeypatch.setattr(
        module,
        "_run_owned_pytest",
        run_owned,
    )

    result = module.run(
        ["unit-1a1", "unit-1a2"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
    )

    assert result == 2
    assert launched == []


def test_serial_runner_rejects_a_completely_empty_plan_before_setup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    setup_calls: list[str] = []

    def record_interpreter_setup() -> dict[str, object]:
        setup_calls.append("interpreter")
        return {}

    def record_command_setup(*_args: object, **_kwargs: object) -> int:
        setup_calls.append("command")
        return 0

    monkeypatch.setattr(
        module,
        "_interpreter_identity",
        record_interpreter_setup,
    )
    monkeypatch.setattr(
        module,
        "_run_command",
        record_command_setup,
    )

    assert module.run([], [], run_isolated=False, aggregate_coverage=False) == 2
    assert setup_calls == []
    output = capsys.readouterr().out
    assert "SERIAL-SHARD-PLAN-EMPTY rc=2" in output
    assert 'phases={"plan": 2}' in output


def test_serial_runner_validate_only_rejects_a_completely_empty_plan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    assert (
        module._validate_only_plan(
            [],
            [],
            max_files_per_batch=1,
            attestation_output=None,
        )
        == 2
    )
    assert "SERIAL-SHARD-VALIDATE-FAIL empty=<plan>" in capsys.readouterr().out


def test_serial_runner_classifies_workspace_creation_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    module.COVERAGE_SHARDS = resources.coverage_shards
    module.COVERAGE_AUDIT = resources.coverage_audit
    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/test_one.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)

    def fail_workspace(*_args: object, **_kwargs: object) -> str:
        raise OSError("workspace volume is unavailable")

    monkeypatch.setattr(module.tempfile, "mkdtemp", fail_workspace)

    assert (
        module.run(
            ["unit-1a1"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    output = capsys.readouterr().out
    assert "SHARD-RESOURCE-SETUP-FAIL" in output
    assert f'"unit-1a1:workspace-setup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output


def test_serial_runner_classifies_batch_workspace_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    module.COVERAGE_SHARDS = resources.coverage_shards
    module.COVERAGE_AUDIT = resources.coverage_audit
    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/test_one.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)

    def occupied_batch(*, prefix: str, dir: str | Path) -> str:
        workspace = Path(dir) / f"{prefix}owned"
        workspace.mkdir()
        (workspace / "batch-001").write_text("not a directory")
        return str(workspace)

    monkeypatch.setattr(module.tempfile, "mkdtemp", occupied_batch)

    assert (
        module.run(
            ["unit-1a1"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    output = capsys.readouterr().out
    assert "SHARD-RESOURCE-SETUP-FAIL" in output
    assert f'"unit-1a1:batch-001:setup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output


def test_serial_runner_classifies_socket_tmpdir_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    module.COVERAGE_SHARDS = resources.coverage_shards
    module.COVERAGE_AUDIT = resources.coverage_audit
    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/test_one.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_disk_headroom_available", lambda *_args, **_kwargs: True)

    def fail_socket_root(*, prefix: str, dir: str | Path) -> str:
        if prefix.startswith("gludd-gate-"):
            workspace = Path(dir) / f"{prefix}owned"
            workspace.mkdir()
            return str(workspace)
        raise OSError("socket temp volume is unavailable")

    monkeypatch.setattr(module.tempfile, "mkdtemp", fail_socket_root)

    assert (
        module.run(
            ["unit-1a1"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    output = capsys.readouterr().out
    assert "SHARD-RESOURCE-SETUP-FAIL" in output
    assert f'"unit-1a1:batch-001:tmpdir-setup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output


def test_coverage_setup_failure_cleans_partially_created_fragments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    occupied_parent = tmp_path / "occupied-audit-parent"
    occupied_parent.write_text("not a directory")
    module.COVERAGE_AUDIT = occupied_parent / "coverage.json"
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)

    assert (
        module.run(
            ["unit-1a1"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    assert not module.COVERAGE_SHARDS.exists()
    output = capsys.readouterr().out
    assert f'"coverage:setup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output
    assert '"coverage:fragments-cleanup": 0' in output


def test_coverage_erase_failure_yields_to_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_AUDIT = tmp_path / "coverage-audit.json"
    cleanup_results = iter((0, module.CLEANUP_FAILURE_EXIT_CODE))
    monkeypatch.setattr(
        module,
        "_remove_owned_tree",
        lambda *_args, **_kwargs: next(cleanup_results),
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 5)

    assert (
        module.run(
            ["unit-1a1"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    output = capsys.readouterr().out
    assert '"coverage:erase": 5' in output
    assert f'"coverage:fragments-cleanup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output


def test_serial_runner_converts_cleanup_io_error_without_masking_test_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def cleanup_failure(_path: Path) -> int:
        raise OSError("owned cleanup denied")

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/failing.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", cleanup_failure)

    result = module.run(
        ["unit-1a1"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
    )

    assert result == module.CLEANUP_FAILURE_EXIT_CODE
    output = capsys.readouterr().out
    assert "SHARD-CLEANUP-FAIL" in output
    assert '"unit-1a1:batch-001": 1' in output
    assert f'"unit-1a1:batch-001:cleanup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output


@pytest.mark.parametrize(
    ("scenario", "expected_rc", "expected_batches"),
    [
        ("disk", 73, []),
        ("interpreter", 78, []),
        ("coverage", 1, ["unit-1a1:batch-001"]),
    ],
)
def test_serial_runner_preserves_immediate_resource_safety_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_rc: int,
    expected_batches: list[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    module.COVERAGE_SHARDS = resources.coverage_shards
    module.COVERAGE_JSON = resources.coverage_json
    module.COVERAGE_AUDIT = resources.coverage_audit
    identity = {
        "implementation": "cpython",
        "version": "3.11.14",
        "executable": "/opt/python/3.11/bin/python3.11",
    }
    launched: list[str] = []
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir(parents=True)
        return str(path)

    def cleanup(path: Path) -> int:
        shutil.rmtree(path, ignore_errors=True)
        return 0

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        return 0

    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "_interpreter_identity", lambda: identity)
    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        module,
        "_disk_headroom_available",
        lambda *_args, **_kwargs: scenario != "disk",
    )
    monkeypatch.setattr(
        module,
        "_interpreter_is_unchanged",
        lambda *_args, **_kwargs: scenario != "interpreter",
    )
    monkeypatch.setattr(module, "_run_owned_pytest", run_owned)
    monkeypatch.setattr(
        module,
        "_save_shard_coverage",
        lambda *_args: scenario != "coverage",
    )
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", cleanup)

    result = module.run(
        ["unit-1a1", "unit-1a2"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
    )

    assert result == expected_rc
    assert launched == expected_batches


def test_serial_runner_records_isolated_failure_and_stops_before_shards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    shard_launched = False

    def fake_run(command: list[str], *, env: dict[str, str] | None = None) -> int:
        return 0

    def fake_run_owned(command: list[str], **_kwargs: object) -> int:
        nonlocal shard_launched
        if "tests/unit/test_all_plugins_runtime.py" in command:
            return 7
        if "-m" in command and "pytest" in command:
            shard_launched = True
        return 0

    monkeypatch.setattr(module, "_run_command", fake_run)
    monkeypatch.setattr(module, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(
        module,
        "_env_for_shard",
        lambda shard, basetemp: {"COVERAGE_FILE": str(basetemp / ".coverage")},
    )
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *args: True)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(["unit-1a1"], [])

    assert result == 7
    assert shard_launched is False


def test_serial_runner_fails_closed_when_coverage_erase_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    expanded = False

    def fake_expand(shard: str) -> list[str]:
        nonlocal expanded
        expanded = True
        return [f"tests/{shard}.py"]

    monkeypatch.setattr(module, "expand_shard", fake_expand)
    monkeypatch.setattr(module, "_run_command", lambda *args, **kwargs: 2)

    result = module.run(["unit-1a1"], [])

    assert result == 2
    assert expanded is False


def test_owned_pytest_runner_times_out_silent_worker_and_emits_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    class SilentProcess:
        pid = 42422
        returncode: int | None = None
        stdout = io.StringIO("")

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            assert self.returncode is not None
            return self.returncode

    process = SilentProcess()

    def terminate(owned: SilentProcess, **_kwargs: object) -> None:
        assert owned is process
        owned.returncode = -signal.SIGTERM

    clock = iter((0.0, 2.0))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(module, "_terminate_owned_process", terminate)
    monkeypatch.setattr(module, "_owned_process_group_alive", lambda _process: False)

    rc = module._run_owned_pytest(
        ["pytest"],
        env={},
        label="unit-3:batch-quiet",
        heartbeat_seconds=1.0,
        no_progress_seconds=1.0,
    )

    output = capsys.readouterr().out
    assert rc == module.NO_PROGRESS_EXIT_CODE
    assert "SHARD-HEARTBEAT" in output
    assert "SHARD-NO-PROGRESS" in output


def test_shard_coverage_fragment_and_aggregate_preserve_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "fragments"
    module.COVERAGE_SHARDS.mkdir()
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "audit.json"
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    coverage_file = batchtemp / ".coverage"
    source = str(ROOT / "src" / "general_ludd" / "__init__.py")
    data = CoverageData(basename=str(coverage_file))
    data.add_lines({source: {1}})
    data.write()
    expected_coverage = coverage_file.read_bytes()
    env = {"COVERAGE_FILE": str(coverage_file)}

    assert module._save_shard_coverage("unit-3", 2, batchtemp, env) is True
    assert (module.COVERAGE_SHARDS / ".coverage.unit-3.batch-002").read_bytes() == (expected_coverage)

    commands: list[list[str]] = []

    def run_command(command: list[str], *, env: dict[str, str] | None = None) -> int:
        commands.append(command)
        return 3 if "xml" in command else 0

    monkeypatch.setattr(module, "_run_command", run_command)

    assert module._aggregate_coverage() == 3
    assert len(commands) == 5
    assert "--max-worker-restart=0" not in " ".join(argument for command in commands for argument in command)
    assert any("--threshold=85" in command for command in commands)
    assert any("--per-file-threshold=75" in command for command in commands)


def test_save_shard_coverage_rejects_corrupt_nonempty_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "fragments"
    module.COVERAGE_SHARDS.mkdir()
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    coverage_file = batchtemp / ".coverage"
    coverage_file.write_bytes(b"not-a-coverage-sqlite-database")

    assert (
        module._save_shard_coverage(
            "unit-1a1",
            1,
            batchtemp,
            {"COVERAGE_FILE": str(coverage_file)},
        )
        is False
    )
    assert not (module.COVERAGE_SHARDS / ".coverage.unit-1a1.batch-001").exists()
    assert "SHARD-COVERAGE-INVALID" in capsys.readouterr().out


def test_coverage_database_validator_rejects_symlink_and_missing_file(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")
    missing = tmp_path / "missing.coverage"
    empty = tmp_path / "empty.coverage"
    empty.write_bytes(b"")
    symlink = tmp_path / "symlink.coverage"
    symlink.symlink_to(missing)

    assert module._coverage_data_error(symlink) == "symbolic links are not accepted"
    assert module._coverage_data_error(missing).startswith("FileNotFoundError:")
    assert module._coverage_data_error(empty) == "file is missing or empty"


def test_coverage_database_validator_classifies_hash_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    coverage_file = tmp_path / ".coverage"
    coverage_file.write_bytes(b"nonempty")

    def fail_hash(_path: Path) -> str:
        raise OSError("coverage read failed")

    monkeypatch.setattr(module, "_file_sha256", fail_hash)

    assert module._coverage_data_error(coverage_file).startswith("OSError:")


def test_save_shard_coverage_classifies_copy_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "fragments"
    module.COVERAGE_SHARDS.mkdir()
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    coverage_file = batchtemp / ".coverage"
    data = CoverageData(basename=str(coverage_file))
    data.add_lines({str(ROOT / "src" / "general_ludd" / "__init__.py"): {1}})
    data.write()

    def fail_copy(_source: Path, _destination: Path) -> None:
        raise OSError("disk became read-only")

    monkeypatch.setattr(module.shutil, "copy2", fail_copy)

    assert (
        module._save_shard_coverage(
            "unit-1a1",
            1,
            batchtemp,
            {"COVERAGE_FILE": str(coverage_file)},
        )
        is False
    )
    assert not (module.COVERAGE_SHARDS / ".coverage.unit-1a1.batch-001").exists()
    assert "SHARD-COVERAGE-TRANSFER-FAIL" in capsys.readouterr().out


def test_save_shard_coverage_classifies_post_validation_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "fragments"
    module.COVERAGE_SHARDS.mkdir()
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    coverage_file = batchtemp / ".coverage"
    data = CoverageData(basename=str(coverage_file))
    data.add_lines({str(ROOT / "src" / "general_ludd" / "__init__.py"): {1}})
    data.write()
    real_hash = module._file_sha256
    hash_calls = 0

    def fail_third_hash(path: Path) -> str:
        nonlocal hash_calls
        hash_calls += 1
        if hash_calls == 3:
            raise OSError("coverage source vanished")
        digest = real_hash(path)
        assert isinstance(digest, str)
        return digest

    monkeypatch.setattr(module, "_file_sha256", fail_third_hash)

    assert (
        module._save_shard_coverage(
            "unit-1a1",
            1,
            batchtemp,
            {"COVERAGE_FILE": str(coverage_file)},
        )
        is False
    )
    assert not (module.COVERAGE_SHARDS / ".coverage.unit-1a1.batch-001").exists()
    assert "SHARD-COVERAGE-TRANSFER-FAIL" in capsys.readouterr().out


def test_save_shard_coverage_unions_controller_and_worker_data(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "durable" / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir(parents=True)
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    coverage_file = batchtemp / ".coverage"
    source = str(ROOT / "src" / "general_ludd" / "__init__.py")

    controller = CoverageData(basename=str(coverage_file))
    controller.add_lines({source: {1}})
    controller.write()
    worker = CoverageData(basename=str(batchtemp / ".coverage.worker"))
    worker.add_lines({source: {2}})
    worker.write()

    assert module._save_shard_coverage(
        "unit-1b",
        6,
        batchtemp,
        {"COVERAGE_FILE": str(coverage_file)},
    )

    destination = module.COVERAGE_SHARDS / ".coverage.unit-1b.batch-006"
    combined = CoverageData(basename=str(destination))
    combined.read()
    assert set(combined.lines(source) or ()) == {1, 2}


def test_hosted_coverage_transfer_survives_workspace_and_python_suffix(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "durable" / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir(parents=True)
    workspace = tmp_path / "ephemeral" / "batch-006"
    workspace.mkdir(parents=True)
    batch_coverage = workspace / ".coverage"
    source = str(ROOT / "src" / "general_ludd" / "__init__.py")
    data = CoverageData(basename=str(batch_coverage))
    data.add_lines({source: {1, 2}})
    data.write()

    assert module._save_shard_coverage(
        "unit-1a2",
        6,
        workspace,
        {"COVERAGE_FILE": str(batch_coverage)},
    )
    shutil.rmtree(workspace.parent)
    destination = tmp_path / "checkout" / ".coverage.unit-1a2-3.11"

    assert module._combine_coverage_output(destination) == 0

    combined = CoverageData(basename=str(destination))
    combined.read()
    assert source in combined.measured_files()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    assert fragment.is_file()
    assert not list(module.COVERAGE_SHARDS.glob(f"{destination.name}.fragment-*"))


@pytest.mark.parametrize("create_directory", [False, True])
def test_coverage_output_fails_before_combine_when_no_fragments_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    create_directory: bool,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    if create_directory:
        module.COVERAGE_SHARDS.mkdir()
    commands: list[list[str]] = []

    def record_command(command: list[str], **_kwargs: object) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(module, "_run_command", record_command)

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert commands == []
    assert "SHARD-COVERAGE-FRAGMENTS-MISSING" in capsys.readouterr().out


def test_coverage_transfer_mismatch_removes_owned_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    source = str(ROOT / "src" / "general_ludd" / "__init__.py")
    data = CoverageData(basename=str(fragment))
    data.add_lines({source: {1}})
    data.write()

    def truncated_copy(_source: Path, destination: Path) -> None:
        destination.write_bytes(b"")

    monkeypatch.setattr(module.shutil, "copy2", truncated_copy)

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert not list(module.COVERAGE_SHARDS.glob(".coverage.unit-1a2-3.11.fragment-*"))


def test_coverage_output_rejects_an_invalid_fragment_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    fragment.write_bytes(b"corrupt coverage database")
    commands: list[list[str]] = []

    def record_command(command: list[str], **_kwargs: object) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(
        module,
        "_run_command",
        record_command,
    )

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert commands == []
    assert "SHARD-COVERAGE-FRAGMENT-INVALID" in capsys.readouterr().out


def test_coverage_output_classifies_destination_setup_io_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    data = CoverageData(basename=str(fragment))
    data.add_lines({str(ROOT / "src" / "general_ludd" / "__init__.py"): {1}})
    data.write()
    occupied_parent = tmp_path / "occupied-parent"
    occupied_parent.write_text("not a directory")

    assert module._combine_coverage_output(occupied_parent / ".coverage") == 1
    assert "SHARD-COVERAGE-OUTPUT-SETUP-FAIL" in capsys.readouterr().out


def test_coverage_output_classifies_fragment_copy_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    data = CoverageData(basename=str(fragment))
    data.add_lines({str(ROOT / "src" / "general_ludd" / "__init__.py"): {1}})
    data.write()

    def fail_copy(_source: Path, _destination: Path) -> None:
        raise OSError("coverage destination is read-only")

    monkeypatch.setattr(module.shutil, "copy2", fail_copy)

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert not list(module.COVERAGE_SHARDS.glob(".coverage.unit-1a2-3.11.fragment-*"))
    assert "SHARD-COVERAGE-TRANSFER-FAIL" in capsys.readouterr().out


def test_coverage_output_requires_combine_to_create_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    data = CoverageData(basename=str(fragment))
    data.add_lines({str(ROOT / "src" / "general_ludd" / "__init__.py"): {1}})
    data.write()
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert not list(module.COVERAGE_SHARDS.glob(".coverage.unit-1a2-3.11.fragment-*"))
    assert "SHARD-COVERAGE-OUTPUT-MISSING" in capsys.readouterr().out


def test_coverage_transfer_rejects_same_size_content_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    source = str(ROOT / "src" / "general_ludd" / "__init__.py")
    data = CoverageData(basename=str(fragment))
    data.add_lines({source: {1}})
    data.write()
    commands: list[list[str]] = []

    def corrupt_copy(source_path: Path, destination: Path) -> None:
        payload = bytearray(source_path.read_bytes())
        payload[-1] ^= 1
        destination.write_bytes(payload)

    def record_command(command: list[str], **_kwargs: object) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(module.shutil, "copy2", corrupt_copy)
    monkeypatch.setattr(
        module,
        "_run_command",
        record_command,
    )

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert commands == []
    assert not list(module.COVERAGE_SHARDS.glob(".coverage.unit-1a2-3.11.fragment-*"))
    assert "SHARD-COVERAGE-TRANSFER-MISMATCH" in capsys.readouterr().out


def test_coverage_output_evidence_is_hash_bound_and_python_specific(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / ".coverage.unit-1a2-3.11"
    destination.write_bytes(b"durable-coverage")

    evidence = module._coverage_output_evidence(destination)

    assert evidence == {
        "artifact": destination.name,
        "bytes": len(b"durable-coverage"),
        "sha256": hashlib.sha256(b"durable-coverage").hexdigest(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def test_cli_binds_hosted_coverage_output_into_terminal_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / ".coverage.unit-1a2-3.11"
    attestation = tmp_path / "unit-1a2-attestation.json"
    resource_paths = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "default-attestation.json",
    )

    def run(*_args: object, **_kwargs: object) -> int:
        destination.write_bytes(b"hosted-coverage")
        return 0

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(module, "_resource_paths", lambda: resource_paths)
    monkeypatch.setattr(module, "run", run)
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
            "--shards=unit-1a2",
            "--skip-isolated",
            "--skip-aggregate",
            f"--coverage-output={destination}",
            f"--attestation-output={attestation}",
        ],
    )

    assert module.main() == 0
    payload = json.loads(attestation.read_text(encoding="utf-8"))
    assert payload["coverage"] == module._coverage_output_evidence(destination)


def test_missing_shard_coverage_attempts_combine_then_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "fragments"
    module.COVERAGE_SHARDS.mkdir()
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    commands: list[list[str]] = []

    def run_command(command: list[str], **_kwargs: object) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(module, "_run_command", run_command)

    assert (
        module._save_shard_coverage(
            "unit-3",
            1,
            batchtemp,
            {"COVERAGE_FILE": str(batchtemp / ".coverage")},
        )
        is False
    )
    assert commands == [
        [
            sys.executable,
            "-m",
            "coverage",
            "combine",
            "--append",
            "--keep",
            f"--data-file={batchtemp / '.coverage'}",
            str(batchtemp),
        ]
    ]


def test_serial_runner_cli_forwards_explicit_resource_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    received: dict[str, object] = {}

    def run(
        shards: list[str],
        pytest_args: list[str],
        **kwargs: object,
    ) -> int:
        received.update(shards=shards, pytest_args=pytest_args, **kwargs)
        return 9

    monkeypatch.setattr(module, "run", run)
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
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2,unit-3a",
            "--pytest-args=-q -W error",
            "--max-files-per-batch=17",
            "--heartbeat-seconds=4",
            "--no-progress-seconds=23",
        ],
    )

    assert module.main() == 9
    assert received == {
        "shards": ["unit-2", "unit-3a"],
        "pytest_args": ["-q", "-W", "error"],
        "max_files_per_batch": 17,
        "heartbeat_seconds": 4.0,
        "no_progress_seconds": 23.0,
        "run_isolated": True,
        "aggregate_coverage": True,
        "coverage_output": None,
        "resume_path": None,
        "watchdog_owned_gate": False,
    }


def test_serial_runner_cli_forwards_hosted_single_shard_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    received: dict[str, object] = {}
    coverage_output = tmp_path / ".coverage.unit-2-3.11"

    def run(
        shards: list[str],
        pytest_args: list[str],
        **kwargs: object,
    ) -> int:
        received.update(shards=shards, pytest_args=pytest_args, **kwargs)
        coverage_output.write_bytes(b"hosted coverage")
        return 0

    monkeypatch.setattr(module, "run", run)
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
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2",
            "--pytest-args=-W error",
            "--skip-isolated",
            "--skip-aggregate",
            f"--coverage-output={coverage_output}",
        ],
    )

    assert module.main() == 0
    assert received["run_isolated"] is False
    assert received["aggregate_coverage"] is False
    assert received["coverage_output"] == coverage_output


def test_release_execution_policy_binds_python_runtime() -> None:
    """Local and hosted evidence must identify the same Python runtime family."""
    module = _load_script("run_ci_shards_serial")

    policy = module.execution_policy(["-W", "error"])

    assert policy["python_version"] == (f"{sys.version_info.major}.{sys.version_info.minor}")
    assert policy["python_implementation"] == sys.implementation.name


def test_release_execution_policy_uses_canonical_hosted_runtime() -> None:
    """Release policy must not depend on the interpreter running the verifier."""
    module = _load_script("run_ci_shards_serial")

    policy = module.release_execution_policy()

    assert policy["python_version"] == "3.11"
    assert policy["python_implementation"] == "cpython"
    assert policy["pytest_args"] == ["-W", "error"]
    assert policy["xdist_workers"] == 0
    assert policy["distribution"] == "none"
    assert policy["max_worker_restart"] is None


def test_serial_runner_fails_closed_after_batch_mutates_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A shared-venv mutation must stop the exact-SHA lane before another batch."""
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    expected = {
        "implementation": "cpython",
        "version": "3.11.14",
        "executable": "/opt/python/3.11/bin/python3.11",
    }
    changed = {
        "implementation": "cpython",
        "version": "3.14.0",
        "executable": "/opt/python/3.14/bin/python3.14",
    }
    identities = iter((expected, expected, changed))
    executed: list[str] = []

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        executed.append(label)
        return 0

    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "COVERAGE_SHARDS", resources.coverage_shards)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["a.py", "b.py"])
    monkeypatch.setattr(
        module,
        "_interpreter_identity",
        lambda: next(identities),
    )
    monkeypatch.setattr(
        module,
        "_run_owned_pytest",
        run_owned,
    )
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)

    result = module.run(
        ["unit-1a1"],
        [],
        max_files_per_batch=1,
        run_isolated=False,
        aggregate_coverage=False,
    )

    assert result == module.INTERPRETER_DRIFT_EXIT_CODE
    assert executed == ["unit-1a1:batch-001"]
    output = capsys.readouterr().out
    assert "SHARD-INTERPRETER-DRIFT" in output
    assert "3.11.14" in output
    assert "3.14.0" in output


def test_resume_state_round_trip_and_invalid_payload(tmp_path: Path) -> None:
    module = _load_script("run_ci_shards_serial")
    resume = tmp_path / "nested" / "resume.json"

    assert module._load_resume_state(resume) == {}
    resume.parent.mkdir()
    resume.write_text("not json", encoding="utf-8")
    assert module._load_resume_state(resume) == {}
    resume.write_text("[]", encoding="utf-8")
    assert module._load_resume_state(resume) == {}

    expected = {"batch": {"returncode": 0}}
    module._save_resume_state(resume, expected)

    assert module._load_resume_state(resume) == expected


def test_disk_headroom_fails_closed_when_observation_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    def fail_observation(_path: Path) -> object:
        raise OSError("disk unavailable")

    assert not module._disk_headroom_available(
        tmp_path,
        minimum_free_bytes=1,
        disk_usage=fail_observation,
        context="coverage-test",
    )
    assert "SHARD-DISK-PREFLIGHT status=error" in capsys.readouterr().out


def test_interpreter_probe_rejects_failure_and_malformed_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "probe exploded"

    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: Result())
    with pytest.raises(RuntimeError, match="probe exploded"):
        module._interpreter_identity()

    Result.returncode = 0
    Result.stdout = "[]"
    Result.stderr = ""
    with pytest.raises(RuntimeError, match="malformed evidence"):
        module._interpreter_identity()

    monkeypatch.setattr(
        module,
        "_interpreter_identity",
        lambda: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    assert not module._interpreter_is_unchanged({}, context="coverage-test")
    assert "SHARD-INTERPRETER-PROBE-FAIL" in capsys.readouterr().out


def test_validate_only_plan_rejects_an_empty_expansion(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    monkeypatch.setattr(module, "_plan_shards", lambda *_args, **_kwargs: [("unit-2", [])])

    assert (
        module._validate_only_plan(
            ["unit-2"],
            [],
            max_files_per_batch=4,
            attestation_output=None,
        )
        == 2
    )
    assert "SERIAL-SHARD-VALIDATE-FAIL empty=unit-2" in capsys.readouterr().out


def test_non_posix_process_group_helpers_use_direct_child_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    class Process:
        pid = 42
        terminated = False
        killed = False

        @classmethod
        def poll(cls) -> None:
            return None

        @classmethod
        def terminate(cls) -> None:
            cls.terminated = True

        @classmethod
        def kill(cls) -> None:
            cls.killed = True

    class NonPosixOS:
        name = "nt"

    monkeypatch.setattr(module, "os", NonPosixOS())

    assert module._owned_process_group_alive(Process()) is True
    module._signal_owned_process_group(Process(), signal.SIGTERM)
    module._signal_owned_process_group(Process(), signal.SIGKILL)
    assert Process.terminated is True
    assert Process.killed is True


def test_validate_only_plan_reports_successful_bounded_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    monkeypatch.setattr(
        module,
        "_plan_shards",
        lambda *_args, **_kwargs: [("unit-2", [["a.py"], ["b.py"]])],
    )

    assert (
        module._validate_only_plan(
            ["unit-2"],
            ["-q"],
            max_files_per_batch=1,
            attestation_output=tmp_path / "attestation.json",
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "SERIAL-SHARD-VALIDATE shards=unit-2 files=2 batches=2" in output
    assert f"attestation={tmp_path / 'attestation.json'}" in output


def test_plan_shards_rejects_nonpositive_batch_size() -> None:
    module = _load_script("run_ci_shards_serial")

    with pytest.raises(ValueError, match="max_files_per_batch must be positive"):
        module._plan_shards(["unit-2"], max_files_per_batch=0)


def test_owned_tree_cleanup_classifies_io_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    def fail_remove(_path: Path) -> None:
        raise OSError("busy")

    monkeypatch.setattr(module.shutil, "rmtree", fail_remove)

    assert (
        module._remove_owned_tree(tmp_path / "owned", context="coverage-test")
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    assert "SHARD-CLEANUP-FAIL context=coverage-test" in capsys.readouterr().out


def test_owned_tmpdir_cleanup_rejects_unowned_path(tmp_path: Path) -> None:
    module = _load_script("run_ci_shards_serial")

    with pytest.raises(ValueError, match="refusing to remove unowned shard temp root"):
        module._cleanup_owned_tmpdir(tmp_path)


def test_partition_and_resume_boundaries_fail_closed(tmp_path: Path) -> None:
    module = _load_script("run_ci_shards_serial")

    with pytest.raises(ValueError, match="max_files must be positive"):
        module._partition_test_paths([], max_files=0)

    shard = "unit-2"
    files = ["tests/unit/test_example.py"]
    key = module._batch_key(shard, 1, files)
    coverage_shards = tmp_path / "coverage"
    assert not module._resume_skip_batch({}, shard, 1, files, coverage_shards, tmp_path)
    assert not module._resume_skip_batch(
        {key: {"rc": 1}},
        shard,
        1,
        files,
        coverage_shards,
        tmp_path,
    )
    assert not module._resume_skip_batch(
        {key: {"rc": 0, "coverage_fragment": None}},
        shard,
        1,
        files,
        coverage_shards,
        tmp_path,
    )


def test_posix_process_group_helpers_observe_and_signal_owned_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    observed: list[tuple[int, object]] = []

    class PosixOS:
        name = "posix"

        @staticmethod
        def killpg(pid: int, signum: object) -> None:
            observed.append((pid, signum))

    class Process:
        pid = 73

    monkeypatch.setattr(module, "os", PosixOS())

    assert module._owned_process_group_alive(Process()) is True
    module._signal_owned_process_group(Process(), signal.SIGTERM)
    assert observed == [(73, 0), (73, signal.SIGTERM)]
