"""Pin exact hosted evidence and fail-closed S83 completion decisions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / "docs/evidence/s83_hosted_candidate_26e95a6f.json"
TASKS = ROOT / "TASKS.md"
OWNERSHIP_DOC = ROOT / "docs/features/application-resource-ownership.md"
UPSTREAM_DOC = ROOT / "docs/design/FREELLMAPI_UPSTREAM_INTEGRATION.md"
SHA = "26e95a6f5749d96ba752e0a824dc8cbad6e256c6"


def _load_receipt() -> dict[str, object]:
    value = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _task_line(task_id: str) -> str:
    prefix = "- ["
    marker = f"] {task_id} -"
    return next(
        line
        for line in TASKS.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix) and marker in line
    )


def test_receipt_binds_exact_candidate_and_terminal_workflows() -> None:
    """The durable receipt is content-addressed and names immutable run IDs."""
    receipt = _load_receipt()
    unsigned = dict(receipt)
    evidence_id = unsigned.pop("evidence_id")

    assert evidence_id == f"sha256:{_canonical_digest(unsigned)}"
    assert receipt["schema_version"] == 1
    assert receipt["gate"] == "s83-hosted-candidate-evidence-v1"
    assert receipt["repository"] == "sandboxcom/gludd"
    assert receipt["candidate_sha"] == SHA
    assert receipt["observed_on"] == "2026-10-06"

    build = receipt["build_and_release"]
    assert isinstance(build, dict)
    assert build == {
        **build,
        "run_id": 37427487620,
        "workflow": "Build and Release",
        "url": "https://github.com/sandboxcom/gludd/actions/runs/37427487620",
        "status": "completed",
        "conclusion": "success",
    }
    jobs = build["jobs"]
    assert jobs == {
        "passed": 27,
        "failed": 0,
        "running": 0,
        "other": 2,
        "expected_skipped": ["release", "release_source_proof"],
    }

    molecule = receipt["molecule"]
    assert molecule == {
        "run_id": 37427534191,
        "workflow": "Molecule Tests",
        "url": "https://github.com/sandboxcom/gludd/actions/runs/37427534191",
        "status": "completed",
        "conclusion": "success",
        "jobs_passed": 6,
        "jobs_failed": 0,
        "jobs_running": 0,
        "jobs_other": 0,
    }


def test_receipt_closes_scheduler_and_freellmapi_without_false_live_claim() -> None:
    """The separate live receipt, not a green shell with skips, closes S83.163."""
    receipt = _load_receipt()
    build = receipt["build_and_release"]
    assert isinstance(build, dict)
    proofs = build["task_proofs"]
    assert isinstance(proofs, dict)

    scheduler = proofs["s83_158_claim_before_provision"]
    assert scheduler == {
        "job": "claim-before-provision-acceptance",
        "conclusion": "success",
        "tests_passed": 97,
        "tests_failed": 0,
        "cloud_credentials_present": False,
        "live_cloud_requested": False,
    }

    upstream = proofs["s83_163_upstream_build"]
    assert upstream == [
        {
            "job": "freellmapi-upstream-build (20.20.2)",
            "node_version": "v20.20.2",
            "decision": "upstream_build_verified",
            "step_count": 6,
            "failed_step": None,
            "runtime_admitted": False,
            "evidence_id": "sha256:8b4a0e81c66b10b041269d7e5fbdb6b9c529357829528bc1dcc231c9bfb37c6e",
        },
        {
            "job": "freellmapi-upstream-build (22.23.2)",
            "node_version": "v22.23.2",
            "decision": "upstream_build_verified",
            "step_count": 6,
            "failed_step": None,
            "runtime_admitted": False,
            "evidence_id": "sha256:14a75c3456717165016829e98b0bf795bbebf234634d24a0dd552bf9a9f82b85",
        },
    ]

    provider = proofs["s83_163_provider_e2e"]
    assert provider == {
        "job": "e2e-providers",
        "conclusion": "success",
        "tests_passed": 2,
        "tests_skipped": 27,
        "live_provider_inputs_present": False,
        "satisfies_live_provider_proof": False,
    }

    workload = proofs["s83_163_hermetic_workload"]
    assert workload == {
        "source": "docs/evidence/freellmapi_self_improvement_workload_proof.json",
        "evidence_id": "sha256:c10ad98649ef38af34d5b72999019ef90682abdb1ae9ce02537280b610b3aceb",
        "accepted_decision": "accepted",
        "rejected_decision": "rejected",
        "claims_distinct_and_released": True,
        "runtime_admitted": False,
    }
    live = proofs["s83_163_live_provider_receipt"]
    assert live == {
        "source": "config/freellmapi/live_provider_receipt.json",
        "decision": "live_provider_rejected",
        "external_opt_in": True,
        "request_count": 1,
        "provider_failure": "rate_limited",
        "accepted_count": 0,
        "queue_empty_after": True,
        "provisioned_compute_remaining": 0,
        "runtime_admitted": False,
        "evidence_id": "sha256:dd1732ff83f62d8799155f69369ab5c8c098b5cc918ae6dbdc770949757e6b20",
        "rollback_evidence_id": "sha256:23acd0ada46af06655f1292063599f31e14a847b3b76731916f71e826485f000",
        "provenance_evidence_id": "sha256:0e9fdb5759bef683c5a71f929cba4d551c1b65784f362536f5e404ae5f186bc1",
        "satisfies_bounded_live_provider_proof": True,
    }

    assessments = receipt["task_assessments"]
    assert assessments == {
        "S83.157": {
            "complete": False,
            "remaining": ["positive GPU utilization and accepted mixed-provider proof"],
        },
        "S83.158": {"complete": True, "remaining": []},
        "S83.163": {"complete": True, "remaining": []},
        "S83.166": {
            "complete": False,
            "remaining": ["publication, deployment, and rollback verification"],
        },
    }


def test_task_ledger_and_feature_docs_match_receipt_decisions() -> None:
    """Ledger markers preserve every independently unsatisfied predecessor."""
    s157 = _task_line("S83.157")
    s158 = _task_line("S83.158")
    s163 = _task_line("S83.163")
    s166 = _task_line("S83.166")

    assert s157.startswith("- [ ]") and "status: in_progress" in s157
    assert s158.startswith("- [x]") and "status: completed" in s158
    assert s163.startswith("- [x]") and "status: completed" in s163
    assert s166.startswith("- [ ]") and "status: in_progress" in s166
    for marker in (SHA, "37427487620", "37427534191", RECEIPT.name):
        assert marker in s158
    for marker in (
        SHA,
        "37427487620",
        "27 skipped",
        "live_provider_rejected",
        "rate_limited",
        RECEIPT.name,
    ):
        assert marker in s163

    ownership = OWNERSHIP_DOC.read_text(encoding="utf-8")
    upstream_doc = UPSTREAM_DOC.read_text(encoding="utf-8")
    for marker in (SHA, "37427487620", "37427534191", RECEIPT.name):
        assert marker in ownership
    for marker in (
        SHA,
        "37427487620",
        "27 skipped",
        "live_provider_rejected",
        "rate_limited",
    ):
        assert marker in upstream_doc
