"""Keep completed post-v0.1.1 backlog work assigned to v0.1.2."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
TASKS = ROOT / "TASKS.md"
RECONCILIATION = ROOT / "config/v012_completed_backlog_reconciliation.json"

RELEASE_HEADING = "## Next release (v0.1.2) — Unreleased"
RELEASE_TOKEN = "v0.1.2-completed-backlog"
COMPLETED_ITEMS = (
    ("S83.114", "Fail-closed chemistry entity resolution"),
    ("S83.115", "Standards-consistent X.509 chain validation"),
    ("S83.116", "Monotonic debounce, throttle, and watchdog state"),
    ("S83.117", "Authenticated TLS 1.3 state and directional records"),
    ("S83.128", "Invoking-worktree-safe virtual-environment reclamation"),
    ("S83.178", "Fast integration admission for deterministic gate failures"),
)
OPEN_ITEMS = ("S83.157", "S83.158", "S83.163", "S83.166", "S83.169")

TASK_LINE = re.compile(r"^- \[(?P<checked>[ x])\] (?P<task_id>S\d+(?:\.\w+)+)\b", re.MULTILINE)


def _checked_task_ids(ledger: str) -> tuple[str, ...]:
    """Return checked task identifiers in their durable ledger order."""
    return tuple(
        match.group("task_id")
        for match in TASK_LINE.finditer(ledger)
        if match.group("checked") == "x"
    )


def _git_text(revision_path: str) -> str:
    """Read one immutable Git object without changing refs or the worktree."""
    result = subprocess.run(
        ["git", "show", revision_path],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _release_section(changelog: str) -> str:
    """Return only the unreleased v0.1.2 section."""
    return changelog.split(RELEASE_HEADING, 1)[1].split("## [0.1.1]", 1)[0]


def test_changelog_assigns_every_formally_completed_backlog_item_to_v012() -> None:
    """The next release notes must enumerate the complete closed backlog set."""
    changelog = CHANGELOG.read_text(encoding="utf-8")
    section = _release_section(changelog)

    assert "Completed backlog items" in section
    for task_id, title in COMPLETED_ITEMS:
        assert task_id in section
        assert title in section

    for task_id in OPEN_ITEMS:
        assert f"- **{task_id}" not in section
    assert "implemented but" in section.lower()
    assert "still open" in section.lower()


def test_v012_scope_matches_every_task_closed_after_v011() -> None:
    """Every newly checked task must be assigned; prior-release tasks stay excluded."""
    prior = set(_checked_task_ids(_git_text("v0.1.1:TASKS.md")))
    current = _checked_task_ids(TASKS.read_text(encoding="utf-8"))
    completed_after_v011 = tuple(task_id for task_id in current if task_id not in prior)

    assert {task_id for task_id, _ in COMPLETED_ITEMS} == set(completed_after_v011)


def test_task_ledger_declares_the_exact_v012_completed_backlog_scope() -> None:
    """The evidence ledger must name the release assignment without changing status."""
    tasks = TASKS.read_text(encoding="utf-8")
    opening = f'<!-- {RELEASE_TOKEN} -->'
    contract = tasks.split(opening, 1)[1].split("<!-- /v0.1.2-completed-backlog -->", 1)[0]

    assert "S83.114-S83.117, S83.128, and S83.178" in contract
    positions = [
        contract.index(f"| {task_id.rsplit('.', 1)[1]} |")
        for task_id, _ in COMPLETED_ITEMS
    ]
    assert positions == sorted(positions)
    assert "six formally completed" in contract
    for task_id in OPEN_ITEMS:
        assert task_id in contract
    assert "remain open" in contract


def test_reconciliation_receipt_keeps_every_completed_commit_reachable() -> None:
    """The release receipt must pin every source commit without duplicate merges."""
    receipt = json.loads(RECONCILIATION.read_text(encoding="utf-8"))
    entries = receipt["completed_items"]

    assert receipt["schema_version"] == 1
    assert receipt["release"] == "v0.1.2"
    assert receipt["baseline"] == {
        "ref": "v0.1.1",
        "commit": "5dcd2f6931aa6cb13d4de526d6c739891c0240f1",
    }
    assert tuple(entry["task_id"] for entry in entries) == tuple(
        task_id for task_id, _ in COMPLETED_ITEMS
    )

    expected_titles = dict(COMPLETED_ITEMS)
    for entry in entries:
        assert entry["title"] == expected_titles[entry["task_id"]]
        assert entry["integration"] == "ancestor"
        assert entry["source_branches"]
        assert entry["evidence_commits"]
        for commit in entry["evidence_commits"]:
            sha = commit["sha"]
            assert re.fullmatch(r"[0-9a-f]{40}", sha)
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (sha, result.stderr)

    inventory = receipt["inventory"]
    assert inventory["terminal"] is True
    assert inventory["truncated"] is False
    assert inventory["pages"] == 16
    assert inventory["selected_current_heads"] == 54


def test_reconciliation_receipt_keeps_ungated_work_open() -> None:
    """Implemented but ungated work must never enter the completed release scope."""
    tasks = TASKS.read_text(encoding="utf-8")
    receipt = json.loads(RECONCILIATION.read_text(encoding="utf-8"))

    assert tuple(receipt["excluded_open_tasks"]) == OPEN_ITEMS
    for task_id in OPEN_ITEMS:
        assert re.search(rf"^- \[ \] {re.escape(task_id)}\b", tasks, re.MULTILINE)
