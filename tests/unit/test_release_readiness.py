from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from general_ludd.review.release_forecast import Blocker, RunObservation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import release_readiness as rr  # noqa: E402


def _completed(
    argv: Sequence[str],
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def test_exit_codes_are_stable_and_prioritized() -> None:
    assert rr._exit_code(rr.Readiness()) == rr.EXIT_ERROR
    assert rr._exit_code(rr.Readiness(errors=["CI evidence is not a successful run"])) == rr.EXIT_CI
    assert rr._exit_code(rr.Readiness(errors=["worktree has 1 dirty path(s)"])) == rr.EXIT_DIRTY
    assert rr._exit_code(
        rr.Readiness(errors=["detached or unintegrated sibling worktree/branch exists"])
    ) == rr.EXIT_WORKTREE
    assert rr._exit_code(rr.Readiness(errors=["project version files are inconsistent"])) == rr.EXIT_VERSION
    assert rr._exit_code(rr.Readiness(errors=["TASKS.md ledger validation failed"])) == rr.EXIT_TASKS
    assert rr._exit_code(
        rr.Readiness(errors=["unmanaged local inference process is running"])
    ) == rr.EXIT_RESOURCE


def test_unmanaged_local_inference_detection_requires_daemon_ancestor() -> None:
    process_table = (
        "  101     1 /project/.venv/bin/python -m general_ludd.cli daemon\n"
        "  102   101 /project/.venv/bin/python -m llama_cpp.server --port 12001\n"
        "46215     1 /project/.venv/bin/python -m llama_cpp.server --port 9999\n"
        "  103     1 /usr/bin/python unrelated.py\n"
    )

    def run(
        argv: Sequence[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert list(argv) == ["ps", "-ax", "-o", "pid=,ppid=,command="]
        return _completed(argv, process_table)

    assert rr._unmanaged_local_inference_processes(run) == [
        {
            "pid": 46215,
            "ppid": 1,
            "command": "/project/.venv/bin/python -m llama_cpp.server --port 9999",
        }
    ]


def test_main_emits_machine_readable_diagnostics(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = rr.Readiness(head="abc123", errors=["CI evidence is not a successful run"])
    monkeypatch.setattr(rr, "assess", lambda **_: blocked)

    assert rr.main([]) == rr.EXIT_CI
    payload = json.loads(capsys.readouterr().out)
    assert payload["head"] == "abc123"
    assert payload["ready"] is False
    assert payload["exit_code"] == rr.EXIT_CI
    assert payload["remediation"]["schema_version"] == 1
    assert payload["remediation"]["recheck_argv"] == [
        "make",
        "release-readiness",
        f"TAG={rr.DEFAULT_RELEASE_TAG}",
        "RELEASE_READINESS_VALIDATE_ONLY=0",
        "RELEASE_COMPLETED_STAGES=",
        "RELEASE_OBSERVATIONS=",
    ]


def test_release_policy_preflight_uses_canonical_bounded_make_invocation(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def run(
        argv: Sequence[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        assert cwd == str(tmp_path)
        return _completed(argv, "SERIAL-SHARD-VALIDATE policy=canonical\n")

    compatible, detail = rr._release_policy_preflight(run, tmp_path)

    assert compatible
    assert detail == "SERIAL-SHARD-VALIDATE policy=canonical"
    assert calls == [
        [
            "make",
            "--no-print-directory",
            "test-ci-dual-track-local",
            "DUAL_TRACK_LOCAL_VALIDATE_ONLY=1",
            "PYTEST_ARGS=",
            "MAX_FILES_PER_BATCH=64",
        ]
    ]


def test_detached_worktree_detection_reuses_porcelain_parser(tmp_path: Path) -> None:
    porcelain = (
        f"worktree {tmp_path}\nHEAD current\nbranch refs/heads/development\n\n"
        f"worktree {tmp_path / 'detached'}\nHEAD detached\ndetached\n"
    )

    def run(
        argv: Sequence[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "worktree", "list", "--porcelain"]:
            return _completed(args, porcelain)
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return _completed(args, str(tmp_path) + "\n")
        raise AssertionError(args)

    detached = rr._detached_worktrees(run, tmp_path)
    assert detached == [{"path": str(tmp_path / "detached"), "head": "detached"}]


def test_assess_passes_when_all_release_evidence_is_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import workflow_state_guard

    state = SimpleNamespace(
        branch="development",
        head="abc123",
        dirty_count=0,
        unintegrated_worktrees=[],
        unintegrated_branches=[],
    )
    monkeypatch.setattr(workflow_state_guard, "collect_state", lambda **_: state)
    monkeypatch.setattr(rr, "_detached_worktrees", lambda *_: [])
    monkeypatch.setattr(rr, "_ci_verdict", lambda *_: ("GREEN", "CI GREEN"))
    monkeypatch.setattr(rr, "_version_check", lambda *_: (True, "OK: 0.1.0-beta.3"))
    monkeypatch.setattr(rr, "_incomplete_tasks", lambda *_: [])
    monkeypatch.setattr(rr, "_ledger_check", lambda *_: (True, "OK"))
    monkeypatch.setattr(rr, "_tasks_tick_check", lambda *_: (True, "OK"))

    result = rr.assess(root=tmp_path, run=lambda *_: _completed([]))
    assert result.ready
    assert result.head == "abc123"


def test_assess_fails_closed_for_noncanonical_local_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import workflow_state_guard

    state = SimpleNamespace(
        branch="development",
        head="abc123",
        dirty_count=0,
        unintegrated_worktrees=[],
        unintegrated_branches=[],
    )
    monkeypatch.setattr(workflow_state_guard, "collect_state", lambda **_: state)
    monkeypatch.setattr(rr, "_release_policy_preflight", lambda *_: (False, "rejected"))
    monkeypatch.setattr(rr, "_detached_worktrees", lambda *_: [])
    monkeypatch.setattr(rr, "_ci_verdict", lambda *_: ("GREEN", "CI GREEN"))
    monkeypatch.setattr(rr, "_version_check", lambda *_: (True, "OK"))
    monkeypatch.setattr(rr, "_incomplete_tasks", lambda *_: [])
    monkeypatch.setattr(rr, "_ledger_check", lambda *_: (True, "OK"))

    result = rr.assess(root=tmp_path, run=lambda *_: _completed([]))

    assert not result.ready
    assert result.release_policy_detail == "rejected"
    assert result.errors == [
        "local dual-track producer execution policy is not canonical"
    ]


def test_assess_fails_closed_for_dirty_and_unintegrated_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import workflow_state_guard

    state = SimpleNamespace(
        branch="development",
        head="abc123",
        dirty_count=2,
        unintegrated_worktrees=[{"path": "/tmp/other", "reasons": ["dirty"]}],
        unintegrated_branches=[],
    )
    monkeypatch.setattr(workflow_state_guard, "collect_state", lambda **_: state)
    monkeypatch.setattr(rr, "_detached_worktrees", lambda *_: [])
    monkeypatch.setattr(rr, "_ci_verdict", lambda *_: ("GREEN", "CI GREEN"))
    monkeypatch.setattr(rr, "_version_check", lambda *_: (True, "OK"))
    monkeypatch.setattr(rr, "_incomplete_tasks", lambda *_: [])
    monkeypatch.setattr(rr, "_ledger_check", lambda *_: (True, "OK"))
    monkeypatch.setattr(rr, "_tasks_tick_check", lambda *_: (True, "OK"))

    result = rr.assess(root=tmp_path, run=lambda *_: _completed([]))
    assert not result.ready
    assert any("dirty" in error for error in result.errors)
    assert any("unintegrated" in error for error in result.errors)


def test_incomplete_tasks_uses_current_policy_and_excludes_release_action(tmp_path: Path) -> None:
    tag = rr.DEFAULT_RELEASE_TAG
    prefix = rr.RELEASE_TASK_PREFIXES[tag][0]
    release_action = next(iter(rr.RELEASE_ACTION_TASKS[tag]))
    pending_task = f"{prefix}997"
    completed_task = f"{prefix}996"
    (tmp_path / "TASKS.md").write_text(
        "## Current Session\n"
        f"- [ ] {pending_task} — build release artifacts\n"
        f"- [x] {completed_task} — local model certified\n"
        f"- [ ] {release_action} — perform the release after readiness\n"
        "- [ ] T-BETA3-RELEASE — obsolete beta3 task\n"
        "- [ ] OTHER-1 — unrelated\n",
        encoding="utf-8",
    )
    assert rr._incomplete_tasks(tmp_path, tag=tag) == [pending_task]


def test_incomplete_tasks_fails_closed_without_task_ledger(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"TASKS\.md is missing"):
        rr._incomplete_tasks(tmp_path)


def test_tasks_tick_check_rejects_checked_pending_evidence(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text(
        "- [x] S86.1 — done | evidence: make gate pending abc1234\n",
        encoding="utf-8",
    )

    passed, detail = rr._tasks_tick_check(tmp_path)

    assert not passed
    assert "Forbidden word 'pending'" in detail


def test_assess_stops_before_external_evidence_when_task_ticks_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import workflow_state_guard

    (tmp_path / "TASKS.md").write_text(
        "- [x] S86.1 — done | evidence: make gate pending abc1234\n",
        encoding="utf-8",
    )
    called = False

    def collect_state(**_: object) -> SimpleNamespace:
        nonlocal called
        called = True
        raise AssertionError("external evidence must not run")

    monkeypatch.setattr(workflow_state_guard, "collect_state", collect_state)

    result = rr.assess(root=tmp_path, run=lambda *_: _completed([]))

    assert not called
    assert any(
        "checked TASKS.md completion evidence is invalid" in error
        for error in result.errors
    )


def test_prunable_registration_remediation_is_owner_gated_and_validate_first() -> None:
    result = rr.Readiness(
        unintegrated_worktrees=[
            {
                "branch": "stale-feature",
                "path": "/tmp/gludd-worktrees/stale-feature",
                "reasons": ["prunable_registration"],
            },
            {
                "branch": "dirty-feature",
                "path": "/tmp/gludd-worktrees/dirty-feature",
                "reasons": ["dirty"],
            },
        ]
    )

    plan = rr.build_remediation_plan(result, tag=rr.DEFAULT_RELEASE_TAG)

    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.code == "prunable_worktree_registration"
    assert step.blockers == ("stale-feature@/tmp/gludd-worktrees/stale-feature",)
    assert step.validate_argv == (
        "make",
        "wt-prune-safe",
        "ACTIVE_WORKSTREAM_REGISTRY=",
        "WT_PRUNE_VALIDATE_ONLY=1",
    )
    assert step.owner_release_argv == (
        ("make", "workstream-unregister", "BRANCH=stale-feature"),
    )
    assert step.apply_argv is not None
    assert step.apply_argv[-1] == "WT_PRUNE_VALIDATE_ONLY=0"
    assert step.requires_owner_confirmation is True
    assert result.ready is False


def test_incomplete_release_task_remediation_never_mutates_the_ledger() -> None:
    result = rr.Readiness(incomplete_release_tasks=["S86.2", "S86.1", "S86.2"])

    plan = rr.build_remediation_plan(result, tag=rr.DEFAULT_RELEASE_TAG)

    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.code == "incomplete_release_tasks"
    assert step.blockers == ("S86.1", "S86.2")
    assert step.validate_argv == ("make", "validate-task-ledger")
    assert step.owner_release_argv == ()
    assert step.apply_argv is None
    assert step.requires_owner_confirmation is True
    assert "must not be checked merely to clear readiness" in step.resolution


def test_nonprunable_worktree_never_receives_cleanup_instructions() -> None:
    result = rr.Readiness(
        unintegrated_worktrees=[
            {
                "branch": "valuable-feature",
                "path": "/tmp/gludd-worktrees/valuable-feature",
                "reasons": ["dirty", "head_not_merged"],
            },
            {
                "branch": "ambiguous-feature",
                "path": "/tmp/gludd-worktrees/ambiguous-feature",
                "reasons": ["prunable_registration", "dirty"],
            },
        ]
    )

    assert rr.build_remediation_plan(
        result, tag=rr.DEFAULT_RELEASE_TAG
    ).steps == ()


def test_coverage_gap_reader_fails_closed_on_missing_or_invalid_shapes(
    tmp_path: Path,
) -> None:
    assert rr._coverage_gap_modules(tmp_path) == ()
    config = tmp_path / "config"
    config.mkdir()
    baseline = config / "coverage_gaps_baseline.json"

    for payload in ("{", "[]", '{"allowed_gaps": "not-a-list"}'):
        baseline.write_text(payload, encoding="utf-8")
        assert rr._coverage_gap_modules(tmp_path) == ()

    baseline.write_text(
        '{"allowed_gaps": ["module.b", 7, "module.a", "module.a"]}',
        encoding="utf-8",
    )
    assert rr._coverage_gap_modules(tmp_path) == ("module.a", "module.b")


def test_forecast_has_no_blockers_when_release_evidence_is_complete() -> None:
    result = rr.Readiness(
        ci_head_matches=True,
        ci_verdict="GREEN",
        version_consistent=True,
        ledger_valid=True,
        release_policy_compatible=True,
    )

    assert rr._forecast_blockers(result) == ()


def test_release_eta_uses_gludd_calibration_and_parallel_critical_path() -> None:
    estimate = rr.estimate_release_eta(
        completed_stages={"readiness_fix"},
        observations={
            "local_dual_track": [80.0],
            "hosted_ci": [35.0],
            "full_gate": [55.0],
        },
    )

    assert estimate.p50_minutes == pytest.approx(175.0)
    assert estimate.p90_minutes == pytest.approx(227.5)
    assert estimate.critical_path == [
        "candidate_commit",
        "local_dual_track+hosted_ci",
        "full_gate",
        "release_dry_run",
        "promotion_and_publish",
    ]
    dual_track = next(stage for stage in estimate.stages if stage.name == "local_dual_track")
    assert dual_track.calibrated_minutes == pytest.approx(80.0)
    assert dual_track.source == "gludd-calibrated"


def test_release_eta_zeroes_when_every_stage_is_complete() -> None:
    estimate = rr.estimate_release_eta(
        completed_stages=set(rr.RELEASE_STAGE_BASELINES),
    )
    assert estimate.p50_minutes == 0
    assert estimate.p90_minutes == 0
    assert estimate.critical_path == []


@pytest.mark.parametrize(
    ("completed", "observations", "message"),
    [
        ({"unknown"}, {}, "unknown release stage"),
        (set(), {"unknown": [1.0]}, "unknown release stage"),
        (set(), {"hosted_ci": [0.0]}, "observations must be positive"),
    ],
)
def test_release_eta_rejects_invalid_stage_evidence(
    completed: set[str],
    observations: dict[str, list[float]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        rr.estimate_release_eta(
            completed_stages=completed,
            observations=observations,
        )


def test_forecast_public_types_are_owned_by_release_forecast_module() -> None:
    assert Blocker.__module__ == "general_ludd.review.release_forecast"
    assert RunObservation.__module__ == "general_ludd.review.release_forecast"
    for name in (
        "Blocker",
        "CanaryItem",
        "Priority",
        "RunObservation",
        "StagePlan",
        "build_forecast",
        "load_observations",
    ):
        assert name not in rr.__dict__


def test_release_eta_emits_empirical_risk_and_canary_plan() -> None:
    history = (
        RunObservation(
            run_id="gha-late-unit-1b",
            phase="hosted_ci",
            lane="gha",
            duration_minutes=44.0,
            succeeded=False,
            failure_class="unit-regression",
            failing_node="tests/unit/test_cloud.py::test_late_unit_1b",
            node_order=930,
            total_nodes=1000,
            platform="linux",
            python_version="3.11",
        ),
        RunObservation(
            run_id="gha-green",
            phase="hosted_ci",
            lane="gha",
            duration_minutes=36.0,
            succeeded=True,
            platform="linux",
            python_version="3.11",
        ),
    )
    blockers = (
        Blocker(
            code="hosted-unit-regression",
            phase="hosted_ci",
            repair_minutes=8.0,
            failure_class="unit-regression",
            platform_gaps=("linux/python-3.11",),
            artifacts=("smoke-attestations",),
        ),
    )

    estimate = rr.estimate_release_eta(
        historical_observations=history,
        blockers=blockers,
        coverage_gap_modules=("general_ludd.cloud.model_pipeline",),
    )

    assert estimate.calibration_sample_count == 2
    assert estimate.method == "empirical-critical-path-v1"
    assert estimate.hosted_canary[0].node.endswith("test_late_unit_1b")
    assert estimate.hosted_canary[0].canary_order == 1
    assert estimate.risk_priorities[0].code == "hosted-unit-regression"
    assert estimate.replay_gaps == ["linux/python-3.11"]
    assert estimate.coverage_gaps == ["general_ludd.cloud.model_pipeline"]


def test_readiness_main_loads_bounded_history_and_current_blockers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    history_path = tmp_path / "history.json"
    history_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "observations": [
                    {
                        "run_id": "gha-late",
                        "phase": "hosted_ci",
                        "lane": "gha",
                        "duration_minutes": 40.0,
                        "succeeded": False,
                        "failure_class": "ci-attestation",
                        "failing_node": "tests/unit/test_ci.py::test_exact_sha",
                        "node_order": 800,
                        "total_nodes": 900,
                        "platform": "linux",
                        "python_version": "3.11",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    blocked = rr.Readiness(
        head="abc123",
        ci_verdict="RED",
        ci_head_matches=True,
        dirty_count=1,
        incomplete_release_tasks=["S86.1", "S86.2"],
        errors=["CI evidence is not a successful run"],
    )
    monkeypatch.setattr(rr, "assess", lambda **_: blocked)
    monkeypatch.setattr(
        rr,
        "_coverage_gap_modules",
        lambda _: ("general_ludd.cloud.model_pipeline",),
    )

    assert rr.main(["--history", str(history_path)]) == rr.EXIT_CI
    payload = json.loads(capsys.readouterr().out)

    estimate = payload["estimate"]
    assert estimate["calibration_sample_count"] == 1
    assert estimate["hosted_canary"][0]["node"].endswith("test_exact_sha")
    assert estimate["coverage_gaps"] == ["general_ludd.cloud.model_pipeline"]
    assert {
        "exact-sha-ci-evidence",
        "worktree-cleanliness",
        "release-task-ledger",
    }.issubset({item["code"] for item in estimate["risk_priorities"]})


def test_readiness_main_validate_only_emits_current_release_eta(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert rr.main(["--tag", rr.DEFAULT_RELEASE_TAG, "--validate-only"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tag"] == rr.DEFAULT_RELEASE_TAG
    assert payload["validate_only"] is True
    assert payload["estimate"]["p50_minutes"] > 0


@pytest.mark.parametrize(
    "argv",
    [
        ["--tag", "v0.1.0-beta.3", "--validate-only"],
        ["--tag", rr.DEFAULT_RELEASE_TAG, "--observations", "broken", "--validate-only"],
        [
            "--tag",
            rr.DEFAULT_RELEASE_TAG,
            "--observations",
            "hosted_ci=nope",
            "--validate-only",
        ],
        [
            "--tag",
            rr.DEFAULT_RELEASE_TAG,
            "--completed-stages",
            "unknown",
            "--validate-only",
        ],
    ],
)
def test_readiness_cli_rejects_invalid_estimation_evidence(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        rr.main(argv)
    assert exc_info.value.code == 2


def test_incomplete_tasks_rejects_unmapped_release(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text("- [ ] S86.5 — pending\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsupported release task mapping"):
        rr._incomplete_tasks(tmp_path, tag="v0.1.0-beta.9")


def test_release_readiness_make_target_is_safe_and_contracted() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "\nrelease-readiness:" in makefile
    assert 'RELEASE_READINESS_VALIDATE_ONLY="$(RELEASE_READINESS_VALIDATE_ONLY)"' in makefile
    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(item for item in contract["targets"] if item["name"] == "release-readiness")
    assert entry["make_variables"] == [
        "TAG",
        "RELEASE_READINESS_VALIDATE_ONLY",
        "RELEASE_COMPLETED_STAGES",
        "RELEASE_OBSERVATIONS",
    ]
    result = subprocess.run(
        [
            "make",
            "release-readiness",
            "TAG=v0.1.0-beta.4",
            "RELEASE_READINESS_VALIDATE_ONLY=1",
            "RELEASE_COMPLETED_STAGES=",
            "RELEASE_OBSERVATIONS=",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["estimate"]["p50_minutes"] == 200.0
    policy = payload["release_policy_preflight"]
    assert policy["command"][-1] == "MAX_FILES_PER_BATCH=64"
    assert policy["execution_policy"]["python_version"] == "3.11"
    assert len(policy["execution_policy_sha256"]) == 64


def test_readiness_remediation_documentation_pins_safe_operator_boundaries() -> None:
    text = (ROOT / "docs" / "features" / "BETA4_DUAL_TRACK_CI.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "Remediating release-readiness blockers",
        "WT_PRUNE_VALIDATE_ONLY=1",
        "requires_owner_confirmation",
        "must not be checked merely to clear readiness",
        "2017-01-09",
        "zero-downtime",
        "Rollback",
        "bounded",
    ):
        assert required in text


def test_make_ps_delegates_to_shared_process_inventory() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    ps_recipe = makefile.split("\nps:\n", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert "scripts/active_work_status.py --process-table" in ps_recipe


def test_release_readiness_make_target_binds_the_invoking_worktree() -> None:
    result = subprocess.run(
        [
            "make",
            "release-readiness",
            "TAG=v0.1.0-beta.4",
            "RELEASE_READINESS_VALIDATE_ONLY=0",
            "RELEASE_COMPLETED_STAGES=",
            "RELEASE_OBSERVATIONS=",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    combined = result.stdout + result.stderr
    assert "not a git repository" not in combined
    assert f'"head": "{_head(ROOT)}"' in result.stdout


def _head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout.strip()


def test_assess_reports_exact_ci_head_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import workflow_state_guard

    state = SimpleNamespace(
        branch="development",
        head="local123",
        dirty_count=0,
        unintegrated_worktrees=[],
        unintegrated_branches=[],
    )
    monkeypatch.setattr(workflow_state_guard, "collect_state", lambda **_: state)
    monkeypatch.setattr(rr, "_detached_worktrees", lambda *_: [])
    monkeypatch.setattr(rr, "_ci_verdict", lambda *_: ("GREEN", "CI GREEN"))
    monkeypatch.setattr(rr, "_version_check", lambda *_: (True, "OK"))
    monkeypatch.setattr(rr, "_incomplete_tasks", lambda *_: [])
    monkeypatch.setattr(rr, "_ledger_check", lambda *_: (True, "OK"))
    monkeypatch.setattr(rr, "_tasks_tick_check", lambda *_: (True, "OK"))

    result = rr.assess(
        root=tmp_path,
        run=lambda *_: _completed([]),
        gha_head_sha="hosted456",
    )

    assert result.ci_head_matches is False
    assert any("CI evidence is not a successful run" in error for error in result.errors)
