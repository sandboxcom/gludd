"""Behavioral contract for durable, all-failure CI ownership."""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

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
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    view = _target_block(source, "ci-view")
    rerun = _target_block(source, "ci-rerun")
    push = _target_block(source, "ci-failure-push-guard")

    assert "scripts/ci_failure_ledger.py observe" in view
    assert "ci-rerun: ci-view" in source
    assert "scripts/ci_failure_ledger.py guard-rerun" in rerun
    assert rerun.index("guard-rerun") < rerun.index("gh run rerun")
    assert "CI_RERUN_ALLOW_UNCHANGED" in rerun
    assert "CI_RERUN_REASON" in rerun
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

    assert [module.main(command) for command in commands] == [0, 0, 0, 0, 0, 0]
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
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, 9, "", ""),
    )
    assert module.main([*base, "--all-open", "--evidence-target", "test-count"]) == 2
    assert "evidence failed" in capsys.readouterr().err


def test_fetch_run_is_id_bound_and_fail_closed() -> None:
    module = _load()
    payload = _failed_payload()
    calls: list[list[str]] = []

    def success(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    assert module.fetch_run(101, "sandboxcom/gludd", runner=success) == payload
    assert calls[0][:4] == ["gh", "run", "view", "101"]
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
    wrong = dict(payload, databaseId=102)
    with pytest.raises(module.LedgerError, match="different run identity"):
        module.fetch_run(
            101,
            "sandboxcom/gludd",
            runner=lambda command, **_: subprocess.CompletedProcess(
                command, 0, json.dumps(wrong), ""
            ),
        )


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
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = _target_block(source, "pipeline-status")
    assert "scripts/ci_failure_ledger.py observe-sha" in block
    assert block.index("observe-sha") < block.index("scripts/pipeline_status.py")
    assert "OBSERVE_RC" in block
    assert "STATUS_RC" in block

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
