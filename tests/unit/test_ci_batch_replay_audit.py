"""Fail-closed contracts for phase-two shadow replay eligibility."""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from coverage import CoverageData
from scripts import ci_batch_replay_audit as replay_module
from scripts import run_ci_shards_serial as serial_runner
from scripts.ci_batch_receipts import (
    BatchReceiptRequest,
    ReceiptPublication,
    ShadowBatchReceiptWriter,
    canonical_json_bytes,
    canonical_json_sha256,
    file_sha256,
)
from scripts.ci_batch_replay_audit import (
    ReplayAdmissionRequest,
    ReplayAdmissionResult,
    ReplayAuditRequest,
    ReplayAuditResult,
    ShadowReplayAuditor,
)
from scripts.ci_receipt_auth import ReceiptSigner, ReceiptTrustPolicy

_SHA = "a" * 40
_AUTH_KEY = bytes(range(32))
_AUTH_SIGNER = ReceiptSigner(_AUTH_KEY, 1_000, 300)
_AUTH_POLICY = ReceiptTrustPolicy(
    trusted_keys={_AUTH_SIGNER.signer_id: _AUTH_KEY},
    verification_epoch=1_100,
)


def _identity() -> dict[str, object]:
    files = ["tests/unit/test_example.py"]
    return {
        "schema_version": 1,
        "source": {
            "candidate_sha": _SHA,
            "expected_sha": _SHA,
            "branch": "feature",
            "clean": True,
            "exact_sha": True,
            "queries_ok": True,
            "repository_state_id": "state-1",
            "test_files": files,
            "test_files_sha256": canonical_json_sha256(files),
        },
        "plan": {
            "shard": "unit-1a1",
            "batch_index": 1,
            "max_files_per_batch": 16,
            "shard_plan_sha256": "1" * 64,
            "complete_plan_sha256": "2" * 64,
            "collection_manifest_kind": "canonical-test-file-plan-v1",
            "collection_manifest_sha256": "3" * 64,
            "execution_policy_sha256": "4" * 64,
        },
        "runner": {
            "receipt_schema": 1,
            "implementation_sha256": "5" * 64,
            "attestation_sha256": "6" * 64,
            "pytest_args": ["-W", "error"],
            "heartbeat_seconds": 30.0,
            "no_progress_seconds": 600.0,
            "worker_count": 1,
            "distribution": "none",
            "cleanup_policy_version": 1,
        },
        "toolchain": {
            "implementation": "cpython",
            "version": "3.11.14",
            "executable": "/private/tmp/python",
            "executable_sha256": "7" * 64,
            "uv_version": "uv 0.8.0",
            "lockfile_sha256": "8" * 64,
            "dependency_profile_sha256": "9" * 64,
            "installed_distributions_sha256": "b" * 64,
        },
        "plugins": {
            "inventory_kind": "installed-pytest11-v1",
            "inventory_sha256": "c" * 64,
            "pytest": "9.1.1",
            "pytest_cov": "7.1.0",
            "coverage": "7.16.2",
            "pytest_timeout": "2.4.0",
            "pytest_asyncio": "1.4.0",
            "pytest_xdist": "3.8.0",
        },
        "coverage": {
            "config_sha256": "d" * 64,
            "branch": True,
            "source": ["src/general_ludd"],
            "omit": [],
            "paths": {},
            "concurrency": ["greenlet", "thread"],
            "data_schema": "coverage.py-7",
        },
        "platform": {
            "system": "Darwin",
            "release": "25.0",
            "machine": "arm64",
            "python_abi": "cpython-311",
            "resource_namespace_schema": 1,
        },
        "environment": {
            "allowlist": {
                "CI": "true",
                "GITHUB_ACTIONS": None,
                "PYTHONHASHSEED": None,
                "TZ": "UTC",
            },
            "undeclared_names_sha256": "e" * 64,
            "restricted_inputs_present": False,
            "values_outside_allowlist_stored": False,
        },
        "external_inputs": [],
    }


def _outcomes(*, node: str = "node-1") -> dict[str, object]:
    terminal = [{"node_sha256": canonical_json_sha256(node), "outcome": "passed"}]
    return {
        "schema_version": 1,
        "counts": {
            "errors": 0,
            "failures": 0,
            "passed": 1,
            "skipped": 0,
            "tests": 1,
        },
        "node_id_sha256": canonical_json_sha256([terminal[0]["node_sha256"]]),
        "terminal_outcome_sha256": canonical_json_sha256(terminal),
    }


def _write_coverage(path: Path, *, branch_target: int = 2) -> None:
    data = CoverageData(basename=str(path))
    data.add_arcs(
        {
            "src/general_ludd/example.py": {
                (1, branch_target),
                (branch_target, -1),
            }
        }
    )
    data.write()
    data.close()


def _publish(
    cache: Path,
    tmp_path: Path,
    identity: dict[str, object],
    *,
    suffix: str = "prior",
) -> Path:
    coverage = tmp_path / f"{suffix}.coverage"
    _write_coverage(coverage)
    result = ShadowBatchReceiptWriter(cache, signer=_AUTH_SIGNER).publish(
        BatchReceiptRequest(
            action_identity=identity,
            observed_action_identity=identity,
            coverage_path=coverage,
            outcome_manifest=_outcomes(),
            originating_run_id=f"{suffix}-run",
            started_at="2026-10-07T00:00:00Z",
            completed_at="2026-10-07T00:01:00Z",
            returncode=0,
            cleanup_returncode=0,
        )
    )
    assert result.published is True
    assert result.path is not None
    return result.path


def _auditor(cache: Path, **kwargs: int) -> ShadowReplayAuditor:
    return ShadowReplayAuditor(
        cache,
        candidate_sha=_SHA,
        trust_policy=_AUTH_POLICY,
        **kwargs,
    )


def _request(
    tmp_path: Path,
    identity: dict[str, object],
    *,
    observed: dict[str, object] | None = None,
    outcomes: dict[str, object] | None = None,
    branch_target: int = 2,
) -> ReplayAuditRequest:
    coverage = tmp_path / f"current-{branch_target}.coverage"
    _write_coverage(coverage, branch_target=branch_target)
    return ReplayAuditRequest(
        action_identity=identity,
        observed_action_identity=observed or identity,
        coverage_path=coverage,
        outcome_manifest=outcomes or _outcomes(),
        returncode=0,
        cleanup_returncode=0,
    )


def test_shadow_auditor_reports_exact_safe_candidate_without_authorizing_skip(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    identity = _identity()
    receipt = _publish(cache, tmp_path, identity)
    auditor = _auditor(cache)

    result = auditor.audit(_request(tmp_path, identity))

    assert result.eligible is True
    assert result.reason == "exact-safe-candidate"
    assert result.receipt_path == receipt
    assert result.action_digest == canonical_json_sha256(identity)
    assert result.skip_authorized is False


def test_exact_authenticated_receipt_admission_restores_coverage(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    identity = _identity()
    receipt = _publish(cache, tmp_path, identity)
    destination = tmp_path / "coverage-fragments" / ".coverage.unit-1a1.batch-001"
    destination.parent.mkdir()

    result = _auditor(cache).admit(
        ReplayAdmissionRequest(
            action_identity=identity,
            observed_action_identity=identity,
            coverage_destination=destination,
        )
    )

    assert result.admitted is True
    assert result.reason == "exact-safe-receipt"
    assert result.authentication == "verified"
    assert result.duration_seconds == 60.0
    assert result.receipt_path == receipt
    assert destination.read_bytes() == (receipt / "coverage.data").read_bytes()


def test_receipt_admission_executes_cold_on_exact_identity_drift(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    prior = _identity()
    _publish(cache, tmp_path, prior)
    current = copy.deepcopy(prior)
    toolchain = current["toolchain"]
    assert isinstance(toolchain, dict)
    toolchain["uv_version"] = "uv 0.9.0"
    destination = tmp_path / "coverage-fragments" / ".coverage.unit-1a1.batch-001"
    destination.parent.mkdir()

    result = _auditor(cache).admit(
        ReplayAdmissionRequest(
            action_identity=current,
            observed_action_identity=current,
            coverage_destination=destination,
        )
    )

    assert result.admitted is False
    assert result.reason == "dependency-drift"
    assert not destination.exists()


def test_receipt_admission_revalidates_snapshotted_content_before_copy(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    identity = _identity()
    receipt = _publish(cache, tmp_path, identity)
    auditor = _auditor(cache)
    (receipt / "coverage.data").write_bytes(b"tampered")
    destination = tmp_path / "coverage-fragments" / ".coverage.unit-1a1.batch-001"
    destination.parent.mkdir()

    result = auditor.admit(
        ReplayAdmissionRequest(
            action_identity=identity,
            observed_action_identity=identity,
            coverage_destination=destination,
        )
    )

    assert result.admitted is False
    assert result.reason == "candidate-corrupt"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("family", "mutation", "reason"),
    [
        ("source", {"repository_state_id": "state-2"}, "candidate-drift"),
        ("toolchain", {"uv_version": "uv 0.9.0"}, "dependency-drift"),
        ("runner", {"pytest_args": ["-q"]}, "command-drift"),
        (
            "environment",
            {
                "allowlist": {
                    "CI": "false",
                    "GITHUB_ACTIONS": None,
                    "PYTHONHASHSEED": None,
                    "TZ": "UTC",
                }
            },
            "environment-drift",
        ),
    ],
)
def test_shadow_auditor_classifies_exact_identity_family_drift(
    tmp_path: Path,
    family: str,
    mutation: dict[str, object],
    reason: str,
) -> None:
    cache = tmp_path / "batch-receipts"
    prior = _identity()
    _publish(cache, tmp_path, prior)
    current = copy.deepcopy(prior)
    current_family = current[family]
    assert isinstance(current_family, dict)
    current_family.update(mutation)
    auditor = _auditor(cache)

    result = auditor.audit(_request(tmp_path, current))

    assert result.eligible is False
    assert result.reason == reason
    assert result.skip_authorized is False


def test_shadow_auditor_refuses_dirty_observed_inputs(tmp_path: Path) -> None:
    cache = tmp_path / "batch-receipts"
    identity = _identity()
    _publish(cache, tmp_path, identity)
    observed = copy.deepcopy(identity)
    source = observed["source"]
    assert isinstance(source, dict)
    source["clean"] = False
    auditor = _auditor(cache)

    result = auditor.audit(_request(tmp_path, identity, observed=observed))

    assert result.eligible is False
    assert result.reason == "dirty-inputs"


def test_shadow_auditor_refuses_node_id_and_coverage_drift(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    identity = _identity()
    _publish(cache, tmp_path, identity)
    auditor = _auditor(cache)

    node_result = auditor.audit(
        _request(tmp_path, identity, outcomes=_outcomes(node="node-2"))
    )
    coverage_result = auditor.audit(
        _request(tmp_path, identity, branch_target=3)
    )

    assert node_result.eligible is False
    assert node_result.reason == "node-id-drift"
    assert coverage_result.eligible is False
    assert coverage_result.reason == "coverage-drift"


def test_shadow_auditor_refuses_candidate_corruption_after_index(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    identity = _identity()
    receipt = _publish(cache, tmp_path, identity)
    auditor = _auditor(cache)
    (receipt / "manifest.json").write_text("{}\n", encoding="utf-8")

    result = auditor.audit(_request(tmp_path, identity))

    assert result.eligible is False
    assert result.reason == "candidate-corrupt"


def test_shadow_auditor_refuses_symlinked_generation_tree(tmp_path: Path) -> None:
    cache = tmp_path / "batch-receipts"
    version = cache / "v1"
    version.mkdir(parents=True, mode=0o700)
    os.chmod(cache, 0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (version / _SHA).symlink_to(outside, target_is_directory=True)

    auditor = _auditor(cache)
    result = auditor.audit(_request(tmp_path, _identity()))

    assert result.eligible is False
    assert result.reason == "cache-tree-symlink"


def test_shadow_auditor_refuses_ambiguous_prior_batch_candidates(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "batch-receipts"
    first = _identity()
    second = copy.deepcopy(first)
    second_runner = second["runner"]
    assert isinstance(second_runner, dict)
    second_runner["pytest_args"] = ["-q"]
    _publish(cache, tmp_path, first, suffix="first")
    _publish(cache, tmp_path, second, suffix="second")
    current = copy.deepcopy(first)
    current_runner = current["runner"]
    assert isinstance(current_runner, dict)
    current_runner["pytest_args"] = ["-x"]

    result = _auditor(cache).audit(
        _request(tmp_path, current)
    )

    assert result.eligible is False
    assert result.reason == "candidate-ambiguous"


def test_shadow_auditor_bounds_prior_candidate_index(tmp_path: Path) -> None:
    cache = tmp_path / "batch-receipts"
    first = _identity()
    second = copy.deepcopy(first)
    second_runner = second["runner"]
    assert isinstance(second_runner, dict)
    second_runner["pytest_args"] = ["-q"]
    _publish(cache, tmp_path, first, suffix="first")
    _publish(cache, tmp_path, second, suffix="second")

    result = _auditor(cache, max_candidates=1).audit(_request(tmp_path, first))

    assert result.eligible is False
    assert result.reason == "candidate-set-too-large"


def test_shadow_auditor_rejects_duplicate_manifest_keys(tmp_path: Path) -> None:
    cache = tmp_path / "batch-receipts"
    identity = _identity()
    receipt = _publish(cache, tmp_path, identity)
    manifest = receipt / "manifest.json"
    original = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        '{"schema_version":1,' + original.removeprefix("{"),
        encoding="utf-8",
    )
    complete = receipt / "complete.json"
    completion = json.loads(complete.read_text(encoding="utf-8"))
    completion["manifest_sha256"] = file_sha256(manifest)
    complete.write_bytes(canonical_json_bytes(completion) + b"\n")
    os.chmod(complete, 0o600)

    result = _auditor(cache).audit(
        _request(tmp_path, identity)
    )

    assert result.eligible is False
    assert result.reason == "cache-corrupt"


@pytest.mark.parametrize(
    ("candidate_sha", "max_candidates", "max_index_bytes"),
    [
        ("short", 1, 1),
        (_SHA, 0, 1),
        (_SHA, replay_module.MAX_AUDIT_CANDIDATES + 1, 1),
        (_SHA, 1, 0),
        (_SHA, 1, replay_module.MAX_AUDIT_INDEX_BYTES + 1),
    ],
)
def test_shadow_auditor_rejects_bounds_wider_than_the_shadow_contract(
    tmp_path: Path,
    candidate_sha: str,
    max_candidates: int,
    max_index_bytes: int,
) -> None:
    with pytest.raises(ValueError):
        ShadowReplayAuditor(
            tmp_path / "cache",
            candidate_sha=candidate_sha,
            max_candidates=max_candidates,
            max_index_bytes=max_index_bytes,
        )


@pytest.mark.parametrize("layout", ["absent", "missing-version", "missing-generation"])
def test_shadow_auditor_reports_a_bounded_cold_miss(
    tmp_path: Path,
    layout: str,
) -> None:
    cache = tmp_path / "batch-receipts"
    if layout != "absent":
        cache.mkdir(mode=0o700)
    if layout == "missing-generation":
        (cache / "v1").mkdir(mode=0o700)

    result = _auditor(cache).audit(
        _request(tmp_path, _identity())
    )

    assert result.eligible is False
    assert result.reason == "no-prior-candidate"
    assert result.skip_authorized is False


@pytest.mark.parametrize(
    ("layout", "reason"),
    [
        ("unsafe-root", "cache-tree-unsafe-mode"),
        ("linked-version", "cache-tree-symlink"),
        ("too-many-generations", "cache-tree-generation-limit"),
        ("invalid-generation", "cache-tree-invalid-generation"),
    ],
)
def test_shadow_auditor_refuses_unsafe_or_unbounded_cache_trees(
    tmp_path: Path,
    layout: str,
    reason: str,
) -> None:
    cache = tmp_path / "batch-receipts"
    if layout == "unsafe-root":
        cache.mkdir(mode=0o755)
        os.chmod(cache, 0o755)
    else:
        cache.mkdir(mode=0o700)
        version = cache / "v1"
        if layout == "linked-version":
            outside = tmp_path / "outside-version"
            outside.mkdir(mode=0o700)
            version.symlink_to(outside, target_is_directory=True)
        else:
            version.mkdir(mode=0o700)
            names = (
                ("b" * 40, "c" * 40, "d" * 40)
                if layout == "too-many-generations"
                else ("not-a-git-sha",)
            )
            for name in names:
                (version / name).mkdir(mode=0o700)

    result = _auditor(cache).audit(
        _request(tmp_path, _identity())
    )

    assert result.eligible is False
    assert result.reason == reason


@pytest.mark.parametrize(
    ("corruption", "reason"),
    [
        ("invalid-name", "cache-corrupt"),
        ("missing-manifest", "cache-corrupt"),
        ("oversized-manifest", "candidate-index-too-large"),
        ("index-byte-limit", "candidate-index-too-large"),
    ],
)
def test_shadow_auditor_refuses_malformed_or_oversized_candidate_indexes(
    tmp_path: Path,
    corruption: str,
    reason: str,
) -> None:
    cache = tmp_path / "batch-receipts"
    receipt = _publish(cache, tmp_path, _identity())
    max_index_bytes = replay_module.MAX_AUDIT_INDEX_BYTES
    if corruption == "invalid-name":
        invalid = receipt.parent / "not-an-action-digest"
        invalid.mkdir(mode=0o700)
    elif corruption == "missing-manifest":
        (receipt / "manifest.json").unlink()
    elif corruption == "oversized-manifest":
        manifest = receipt / "manifest.json"
        manifest.write_bytes(b"{" + b" " * replay_module.MAX_AUDIT_MANIFEST_BYTES)
        os.chmod(manifest, 0o600)
    else:
        max_index_bytes = 1

    result = _auditor(cache, max_index_bytes=max_index_bytes).audit(
        _request(tmp_path, _identity())
    )

    assert result.eligible is False
    assert result.reason == reason


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("failed", "current-not-passing"),
        ("cleanup", "cleanup-incomplete"),
        ("restricted-environment", "dirty-inputs"),
        ("malformed-plan", "current-identity-invalid"),
        ("candidate-sha", "candidate-drift"),
        ("ambiguous-observation", "identity-drift-ambiguous"),
        ("outcomes", "outcomes-invalid"),
        ("coverage", "coverage-invalid"),
    ],
)
def test_shadow_auditor_refuses_ineligible_current_evidence(
    tmp_path: Path,
    mutation: str,
    reason: str,
) -> None:
    identity = _identity()
    observed = copy.deepcopy(identity)
    request = _request(tmp_path, identity, observed=observed)
    if mutation == "failed":
        request = ReplayAuditRequest(**{**request.__dict__, "returncode": 1})
    elif mutation == "cleanup":
        request = ReplayAuditRequest(**{**request.__dict__, "cleanup_returncode": 74})
    elif mutation == "restricted-environment":
        environment = observed["environment"]
        assert isinstance(environment, dict)
        environment["restricted_inputs_present"] = True
    elif mutation == "malformed-plan":
        identity["plan"] = "invalid"
        observed["plan"] = "invalid"
    elif mutation == "candidate-sha":
        source = identity["source"]
        observed_source = observed["source"]
        assert isinstance(source, dict)
        assert isinstance(observed_source, dict)
        source["candidate_sha"] = source["expected_sha"] = "b" * 40
        observed_source["candidate_sha"] = observed_source["expected_sha"] = "b" * 40
    elif mutation == "ambiguous-observation":
        observed["runner"] = {"changed": True}
        observed["toolchain"] = {"changed": True}
    elif mutation == "outcomes":
        request = ReplayAuditRequest(**{**request.__dict__, "outcome_manifest": None})
    else:
        request = ReplayAuditRequest(
            **{**request.__dict__, "coverage_path": tmp_path / "missing.coverage"}
        )

    result = ShadowReplayAuditor(
        tmp_path / "batch-receipts", candidate_sha=_SHA
    ).audit(request)

    assert result.eligible is False
    assert result.reason == reason
    assert result.skip_authorized is False


def test_shadow_auditor_refuses_terminal_outcome_drift(tmp_path: Path) -> None:
    cache = tmp_path / "batch-receipts"
    identity = _identity()
    _publish(cache, tmp_path, identity)
    outcomes = _outcomes()
    outcomes["terminal_outcome_sha256"] = "f" * 64

    result = _auditor(cache).audit(
        _request(tmp_path, identity, outcomes=outcomes)
    )

    assert result.eligible is False
    assert result.reason == "outcome-drift"


def test_serial_runner_audits_after_execution_coverage_and_owned_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    coverage_shards = tmp_path / "coverage-fragments"
    resource_root = tmp_path / "resources"
    serial_runner.COVERAGE_SHARDS = coverage_shards
    serial_runner.COVERAGE_JSON = tmp_path / "coverage.json"
    serial_runner.COVERAGE_AUDIT = tmp_path / "coverage-audit.json"
    monkeypatch.setattr(
        serial_runner,
        "_resource_paths",
        lambda: serial_runner.ResourcePaths(
            root=resource_root,
            coverage_shards=coverage_shards,
            coverage_json=tmp_path / "coverage.json",
            coverage_audit=tmp_path / "coverage-audit.json",
            attestation=tmp_path / "attestation.json",
            resume=tmp_path / "resume.json",
        ),
    )
    monkeypatch.setattr(
        serial_runner,
        "expand_shard",
        lambda _shard: ["tests/unit/test_example.py"],
    )
    monkeypatch.setattr(serial_runner, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(serial_runner, "_git_output", lambda *_args: (0, _SHA))
    monkeypatch.setattr(serial_runner, "_interpreter_identity", lambda: {"python": "same"})
    monkeypatch.setattr(
        serial_runner,
        "_interpreter_is_unchanged",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        serial_runner,
        "_disk_headroom_available",
        lambda *_args, **_kwargs: True,
    )
    owned_roots: list[Path] = []
    events: list[str] = []

    def fake_owned_tmpdir(_label: str) -> Path:
        owned = tmp_path / f"owned-{len(owned_roots) + 1}"
        owned.mkdir()
        owned_roots.append(owned)
        return owned

    def fake_run_owned(command: list[str], **_kwargs: object) -> int:
        events.append("executed")
        junit = next(
            Path(argument.removeprefix("--junitxml="))
            for argument in command
            if argument.startswith("--junitxml=")
        )
        junit.parent.mkdir(parents=True, exist_ok=True)
        junit.write_text(
            '<testsuite><testcase classname="case" name="pass" /></testsuite>',
            encoding="utf-8",
        )
        return 0

    def fake_save_coverage(
        shard: str,
        batch_index: int,
        _basetemp: Path,
        _env: dict[str, str],
    ) -> bool:
        events.append("coverage")
        coverage_shards.mkdir(parents=True, exist_ok=True)
        _write_coverage(
            coverage_shards / f".coverage.{shard}.batch-{batch_index:03d}"
        )
        return True

    def cleanup_tmpdir(path: Path, **_kwargs: object) -> int:
        shutil.rmtree(path)
        events.append("tmp-cleanup")
        return 0

    monkeypatch.setattr(serial_runner, "_owned_socket_safe_tmpdir", fake_owned_tmpdir)
    monkeypatch.setattr(serial_runner, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(serial_runner, "_save_shard_coverage", fake_save_coverage)
    monkeypatch.setattr(
        serial_runner,
        "_cleanup_owned_tmpdir_safely",
        cleanup_tmpdir,
    )

    class RecordingAuditor:
        def audit(self, request: ReplayAuditRequest) -> ReplayAuditResult:
            assert all(not path.exists() for path in owned_roots)
            assert not any((resource_root / "workspaces").glob("*/batch-001"))
            assert request.returncode == 0
            assert request.cleanup_returncode == 0
            events.append("audit")
            return ReplayAuditResult(
                True,
                "exact-safe-candidate",
                action_digest="d" * 64,
                receipt_path=tmp_path / "prior-receipt",
                authentication="verified",
            )

    class RecordingWriter:
        def publish(self, request: BatchReceiptRequest) -> ReceiptPublication:
            assert events[-1] == "audit"
            events.append("publish")
            return ReceiptPublication(
                True,
                "published",
                path=tmp_path / "completed-receipt",
                action_digest="d" * 64,
            )

    class RecordingProgress:
        def record(self, execution: object, **kwargs: object) -> dict[str, object]:
            assert events[-1] == "publish"
            assert kwargs["completed_receipt"] == tmp_path / "completed-receipt"
            events.append("progress")
            return {
                "gate_result": "unknown",
                "overall_green": None,
                "skips": 0,
            }

        def render(self, summary: object) -> str:
            assert isinstance(summary, dict)
            return json.dumps(summary, sort_keys=True, separators=(",", ":"))

    session = serial_runner.BatchReceiptSession(
        writer=RecordingWriter(),  # type: ignore[arg-type]
        run_id="run-1",
        expected_identity=lambda *_args: _identity(),
        observed_identity=lambda *_args: _identity(),
        auditor=RecordingAuditor(),  # type: ignore[arg-type]
        progress=RecordingProgress(),  # type: ignore[arg-type]
    )

    result = serial_runner.run(
        ["unit-1a1"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
        receipt_session=session,
    )

    assert result == 0
    assert events[:6] == [
        "executed",
        "coverage",
        "tmp-cleanup",
        "audit",
        "publish",
        "progress",
    ]
    output = capsys.readouterr().out
    assert "BATCH-REPLAY-SHADOW status=candidate" in output
    assert "reason=exact-safe-candidate" in output
    assert "authentication=verified" in output
    assert "skips=0" in output
    assert "GATE-PROGRESS-SHADOW" in output


def test_serial_runner_admits_exact_receipt_without_starting_pytest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    coverage_shards = tmp_path / "coverage-fragments"
    resource_root = tmp_path / "resources"
    serial_runner.COVERAGE_SHARDS = coverage_shards
    serial_runner.COVERAGE_JSON = tmp_path / "coverage.json"
    serial_runner.COVERAGE_AUDIT = tmp_path / "coverage-audit.json"
    monkeypatch.setattr(
        serial_runner,
        "_resource_paths",
        lambda: serial_runner.ResourcePaths(
            root=resource_root,
            coverage_shards=coverage_shards,
            coverage_json=tmp_path / "coverage.json",
            coverage_audit=tmp_path / "coverage-audit.json",
            attestation=tmp_path / "attestation.json",
            resume=tmp_path / "resume.json",
        ),
    )
    monkeypatch.setattr(
        serial_runner,
        "expand_shard",
        lambda _shard: ["tests/unit/test_example.py"],
    )
    monkeypatch.setattr(serial_runner, "_git_output", lambda *_args: (0, _SHA))
    monkeypatch.setattr(serial_runner, "_interpreter_identity", lambda: {"python": "same"})
    monkeypatch.setattr(
        serial_runner,
        "_disk_headroom_available",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        serial_runner,
        "_run_owned_pytest",
        lambda *_args, **_kwargs: pytest.fail("admitted receipt must not start pytest"),
    )

    class AdmissionAuditor:
        def admit(self, request: ReplayAdmissionRequest) -> ReplayAdmissionResult:
            _write_coverage(request.coverage_destination)
            return ReplayAdmissionResult(
                True,
                "exact-safe-receipt",
                action_digest="d" * 64,
                receipt_path=tmp_path / "prior-receipt",
                authentication="verified",
                duration_seconds=60.0,
            )

    class NeverWriter:
        def publish(self, _request: BatchReceiptRequest) -> ReceiptPublication:
            pytest.fail("admitted receipt must not be republished")

    session = serial_runner.BatchReceiptSession(
        writer=NeverWriter(),  # type: ignore[arg-type]
        run_id="run-1",
        expected_identity=lambda *_args: _identity(),
        observed_identity=lambda *_args: _identity(),
        auditor=AdmissionAuditor(),  # type: ignore[arg-type]
        admission_enabled=True,
    )

    result = serial_runner.run(
        ["unit-1a1"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
        receipt_session=session,
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "BATCH-RECEIPT-ADMISSION status=admitted" in output
    assert "GATE-BATCH-EXECUTION planned=1 executed=0 resumed=1" in output
    assert "time_saved_seconds=60.000" in output


def test_cli_can_disable_replay_audit_without_disabling_shadow_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = {
        "head_sha": _SHA,
        "expected_sha": _SHA,
        "branch": "feature",
        "clean": True,
        "exact_sha": True,
        "queries_ok": True,
    }
    received: list[bool] = []
    session = object()

    def create_session(**kwargs: object) -> object:
        enabled = kwargs["replay_audit_enabled"]
        assert isinstance(enabled, bool)
        received.append(enabled)
        return session

    monkeypatch.setattr(
        serial_runner,
        "_repository_identity",
        lambda **_kwargs: identity,
    )
    monkeypatch.setattr(serial_runner, "_attestation_pairing", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        serial_runner,
        "_create_shadow_receipt_session",
        create_session,
    )
    monkeypatch.setattr(serial_runner, "run", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(serial_runner, "_write_terminal_attestation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        serial_runner,
        "_resource_paths",
        lambda: serial_runner.ResourcePaths(
            root=tmp_path,
            coverage_shards=tmp_path / "coverage-fragments",
            coverage_json=tmp_path / "coverage.json",
            coverage_audit=tmp_path / "coverage-audit.json",
            attestation=tmp_path / "attestation.json",
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-1a1",
            "--skip-isolated",
            "--skip-aggregate",
            "--no-shadow-replay-audit",
        ],
    )

    assert serial_runner.main() == 0
    assert received == [False]
    output = capsys.readouterr().out
    assert "BATCH-REPLAY-SHADOW status=disabled reason=operator-off skips=0" in output
