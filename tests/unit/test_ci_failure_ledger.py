"""Behavioral contract for durable, all-failure CI ownership."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import stat
import subprocess
import sys
import textwrap
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

import pytest
from scripts.makefile_layout import compose_makefile

if TYPE_CHECKING:
    import ci_failure_ledger as _native_owner
else:
    from scripts import ci_failure_ledger as _native_owner

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci_failure_ledger.py"
SHA_FAILED = "a" * 40
SHA_REPAIR = "b" * 40


def _load() -> ModuleType:
    assert SCRIPT.exists(), "implement scripts/ci_failure_ledger.py"
    spec = importlib.util.spec_from_file_location("ci_failure_ledger_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _job(name: str, conclusion: str, *failed_steps: str) -> dict[str, Any]:
    steps = [
        {"name": step, "status": "completed", "conclusion": "failure", "number": index}
        for index, step in enumerate(failed_steps, start=1)
    ]
    return {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "steps": steps,
    }


def _payload(
    run_id: int,
    sha: str,
    jobs: list[dict[str, Any]],
    *,
    conclusion: str = "failure",
) -> dict[str, Any]:
    return {
        "databaseId": run_id,
        "headSha": sha,
        "headBranch": "development",
        "status": "completed",
        "conclusion": conclusion,
        "workflowName": "Build and Release",
        "url": f"https://github.invalid/actions/runs/{run_id}",
        "jobs": jobs,
    }


def _failed_payload() -> dict[str, Any]:
    return _payload(
        101,
        SHA_FAILED,
        [
            _job("unit-1a1", "failure", "Run tests", "Upload diagnostics"),
            _job("unit-2", "failure", "Run tests"),
            _job("ansible-ee", "cancelled"),
            _job("unit-3a", "success"),
        ],
    )


def test_observe_records_every_failed_job_and_step_without_first_failure_bias() -> None:
    module = _load()
    ledger = module.new_ledger()

    result = module.observe_payload(ledger, _failed_payload(), observed_at="2026-09-29T12:00:00Z")

    assert result == "recorded"
    assert len(ledger["families"]) == 3
    families = list(ledger["families"].values())
    assert {family["job"] for family in families} == {"unit-1a1", "unit-2", "ansible-ee"}
    unit = next(family for family in families if family["job"] == "unit-1a1")
    assert unit["failed_steps"] == ["Run tests", "Upload diagnostics"]
    cancelled = next(family for family in families if family["job"] == "ansible-ee")
    assert cancelled["failed_steps"] == ["<job:cancelled>"]


def test_reobserving_same_terminal_run_is_idempotent() -> None:
    module = _load()
    ledger = module.new_ledger()
    payload = _failed_payload()

    assert module.observe_payload(ledger, payload, observed_at="first") == "recorded"
    assert module.observe_payload(ledger, payload, observed_at="later") == "unchanged"
    assert len(ledger["runs"]) == 1
    assert all(len(family["occurrences"]) == 1 for family in ledger["families"].values())


def test_validate_accepts_attempt_qualified_historical_run_keys() -> None:
    """Ledgers written for workflow retries remain readable by every guard."""
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    retry = ledger["runs"].pop("101")
    retry["attempt"] = 2
    ledger["runs"]["101:2"] = retry

    module.validate_ledger(ledger)


def test_terminal_run_identity_cannot_be_silently_rewritten() -> None:
    module = _load()
    ledger = module.new_ledger()
    payload = _failed_payload()
    module.observe_payload(ledger, payload, observed_at="first")
    changed = json.loads(json.dumps(payload))
    changed["jobs"][0]["steps"].append(
        {"name": "Late mutation", "status": "completed", "conclusion": "failure", "number": 9}
    )

    with pytest.raises(module.LedgerError, match="immutable terminal run"):
        module.observe_payload(ledger, changed, observed_at="later")


def test_unchanged_rerun_is_blocked_unless_reasoned_override_is_explicit() -> None:
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")

    assert "unchanged failed run" in " ".join(module.guard_rerun(ledger, 101))
    assert "non-empty reason" in " ".join(
        module.guard_rerun(ledger, 101, allow_unchanged=True, reason="")
    )
    assert module.guard_rerun(
        ledger,
        101,
        allow_unchanged=True,
        reason="GitHub-hosted runner outage; retry is the controlled experiment",
    ) == []


def test_rerun_attempts_share_a_run_id_but_keep_immutable_attempt_records() -> None:
    module = _load()
    ledger = module.new_ledger()
    first = _failed_payload()
    first["attempt"] = 1
    second = _payload(
        101,
        SHA_FAILED,
        [_job("unit-1a1", "success"), _job("unit-2", "success"), _job("ansible-ee", "success")],
        conclusion="success",
    )
    second["attempt"] = 2

    assert module.observe_payload(ledger, first, observed_at="first") == "recorded"
    assert module.observe_payload(ledger, second, observed_at="second") == "recorded"
    assert module.observe_payload(ledger, second, observed_at="later") == "unchanged"

    assert set(ledger["runs"]) == {"101", "101:2"}
    assert ledger["runs"]["101:2"]["attempt"] == 2
    assert "already succeeded" in " ".join(
        module.guard_rerun(
            ledger,
            101,
            allow_unchanged=True,
            reason="must inspect the latest attempt",
        )
    )


def test_runner_acquisition_recovery_is_proven_complete_and_bounded() -> None:
    module = _load()
    payload = _payload(
        101,
        SHA_FAILED,
        [
            {**_job("gate (3.12)", "cancelled"), "databaseId": 9001},
            {**_job("gate (3.11)", "success"), "databaseId": 9002},
        ],
    )
    payload["attempt"] = 1
    annotations = {
        9001: ["The job was not acquired by Runner of type hosted even after multiple attempts"],
    }

    assert module.guard_runner_acquisition_rerun(payload, annotations) == []

    missing = module.guard_runner_acquisition_rerun(payload, {})
    assert "no runner-acquisition annotation" in " ".join(missing)

    real_failure = json.loads(json.dumps(payload))
    real_failure["jobs"][0]["steps"] = [
        {"name": "Run tests", "status": "completed", "conclusion": "failure", "number": 1}
    ]
    assert "executed failing steps" in " ".join(
        module.guard_runner_acquisition_rerun(real_failure, annotations)
    )

    exhausted = json.loads(json.dumps(payload))
    exhausted["attempt"] = 2
    assert "recovery limit" in " ".join(
        module.guard_runner_acquisition_rerun(exhausted, annotations)
    )


def test_push_guard_reports_all_open_failures_not_only_the_first() -> None:
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")

    blockers = module.guard_push(ledger, SHA_REPAIR, is_ancestor=lambda _old, _new: True)

    assert len(blockers) == 3
    assert {name for name in ("unit-1a1", "unit-2", "ansible-ee") if name in " ".join(blockers)} == {
        "unit-1a1",
        "unit-2",
        "ansible-ee",
    }


def test_status_and_push_guard_expose_copyable_full_family_ids() -> None:
    """Repair instructions must not expose an unusable abbreviated selector."""
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    family_ids = sorted(ledger["families"])

    status = "\n".join(module.status_lines(ledger))
    blockers = "\n".join(
        module.guard_push(
            ledger,
            SHA_REPAIR,
            is_ancestor=lambda _old, _new: True,
        )
    )

    assert all(f"family={family_id}" in status for family_id in family_ids)
    assert all(f"family={family_id}" in blockers for family_id in family_ids)


def test_verified_repair_receipts_unlock_descendant_push_but_never_failed_sha() -> None:
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    family_ids = sorted(ledger["families"])

    module.record_repair(
        ledger,
        family_ids,
        repair_sha=SHA_REPAIR,
        evidence="make test-files TESTFILES=tests/unit/test_ci_failure_ledger.py PYTEST_ARGS=-q",
        recorded_at="repaired",
        is_ancestor=lambda old, new: old == SHA_FAILED and new == SHA_REPAIR,
    )

    assert module.guard_push(
        ledger, SHA_REPAIR, is_ancestor=lambda old, new: old == SHA_REPAIR and new == SHA_REPAIR
    ) == []
    assert "same SHA" in " ".join(
        module.guard_push(ledger, SHA_FAILED, is_ancestor=lambda _old, _new: True)
    )


def test_later_hosted_success_resolves_matching_failure_families() -> None:
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    success = _payload(
        102,
        SHA_REPAIR,
        [
            _job("unit-1a1", "success"),
            _job("unit-2", "success"),
            _job("ansible-ee", "success"),
        ],
        conclusion="success",
    )

    assert module.observe_payload(ledger, success, observed_at="hosted-green") == "recorded"
    assert {family["status"] for family in ledger["families"].values()} == {"resolved"}
    assert all(family["resolved_by_run"] == 102 for family in ledger["families"].values())


@pytest.mark.parametrize("conclusion", ["skipped", "neutral"])
@pytest.mark.parametrize("prior_status", ["open", "repaired"])
def test_non_success_jobs_do_not_resolve_existing_failure_families(
    conclusion: str, prior_status: str
) -> None:
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    family_ids = sorted(ledger["families"])
    if prior_status == "repaired":
        module.record_repair(
            ledger,
            family_ids,
            repair_sha=SHA_REPAIR,
            evidence="make test-count",
            recorded_at="repair",
            is_ancestor=lambda _old, _new: True,
        )
    original_families = json.loads(json.dumps(ledger["families"]))
    non_success = _payload(
        102,
        SHA_REPAIR,
        [_job(job, conclusion) for job in ("unit-1a1", "unit-2", "ansible-ee")],
        conclusion="success",
    )

    assert module.observe_payload(ledger, non_success, observed_at="no-proof") == "recorded"

    assert ledger["families"] == original_families
    assert {family["status"] for family in ledger["families"].values()} == {prior_status}
    assert ledger["runs"]["102"]["jobs"][0]["conclusion"] == conclusion


def _legacy_non_success_resolution(
    module: ModuleType,
    conclusion: str,
    *,
    repaired: bool = False,
    attempt: int = 1,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Build the historical projection while keeping its immutable runs truthful."""
    ledger = module.new_ledger()
    module.observe_payload(
        ledger,
        _payload(101, SHA_FAILED, [_job("unit", "failure"), _job("proven", "failure")]),
        observed_at="first",
    )
    family_id = next(
        key for key, family in ledger["families"].items() if family["job"] == "unit"
    )
    if repaired:
        module.record_repair(
            ledger,
            [family_id],
            repair_sha=SHA_REPAIR,
            evidence="make test-count",
            recorded_at="repair",
            is_ancestor=lambda _old, _new: True,
        )
    payload = _payload(
        102,
        SHA_REPAIR,
        [_job("unit", conclusion), _job("proven", "success")],
        conclusion="success",
    )
    payload["attempt"] = attempt
    module.observe_payload(ledger, payload, observed_at="legacy")
    ledger["families"][family_id].update(
        status="resolved",
        resolved_by_run=102,
        resolved_by_sha=SHA_REPAIR,
        resolved_at="legacy",
    )
    return ledger, payload, family_id


@pytest.mark.parametrize("conclusion", ["skipped", "neutral"])
@pytest.mark.parametrize("repaired", [False, True])
@pytest.mark.parametrize("attempt", [1, 2])
def test_identical_observation_reconciles_legacy_non_success_resolution(
    conclusion: str, repaired: bool, attempt: int
) -> None:
    module = _load()
    ledger, payload, family_id = _legacy_non_success_resolution(
        module, conclusion, repaired=repaired, attempt=attempt
    )
    original = json.loads(json.dumps(ledger))

    assert module.observe_payload(ledger, payload, observed_at="later") == "reconciled"

    expected_family = dict(original["families"][family_id])
    expected_family["status"] = "repaired" if repaired else "open"
    for key in ("resolved_by_run", "resolved_by_sha", "resolved_at"):
        expected_family.pop(key)
    assert ledger["families"][family_id] == expected_family
    assert ledger["runs"] == original["runs"]
    assert all(
        family == original["families"][key]
        for key, family in ledger["families"].items()
        if key != family_id
    )
    reconciled = json.loads(json.dumps(ledger))
    assert module.observe_payload(ledger, payload, observed_at="even-later") == "unchanged"
    assert ledger == reconciled
    blockers = module.guard_push(ledger, SHA_REPAIR, is_ancestor=lambda _old, _new: True)
    assert len(blockers) == (0 if repaired else 1)


@pytest.mark.parametrize("conclusion", ["skipped", "neutral"])
def test_new_observation_reconciles_proven_legacy_non_success_resolution(
    conclusion: str,
) -> None:
    module = _load()
    ledger, _payload_before, family_id = _legacy_non_success_resolution(module, conclusion)
    original = json.loads(json.dumps(ledger))
    unrelated = _payload(103, "c" * 40, [_job("unrelated", "success")], conclusion="success")

    assert module.observe_payload(ledger, unrelated, observed_at="new-run") == "recorded"

    assert ledger["families"][family_id]["status"] == "open"
    assert ledger["families"][family_id]["occurrences"] == original["families"][family_id]["occurrences"]
    assert {key: ledger["runs"][key] for key in original["runs"]} == original["runs"]
    assert all(
        family == original["families"][key]
        for key, family in ledger["families"].items()
        if key != family_id
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("resolved_by_run", 999),
        ("resolved_by_sha", "c" * 40),
        ("branch", "unrelated"),
        ("workflow", "Other workflow"),
        ("job", "unknown-job"),
    ],
)
def test_reconciliation_preserves_resolutions_without_matching_immutable_evidence(
    field: str, value: str | int
) -> None:
    module = _load()
    ledger, payload, family_id = _legacy_non_success_resolution(module, "skipped")
    ledger["families"][family_id][field] = value
    original = json.loads(json.dumps(ledger))

    assert module.observe_payload(ledger, payload, observed_at="later") == "unchanged"
    assert ledger == original


@pytest.mark.parametrize("conclusion", ["skipped", "neutral"])
def test_reconciliation_preserves_genuine_success_from_an_earlier_attempt(
    conclusion: str,
) -> None:
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(
        ledger, _payload(101, SHA_FAILED, [_job("unit", "failure")]), observed_at="first"
    )
    success = _payload(102, SHA_REPAIR, [_job("unit", "success")], conclusion="success")
    module.observe_payload(ledger, success, observed_at="hosted-green")
    retry = _payload(102, SHA_REPAIR, [_job("unit", conclusion)], conclusion="success")
    retry["attempt"] = 2
    module.observe_payload(ledger, retry, observed_at="retry")
    original = json.loads(json.dumps(ledger))

    assert module.observe_payload(ledger, retry, observed_at="later") == "unchanged"
    assert ledger == original
    assert next(iter(ledger["families"].values()))["resolved_at"] == "hosted-green"


def test_conflicting_terminal_payload_cannot_reconcile_legacy_projection() -> None:
    module = _load()
    ledger, payload, _family_id_before = _legacy_non_success_resolution(module, "skipped")
    original = json.loads(json.dumps(ledger))
    changed = json.loads(json.dumps(payload))
    changed["jobs"][0]["conclusion"] = "neutral"

    with pytest.raises(module.LedgerError, match="immutable terminal run"):
        module.observe_payload(ledger, changed, observed_at="later")
    assert ledger == original


def test_cli_identical_observation_persists_reconciled_legacy_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    module = _load()
    ledger, payload, family_id = _legacy_non_success_resolution(module, "neutral", repaired=True)
    ledger_path = tmp_path / "ledger.json"
    module.write_ledger(ledger_path, ledger)
    monkeypatch.setattr(module, "fetch_run", lambda _run, _repo: payload)
    command = ["observe", "--run", "102", "--ledger", str(ledger_path)]

    assert module.main(command) == 0

    persisted = module.read_ledger(ledger_path)
    assert persisted["families"][family_id]["status"] == "repaired"
    assert persisted["families"][family_id]["repair"] == ledger["families"][family_id]["repair"]
    assert persisted["runs"] == ledger["runs"]
    assert "result=reconciled" in capsys.readouterr().out
    encoded = ledger_path.read_bytes()
    assert module.main(command) == 0
    assert "result=unchanged" in capsys.readouterr().out
    assert ledger_path.read_bytes() == encoded


def test_atomic_ledger_round_trip_is_private_and_malformed_data_fails_closed(
    tmp_path: Path,
) -> None:
    module = _load()
    path = tmp_path / "ci-failure-ledger.json"
    ledger = module.new_ledger()
    module.write_ledger(path, ledger)

    assert module.read_ledger(path) == ledger
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text('{"version": 999}', encoding="utf-8")
    with pytest.raises(module.LedgerError, match="unsupported ledger version"):
        module.read_ledger(path)


def _target_block(source: str, name: str) -> str:
    marker = f"{name}:"
    assert marker in source
    return source.split(marker, 1)[1].split("\n\n", 1)[0]


def test_make_wiring_observes_before_rerun_and_guards_every_push_path() -> None:
    source = compose_makefile(ROOT / "Makefile")
    view = _target_block(source, "ci-view")
    rerun = _target_block(source, "ci-rerun")
    recover = _target_block(source, "ci-recover-runner-acquisition")
    push = _target_block(source, "ci-failure-push-guard")

    assert "scripts/ci_failure_ledger.py observe" in view
    assert "ci-rerun: ci-view" in source
    assert "scripts/ci_failure_ledger.py guard-rerun" in rerun
    assert rerun.index("guard-rerun") < rerun.index("gh run rerun")
    assert "CI_RERUN_ALLOW_UNCHANGED" in rerun
    assert "CI_RERUN_REASON" in rerun
    assert "ci-recover-runner-acquisition: ci-view" in source
    assert "guard-runner-acquisition-rerun" in recover
    assert recover.index("guard-runner-acquisition-rerun") < recover.index("gh run rerun")
    assert "scripts/ci_failure_ledger.py guard-push" in push
    assert "_push-rate-guard: ci-failure-push-guard" in source


def test_all_ci_failure_make_targets_have_network_free_behavioral_contracts() -> None:
    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    contracts = {item["name"]: item for item in payload["targets"]}

    expected = {
        "ci-view",
        "ci-rerun",
        "ci-recover-runner-acquisition",
        "ci-failure-status",
        "ci-failure-repair",
        "ci-failure-push-guard",
    }
    assert expected <= contracts.keys()
    for name in expected:
        assert "CI_FAILURE_VALIDATE_ONLY=1" in contracts[name]["behavior"]


def test_every_cli_contract_path_is_network_free_in_validation_mode(capsys: Any) -> None:
    module = _load()
    commands = [
        ["observe", "--run", "7", "--validate-only"],
        ["observe-sha", "--sha", "0" * 40, "--validate-only"],
        ["status", "--validate-only"],
        ["guard-rerun", "--run", "7", "--validate-only"],
        ["guard-runner-acquisition-rerun", "--run", "7", "--validate-only"],
        [
            "repair",
            "--all-open",
            "--sha",
            "0" * 40,
            "--evidence-target",
            "test-count",
            "--validate-only",
        ],
        ["guard-push", "--head", "0" * 40, "--validate-only"],
    ]

    assert [module.main(command) for command in commands] == [0, 0, 0, 0, 0, 0, 0]
    output = capsys.readouterr().out
    assert "CI_FAILURE_LEDGER_OBSERVE" in output
    assert "VALIDATE_ONLY_PASS" in output


def test_cli_observe_status_rerun_and_push_share_one_durable_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    module = _load()
    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setattr(module, "fetch_run", lambda run, repo: _failed_payload())

    assert module.main(["observe", "--run", "101", "--ledger", str(ledger_path)]) == 0
    assert module.main(["status", "--ledger", str(ledger_path)]) == 1
    assert module.main(["guard-rerun", "--run", "101", "--ledger", str(ledger_path)]) == 1
    assert (
        module.main(
            [
                "guard-rerun",
                "--run",
                "101",
                "--ledger",
                str(ledger_path),
                "--allow-unchanged",
                "--reason",
                "runner outage",
            ]
        )
        == 0
    )
    assert (
        module.main(
            [
                "guard-push",
                "--ledger",
                str(ledger_path),
                "--head",
                SHA_FAILED,
                "--branch",
                "development",
            ]
        )
        == 1
    )
    assert "count=3" in capsys.readouterr().err


def test_cli_push_is_explicitly_inactive_before_first_observation(
    tmp_path: Path, capsys: Any
) -> None:
    module = _load()

    assert (
        module.main(
            ["guard-push", "--ledger", str(tmp_path / "absent.json"), "--head", SHA_REPAIR]
        )
        == 0
    )
    assert "INACTIVE" in capsys.readouterr().out


def test_cli_repair_runs_exact_make_evidence_before_writing_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    module = _load()
    ledger_path = tmp_path / "ledger.json"
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    module.write_ledger(ledger_path, ledger)
    calls: list[list[str]] = []

    def successful_evidence(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if command == ["make", "--no-print-directory", "worktree-state"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "path": str(ROOT), "head": SHA_REPAIR, "is_clean": True, "dirty_count": 0,
            }), "")
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", successful_evidence)
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)

    assert (
        module.main(
            [
                "repair",
                "--ledger",
                str(ledger_path),
                "--all-open",
                "--sha",
                SHA_REPAIR,
                "--evidence-target",
                "test-files",
                "--evidence-var",
                "TESTFILES=tests/unit/test_ci_failure_ledger.py",
            ]
        )
        == 0
    )
    assert calls == [
        ["make", "test-files", "TESTFILES=tests/unit/test_ci_failure_ledger.py"]
    ]
    repaired = module.read_ledger(ledger_path)
    assert {family["status"] for family in repaired["families"].values()} == {"repaired"}
    assert "families=3" in capsys.readouterr().out


def test_cli_repair_rejects_unsafe_or_failed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    module = _load()
    base = [
        "repair",
        "--ledger",
        str(tmp_path / "ledger.json"),
        "--sha",
        SHA_REPAIR,
    ]
    assert module.main([*base, "--evidence-target", "bad target", "--validate-only"]) == 2
    assert (
        module.main(
            [
                *base,
                "--evidence-target",
                "test-count",
                "--evidence-var",
                "lower=value",
                "--validate-only",
            ]
        )
        == 2
    )
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    module.write_ledger(tmp_path / "ledger.json", ledger)
    def failed_evidence(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if command == ["make", "--no-print-directory", "worktree-state"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "path": str(ROOT), "head": SHA_REPAIR, "is_clean": True, "dirty_count": 0,
            }), "")
        return subprocess.CompletedProcess(command, 9, "", "")

    monkeypatch.setattr(module.subprocess, "run", failed_evidence)
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    assert module.main([*base, "--all-open", "--evidence-target", "test-count"]) == 2
    assert "evidence failed" in capsys.readouterr().err


@pytest.mark.parametrize("invalid_sha", ["5ea84e562", "A" * 40, "b" * 41])
def test_cli_repair_rejects_invalid_sha_before_running_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, invalid_sha: str,
) -> None:
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    path = tmp_path / "ledger.json"
    module.write_ledger(path, ledger)
    original = path.read_bytes()
    calls: list[list[str]] = []

    def completed(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", completed)

    assert module.main([
        "repair", "--ledger", str(path), "--all-open", "--sha", invalid_sha,
        "--evidence-target", "test-count",
    ]) == 2
    assert "repair SHA must be a full lowercase 40-character hash" in capsys.readouterr().err
    assert calls == []
    assert path.read_bytes() == original


def _repair_cli_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ModuleType, Path, list[list[str]], list[str]]:
    module = _load()
    path = tmp_path / "ledger.json"
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    module.write_ledger(path, ledger)
    original = path.read_bytes()
    calls: list[list[str]] = []

    def successful(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command == ["make", "--no-print-directory", "worktree-state"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "path": str(ROOT), "head": SHA_REPAIR, "is_clean": True, "dirty_count": 0,
            }), "")
        assert path.read_bytes() == original, "receipt must follow successful evidence"
        assert kwargs["cwd"] == ROOT
        assert kwargs["check"] is False
        assert kwargs.get("shell", False) is False
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", successful)
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    command = [
        "repair", "--ledger", str(path), "--all-open", "--sha", SHA_REPAIR,
        "--evidence-target", "test-files",
    ]
    return module, path, calls, command


@pytest.mark.parametrize("invalid", [
    "unknown", "resolved", "empty", "empty-all-open", "same-sha", "non-descendant", "mixed",
])
def test_cli_repair_selection_is_admitted_before_any_evidence_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, invalid: str,
) -> None:
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    family_id = sorted(ledger["families"])[0]
    selection = ["--family", family_id]
    sha = SHA_FAILED if invalid == "same-sha" else SHA_REPAIR
    if invalid == "resolved":
        ledger["families"][family_id]["status"] = "resolved"
    elif invalid == "unknown":
        selection = ["--family", "f" * 64]
    elif invalid == "mixed":
        selection.extend(["--family", "f" * 64])
    elif invalid == "empty":
        selection = []
    elif invalid == "empty-all-open":
        for family in ledger["families"].values():
            family["status"] = "resolved"
        selection = ["--all-open"]
    path = tmp_path / "ledger.json"
    module.write_ledger(path, ledger)
    before = path.read_bytes()
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command == ["make", "--no-print-directory", "worktree-state"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "path": str(ROOT), "head": sha, "is_clean": True, "dirty_count": 0,
            }), "")
        assert path.read_bytes() == before
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: invalid != "non-descendant")
    assert module.main([
        "repair", "--ledger", str(path), "--sha", sha,
        "--evidence-target", "test-files", *selection,
    ]) == 2
    assert calls == [], "an ineligible selection must launch zero evidence children"
    assert path.read_bytes() == before
    assert capsys.readouterr().err


@pytest.mark.parametrize("clean,sha,expected", [
    (True, SHA_REPAIR, SHA_REPAIR), (False, SHA_REPAIR, None),
    (True, SHA_FAILED, None), (True, "short", None),
])
def test_repair_guidance_is_per_family_and_requires_explicit_evidence_coverage(
    tmp_path: Path, clean: bool, sha: str, expected: str | None,
) -> None:
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    original = deepcopy(ledger)
    guidance = module.build_repair_guidance(
        ledger, ledger_path=tmp_path / "ledger.json", branch="development",
        repair_sha=sha, checkout_clean=clean,
    )

    assert len(guidance) == 3
    assert {tuple(item["family_ids"]) for item in guidance} == {(key,) for key in ledger["families"]}
    for item in guidance:
        assert item["candidate_sha"] == expected
        assert item["argv"] is None
        assert item["argv_template"][:2] == ["make", "ci-failure-repair"]
        assert "CI_REPAIR_ALL_OPEN=0" in item["argv_template"]
        assert "CI_REPAIR_EVIDENCE_TARGET=<explicit-evidence-target>" in item["argv_template"]
        assert "CI_REPAIR_EVIDENCE_TARGET" in item["required_parameters"]
        assert "CI_REPAIR_EVIDENCE_VARS" in item["required_parameters"]
        assert "release-proof-admission" not in " ".join(item["argv_template"])
        assert ("CI_REPAIR_SHA" in item["required_parameters"]) is (expected is None)
    assert ledger == original


def test_first_selected_repair_execution_records_only_its_single_evidence_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, path, calls, _command = _repair_cli_probe(tmp_path, monkeypatch)
    ledger = module.read_ledger(path)
    family_id = sorted(ledger["families"])[0]
    guidance = module.build_repair_guidance(
        ledger, ledger_path=path, branch="development", repair_sha=SHA_REPAIR, checkout_clean=True,
    )
    selected = next(item for item in guidance if item["family_ids"] == [family_id])
    values = dict(argument.split("=", 1) for argument in selected["argv_template"][2:])
    values["CI_REPAIR_EVIDENCE_TARGET"] = "test-files"
    values["CI_REPAIR_EVIDENCE_VARS"] = "TESTFILES=tests/unit/test_ci_failure_ledger.py PYTEST_ARGS=-q"
    monkeypatch.setenv("CI_REPAIR_EVIDENCE_VARS", values["CI_REPAIR_EVIDENCE_VARS"])

    assert module.main([
        "repair", "--ledger", values["CI_FAILURE_LEDGER"],
        "--family", values["CI_FAILURE_FAMILIES"], "--sha", values["CI_REPAIR_SHA"],
        "--evidence-target", values["CI_REPAIR_EVIDENCE_TARGET"], "--evidence-vars-from-env",
    ]) == 0

    assert calls == [["make", "test-files", "TESTFILES=tests/unit/test_ci_failure_ledger.py", "PYTEST_ARGS=-q"]]
    persisted = module.read_ledger(path)
    assert persisted["families"][family_id]["status"] == "repaired"
    assert all(family["status"] == "open" for key, family in persisted["families"].items() if key != family_id)


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("invalid", [
    "wrong-head", "dirty", "foreign-root", "missing-clean", "query-failed", "malformed", "not-object",
    "timeout", "io",
])
def test_cli_repair_refuses_evidence_from_a_different_or_unverified_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, invalid: str,
) -> None:
    module, path, calls, command = _repair_cli_probe(tmp_path, monkeypatch)
    original = path.read_bytes()
    run = cast(_native_owner.Runner, module.subprocess.run)
    state_queries = 0

    def current_checkout(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal state_queries
        if argv != ["make", "--no-print-directory", "worktree-state"]:
            return run(argv, **kwargs)
        state_queries += 1
        state = {"path": str(ROOT), "head": SHA_REPAIR, "is_clean": True, "dirty_count": 0}
        if state_queries == (1 if phase == "before" else 2):
            if invalid == "wrong-head":
                state["head"] = SHA_FAILED
            elif invalid == "dirty":
                state.update(is_clean=False, dirty_count=1)
            elif invalid == "foreign-root":
                state["path"] = str(tmp_path)
            elif invalid == "missing-clean":
                del state["is_clean"]
            elif invalid == "query-failed":
                return subprocess.CompletedProcess(argv, 1, json.dumps(state), "state query failed")
            elif invalid == "malformed":
                return subprocess.CompletedProcess(argv, 0, "not-json", "")
            elif invalid == "not-object":
                return subprocess.CompletedProcess(argv, 0, "[]", "")
            elif invalid == "timeout":
                raise subprocess.TimeoutExpired(argv, 15)
            elif invalid == "io":
                raise OSError("state process unavailable")
        assert kwargs == {"cwd": ROOT, "check": False, "capture_output": True, "text": True, "timeout": 15}
        return subprocess.CompletedProcess(argv, 0, json.dumps(state), "")

    monkeypatch.setattr(module.subprocess, "run", current_checkout)
    assert module.main(command) == 2
    assert calls == ([] if phase == "before" else [["make", "test-files"]])
    assert path.read_bytes() == original
    assert all(family["status"] == "open" for family in module.read_ledger(path)["families"].values())


def test_native_repair_checkout_binding_is_admitted_before_publication() -> None:
    body = compose_makefile(ROOT / "Makefile").split("\n_pytest-runtime-compatibility-admission:", 1)[1]
    body = body.split("\n_ansible-role-variable-prefix:", 1)[0]
    assert "tests/unit/test_ci_failure_ledger.py" in next(
        line for line in body.splitlines() if line.strip().startswith("TESTFILES=")
    )
    payload = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    family = next(family for family in payload["families"] if family["id"] == "ci-repair-checkout-source-binding")
    assert family["owner_target"] == "_pytest-runtime-compatibility-admission"
    assert family["node"] == (
        "tests/unit/test_ci_failure_ledger.py::"
        "test_cli_repair_refuses_evidence_from_a_different_or_unverified_checkout"
    )
    assert family["full_gate_phase"] == "test"


def test_cli_repair_env_preserves_multifile_and_quoted_pytest_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, path, calls, command = _repair_cli_probe(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "CI_REPAIR_EVIDENCE_VARS",
        'TESTFILES="tests/unit/a.py tests/unit/b.py" PYTEST_ARGS=\'-q -k "repair or quoted"\'',
    )
    expected = [
        "make", "test-files", "TESTFILES=tests/unit/a.py tests/unit/b.py",
        'PYTEST_ARGS=-q -k "repair or quoted"',
    ]

    assert module.main([*command, "--evidence-vars-from-env"]) == 0

    assert calls == [expected]
    families = module.read_ledger(path)["families"].values()
    assert all(family["status"] == "repaired" for family in families)
    assert all(shlex.split(family["repair"]["evidence"]) == expected for family in families)


def test_cli_repair_env_keeps_literal_quotes_and_shell_metacharacters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, path, calls, command = _repair_cli_probe(tmp_path, monkeypatch)
    assignment = (
        'PYTEST_ARGS=  "double" \'single\' $UNSET ${UNSET} '
        "$(printf expanded) `printf expanded` * ;"
    )
    monkeypatch.setenv("CI_REPAIR_EVIDENCE_VARS", shlex.join([assignment]))
    expected = ["make", "test-files", assignment]

    assert module.main([*command, "--evidence-vars-from-env"]) == 0

    assert calls == [expected]
    assert all(
        shlex.split(family["repair"]["evidence"]) == expected
        for family in module.read_ledger(path)["families"].values()
    )


def test_cli_repair_ignores_ambient_evidence_vars_without_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _path, calls, command = _repair_cli_probe(tmp_path, monkeypatch)
    monkeypatch.setenv("CI_REPAIR_EVIDENCE_VARS", 'TESTFILES="unterminated')

    assert module.main(command) == 0

    assert calls == [["make", "test-files"]]


def test_cli_repair_env_rejects_malformed_quoting_before_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    module, path, calls, command = _repair_cli_probe(tmp_path, monkeypatch)
    original = path.read_bytes()
    monkeypatch.setenv("CI_REPAIR_EVIDENCE_VARS", 'TESTFILES="unterminated')

    assert module.main([*command, "--evidence-vars-from-env"]) == 2

    error = capsys.readouterr().err
    assert "CI_REPAIR_EVIDENCE_VARS" in error
    assert "quot" in error.lower()
    assert calls == []
    assert path.read_bytes() == original


def test_cli_repair_env_rejects_invalid_assignment_before_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    module, path, calls, command = _repair_cli_probe(tmp_path, monkeypatch)
    original = path.read_bytes()
    monkeypatch.setenv("CI_REPAIR_EVIDENCE_VARS", 'TESTFILES="a.py b.py" not-an-assignment')

    assert module.main([*command, "--evidence-vars-from-env"]) == 2

    assert "UPPER_CASE=value" in capsys.readouterr().err
    assert calls == []
    assert path.read_bytes() == original


def test_cli_repair_legacy_repeated_evidence_vars_preserve_complete_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, path, calls, command = _repair_cli_probe(tmp_path, monkeypatch)
    monkeypatch.setenv("CI_REPAIR_EVIDENCE_VARS", 'TESTFILES="unterminated')
    assignments = ["TESTFILES=a.py b.py", 'PYTEST_ARGS=-q -k "repair or quoted"']

    assert module.main([
        *command, "--evidence-var", assignments[0], "--evidence-var", assignments[1],
    ]) == 0

    expected = ["make", "test-files", *assignments]
    assert calls == [expected]
    assert all(
        family["repair"]["evidence"] == shlex.join(expected)
        for family in module.read_ledger(path)["families"].values()
    )


def test_cli_repair_invalid_sha_precedes_environment_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    module, path, calls, command = _repair_cli_probe(tmp_path, monkeypatch)
    original = path.read_bytes()
    command[command.index("--sha") + 1] = "short-sha"
    monkeypatch.setenv("CI_REPAIR_EVIDENCE_VARS", 'TESTFILES="unterminated')

    assert module.main([*command, "--evidence-vars-from-env"]) == 2

    error = capsys.readouterr().err
    assert "repair SHA must be a full lowercase 40-character hash" in error
    assert "CI_REPAIR_EVIDENCE_VARS" not in error
    assert calls == []
    assert path.read_bytes() == original


def test_make_ci_failure_repair_exports_quoted_values_to_cli_probe(tmp_path: Path) -> None:
    """Exercise the real recipe with a Python probe that cannot perform repairs."""
    probe = tmp_path / "argv_probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], "
        "'evidence_vars': os.environ.get('CI_REPAIR_EVIDENCE_VARS')}))\n",
        encoding="utf-8",
    )
    evidence_vars = (
        'TESTFILES="tests/unit/a.py tests/unit/b.py" PYTEST_ARGS=\'-q -k "repair or quoted"\''
    )
    wrapper = tmp_path / "probe.mk"
    wrapper.write_text(
        f"CI_REPAIR_EVIDENCE_VARS := {evidence_vars}\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "untouched-ledger.json"
    environment = dict(os.environ)
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "CI_REPAIR_EVIDENCE_VARS"):
        environment.pop(name, None)

    result = subprocess.run(
        [
            "make", "--no-print-directory", "-f", "Makefile", "-f", str(wrapper),
            "ci-failure-repair", f"PYTHON={shlex.join([sys.executable, str(probe)])}",
            f"CI_FAILURE_LEDGER={ledger}", "CI_FAILURE_FAMILIES=", "CI_REPAIR_ALL_OPEN=1",
            f"CI_REPAIR_SHA={SHA_REPAIR}", "CI_REPAIR_EVIDENCE_TARGET=test-files",
            "CI_FAILURE_VALIDATE_ONLY=1",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["argv"] == [
        "scripts/ci_failure_ledger.py", "repair", "--ledger", str(ledger),
        "--sha", SHA_REPAIR, "--evidence-target", "test-files", "--all-open",
        "--evidence-vars-from-env", "--validate-only",
    ]
    assert observed["evidence_vars"] == evidence_vars
    assert not ledger.exists()


def test_fetch_run_is_id_bound_and_fail_closed() -> None:
    module = _load()
    payload = _failed_payload()
    payload.update(attempt=1, url="https://github.com/sandboxcom/gludd/actions/runs/101")
    native_jobs = []
    for index, job in enumerate(payload["jobs"], start=1):
        job.update(
            databaseId=9000 + index,
            startedAt="0001-01-01T00:00:00Z",
            completedAt="0001-01-01T00:00:00Z",
            url=f"https://github.com/sandboxcom/gludd/actions/runs/101/job/{9000 + index}",
        )
        for step in job["steps"]:
            step.update(startedAt="0001-01-01T00:00:00Z", completedAt="0001-01-01T00:00:00Z")
        native_jobs.append({
            "id": job["databaseId"], "run_id": 101, "run_attempt": 1,
            "run_url": "https://api.github.com/repos/sandboxcom/gludd/actions/runs/101",
            "head_sha": payload["headSha"],
            "url": f"https://api.github.com/repos/sandboxcom/gludd/actions/jobs/{job['databaseId']}",
            "html_url": job["url"], "name": job["name"], "status": job["status"],
            "conclusion": job["conclusion"], "started_at": None, "completed_at": None,
            "steps": [{
                "name": step["name"], "status": step["status"], "conclusion": step["conclusion"],
                "number": step["number"], "started_at": None, "completed_at": None,
            } for step in job["steps"]],
        })
    metadata = {key: value for key, value in payload.items() if key != "jobs"}
    calls: list[list[str]] = []

    def success(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        response = ([{"total_count": len(native_jobs), "jobs": native_jobs}]
                    if command[:2] == ["gh", "api"] else metadata)
        return subprocess.CompletedProcess(command, 0, json.dumps(response), "")

    assert module.fetch_run(101, "sandboxcom/gludd", runner=success) == payload
    assert calls[0][:4] == ["gh", "run", "view", "101"]
    assert "attempt" in calls[0][-1]
    with pytest.raises(module.LedgerError, match="repository"):
        module.fetch_run(101, "not-a-repository", runner=success)

    def failure(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "authentication failed")

    with pytest.raises(module.LedgerError, match="authentication failed"):
        module.fetch_run(101, "sandboxcom/gludd", runner=failure)

    def invalid(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "not-json", "")

    with pytest.raises(module.LedgerError, match="invalid JSON"):
        module.fetch_run(101, "sandboxcom/gludd", runner=invalid)
    wrong = dict(metadata, databaseId=102)
    with pytest.raises(module.LedgerError, match="different run identity"):
        module.fetch_run(
            101,
            "sandboxcom/gludd",
            runner=lambda command, **_: subprocess.CompletedProcess(
                command, 0, json.dumps(wrong), ""
            ),
        )


def test_check_run_annotations_are_paginated_typed_and_failure_closed() -> None:
    module = _load()
    calls: list[list[str]] = []

    def success(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        pages = [[{"message": module.RUNNER_ACQUISITION_MESSAGE}], [{"message": "notice"}]]
        return subprocess.CompletedProcess(command, 0, json.dumps(pages), "")

    assert module.fetch_job_annotations(9001, "sandboxcom/gludd", runner=success) == [
        module.RUNNER_ACQUISITION_MESSAGE,
        "notice",
    ]
    assert calls == [
        [
            "gh",
            "api",
            "repos/sandboxcom/gludd/check-runs/9001/annotations",
            "--paginate",
            "--slurp",
        ]
    ]
    with pytest.raises(module.LedgerError, match="repository"):
        module.fetch_job_annotations(9001, "unsafe", runner=success)
    with pytest.raises(module.LedgerError, match="positive integer"):
        module.fetch_job_annotations(0, "sandboxcom/gludd", runner=success)
    with pytest.raises(module.LedgerError, match="lookup failed"):
        module.fetch_job_annotations(
            9001,
            "sandboxcom/gludd",
            runner=lambda command, **_: subprocess.CompletedProcess(command, 1, "", "denied"),
        )


def test_runner_acquisition_cli_requires_observation_and_proven_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    module = _load()
    ledger_path = tmp_path / "ledger.json"
    payload = _payload(
        101,
        SHA_FAILED,
        [{**_job("gate", "cancelled"), "databaseId": 9001}],
    )
    ledger = module.new_ledger()
    module.observe_payload(ledger, payload, observed_at="first")
    module.write_ledger(ledger_path, ledger)
    monkeypatch.setattr(module, "fetch_run", lambda _run, _repo: payload)
    monkeypatch.setattr(
        module,
        "fetch_runner_acquisition_annotations",
        lambda _payload, _repo: {9001: [module.RUNNER_ACQUISITION_MESSAGE]},
    )

    command = [
        "guard-runner-acquisition-rerun",
        "--run",
        "101",
        "--repo",
        "sandboxcom/gludd",
        "--ledger",
        str(ledger_path),
    ]
    assert module.main(command) == 0
    assert "GUARD_PASS" in capsys.readouterr().out

    command[-1] = str(tmp_path / "absent.json")
    assert module.main(command) == 2
    assert "must be observed" in capsys.readouterr().err


def test_git_identity_helpers_require_exact_successful_results() -> None:
    module = _load()

    def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, SHA_REPAIR.upper() + "\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    assert module._git_is_ancestor(SHA_FAILED, SHA_REPAIR, runner=runner) is True
    assert module._git_head(runner=runner) == SHA_REPAIR
    assert (
        module._git_is_ancestor(
            SHA_FAILED,
            SHA_REPAIR,
            runner=lambda command, **_: subprocess.CompletedProcess(command, 1, "", ""),
        )
        is False
    )
    with pytest.raises(module.LedgerError, match="exact local HEAD"):
        module._git_head(
            runner=lambda command, **_: subprocess.CompletedProcess(command, 1, "bad", "")
        )


def test_snapshot_rejects_every_ambiguous_nonterminal_shape() -> None:
    module = _load()
    cases: list[tuple[dict[str, Any], str]] = []
    baseline = _failed_payload()
    cases.append((dict(baseline, databaseId=True), "positive integer"))
    cases.append((dict(baseline, databaseId="nope"), "positive integer"))
    cases.append((dict(baseline, databaseId=0), "positive integer"))
    cases.append((dict(baseline, attempt=True), "run attempt"))
    cases.append((dict(baseline, attempt=0), "run attempt"))
    cases.append((dict(baseline, headSha="short"), "full lowercase"))
    cases.append((dict(baseline, status="queued"), "not terminal"))
    cases.append((dict(baseline, jobs=[]), "non-empty array"))
    cases.append((dict(baseline, jobs=["not-an-object"]), "must be an object"))
    cases.append((dict(baseline, jobs=[dict(_job("x", "failure"), status="queued")]), "non-terminal job"))
    cases.append((dict(baseline, jobs=[dict(_job("x", "failure"), steps="bad")]), "steps must be an array"))
    cases.append(
        (
            dict(
                baseline,
                jobs=[
                    dict(
                        _job("x", "failure"),
                        steps=[{"name": "", "conclusion": "failure"}],
                    )
                ],
            ),
            "non-empty string",
        )
    )

    for payload, message in cases:
        with pytest.raises(module.LedgerError, match=message):
            module.observe_payload(module.new_ledger(), payload, observed_at="now")


def test_schema_validation_and_size_bounds_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    corruptions = [
        {"version": 1, "runs": [], "families": {}},
        {"version": 1, "runs": {"bad": {}}, "families": {}},
        {"version": 1, "runs": {"1": {"run_id": 2}}, "families": {}},
        {
            "version": 1,
            "runs": {},
            "families": {"bad": {}},
        },
    ]
    for ledger in corruptions:
        with pytest.raises(module.LedgerError):
            module.validate_ledger(ledger)

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(module.LedgerError, match="invalid JSON"):
        module.read_ledger(malformed)
    assert module.read_ledger(tmp_path / "absent.json") == module.new_ledger()
    monkeypatch.setattr(module, "MAX_LEDGER_BYTES", 1)
    with pytest.raises(module.LedgerError, match="2 MiB"):
        module.write_ledger(tmp_path / "large.json", module.new_ledger())
    oversized = tmp_path / "oversized.json"
    oversized.write_text("{}", encoding="utf-8")
    with pytest.raises(module.LedgerError, match="2 MiB"):
        module.read_ledger(oversized)


def test_failure_regression_reopens_family_and_repair_errors_are_atomic() -> None:
    module = _load()
    ledger = module.new_ledger()
    module.observe_payload(ledger, _failed_payload(), observed_at="first")
    family_ids = sorted(ledger["families"])
    module.record_repair(
        ledger,
        family_ids,
        repair_sha=SHA_REPAIR,
        evidence="make test-count",
        recorded_at="repair",
        is_ancestor=lambda _old, _new: True,
    )
    regression = _failed_payload()
    regression["databaseId"] = 103
    regression["headSha"] = SHA_REPAIR
    module.observe_payload(ledger, regression, observed_at="regression")
    assert {family["status"] for family in ledger["families"].values()} == {"open"}
    assert all("repair" not in family for family in ledger["families"].values())
    assert all(len(family["occurrences"]) == 2 for family in ledger["families"].values())

    for kwargs, message in [
        ({"family_ids": family_ids, "repair_sha": "bad", "evidence": "make test-count"}, "repair SHA"),
        ({"family_ids": family_ids, "repair_sha": SHA_FAILED, "evidence": ""}, "evidence"),
        ({"family_ids": [], "repair_sha": SHA_FAILED, "evidence": "make test-count"}, "at least one"),
        ({"family_ids": ["f" * 64], "repair_sha": SHA_FAILED, "evidence": "make test-count"}, "unknown family"),
        ({"family_ids": family_ids, "repair_sha": SHA_REPAIR, "evidence": "make test-count"}, "same SHA"),
        ({"family_ids": family_ids, "repair_sha": "c" * 40, "evidence": "make test-count"}, "does not descend"),
    ]:
        with pytest.raises(module.LedgerError, match=message):
            module.record_repair(
                ledger,
                kwargs["family_ids"],
                repair_sha=kwargs["repair_sha"],
                evidence=kwargs["evidence"],
                recorded_at="now",
                is_ancestor=lambda _old, _new: False,
            )


def test_guards_cover_unknown_success_invalid_and_nonancestral_states() -> None:
    module = _load()
    ledger = module.new_ledger()
    assert "not been observed" in module.guard_rerun(ledger, 999)[0]
    success = _payload(200, SHA_REPAIR, [_job("unit", "success")], conclusion="success")
    module.observe_payload(ledger, success, observed_at="green")
    assert "already succeeded" in module.guard_rerun(ledger, 200)[0]
    assert "full lowercase" in module.guard_push(
        ledger, "bad", is_ancestor=lambda _old, _new: True
    )[0]

    failed = _failed_payload()
    module.observe_payload(ledger, failed, observed_at="red")
    family_ids = sorted(ledger["families"])
    module.record_repair(
        ledger,
        family_ids,
        repair_sha=SHA_REPAIR,
        evidence="make test-count",
        recorded_at="repair",
        is_ancestor=lambda _old, _new: True,
    )
    blockers = module.guard_push(
        ledger,
        "c" * 40,
        is_ancestor=lambda _old, _new: False,
    )
    assert len(blockers) == 3
    assert all("not an ancestor" in blocker for blocker in blockers)
    assert module.guard_push(
        ledger,
        "c" * 40,
        branch="unrelated",
        is_ancestor=lambda _old, _new: False,
    ) == []


def test_exact_sha_observer_records_newest_terminal_run_from_every_workflow() -> None:
    module = _load()
    ledger = module.new_ledger()
    index = [
        {
            "databaseId": 100,
            "headSha": SHA_FAILED,
            "headBranch": "development",
            "event": "push",
            "workflowName": "Build and Release",
            "status": "completed",
            "conclusion": "failure",
            "createdAt": "2026-09-29T10:00:00Z",
        },
        {
            "databaseId": 101,
            "headSha": SHA_FAILED,
            "headBranch": "development",
            "event": "push",
            "workflowName": "Build and Release",
            "status": "completed",
            "conclusion": "failure",
            "createdAt": "2026-09-29T11:00:00Z",
        },
        {
            "databaseId": 201,
            "headSha": SHA_FAILED,
            "headBranch": "development",
            "event": "push",
            "workflowName": "Molecule Tests",
            "status": "completed",
            "conclusion": "failure",
            "createdAt": "2026-09-29T11:01:00Z",
        },
        {
            "databaseId": 301,
            "headSha": SHA_FAILED,
            "headBranch": "development",
            "event": "push",
            "workflowName": "Future Workflow",
            "status": "in_progress",
            "conclusion": "",
            "createdAt": "2026-09-29T11:02:00Z",
        },
    ]
    fetched: list[int] = []

    def fetcher(run_id: int, _repository: str) -> dict[str, Any]:
        fetched.append(run_id)
        workflow = "Build and Release" if run_id == 101 else "Molecule Tests"
        payload = _payload(run_id, SHA_FAILED, [_job(f"job-{run_id}", "failure")])
        payload["workflowName"] = workflow
        return payload

    results, errors = module.observe_exact_sha(
        ledger,
        index,
        sha=SHA_FAILED,
        branch="development",
        repository="sandboxcom/gludd",
        observed_at="now",
        fetcher=fetcher,
    )

    assert fetched == [101, 201]
    assert results == [(101, "recorded"), (201, "recorded")]
    assert errors == []
    assert set(ledger["runs"]) == {"101", "201"}


def test_exact_sha_observer_collects_all_fetch_errors_without_short_circuiting() -> None:
    module = _load()
    ledger = module.new_ledger()
    index = [
        {
            "databaseId": run_id,
            "headSha": SHA_FAILED,
            "headBranch": "development",
            "event": "push",
            "workflowName": workflow,
            "status": "completed",
            "conclusion": "failure",
            "createdAt": f"2026-09-29T11:0{run_id}:00Z",
        }
        for run_id, workflow in ((1, "First"), (2, "Second"))
    ]
    fetched: list[int] = []

    def fetcher(run_id: int, _repository: str) -> dict[str, Any]:
        fetched.append(run_id)
        if run_id == 1:
            raise module.LedgerError("first detail unavailable")
        payload = _payload(run_id, SHA_FAILED, [_job("second", "failure")])
        payload["workflowName"] = "Second"
        return payload

    results, errors = module.observe_exact_sha(
        ledger,
        index,
        sha=SHA_FAILED,
        branch="development",
        repository="sandboxcom/gludd",
        observed_at="now",
        fetcher=fetcher,
    )

    assert fetched == [1, 2]
    assert results == [(2, "recorded")]
    assert errors == ["run 1: first detail unavailable"]
    assert set(ledger["runs"]) == {"2"}


def test_pipeline_status_automatically_observes_all_exact_sha_workflows() -> None:
    source = compose_makefile(ROOT / "Makefile")
    block = _target_block(source, "pipeline-status")
    assert "scripts/pipeline_status.py status --observe-ledger" in block
    assert block.count("$(UV) run python") == 1
    assert '--failure-ledger "$(PIPELINE_STATUS_FAILURE_LEDGER)"' in block

    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    contract = {item["name"]: item for item in payload["targets"]}["pipeline-status"]
    assert "PIPELINE_STATUS_FAILURE_LEDGER" in contract["make_variables"]


def test_run_index_fetch_is_bounded_typed_and_fail_closed() -> None:
    module = _load()
    index = [{"databaseId": 1, "workflowName": "Build"}]

    def success(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        assert command[:4] == ["gh", "run", "list", "--commit"]
        assert "--limit" in command and "100" in command
        return subprocess.CompletedProcess(command, 0, json.dumps(index), "")

    assert module.fetch_run_index(SHA_FAILED, "sandboxcom/gludd", runner=success) == index
    with pytest.raises(module.LedgerError, match="index SHA"):
        module.fetch_run_index("bad", "sandboxcom/gludd", runner=success)
    with pytest.raises(module.LedgerError, match="repository"):
        module.fetch_run_index(SHA_FAILED, "bad", runner=success)

    def result(stdout: str, returncode: int = 0, stderr: str = "") -> Any:
        return lambda command, **_: subprocess.CompletedProcess(
            command, returncode, stdout, stderr
        )

    with pytest.raises(module.LedgerError, match="lookup failed"):
        module.fetch_run_index(
            SHA_FAILED,
            "sandboxcom/gludd",
            runner=result("", 1, "API unavailable"),
        )
    with pytest.raises(module.LedgerError, match="invalid JSON"):
        module.fetch_run_index(SHA_FAILED, "sandboxcom/gludd", runner=result("{"))
    with pytest.raises(module.LedgerError, match="must be an array"):
        module.fetch_run_index(SHA_FAILED, "sandboxcom/gludd", runner=result("{}"))
    with pytest.raises(module.LedgerError, match="reached its limit"):
        module.fetch_run_index(
            SHA_FAILED,
            "sandboxcom/gludd",
            runner=result(json.dumps([{}] * 100)),
        )
    with pytest.raises(module.LedgerError, match="entries must be objects"):
        module.fetch_run_index(
            SHA_FAILED,
            "sandboxcom/gludd",
            runner=result(json.dumps(["bad"])),
        )


def test_remote_head_is_exact_and_fail_closed() -> None:
    module = _load()

    def result(stdout: str, returncode: int = 0, stderr: str = "") -> Any:
        return lambda command, **_: subprocess.CompletedProcess(
            command, returncode, stdout, stderr
        )

    assert (
        module.remote_head(
            "development",
            "sandboxcom",
            runner=result(f"{SHA_REPAIR}\trefs/heads/development\n"),
        )
        == SHA_REPAIR
    )
    with pytest.raises(module.LedgerError, match="non-empty"):
        module.remote_head("", "sandboxcom", runner=result(""))
    with pytest.raises(module.LedgerError, match="lookup failed"):
        module.remote_head("development", "sandboxcom", runner=result("", 1, "offline"))
    with pytest.raises(module.LedgerError, match="no exact remote head"):
        module.remote_head("development", "sandboxcom", runner=result("not-a-sha\n"))


def test_observe_sha_cli_persists_results_and_reports_all_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    module = _load()
    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setattr(module, "remote_head", lambda branch, remote: SHA_FAILED)
    monkeypatch.setattr(module, "fetch_run_index", lambda sha, repo: [])
    monkeypatch.setattr(
        module,
        "observe_exact_sha",
        lambda *args, **kwargs: ([(11, "recorded"), (12, "unchanged")], []),
    )

    assert (
        module.main(
            [
                "observe-sha",
                "--branch",
                "development",
                "--remote",
                "sandboxcom",
                "--repo",
                "sandboxcom/gludd",
                "--ledger",
                str(ledger_path),
            ]
        )
        == 0
    )
    assert ledger_path.exists()
    assert "run=11" in capsys.readouterr().out

    monkeypatch.setattr(
        module,
        "observe_exact_sha",
        lambda *args, **kwargs: ([], ["run 1: unavailable", "run 2: invalid"]),
    )
    assert (
        module.main(
            [
                "observe-sha",
                "--sha",
                SHA_FAILED,
                "--ledger",
                str(ledger_path),
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "count=2" in error
    assert "run 1" in error and "run 2" in error


NATIVE_REPOSITORY = "sandboxcom/gludd"
NATIVE_RUN_ID = 101
NATIVE_ATTEMPT = 3
NATIVE_SHA = "a" * 40
NATIVE_ZERO_TIME = "0001-01-01T00:00:00Z"


def _native_metadata() -> dict[str, Any]:
    return {
        "attempt": NATIVE_ATTEMPT,
        "databaseId": NATIVE_RUN_ID,
        "headSha": NATIVE_SHA,
        "headBranch": "development",
        "status": "completed",
        "conclusion": "failure",
        "url": f"https://github.com/{NATIVE_REPOSITORY}/actions/runs/{NATIVE_RUN_ID}",
        "workflowName": "Build and Release",
    }


def _native_job(index: int = 1, *, failed: bool = False) -> dict[str, Any]:
    job_id = 9000 + index
    return {
        "id": job_id,
        "run_id": NATIVE_RUN_ID,
        "run_attempt": NATIVE_ATTEMPT,
        "run_url": f"https://api.github.com/repos/{NATIVE_REPOSITORY}/actions/runs/{NATIVE_RUN_ID}",
        "head_sha": NATIVE_SHA,
        "url": f"https://api.github.com/repos/{NATIVE_REPOSITORY}/actions/jobs/{job_id}",
        "html_url": f"https://github.com/{NATIVE_REPOSITORY}/actions/runs/{NATIVE_RUN_ID}/job/{job_id}",
        "name": f"unit-{index}",
        "status": "completed",
        "conclusion": "failure" if failed else "success",
        "started_at": "2026-10-13T12:00:00Z",
        "completed_at": "2026-10-13T12:01:00Z",
        "steps": [
            {
                "name": "Run tests",
                "status": "completed",
                "conclusion": "failure" if failed else "success",
                "number": 1,
                "started_at": "2026-10-13T12:00:01Z",
                "completed_at": "2026-10-13T12:00:59Z",
            },
        ],
    }


def _native_project(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "databaseId": job["id"],
        "status": job["status"],
        "conclusion": job["conclusion"] or "",
        "name": job["name"],
        "startedAt": job["started_at"] or NATIVE_ZERO_TIME,
        "completedAt": job["completed_at"] or NATIVE_ZERO_TIME,
        "url": job["html_url"],
        "steps": [
            {
                "name": step["name"],
                "status": step["status"],
                "conclusion": step["conclusion"] or "",
                "number": step["number"],
                "startedAt": step["started_at"] or NATIVE_ZERO_TIME,
                "completedAt": step["completed_at"] or NATIVE_ZERO_TIME,
            }
            for step in job["steps"] or []
        ],
    }


class NativeResponses:
    """Record bounded native transport calls without any network access."""

    def __init__(self, metadata: object, pages: object, *, jobs_rc: int = 0) -> None:
        self.metadata = metadata
        self.pages = pages
        self.jobs_rc = jobs_rc
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        assert kwargs == {"capture_output": True, "text": True, "timeout": 30, "check": False}
        if command[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.metadata), "")
        assert command[:2] == ["gh", "api"]
        return subprocess.CompletedProcess(
            command, self.jobs_rc, json.dumps(self.pages), "last page HTTP 502" if self.jobs_rc else ""
        )


def test_native_inventory_has_every_page_and_exact_run_view_fields() -> None:
    jobs = [_native_job(index, failed=index == 123) for index in range(1, 124)]
    pages = [{"total_count": len(jobs), "jobs": jobs[offset:offset + 50]} for offset in (0, 50, 100)]
    runner = NativeResponses(_native_metadata(), pages)

    payload = _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=runner)

    assert payload == {**_native_metadata(), "jobs": [_native_project(job) for job in jobs]}
    assert len(runner.calls) == 2
    assert runner.calls[0][:6] == ["gh", "run", "view", str(NATIVE_RUN_ID), "--repo", NATIVE_REPOSITORY]
    assert "jobs" not in runner.calls[0][-1].split(",")
    assert runner.calls[1] == [
        "gh", "api",
        f"repos/{NATIVE_REPOSITORY}/actions/runs/{NATIVE_RUN_ID}/attempts/{NATIVE_ATTEMPT}/jobs?per_page=50",
        "--paginate", "--slurp",
    ]
    ledger = _native_owner.new_ledger()
    _native_owner.observe_payload(ledger, payload, observed_at="2026-10-13T12:02:00Z")
    assert len(ledger["runs"][f"{NATIVE_RUN_ID}:{NATIVE_ATTEMPT}"]["jobs"]) == 123
    assert [family["job"] for family in ledger["families"].values()] == ["unit-123"]
    assert next(iter(ledger["families"].values()))["failed_steps"] == ["Run tests"]


def test_later_page_error_rejects_valid_partial_output_without_retry() -> None:
    runner = NativeResponses(_native_metadata(), [{"total_count": 51, "jobs": [_native_job()]}], jobs_rc=1)
    with pytest.raises(_native_owner.LedgerError, match="last page HTTP 502"):
        _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=runner)
    assert len(runner.calls) == 2


@pytest.mark.parametrize("pages", [
    {}, None, [], [None], [{}], [{"total_count": True, "jobs": []}],
    [{"total_count": -1, "jobs": []}], [{"total_count": "1", "jobs": [_native_job()]}],
    [{"total_count": 1.5, "jobs": [_native_job()]}], [{"total_count": 1, "jobs": {}}],
    [{"total_count": 1, "jobs": []}], [{"total_count": 0, "jobs": [_native_job()]}],
    [{"total_count": 2, "jobs": [_native_job()]}, {"total_count": 3, "jobs": [_native_job(2)]}],
    [{"total_count": 2, "jobs": [_native_job()]}, {"total_count": 2, "jobs": [_native_job()]}],
    [{"total_count": 1, "jobs": [None]}],
])
def test_native_inventory_rejects_malformed_incomplete_and_duplicate_pages(pages: object) -> None:
    with pytest.raises(_native_owner.LedgerError):
        _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=NativeResponses(_native_metadata(), pages))


@pytest.mark.parametrize(("field", "value"), [
    ("run_id", 102), ("run_id", None), ("id", 0), ("id", True), ("id", 1.5),
    ("run_attempt", NATIVE_ATTEMPT - 1), ("run_attempt", NATIVE_ATTEMPT + 1), ("run_attempt", True),
    ("head_sha", "b" * 40),
    ("run_url", f"https://api.github.com/repos/other/gludd/actions/runs/{NATIVE_RUN_ID}"),
    ("run_url", f"https://api.github.com/repos/{NATIVE_REPOSITORY}/actions/runs/102"),
    ("url", f"https://api.github.com/repos/{NATIVE_REPOSITORY}/actions/jobs/9002"),
    ("name", []), ("status", 4), ("conclusion", {}), ("steps", {}),
    ("steps", [None]), ("started_at", "not-a-time"), ("completed_at", 123),
])
def test_native_inventory_rejects_foreign_or_malformed_jobs(field: str, value: object) -> None:
    job = {**_native_job(), field: value}
    with pytest.raises(_native_owner.LedgerError):
        _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=NativeResponses(
            _native_metadata(), [{"total_count": 1, "jobs": [job]}]
        ))


@pytest.mark.parametrize(("field", "value"), [
    ("name", None), ("status", None), ("number", True), ("number", 0),
    ("number", "1"), ("number", 1.5), ("conclusion", []),
    ("started_at", "2026-10-13T12:00:00"), ("completed_at", "2026-10-99T12:00:00Z"),
])
def test_native_inventory_rejects_malformed_steps(field: str, value: object) -> None:
    job = _native_job()
    job["steps"][0][field] = value
    with pytest.raises(_native_owner.LedgerError):
        _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=NativeResponses(
            _native_metadata(), [{"total_count": 1, "jobs": [job]}]
        ))


@pytest.mark.parametrize(("field", "value"), [
    ("databaseId", 102), ("databaseId", True), ("attempt", 0), ("attempt", None),
    ("attempt", True), ("attempt", 2.5), ("headSha", "short"), ("headBranch", None),
    ("status", []), ("conclusion", []), ("workflowName", 123),
    ("url", f"https://github.com/other/gludd/actions/runs/{NATIVE_RUN_ID}"),
])
def test_run_metadata_errors_fail_before_job_lookup(field: str, value: object) -> None:
    runner = NativeResponses({**_native_metadata(), field: value}, [{"total_count": 1, "jobs": [_native_job()]}])
    with pytest.raises(_native_owner.LedgerError):
        _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=runner)
    assert len(runner.calls) == 1


@pytest.mark.parametrize("metadata", [[], None, {}, {**_native_metadata(), "jobs": []}])
def test_run_metadata_shape_must_match_requested_fields(metadata: object) -> None:
    runner = NativeResponses(metadata, [{"total_count": 1, "jobs": [_native_job()]}])
    with pytest.raises(_native_owner.LedgerError):
        _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=runner)
    assert len(runner.calls) == 1


@pytest.mark.parametrize("run_id", [0, -1, True, 1.5])
def test_invalid_numeric_identity_never_starts_transport(run_id: int) -> None:
    runner = NativeResponses(_native_metadata(), [])
    with pytest.raises(_native_owner.LedgerError):
        _native_owner.fetch_run(run_id, NATIVE_REPOSITORY, runner=runner)
    assert runner.calls == []


def test_empty_active_inventory_and_nulls_keep_cli_projection() -> None:
    metadata = {**_native_metadata(), "status": "queued", "conclusion": ""}
    empty = _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=NativeResponses(
        metadata, [{"total_count": 0, "jobs": []}]
    ))
    assert empty == {**metadata, "jobs": []}
    job = {**_native_job(), "status": "queued", "conclusion": None, "started_at": None,
           "completed_at": None, "steps": None}
    runner = NativeResponses(metadata, [{"total_count": 1, "jobs": [job]}])
    assert _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=runner) == {
        **metadata, "jobs": [_native_project(job)]
    }


def test_steps_preserve_order_offsets_and_nanoseconds_like_native_run_view() -> None:
    job = _native_job()
    step = deepcopy(job["steps"][0])
    step.update(number=2, conclusion=None, started_at=None, completed_at=None)
    job["steps"].insert(0, step)
    job["steps"][1]["started_at"] = "2026-10-13T09:42:40.000-08:00"
    job["steps"][1]["completed_at"] = "2026-10-13T12:00:59.123456789Z"
    job["started_at"] = "2026-10-13T12:00:00.120000Z"
    runner = NativeResponses(_native_metadata(), [{"total_count": 1, "jobs": [job]}])
    projected = _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=runner)["jobs"][0]
    assert projected["startedAt"] == "2026-10-13T12:00:00.12Z"
    assert [value["number"] for value in projected["steps"]] == [2, 1]
    assert projected["steps"][0]["conclusion"] == ""
    assert projected["steps"][0]["completedAt"] == NATIVE_ZERO_TIME
    assert projected["steps"][1]["startedAt"] == "2026-10-13T09:42:40-08:00"
    assert projected["steps"][1]["completedAt"] == "2026-10-13T12:00:59.123456789Z"


def test_attempt_endpoint_is_authoritative_when_api_omits_run_attempt() -> None:
    job = _native_job()
    del job["run_attempt"]
    runner = NativeResponses(_native_metadata(), [{"total_count": 1, "jobs": [job]}])
    assert _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=runner)["jobs"] == [_native_project(job)]


@pytest.mark.parametrize("stage", ["metadata", "jobs"])
def test_invalid_transport_json_is_rejected(stage: str) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if stage == "jobs" and len(calls) == 1:
            return subprocess.CompletedProcess(command, 0, json.dumps(_native_metadata()), "")
        return subprocess.CompletedProcess(command, 0, "{", "")

    with pytest.raises(_native_owner.LedgerError, match="invalid JSON"):
        _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=runner)
    assert len(calls) == (2 if stage == "jobs" else 1)


@pytest.mark.parametrize("kind", ["job", "step"])
def test_native_inventory_rejects_missing_required_fields(kind: str) -> None:
    job = _native_job()
    if kind == "job":
        del job["conclusion"]
    else:
        del job["steps"][0]["completed_at"]
    runner = NativeResponses(_native_metadata(), [{"total_count": 1, "jobs": [job]}])
    with pytest.raises(_native_owner.LedgerError, match="missing required fields"):
        _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=runner)


def test_native_inventory_normalizes_utc_offset_and_keeps_failed_annotation_ids() -> None:
    job = _native_job(failed=True)
    job["started_at"] = "2026-10-13T12:00:00.000+00:00"
    runner = NativeResponses(_native_metadata(), [{"total_count": 1, "jobs": [job]}])
    payload = _native_owner.fetch_run(NATIVE_RUN_ID, NATIVE_REPOSITORY, runner=runner)
    assert payload["jobs"][0]["startedAt"] == "2026-10-13T12:00:00Z"
    calls: list[tuple[int, str]] = []

    def annotations(job_id: int, repository: str) -> list[str]:
        calls.append((job_id, repository))
        return ["hosted runner error"]

    assert _native_owner.fetch_runner_acquisition_annotations(
        payload, NATIVE_REPOSITORY, fetcher=annotations
    ) == {9001: ["hosted runner error"]}
    assert calls == [(9001, NATIVE_REPOSITORY)]


DETAIL_RUN_ID = 38477108604
# Public Git object and content-addressed receipt identities, not credentials.
DETAIL_GIT_OBJECT_ID = "768eaf6e759215c4bf7735930b917f69e9ab484c"
DETAIL_RECEIPT_CONTENT_ID = "f7d45cf69445d9f222e4fccc44ed82bcc746dc3417865741c87f4ad4e73841a8"
DETAIL_STEP = "Known runtime and handoff regression admission"
DETAIL_FIRST = "2026-10-13T22:32:05Z"
DETAIL_LATER = "2026-10-13T22:37:00Z"


def _detail_payload() -> dict[str, Any]:
    """Freeze the actual 19-job observation without reading mutable operational data."""
    jobs = [
        ("version", "success"), ("e2e-providers", "skipped"), ("gate (3.12)", "failure"),
        ("claim-before-provision-acceptance", "success"),
        ("postgres-winner-compute-lifecycle-acceptance", "success"), ("gate (3.11)", "success"),
        ("release_source_proof", "skipped"), ("freellmapi-upstream-build", "skipped"),
        ("linux", "skipped"), ("game-building", "skipped"), ("coverage", "skipped"),
        ("container", "skipped"), ("termux", "skipped"), ("macos", "skipped"),
        ("molecule", "skipped"), ("test-shard", "skipped"), ("windows", "skipped"),
        ("ansible-ee", "skipped"), ("release", "skipped"),
    ]
    return {
        "attempt": 1, "databaseId": DETAIL_RUN_ID, "headSha": DETAIL_GIT_OBJECT_ID, "headBranch": "development",
        "status": "completed", "conclusion": "failure", "workflowName": "Build and Release",
        "url": f"https://github.com/sandboxcom/gludd/actions/runs/{DETAIL_RUN_ID}",
        "jobs": [
            {"name": name, "status": "completed", "conclusion": conclusion, "steps": []}
            for name, conclusion in jobs
        ],
    }


def _detail_enriched() -> dict[str, Any]:
    payload = _detail_payload()
    payload["jobs"][2]["steps"] = [{"name": DETAIL_STEP, "status": "completed", "conclusion": "failure"}]
    return payload


def _detail_seed(module: ModuleType) -> dict[str, Any]:
    ledger: dict[str, Any] = module.new_ledger()
    assert module.observe_payload(ledger, _detail_payload(), observed_at=DETAIL_FIRST) == "recorded"
    assert ledger["runs"][str(DETAIL_RUN_ID)]["payload_fingerprint"] == DETAIL_RECEIPT_CONTENT_ID
    return ledger


@pytest.mark.parametrize("history", ["open", "repaired", "resolved"])
def test_native_cli_accepts_exact_late_step_detail_and_retains_all_original_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, history: str,
) -> None:
    module = _load()
    ledger = _detail_seed(module)
    if history == "repaired":
        module.record_repair(
            ledger, list(ledger["families"]), repair_sha="b" * 40,
            evidence="make test-count", recorded_at="repair", is_ancestor=lambda _old, _new: True,
        )
    elif history == "resolved":
        success = _detail_payload()
        success.update(databaseId=DETAIL_RUN_ID + 1, headSha="b" * 40, conclusion="success")
        success["url"] = f"https://github.com/sandboxcom/gludd/actions/runs/{DETAIL_RUN_ID + 1}"
        success["jobs"][2]["conclusion"] = "success"
        module.observe_payload(ledger, success, observed_at="hosted-success")
    original = deepcopy(ledger)
    path = tmp_path / "ledger.json"
    module.write_ledger(path, ledger)
    monkeypatch.setattr(module, "fetch_run", lambda _run, _repo: _detail_enriched())
    monkeypatch.setattr(module, "_now", lambda: DETAIL_LATER)
    command = ["observe", "--run", str(DETAIL_RUN_ID), "--ledger", str(path)]

    assert module.main(command) == 0

    current = module.read_ledger(path)
    assert current["runs"] == original["runs"]
    assert current["families"] == original["families"]
    detail = current["terminal_details"][str(DETAIL_RUN_ID)]
    assert detail == {
        "schema_version": 1, "original_payload_fingerprint": DETAIL_RECEIPT_CONTENT_ID,
        "payload_fingerprint": module._digest(module._snapshot(_detail_enriched())),
        "observed_at": DETAIL_LATER, "jobs": module._snapshot(_detail_enriched())["jobs"],
    }
    assert "result=enriched" in capsys.readouterr().out
    assert "unchanged failed run" in " ".join(module.guard_rerun(current, DETAIL_RUN_ID))
    if history == "open":
        assert len(module.guard_push(current, "b" * 40, is_ancestor=lambda _old, _new: True)) == 1
    encoded = path.read_bytes()
    monkeypatch.setattr(module, "_now", lambda: "2026-10-13T23:00:00Z")
    assert module.main(command) == 0
    assert "result=unchanged" in capsys.readouterr().out
    assert path.read_bytes() == encoded


@pytest.mark.parametrize("change", [
    "sha", "branch", "workflow", "repository", "outcome", "job-outcome", "job-name",
    "job-status", "job-order", "job-drop", "job-add", "known-step-add", "known-step-rename", "known-step-drop",
])
def test_accepted_detail_never_permits_identity_outcome_or_known_step_mutation(change: str) -> None:
    module = _load()
    ledger = _detail_seed(module)
    module.observe_payload(ledger, _detail_enriched(), observed_at=DETAIL_LATER)
    original = deepcopy(ledger)
    payload = _detail_enriched()
    fields = {"sha": "headSha", "branch": "headBranch", "workflow": "workflowName", "repository": "url"}
    if change in fields:
        payload[fields[change]] = "b" * 40 if change == "sha" else "changed"
    elif change == "outcome":
        payload["conclusion"] = "success"
    elif change == "job-outcome":
        payload["jobs"][2]["conclusion"] = "success"
    elif change == "job-name":
        payload["jobs"][2]["name"] = "changed"
    elif change == "job-status":
        payload["jobs"][2]["status"] = "queued"
    elif change == "job-order":
        payload["jobs"].reverse()
    elif change == "job-drop":
        payload["jobs"].pop()
    elif change == "job-add":
        payload["jobs"].append(deepcopy(payload["jobs"][-1]))
    elif change == "known-step-add":
        payload["jobs"][2]["steps"].append({"name": "unexpected", "conclusion": "failure"})
    elif change == "known-step-rename":
        payload["jobs"][2]["steps"][0]["name"] = "unexpected"
    else:
        payload["jobs"][2]["steps"] = []

    with pytest.raises(module.LedgerError):
        module.observe_payload(ledger, payload, observed_at="later")
    assert ledger == original


@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out", "skipped", "neutral", "success"])
def test_only_missing_failure_steps_qualify_for_enrichment(conclusion: str) -> None:
    module = _load()
    ledger = module.new_ledger()
    baseline = _detail_payload()
    baseline["jobs"][2]["conclusion"] = conclusion
    module.observe_payload(ledger, baseline, observed_at=DETAIL_FIRST)
    original = deepcopy(ledger)
    enriched = deepcopy(baseline)
    enriched["jobs"][2]["steps"] = _detail_enriched()["jobs"][2]["steps"]

    with pytest.raises(module.LedgerError, match="immutable terminal run"):
        module.observe_payload(ledger, enriched, observed_at=DETAIL_LATER)
    assert ledger == original


def test_enrichment_is_monotonic_bounded_and_distinct_for_each_attempt() -> None:
    module = _load()
    ledger = module.new_ledger()
    baseline = _detail_payload()
    baseline["jobs"][5]["conclusion"] = "failure"
    module.observe_payload(ledger, baseline, observed_at=DETAIL_FIRST)
    original = deepcopy(ledger)
    first = deepcopy(baseline)
    first["jobs"][2]["steps"] = _detail_enriched()["jobs"][2]["steps"]
    assert module.observe_payload(ledger, first, observed_at=DETAIL_LATER) == "enriched"
    second = deepcopy(first)
    second["jobs"][5]["steps"] = [{"name": "second job detail", "conclusion": "failure"}]
    assert module.observe_payload(ledger, second, observed_at="second") == "enriched"
    assert module.observe_payload(ledger, second, observed_at="again") == "unchanged"
    assert ledger["runs"] == original["runs"]
    assert ledger["families"] == original["families"]
    assert len(ledger["terminal_details"]) == 1
    retry = _detail_payload()
    retry["attempt"] = 2
    assert module.observe_payload(ledger, retry, observed_at="retry") == "recorded"
    retry_detail = _detail_enriched()
    retry_detail["attempt"] = 2
    assert module.observe_payload(ledger, retry_detail, observed_at="retry-detail") == "enriched"
    assert set(ledger["terminal_details"]) == {str(DETAIL_RUN_ID), f"{DETAIL_RUN_ID}:2"}
    assert ledger["runs"][str(DETAIL_RUN_ID)] == original["runs"][str(DETAIL_RUN_ID)]


@pytest.mark.parametrize("corruption", [
    "map", "entry", "version", "boolean-version", "extra-field", "missing-field", "unknown-run",
    "binding", "digest", "jobs-type", "job-type", "outcome", "no-refinement", "original-receipt",
])
def test_optional_terminal_detail_schema_rejects_malformed_or_stale_receipt_bindings(corruption: str) -> None:
    module = _load()
    ledger = _detail_seed(module)
    module.observe_payload(ledger, _detail_enriched(), observed_at=DETAIL_LATER)
    details = ledger["terminal_details"]
    detail = details[str(DETAIL_RUN_ID)]
    if corruption == "map":
        ledger["terminal_details"] = []
    elif corruption == "entry":
        details[str(DETAIL_RUN_ID)] = []
    elif corruption == "version":
        detail["schema_version"] = 2
    elif corruption == "boolean-version":
        detail["schema_version"] = True
    elif corruption == "extra-field":
        detail["extra"] = True
    elif corruption == "missing-field":
        detail.pop("observed_at")
    elif corruption == "unknown-run":
        details["999"] = details.pop(str(DETAIL_RUN_ID))
    elif corruption == "binding":
        detail["original_payload_fingerprint"] = "0" * 64
    elif corruption == "digest":
        detail["payload_fingerprint"] = "0" * 64
    elif corruption == "jobs-type":
        detail["jobs"] = {}
    elif corruption == "job-type":
        detail["jobs"][2] = "bad job"
    elif corruption == "outcome":
        detail["jobs"][2]["conclusion"] = "success"
    elif corruption == "no-refinement":
        detail["jobs"] = deepcopy(ledger["runs"][str(DETAIL_RUN_ID)]["jobs"])
    else:
        ledger["runs"][str(DETAIL_RUN_ID)]["branch"] = "rewritten"

    with pytest.raises(module.LedgerError, match="terminal detail"):
        module.validate_ledger(ledger)


def test_optional_details_preserve_legacy_version_one_and_existing_size_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any,
) -> None:
    module = _load()
    ledger = _detail_seed(module)
    assert "terminal_details" not in ledger
    path = tmp_path / "ledger.json"
    module.write_ledger(path, ledger)
    original = path.read_bytes()
    enriched = _detail_enriched()
    enriched["jobs"][2]["steps"][0]["name"] = "x" * module.MAX_LEDGER_BYTES
    monkeypatch.setattr(module, "fetch_run", lambda _run, _repo: enriched)

    assert module.main(["observe", "--run", str(DETAIL_RUN_ID), "--ledger", str(path)]) == 2
    assert "2 MiB" in capsys.readouterr().err
    assert path.read_bytes() == original


def _fresh_filelock_read_probe(
    tmp_path: Path, ledger_import_style: str, body: str,
) -> subprocess.CompletedProcess[str]:
    """Use a fresh real interpreter, never a cached or substituted FileLock module."""
    source_path = ROOT if ledger_import_style == "package" else ROOT / "scripts"
    program = f"""
import importlib
import importlib.abc
import json
import runpy
import sys
from pathlib import Path
assert not any(name == "filelock" or name.startswith("filelock.") for name in sys.modules)
attempts = []
class RejectFileLock(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "filelock" or fullname.startswith("filelock."):
            attempts.append(fullname)
            raise ModuleNotFoundError("read-only path requested FileLock", name=fullname)
        return None
sys.meta_path.insert(0, RejectFileLock())
sys.path.insert(0, {str(source_path)!r})
exit_code = 0
""" + textwrap.dedent(body) + """
assert attempts == [], attempts
assert not any(name == "filelock" or name.startswith("filelock.") for name in sys.modules)
print("LEDGER_READ_IMPORT_GUARD_PASS")
raise SystemExit(exit_code)
"""
    return subprocess.run(
        [sys.executable, "-W", "error", "-c", program],
        cwd=tmp_path, check=False, capture_output=True, text=True, timeout=30,
    )


def _importguard_ledger_inputs(tmp_path: Path) -> dict[str, Path]:
    ledger = _native_owner.new_ledger()
    _native_owner.observe_payload(ledger, _failed_payload(), observed_at="import-boundary")
    encoded = json.dumps(ledger).encode("utf-8")
    paths = {name: tmp_path / f"{name}.json" for name in (
        "missing", "valid", "bounded", "malformed", "schema", "oversized",
    )}
    paths["valid"].write_bytes(encoded)
    paths["bounded"].write_bytes(
        encoded + b" " * (_native_owner.MAX_LEDGER_BYTES - len(encoded)),
    )
    paths["malformed"].write_text("{", encoding="utf-8")
    paths["schema"].write_text('{"version": 999, "runs": {}, "families": {}}', encoding="utf-8")
    paths["oversized"].write_bytes(b" " * (_native_owner.MAX_LEDGER_BYTES + 1))
    return paths


@pytest.mark.parametrize("ledger_import_style", ("package", "flat"))
def test_fresh_readonly_ledger_inputs_and_guards_do_not_import_filelock(
    tmp_path: Path, ledger_import_style: str,
) -> None:
    paths = _importguard_ledger_inputs(tmp_path)
    before = {path: path.read_bytes() for path in paths.values() if path.exists()}
    module_name = "scripts.ci_failure_ledger" if ledger_import_style == "package" else "ci_failure_ledger"
    invalid = [
        (str(paths["malformed"]), "ledger is invalid JSON"),
        (str(paths["schema"]), "unsupported ledger version"),
        (str(paths["oversized"]), "2 MiB safety bound"),
    ]
    result = _fresh_filelock_read_probe(tmp_path, ledger_import_style, f"""
        owner = importlib.import_module({module_name!r})
        expected = json.loads(Path({str(paths["valid"])!r}).read_text(encoding="utf-8"))
        assert owner.read_ledger(Path({str(paths["missing"])!r})) == owner.new_ledger()
        for path in {tuple(str(paths[key]) for key in ("valid", "bounded"))!r}:
            assert owner.read_ledger(Path(path)) == expected
        for path, message in {invalid!r}:
            try:
                owner.read_ledger(Path(path))
            except owner.LedgerError as error:
                assert message in str(error), str(error)
            else:
                raise AssertionError("invalid reader input was accepted")
        assert "open=3 repaired=0 resolved=0 runs=1" in owner.status_lines(expected)[-1]
        assert len(owner.guard_push(expected, {"b" * 40!r}, is_ancestor=lambda old, new: True)) == 3
        assert "unchanged failed run" in " ".join(owner.guard_rerun(expected, 101))
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LEDGER_READ_IMPORT_GUARD_PASS" in result.stdout
    assert not paths["missing"].exists()
    assert all(path.read_bytes() == content for path, content in before.items())
    assert all(not path.with_name(f".{path.name}.lock").exists() for path in paths.values())


@pytest.mark.parametrize("ledger_import_style", ("package", "flat", "script"))
@pytest.mark.parametrize(("ledger_input_case", "expected_exit", "expected_output"), (
    ("missing", 0, "CI_FAILURE_LEDGER_INACTIVE"),
    ("valid", 1, "open=3 repaired=0 resolved=0 runs=1"),
    ("bounded", 1, "open=3 repaired=0 resolved=0 runs=1"),
    ("malformed", 2, "ledger is invalid JSON"),
    ("schema", 2, "unsupported ledger version"),
    ("oversized", 2, "2 MiB safety bound"),
))
def test_fresh_native_ledger_status_does_not_import_filelock(
    tmp_path: Path, ledger_import_style: str, ledger_input_case: str,
    expected_exit: int, expected_output: str,
) -> None:
    paths = _importguard_ledger_inputs(tmp_path)
    path = paths[ledger_input_case]
    before = path.read_bytes() if path.exists() else None
    module_name = "scripts.ci_failure_ledger" if ledger_import_style == "package" else "ci_failure_ledger"
    entry = (
        f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')"
        if ledger_import_style == "script"
        else f"runpy.run_module({module_name!r}, run_name='__main__')"
    )
    result = _fresh_filelock_read_probe(tmp_path, ledger_import_style, f"""
        sys.argv = [{str(SCRIPT)!r}, "status", "--ledger", {str(path)!r}]
        try:
            {entry}
        except SystemExit as error:
            exit_code = error.code
    """)
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert "LEDGER_READ_IMPORT_GUARD_PASS" in result.stdout, result.stdout + result.stderr
    assert expected_output in result.stdout + result.stderr
    assert (path.read_bytes() if path.exists() else None) == before
    assert not path.with_name(f".{path.name}.lock").exists()


@pytest.mark.parametrize("ledger_import_style", ("package", "flat"))
def test_fresh_ledger_validation_commands_do_not_import_filelock(
    tmp_path: Path, ledger_import_style: str,
) -> None:
    module_name = "scripts.ci_failure_ledger" if ledger_import_style == "package" else "ci_failure_ledger"
    ledger_path = tmp_path / "validation-must-not-create" / "ledger.json"
    result = _fresh_filelock_read_probe(tmp_path, ledger_import_style, f"""
        owner = importlib.import_module({module_name!r})
        commands = [
            ["observe", "--run", "7"],
            ["observe-sha", "--sha", {"0" * 40!r}],
            ["status"],
            ["guard-rerun", "--run", "7"],
            ["guard-runner-acquisition-rerun", "--run", "7"],
            ["repair", "--all-open", "--sha", {"0" * 40!r}, "--evidence-target", "test-count"],
            ["guard-push", "--head", {"0" * 40!r}],
        ]
        for command in commands:
            assert owner.main(command + ["--validate-only", "--ledger", {str(ledger_path)!r}]) == 0
        assert not Path({str(ledger_path.parent)!r}).exists()
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LEDGER_READ_IMPORT_GUARD_PASS" in result.stdout
    assert "CI_FAILURE_LEDGER_OBSERVE run=7" in result.stdout
    assert "VALIDATE_ONLY_PASS" in result.stdout
    assert not ledger_path.parent.exists()


@pytest.mark.parametrize("ledger_import_style", ("package", "flat", "script"))
def test_fresh_real_pipeline_plan_does_not_import_filelock(
    tmp_path: Path, ledger_import_style: str,
) -> None:
    pipeline = ROOT / "scripts" / "pipeline_status.py"
    module_name = "scripts.pipeline_status" if ledger_import_style == "package" else "pipeline_status"
    entry = (
        f"runpy.run_path({str(pipeline)!r}, run_name='__main__')"
        if ledger_import_style == "script"
        else f"runpy.run_module({module_name!r}, run_name='__main__')"
    )
    snapshot = tmp_path / "plan-must-not-publish.json"
    ledger_path = tmp_path / "plan-ledger-must-not-exist.json"
    result = _fresh_filelock_read_probe(tmp_path, ledger_import_style, f"""
        sys.argv = [{str(pipeline)!r}, "status", "--validate-only", "--sha", {"a" * 40!r},
                    "--snapshot-output", {str(snapshot)!r}, "--failure-ledger", {str(ledger_path)!r}]
        try:
            {entry}
        except SystemExit as error:
            exit_code = error.code
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LEDGER_READ_IMPORT_GUARD_PASS" in result.stdout
    assert "PIPELINE_STATUS_VALIDATE_ONLY_PASS" in result.stdout
    assert not snapshot.exists() and not ledger_path.exists()
