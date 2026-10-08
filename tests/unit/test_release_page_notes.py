"""Deterministic v0.1.2 GitHub release-page note coverage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from release_page_notes import (  # noqa: E402
    MAX_LEDGER_BYTES,
    MAX_OUTPUT_BYTES,
    ReleaseNotesError,
    build_release_page_notes,
    load_release_ledger,
    main,
    sync_release_page_notes,
)
from scripts.makefile_layout import compose_makefile  # noqa: E402


def _ledger() -> dict[str, object]:
    return {
        "schema_version": 1,
        "release": "v0.1.2",
        "baseline": {
            "ref": "v0.1.1",
            "commit": "a" * 40,
        },
        "release_page": {
            "status": "unreleased",
            "repository": "sandboxcom/gludd",
        },
        "completed_items": [
            {
                "task_id": "S83.117",
                "title": "Authenticated TLS state",
                "release_page_category": "Features",
                "evidence_commits": [
                    {"role": "implementation", "sha": "b" * 40},
                ],
            },
            {
                "task_id": "S83.128",
                "title": "Worktree-safe reclamation",
                "release_page_category": "Improvements",
                "evidence_commits": [
                    {"role": "formal_closeout", "sha": "c" * 40},
                ],
            },
        ],
        "excluded_open_tasks": ["S83.157"],
    }


def _write_ledger(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_build_notes_is_deterministic_categorized_and_unreleased(tmp_path: Path) -> None:
    ledger = load_release_ledger(_write_ledger(tmp_path, _ledger()), "v0.1.2")

    first = build_release_page_notes(ledger)
    second = build_release_page_notes(ledger)

    assert first == second
    assert first.startswith("# Gludd v0.1.2\n")
    assert "**Status: Unreleased.**" in first
    assert "does not create or publish a GitHub release" in first
    assert first.index("## Features") < first.index("## Improvements")
    assert first.count("S83.117 — Authenticated TLS state") == 1
    assert first.count("S83.128 — Worktree-safe reclamation") == 1
    assert "https://github.com/sandboxcom/gludd/commit/" + "b" * 40 in first
    assert "Excluded open work: `S83.157`." in first
    assert len(first.encode("utf-8")) <= MAX_OUTPUT_BYTES


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload["release_page"].update(status="published"), "unreleased"),
        (lambda payload: payload.update(release="v9.9.9"), "expected release"),
        (
            lambda payload: payload["completed_items"][0].update(
                release_page_category="Fixes"
            ),
            "release_page_category",
        ),
        (
            lambda payload: payload["completed_items"][0]["evidence_commits"][0].update(
                sha="short"
            ),
            "40 lowercase hexadecimal",
        ),
    ],
)
def test_load_release_ledger_fails_closed(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    payload = _ledger()
    mutation(payload)  # type: ignore[operator]

    with pytest.raises(ReleaseNotesError, match=message):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")


def test_load_release_ledger_rejects_duplicate_or_uncategorized_items(tmp_path: Path) -> None:
    payload = _ledger()
    completed = payload["completed_items"]
    assert isinstance(completed, list)
    completed.append(dict(completed[0]))

    with pytest.raises(ReleaseNotesError, match="unique"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    completed.pop()
    completed[1]["release_page_category"] = "Features"
    with pytest.raises(ReleaseNotesError, match="Features and Improvements"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")


def test_load_release_ledger_rejects_oversized_input(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_bytes(b" " * (MAX_LEDGER_BYTES + 1))

    with pytest.raises(ReleaseNotesError, match="exceeds"):
        load_release_ledger(path, "v0.1.2")


@pytest.mark.parametrize("content", [b"not-json", b"[]"])
def test_load_release_ledger_rejects_malformed_roots(
    tmp_path: Path,
    content: bytes,
) -> None:
    path = tmp_path / "ledger.json"
    path.write_bytes(content)

    with pytest.raises(ReleaseNotesError):
        load_release_ledger(path, "v0.1.2")


def test_load_release_ledger_rejects_invalid_structural_fields(tmp_path: Path) -> None:
    payload = _ledger()
    payload["completed_items"] = {}
    with pytest.raises(ReleaseNotesError, match="must be an array"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    payload = _ledger()
    payload["completed_items"] = []
    with pytest.raises(ReleaseNotesError, match="between 1"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    payload = _ledger()
    payload["schema_version"] = 2
    with pytest.raises(ReleaseNotesError, match="schema_version"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    payload = _ledger()
    page = payload["release_page"]
    assert isinstance(page, dict)
    page["repository"] = "not-an-owner-pair"
    with pytest.raises(ReleaseNotesError, match="owner/name"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    payload = _ledger()
    baseline = payload["baseline"]
    assert isinstance(baseline, dict)
    baseline["commit"] = "not-a-commit"
    with pytest.raises(ReleaseNotesError, match="baseline"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")


def test_load_release_ledger_rejects_unsafe_item_and_exclusion_fields(
    tmp_path: Path,
) -> None:
    payload = _ledger()
    completed = payload["completed_items"]
    assert isinstance(completed, list)
    completed[0]["task_id"] = "not-a-task"
    with pytest.raises(ReleaseNotesError, match="task_id"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    payload = _ledger()
    completed = payload["completed_items"]
    assert isinstance(completed, list)
    completed[0]["title"] = "unsafe\nheading"
    with pytest.raises(ReleaseNotesError, match="safe text boundary"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    payload = _ledger()
    completed = payload["completed_items"]
    assert isinstance(completed, list)
    completed[0]["evidence_commits"][0]["role"] = "Bad Role"
    with pytest.raises(ReleaseNotesError, match="invalid identifier"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    payload = _ledger()
    completed = payload["completed_items"]
    assert isinstance(completed, list)
    evidence = completed[0]["evidence_commits"]
    assert isinstance(evidence, list)
    evidence.append(dict(evidence[0]))
    with pytest.raises(ReleaseNotesError, match="evidence commits must be unique"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    payload = _ledger()
    payload["excluded_open_tasks"] = ["invalid"]
    with pytest.raises(ReleaseNotesError, match="excluded_open_tasks"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    payload = _ledger()
    payload["excluded_open_tasks"] = ["S83.117"]
    with pytest.raises(ReleaseNotesError, match="unique and open"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")


def test_load_release_ledger_rejects_missing_or_symlink_input(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ReleaseNotesError, match="regular non-symlink"):
        load_release_ledger(missing, "v0.1.2")

    target = _write_ledger(tmp_path, _ledger())
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ReleaseNotesError, match="regular non-symlink"):
        load_release_ledger(link, "v0.1.2")


def test_render_rejects_oversized_release_page(tmp_path: Path) -> None:
    payload = _ledger()
    page = payload["release_page"]
    assert isinstance(page, dict)
    page["repository"] = f"{'a' * 63}/{'b' * 63}"
    payload["completed_items"] = [
        {
            "task_id": f"S83.{index}",
            "title": "x" * 180,
            "release_page_category": (
                "Features" if index % 2 else "Improvements"
            ),
            "evidence_commits": [
                {
                    "role": "implementation",
                    "sha": f"{index * 8 + evidence_index + 1:040x}",
                }
                for evidence_index in range(8)
            ],
        }
        for index in range(1, 65)
    ]
    ledger = load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    with pytest.raises(ReleaseNotesError, match="notes size"):
        build_release_page_notes(ledger)


def test_sync_writes_atomically_then_validates_without_mutation(tmp_path: Path) -> None:
    ledger_path = _write_ledger(tmp_path, _ledger())
    output = tmp_path / "v0.1.2.md"

    generated = sync_release_page_notes(
        ledger_path,
        output,
        "v0.1.2",
        validate_only=False,
    )
    before = output.stat()
    assert output.read_text(encoding="utf-8") == generated

    checked = sync_release_page_notes(
        ledger_path,
        output,
        "v0.1.2",
        validate_only=True,
    )
    after = output.stat()
    assert checked == generated
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)

    output.write_text("drift\n", encoding="utf-8")
    with pytest.raises(ReleaseNotesError, match="out of date"):
        sync_release_page_notes(
            ledger_path,
            output,
            "v0.1.2",
            validate_only=True,
        )
    assert output.read_text(encoding="utf-8") == "drift\n"


def test_sync_rejects_missing_preview_and_missing_output_parent(tmp_path: Path) -> None:
    ledger_path = _write_ledger(tmp_path, _ledger())
    output = tmp_path / "missing" / "notes.md"

    with pytest.raises(ReleaseNotesError, match="preview is missing"):
        sync_release_page_notes(
            ledger_path,
            output,
            "v0.1.2",
            validate_only=True,
        )
    with pytest.raises(ReleaseNotesError, match="output parent"):
        sync_release_page_notes(
            ledger_path,
            output,
            "v0.1.2",
            validate_only=False,
        )


def test_cli_supports_write_and_no_write_validation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger_path = _write_ledger(tmp_path, _ledger())
    output = tmp_path / "notes.md"
    args = [
        "v0.1.2",
        "--ledger",
        str(ledger_path),
        "--output",
        str(output),
    ]

    assert main(args) == 0
    assert "wrote" in capsys.readouterr().out
    assert main([*args, "--validate-only"]) == 0
    assert "validated" in capsys.readouterr().out


def test_cli_reports_validation_failures_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ledger_path = _write_ledger(tmp_path, _ledger())
    output = tmp_path / "notes.md"

    assert main(
        [
            "not-a-release",
            "--ledger",
            str(ledger_path),
            "--output",
            str(output),
        ]
    ) == 2
    assert "ERROR" in capsys.readouterr().out
    assert not output.exists()


def test_repository_v012_preview_matches_completed_backlog_ledger() -> None:
    ledger_path = ROOT / "config" / "v012_completed_backlog_reconciliation.json"
    output = ROOT / "docs" / "releases" / "v0.1.2.md"

    ledger = load_release_ledger(ledger_path, "v0.1.2")
    expected = build_release_page_notes(ledger)

    assert output.read_text(encoding="utf-8") == expected
    assert {item.category for item in ledger.completed_items} == {
        "Features",
        "Improvements",
    }


def test_release_dry_run_validates_v012_page_without_publishing() -> None:
    makefile = compose_makefile(ROOT / "Makefile")

    assert "release-page-notes:" in makefile
    assert "release-dry-run: _release-page-notes-preview _release-dry-run-guard" in makefile
    preview = makefile.split("_release-page-notes-preview:", 1)[1].split("\n\n", 1)[0]
    assert "RELEASE_PAGE_NOTES_VALIDATE_ONLY=1" in preview
    assert "gh release" not in preview
