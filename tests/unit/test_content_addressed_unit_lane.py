"""S48 contracts for the Pants-backed content-addressed unit lane."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import content_addressed_unit_lane as lane
from scripts.ci_receipt_auth import (
    ReceiptSigner,
    ReceiptTrustPolicy,
    authenticate_receipt_envelope,
)

SHA = "a" * 40


def _manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "pants_version": "2.33.0",
        "targets": ["tests/pants_cached_unit:tests"],
        "control_files": [
            "pants.toml",
            "config/pants_unit_cache_lane.json",
            "config/pants_unit_cache_toolchain.lock",
            "config/pants_unit_cache_coverage.ini",
            "src/general_ludd/algorithms/BUILD.pants",
            "tests/pants_cached_unit/BUILD.pants",
        ],
        "relevant_environment": ["GLUDD_PANTS_UNIT_POLICY"],
        "dynamic_inputs": [],
        "external_inputs": [],
        "max_local_workers": 2,
        "hosted_workers": 1,
        "minimum_aggregate_coverage": 85,
        "minimum_file_coverage": 75,
    }


def _manifest(tmp_path: Path, **changes: object) -> lane.LaneManifest:
    payload = _manifest_payload()
    payload.update(changes)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return lane.load_manifest(path)


@pytest.mark.parametrize(
    "field",
    (
        "schema_version",
        "pants_version",
        "targets",
        "control_files",
        "relevant_environment",
        "dynamic_inputs",
        "external_inputs",
        "max_local_workers",
        "hosted_workers",
        "minimum_aggregate_coverage",
        "minimum_file_coverage",
    ),
)
def test_manifest_rejects_missing_or_extra_contract_fields(
    tmp_path: Path,
    field: str,
) -> None:
    payload = _manifest_payload()
    del payload[field]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest fields"):
        lane.load_manifest(path)

    payload = _manifest_payload()
    payload["unexpected"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest fields"):
        lane.load_manifest(path)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"schema_version": 2}, "schema"),
        ({"pants_version": "latest"}, "Pants version"),
        ({"targets": []}, "targets"),
        ({"targets": ["::"]}, "target"),
        ({"control_files": ["/tmp/escape"]}, "control file"),
        ({"relevant_environment": ["PATH"]}, "environment"),
        ({"max_local_workers": 3}, "workers"),
        ({"hosted_workers": 2}, "hosted"),
        ({"minimum_aggregate_coverage": 84}, "aggregate"),
        ({"minimum_file_coverage": 74}, "file coverage"),
    ),
)
def test_manifest_is_bounded_and_fail_closed(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    payload = _manifest_payload()
    payload.update(changes)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        lane.load_manifest(path)


@pytest.mark.parametrize(
    ("category", "mutated"),
    (
        ("source", b"source-v2"),
        ("test", b"test-v2"),
        ("conftest", b"conftest-v2"),
        ("lock", b"lock-v2"),
        ("config", b"config-v2"),
        ("interpreter", b"cpython-3.12"),
        ("plugin", b"pytest-9"),
        ("platform", b"linux-aarch64"),
        ("environment", b"policy-v2"),
        ("coverage", b"branch=false"),
    ),
)
def test_every_relevant_pants_process_input_mutation_changes_execution_identity(
    category: str,
    mutated: bytes,
) -> None:
    """Model the opaque Pants process digest; Gludd never walks dependencies."""
    original = {
        "source": b"source-v1",
        "test": b"test-v1",
        "conftest": b"conftest-v1",
        "lock": b"lock-v1",
        "config": b"config-v1",
        "interpreter": b"cpython-3.11",
        "plugin": b"pytest-8",
        "platform": b"darwin-arm64",
        "environment": b"policy-v1",
        "coverage": b"branch=true",
    }
    changed = copy.deepcopy(original)
    changed[category] = mutated

    before = lane.pants_process_identity(original)
    after = lane.pants_process_identity(changed)

    assert before != after
    assert set(before) == {"authority", "digest", "input_categories"}
    assert before["authority"] == "pants-hermetic-process-cas-v1"
    assert before["input_categories"] == sorted(original)


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"graph_status": "ambiguous"}, "dependency-ambiguous"),
        ({"graph_status": "unowned"}, "dependency-unowned"),
        ({"graph_status": "error"}, "dependency-probe-error"),
        ({"dynamic_inputs": ("plugin-string-import",)}, "dynamic-input"),
        ({"external_inputs": ("network",)}, "external-input"),
    ),
)
def test_ambiguous_unowned_dynamic_and_external_inputs_force_fresh(
    changes: dict[str, object],
    reason: str,
) -> None:
    request = lane.ExecutionRequest(mode="cached", hosted_ci=False, **changes)

    decision = lane.decide_execution(request, max_local_workers=2, hosted_workers=1)

    assert decision.force_fresh is True
    assert decision.allow_cross_commit_cache is False
    assert reason in decision.reasons
    assert decision.workers == 2


def test_local_cache_hit_path_is_bounded_and_hosted_ci_is_cold_single_worker() -> None:
    local = lane.decide_execution(
        lane.ExecutionRequest(mode="cached", hosted_ci=False),
        max_local_workers=2,
        hosted_workers=1,
    )
    hosted = lane.decide_execution(
        lane.ExecutionRequest(mode="cached", hosted_ci=True),
        max_local_workers=2,
        hosted_workers=1,
    )

    assert local.allow_cross_commit_cache is True
    assert local.force_fresh is False
    assert local.workers == 2
    assert hosted.allow_cross_commit_cache is False
    assert hosted.force_fresh is True
    assert hosted.workers == 1
    assert hosted.reasons == ("hosted-ci-cold-lane",)


def test_fresh_mode_and_nightly_comparison_never_authorize_a_cached_result() -> None:
    fresh = lane.decide_execution(
        lane.ExecutionRequest(mode="fresh", hosted_ci=False),
        max_local_workers=2,
        hosted_workers=1,
    )
    nightly_fresh = lane.decide_execution(
        lane.ExecutionRequest(mode="nightly-fresh", hosted_ci=False),
        max_local_workers=2,
        hosted_workers=1,
    )

    assert fresh.force_fresh and not fresh.allow_cross_commit_cache
    assert nightly_fresh.force_fresh and not nightly_fresh.allow_cross_commit_cache
    assert "operator-forced-fresh" in fresh.reasons
    assert "nightly-forced-fresh" in nightly_fresh.reasons


def test_pants_command_never_exceeds_worker_bound_and_cold_lane_disables_cache(
    tmp_path: Path,
) -> None:
    cached = lane.build_pants_command(
        pants_binary="pants",
        decision=lane.ExecutionDecision(False, True, 2, ()),
        targets=("tests/pants_cached_unit:tests",),
        cache_root=tmp_path / "cache",
        workdir=tmp_path / "work",
        distdir=tmp_path / "dist",
    )
    cold = lane.build_pants_command(
        pants_binary="pants",
        decision=lane.ExecutionDecision(True, False, 1, ("hosted-ci-cold-lane",)),
        targets=("tests/pants_cached_unit:tests",),
        cache_root=tmp_path / "cache",
        workdir=tmp_path / "work",
        distdir=tmp_path / "dist",
    )

    assert "--process-execution-local-parallelism=2" in cached
    assert "--test-force" not in cached
    assert "--no-local-cache" not in cached
    assert "--process-execution-local-parallelism=1" in cold
    assert "--test-force" in cold
    assert "--no-local-cache" in cold
    assert cached[-2:] == ["test", "tests/pants_cached_unit:tests"]


def test_candidate_sha_is_only_final_provenance_not_cache_execution_identity() -> None:
    execution = lane.pants_process_identity({"source": b"one"})
    receipt = lane.build_receipt_payload(
        candidate={
            "candidate_sha": SHA,
            "repository_state_id": "b" * 64,
            "clean": True,
            "exact_sha": True,
        },
        execution=execution,
        result={"returncode": 0, "manifest_sha256": "c" * 64},
        mode="cached",
        comparison=None,
    )

    assert SHA not in json.dumps(receipt["execution_identity"], sort_keys=True)
    assert receipt["candidate_provenance"]["candidate_sha"] == SHA
    assert receipt["release_eligible"] is True


def test_authenticated_receipt_binds_candidate_and_execution_result() -> None:
    signer = ReceiptSigner(key=b"k" * 32, issued_at=100, validity_seconds=300)
    policy = ReceiptTrustPolicy(
        trusted_keys={signer.signer_id: b"k" * 32},
        verification_epoch=101,
    )
    payload = lane.build_receipt_payload(
        candidate={
            "candidate_sha": SHA,
            "repository_state_id": "b" * 64,
            "clean": True,
            "exact_sha": True,
        },
        execution=lane.pants_process_identity({"source": b"one"}),
        result={"returncode": 0, "manifest_sha256": "c" * 64},
        mode="cached",
        comparison=None,
    )
    envelope = lane.authenticate_payload(payload, signer=signer)
    action_digest = lane.canonical_sha256(payload["execution_identity"])
    content_digest = lane.canonical_sha256(payload)

    authentication = authenticate_receipt_envelope(
        envelope,
        expected_kind="pass",
        expected_action_digest=action_digest,
        expected_content_sha256=content_digest,
        trust_policy=policy,
    )

    assert authentication.verified is True
    tampered = copy.deepcopy(payload)
    tampered["candidate_provenance"]["candidate_sha"] = "d" * 40
    assert lane.canonical_sha256(tampered) != content_digest


def test_normalized_junit_manifest_tracks_exact_nodes_and_outcomes(tmp_path: Path) -> None:
    report = tmp_path / "TEST-example.xml"
    report.write_text(
        """<?xml version='1.0' encoding='utf-8'?>
<testsuite tests="3" failures="1" skipped="1">
  <testcase classname="slice.TestPure" name="test_pass" time="0.2" />
  <testcase classname="slice.TestPure" name="test_skip" time="0.0"><skipped /></testcase>
  <testcase classname="slice.TestPure" name="test_fail" time="0.1"><failure message="x" /></testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    manifest = lane.normalized_junit_manifest(tmp_path)

    assert manifest["counts"] == {"failed": 1, "passed": 1, "skipped": 1}
    assert manifest["nodes"] == [
        {"node": "slice.TestPure::test_fail", "outcome": "failed"},
        {"node": "slice.TestPure::test_pass", "outcome": "passed"},
        {"node": "slice.TestPure::test_skip", "outcome": "skipped"},
    ]


def test_normalized_coverage_manifest_tracks_branches_and_per_file_floor(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "meta": {"branch_coverage": True},
                "totals": {"percent_covered": 91.25, "num_branches": 8, "covered_branches": 7},
                "files": {
                    "src/example.py": {
                        "summary": {
                            "percent_covered": 87.5,
                            "num_branches": 4,
                            "covered_branches": 3,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    manifest = lane.normalized_coverage_manifest(
        report,
        aggregate_floor=85,
        file_floor=75,
    )

    assert manifest["branch_coverage"] is True
    assert manifest["aggregate_percent"] == 91.25
    assert manifest["files"] == {"src/example.py": 87.5}
    assert manifest["floors_satisfied"] is True


def test_nightly_comparison_fails_closed_on_nodes_outcomes_or_coverage() -> None:
    cached = {
        "junit": {"nodes": [{"node": "x::test", "outcome": "passed"}]},
        "coverage": {"aggregate_percent": 90.0, "files": {"x.py": 90.0}},
    }
    assert lane.compare_manifests(cached, copy.deepcopy(cached))["equivalent"] is True

    for mutation in (
        {"junit": {"nodes": [{"node": "x::other", "outcome": "passed"}]}},
        {"junit": {"nodes": [{"node": "x::test", "outcome": "failed"}]}},
        {"coverage": {"aggregate_percent": 89.0, "files": {"x.py": 89.0}}},
    ):
        fresh = copy.deepcopy(cached)
        fresh.update(mutation)
        comparison = lane.compare_manifests(cached, fresh)
        assert comparison["equivalent"] is False
        assert comparison["mismatches"]


def test_static_pants_contract_declares_hermetic_allowlist_and_no_remote_cache() -> None:
    root = Path(__file__).resolve().parents[2]
    pants = (root / "pants.toml").read_text(encoding="utf-8")
    source_build = (root / "src/general_ludd/algorithms/BUILD.pants").read_text(encoding="utf-8")
    test_build = (root / "tests/pants_cached_unit/BUILD.pants").read_text(encoding="utf-8")
    control_build = (root / "config/BUILD.pants").read_text(encoding="utf-8")
    manifest = json.loads((root / "config/pants_unit_cache_lane.json").read_text(encoding="utf-8"))
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")

    assert 'pants_version = "2.33.0"' in pants
    assert "process_execution_local_parallelism = 2" in pants
    assert "remote_cache_read = false" in pants
    assert "remote_cache_write = false" in pants
    assert 'pants_ignore = [' in pants
    assert '"/*"' in pants
    assert '"!/src/general_ludd/algorithms/"' in pants
    assert '"!/tests/pants_cached_unit/"' in pants
    assert 'root_patterns = ["/config", "/src/general_ludd/algorithms", "/tests/pants_cached_unit"]' in pants
    assert 'output_dir = "{distdir}/coverage/python"' in pants
    assert 'filter = ["hash_set", "radix_sort"]' in pants
    assert 'unowned_dependency_behavior = "error"' in pants
    assert 'ambiguity_resolution = "none"' in pants
    assert "conftests = false" in pants
    assert "branch = true" in (root / "config/pants_unit_cache_coverage.ini").read_text(encoding="utf-8")
    assert 'sources=["hash_set.py", "radix_sort.py"]' in source_build
    assert 'dependencies=[":fixtures", "//src/general_ludd/algorithms:cached_unit_sources"' in test_build
    assert '"pants_unit_cache_coverage.ini"' not in control_build
    assert '"GLUDD_PANTS_UNIT_POLICY"' in test_build
    assert manifest["dynamic_inputs"] == []
    assert manifest["external_inputs"] == []
    assert "/.pants.d/" in gitignore


def _lane_manifest(
    *,
    control_files: tuple[str, ...] = ("control.txt",),
) -> lane.LaneManifest:
    return lane.LaneManifest(
        schema_version=1,
        pants_version="2.33.0",
        targets=("tests/pants_cached_unit:tests",),
        control_files=control_files,
        relevant_environment=("GLUDD_PANTS_UNIT_POLICY",),
        dynamic_inputs=(),
        external_inputs=(),
        max_local_workers=2,
        hosted_workers=1,
        minimum_aggregate_coverage=85,
        minimum_file_coverage=75,
    )


def _runtime_args(tmp_path: Path, *, mode: str, validate_only: bool) -> SimpleNamespace:
    return SimpleNamespace(
        manifest=tmp_path / "manifest.json",
        pants_bin="pants",
        mode=mode,
        hosted_ci=0,
        validate_only=validate_only,
        cache_root=tmp_path / "cache",
        receipt_root=tmp_path / "receipts",
        policy_environment="v1",
    )


def _candidate() -> dict[str, object]:
    return {
        "candidate_sha": SHA,
        "expected_sha": SHA,
        "repository_state_id": "b" * 64,
        "clean": True,
        "exact_sha": True,
    }


def _result_evidence(*, percentage: float = 90.0) -> dict[str, object]:
    return {
        "junit": {
            "nodes": [{"node": "slice.TestPure::test", "outcome": "passed"}],
            "counts": {"failed": 0, "passed": 1, "skipped": 0},
        },
        "coverage": {
            "branch_coverage": True,
            "aggregate_percent": percentage,
            "files": {"src/example.py": percentage},
            "floors": {"aggregate": 85, "file": 75},
            "floors_satisfied": True,
        },
    }


@pytest.mark.parametrize(
    "inputs",
    (
        {},
        {"x": b"y", **{f"x{index}": b"y" for index in range(32)}},
        {"": b"value"},
        {"source": "not-bytes"},
    ),
)
def test_pants_process_identity_rejects_unbounded_or_malformed_inputs(
    inputs: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="Pants process"):
        lane.pants_process_identity(inputs)


@pytest.mark.parametrize(
    ("execution_request", "local_workers", "hosted_workers", "message"),
    (
        (lane.ExecutionRequest(mode="cached", hosted_ci=False), 3, 1, "worker"),
        (lane.ExecutionRequest(mode="cached", hosted_ci=False), 2, 2, "worker"),
        (
            lane.ExecutionRequest(mode="unsupported", hosted_ci=False),
            2,
            1,
            "mode",
        ),
        (
            lane.ExecutionRequest(
                mode="cached",
                hosted_ci=False,
                graph_status="unknown",
            ),
            2,
            1,
            "graph",
        ),
    ),
)
def test_execution_policy_rejects_unsafe_bounds(
    execution_request: lane.ExecutionRequest,
    local_workers: int,
    hosted_workers: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        lane.decide_execution(
            execution_request,
            max_local_workers=local_workers,
            hosted_workers=hosted_workers,
        )


def test_atomic_json_candidate_and_control_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "nested" / "evidence.json"
    lane._atomic_write_json(destination, {"b": 2, "a": 1})
    assert json.loads(destination.read_text(encoding="ascii")) == {"a": 1, "b": 2}

    outputs = iter(((0, SHA), (0, "")))
    monkeypatch.setattr(lane, "_git_output", lambda *_args: next(outputs))
    monkeypatch.setattr(
        lane,
        "repository_state_id",
        lambda _root, *, source: "c" * 64 if source == "index" else "d" * 64,
    )
    assert lane._candidate_provenance() == {
        "candidate_sha": SHA,
        "expected_sha": SHA,
        "repository_state_id": "c" * 64,
        "clean": True,
        "exact_sha": True,
    }

    monkeypatch.setattr(lane, "ROOT", tmp_path)
    (tmp_path / "control.txt").write_bytes(b"control")
    monkeypatch.setenv("GLUDD_PANTS_UNIT_POLICY", "v2")
    evidence = lane._control_input_evidence(
        _lane_manifest(),
        graph_output="owned graph",
    )
    assert set(evidence) == {
        "pants_transitive_graph",
        "control_plane",
        "interpreter",
        "plugin",
        "platform",
        "environment",
        "coverage",
    }
    assert b"v2" in evidence["environment"]

    (tmp_path / "control.txt").unlink()
    with pytest.raises(ValueError, match="missing or unsafe"):
        lane._control_input_evidence(_lane_manifest(), graph_output="graph")


def test_git_output_and_owned_command_helpers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    git_calls: list[list[str]] = []

    def fake_git(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        git_calls.append(command)
        return subprocess.CompletedProcess(command, 0, " value \n", "")

    monkeypatch.setattr(lane.subprocess, "run", fake_git)
    assert lane._git_output("rev-parse", "HEAD") == (0, "value")
    assert git_calls == [["git", "rev-parse", "HEAD"]]

    times = iter((10.0, 10.25))
    monkeypatch.setattr(lane.time, "monotonic", lambda: next(times))
    seen_environment: list[str] = []

    def fake_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == lane.ROOT
        assert check is False
        seen_environment.append(env["GLUDD_PANTS_UNIT_POLICY"])
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(lane.subprocess, "run", fake_command)
    result = lane._run_command(["pants", "test"], policy_environment="v3")
    assert result == lane.CommandResult(returncode=7, elapsed_seconds=0.25)
    assert seen_environment == ["v3"]

    decision = lane.ExecutionDecision(False, True, 2, ())
    with pytest.raises(ValueError, match="bound"):
        lane.build_pants_command(
            pants_binary="pants",
            decision=lane.ExecutionDecision(False, True, 3, ()),
            targets=("target",),
            cache_root=tmp_path,
            workdir=tmp_path,
            distdir=tmp_path,
        )
    assert "dependencies" in lane._graph_probe_command(
        pants_binary="pants",
        workers=decision.workers,
        cache_root=tmp_path / "cache",
        workdir=tmp_path / "work",
        targets=("target",),
    )


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected"),
    (
        (0, "target\n", "", "owned"),
        (1, "", "ambiguous dependency", "ambiguous"),
        (1, "", "unowned import", "unowned"),
        (1, "", "engine exploded", "error"),
    ),
)
def test_graph_probe_classifies_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    stdout: str,
    stderr: str,
    expected: str,
) -> None:
    monkeypatch.setattr(
        lane.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            returncode,
            stdout,
            stderr,
        ),
    )
    status, graph = lane._probe_graph(
        pants_binary="pants",
        workers=2,
        cache_root=tmp_path / "cache",
        workdir=tmp_path / "work",
        targets=("target",),
    )
    assert status == expected
    assert graph == stdout


def test_report_helpers_find_exact_files_and_enforce_floors(tmp_path: Path) -> None:
    report_root = tmp_path / "dist"
    report_root.mkdir()
    junit = report_root / "TEST-slice.xml"
    junit.write_text(
        '<testsuite><testcase classname="slice" name="test" /></testsuite>',
        encoding="utf-8",
    )
    coverage = report_root / "coverage.json"
    coverage.write_text(
        json.dumps(
            {
                "meta": {"branch_coverage": True},
                "totals": {"percent_covered": 90.0},
                "files": {"src/example.py": {"summary": {"percent_covered": 80.0}}},
            }
        ),
        encoding="utf-8",
    )
    result = lane._result_manifest(report_root, manifest=_lane_manifest())
    assert result["coverage"]["floors_satisfied"] is True
    assert lane._find_one(report_root, "coverage.json") == coverage

    coverage.write_text(
        json.dumps(
            {
                "meta": {"branch_coverage": True},
                "totals": {"percent_covered": 90.0},
                "files": {"src/example.py": {"summary": {"percent_covered": 70.0}}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="below_files"):
        lane._result_manifest(report_root, manifest=_lane_manifest())

    coverage.unlink()
    with pytest.raises(ValueError, match="exactly one"):
        lane._find_one(report_root, "coverage.json")
    (report_root / "one").mkdir()
    (report_root / "two").mkdir()
    (report_root / "one" / "coverage.json").write_text("{}", encoding="utf-8")
    (report_root / "two" / "coverage.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        lane._find_one(report_root, "coverage.json")


def test_terminal_receipt_writer_persists_authenticated_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = ReceiptSigner(key=b"z" * 32, issued_at=100, validity_seconds=300)
    monkeypatch.setattr(
        lane,
        "load_gate_receipt_auth_context",
        lambda *_args, **_kwargs: SimpleNamespace(signer=signer),
    )
    payload = lane.build_receipt_payload(
        candidate=_candidate(),
        execution=lane.pants_process_identity({"source": b"one"}),
        result={"returncode": 0, "manifest_sha256": "c" * 64},
        mode="cached",
        comparison=None,
    )
    destination = lane._write_terminal_receipt(tmp_path, payload=payload)
    assert destination == tmp_path / SHA
    assert json.loads((destination / "receipt.json").read_text(encoding="ascii")) == payload
    envelope = json.loads((destination / "attestation.json").read_text(encoding="ascii"))
    assert envelope["receipt_kind"] == "pass"


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    result_manifest: object | None = None,
    command_returncode: int = 0,
) -> list[dict[str, object]]:
    monkeypatch.setattr(lane, "load_manifest", lambda _path: _lane_manifest())
    monkeypatch.setattr(lane, "_validate_control_files", lambda _manifest: None)
    monkeypatch.setattr(lane, "project_resource_root", lambda _root: tmp_path / "resources")
    monkeypatch.setattr(lane, "_probe_graph", lambda **_kwargs: ("owned", "graph"))
    monkeypatch.setattr(lane, "_candidate_provenance", _candidate)
    monkeypatch.setattr(
        lane,
        "_control_input_evidence",
        lambda *_args, **_kwargs: {"source": b"one"},
    )
    monkeypatch.setattr(
        lane,
        "_run_command",
        lambda *_args, **_kwargs: lane.CommandResult(command_returncode, 0.25),
    )
    resolved_manifest = _result_evidence() if result_manifest is None else result_manifest
    if isinstance(resolved_manifest, BaseException):
        monkeypatch.setattr(
            lane,
            "_result_manifest",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(resolved_manifest),
        )
    else:
        monkeypatch.setattr(
            lane,
            "_result_manifest",
            lambda *_args, **_kwargs: copy.deepcopy(resolved_manifest),
        )
    receipts: list[dict[str, object]] = []
    monkeypatch.setattr(
        lane,
        "_write_terminal_receipt",
        lambda _root, *, payload: receipts.append(dict(payload)) or tmp_path,
    )
    return receipts


def test_run_lane_validate_cached_nightly_and_failure_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    receipts = _patch_runtime(monkeypatch, tmp_path)
    assert lane._run_lane(
        _runtime_args(tmp_path, mode="cached", validate_only=True)
    ) == 0
    assert "PANTS-CACHE-VALIDATE" in capsys.readouterr().out

    assert lane._run_lane(
        _runtime_args(tmp_path, mode="cached", validate_only=False)
    ) == 0
    assert receipts[-1]["mode"] == "cached"

    assert lane._run_lane(
        _runtime_args(tmp_path, mode="nightly", validate_only=False)
    ) == 0
    comparison = receipts[-1]["comparison"]
    assert isinstance(comparison, dict) and comparison["equivalent"] is True

    _patch_runtime(monkeypatch, tmp_path, command_returncode=5)
    assert lane._run_lane(
        _runtime_args(tmp_path, mode="cached", validate_only=False)
    ) == 5

    _patch_runtime(
        monkeypatch,
        tmp_path,
        result_manifest=ValueError("bad report"),
    )
    assert lane._run_lane(
        _runtime_args(tmp_path, mode="cached", validate_only=False)
    ) == 2

    monkeypatch.setattr(
        lane,
        "_probe_graph",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("probe failed")),
    )
    assert lane._run_lane(
        _runtime_args(tmp_path, mode="cached", validate_only=False)
    ) == 2


def test_run_lane_nightly_detects_manifest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch, tmp_path)

    def changing_manifest(distdir: Path, **_kwargs: object) -> dict[str, object]:
        percentage = 91.0 if "fresh" in distdir.parts else 90.0
        return _result_evidence(percentage=percentage)

    monkeypatch.setattr(lane, "_result_manifest", changing_manifest)
    assert lane._run_lane(
        _runtime_args(tmp_path, mode="nightly", validate_only=False)
    ) == 1


def test_parser_main_and_receipt_validation_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = lane._parser().parse_args(
        [
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--pants-bin",
            "pants",
            "--mode",
            "fresh",
            "--hosted-ci",
            "1",
            "--validate-only",
            "0",
        ]
    )
    assert parsed.mode == "fresh" and parsed.hosted_ci == 1

    monkeypatch.setattr(lane.sys, "argv", ["lane", "--validate-only", "1"])
    monkeypatch.setattr(lane, "_run_lane", lambda args: 9 if args.validate_only else 8)
    assert lane.main() == 9

    execution = lane.pants_process_identity({"source": b"one"})
    with pytest.raises(ValueError, match="candidate SHA"):
        lane.build_receipt_payload(
            candidate={
                "candidate_sha": "bad",
                "repository_state_id": "b" * 64,
            },
            execution=execution,
            result={"returncode": 0},
            mode="cached",
            comparison=None,
        )
    with pytest.raises(ValueError, match="state identity"):
        lane.build_receipt_payload(
            candidate={"candidate_sha": SHA, "repository_state_id": "bad"},
            execution=execution,
            result={"returncode": 0},
            mode="cached",
            comparison=None,
        )
    with pytest.raises(ValueError, match="contaminate"):
        lane.build_receipt_payload(
            candidate={"candidate_sha": SHA, "repository_state_id": "b" * 64},
            execution={"sha": SHA},
            result={"returncode": 0},
            mode="cached",
            comparison=None,
        )
    signer = ReceiptSigner(key=b"k" * 32, issued_at=100, validity_seconds=300)
    with pytest.raises(ValueError, match="incomplete"):
        lane.authenticate_payload({"execution_identity": {}}, signer=signer)

