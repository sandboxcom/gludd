from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

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

    result = rr.assess(root=tmp_path, run=lambda *_: _completed([]))
    assert result.ready
    assert result.head == "abc123"


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
    assert json.loads(result.stdout)["estimate"]["p50_minutes"] == 200.0


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
