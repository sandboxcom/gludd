"""Deterministic v0.1.2 GitHub release-page note coverage."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile
from scripts.release_page_notes import (
    MAX_LEDGER_BYTES,
    MAX_OUTPUT_BYTES,
    ReleaseNotesError,
    build_release_page_notes,
    load_release_ledger,
    main,
    sync_release_page_notes,
)

ROOT = Path(__file__).resolve().parents[2]


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
        "implemented_items": [
            {
                "item_id": "S29",
                "title": "Managed-process registry lifecycle",
                "release_page_category": "Features",
                "evidence_commits": [
                    {"role": "implementation", "sha": "d" * 40},
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
    assert first.count("S29 — Managed-process registry lifecycle") == 1
    assert "Implementation evidence; exact-head/release proof pending." in first
    assert "https://github.com/sandboxcom/gludd/commit/" + "b" * 40 in first
    assert "https://github.com/sandboxcom/gludd/commit/" + "d" * 40 in first
    assert "Open work excluded from completion claims: `S83.157`." in first
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


def test_load_release_ledger_rejects_duplicate_or_unsafe_implemented_items(
    tmp_path: Path,
) -> None:
    payload = _ledger()
    implemented = payload["implemented_items"]
    assert isinstance(implemented, list)
    implemented.append(dict(implemented[0]))

    with pytest.raises(ReleaseNotesError, match="implemented item identifiers"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    implemented.pop()
    implemented[0]["item_id"] = "unsafe item"
    with pytest.raises(ReleaseNotesError, match="item_id"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")

    payload = _ledger()
    implemented = payload["implemented_items"]
    assert isinstance(implemented, list)
    implemented[0]["item_id"] = "S83.117"
    with pytest.raises(ReleaseNotesError, match="must not duplicate completed"):
        load_release_ledger(_write_ledger(tmp_path, payload), "v0.1.2")


def test_load_release_ledger_rejects_oversized_input(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_bytes(b" " * (MAX_LEDGER_BYTES + 1))

    with pytest.raises(ReleaseNotesError, match="exceeds"):
        load_release_ledger(path, "v0.1.2")


def test_load_release_ledger_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")

    with pytest.raises(ReleaseNotesError, match="duplicate JSON key"):
        load_release_ledger(path, "v0.1.2")


@pytest.mark.parametrize("drift", ["same-size", "inode", "mtime"])
def test_load_release_ledger_rejects_file_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    path = _write_ledger(tmp_path, _ledger())
    original_read_bytes = Path.read_bytes

    def read_then_drift(candidate: Path) -> bytes:
        raw = original_read_bytes(candidate)
        if candidate != path:
            return raw
        if drift == "same-size":
            changed = raw.replace(b"v0.1.2", b"v0.1.3", 1)
            assert len(changed) == len(raw)
            candidate.write_bytes(changed)
        elif drift == "inode":
            replacement = tmp_path / "replacement.json"
            replacement.write_bytes(raw)
            replacement.replace(candidate)
        else:
            before = candidate.stat()
            os.utime(
                candidate,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000),
            )
        return raw

    monkeypatch.setattr(Path, "read_bytes", read_then_drift)

    with pytest.raises(ReleaseNotesError, match="changed while it was being read"):
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
    assert tuple(item.item_id for item in ledger.implemented_items) == (
        "SEARXNG",
        "ANSIBLE",
        "S83.163",
        "S83.166",
        "S83.157",
        "S83.158",
        "#65",
        "#75",
        "FFDH",
        "#77",
        "S11.1",
        "S14",
        "S15",
        "S16",
        "S17",
        "S18",
        "S23",
        "S24",
        "S29",
        "S30",
        "S31",
        "S32",
        "S33",
        "S34",
        "S35",
        "S36",
        "S37",
        "S38",
        "S39",
        "S47",
        "S40",
        "S41",
        "S42",
        "S43",
        "S44",
        "S45",
        "GATE",
    )
    assert len(ledger.completed_items) == 6
    s30 = next(item for item in ledger.implemented_items if item.item_id == "S30")
    assert s30.title == "Transactionally durable, session-safe model-performance telemetry"
    assert s30.category == "Improvements"
    assert tuple(
        (evidence.role, evidence.sha) for evidence in s30.evidence_commits
    ) == (
        ("implementation", "0b2629d00dcbfb6050f65e13e1a06f19e8f8fd7e"),
        ("documentation_coverage", "a7eb66268cf4f2b3829f183582fbd0a92d0deda6"),
    )
    assert expected.count(
        "S30 — Transactionally durable, session-safe model-performance telemetry"
    ) == 1
    candidate_contracts = {
        "S31": (
            "Operation-scoped model-performance query lifecycle",
            "Improvements",
            (
                ("implementation", "80835b29e79174bc0a4643c4643a552383693e8a"),
                ("documentation", "4daa46c3b59c6bd8afac4a7535f72bcd262750ed"),
            ),
        ),
        "S32": (
            "Operation-scoped benchmark-query lifecycle",
            "Improvements",
            (
                ("implementation", "6ac5f05f04d2fe117ff579719d60af0b6dec6305"),
                ("documentation", "de6f637c28f6e55d40d3bd8fb83d80f5adbe7add"),
            ),
        ),
        "S33": (
            "Factory-owned MemoryRepository result lifecycle",
            "Improvements",
            (
                ("implementation", "0d03819879e56beecf3df665c46ebe70d76a4925"),
                ("task_evidence", "15064dfdd2db898e9cfb987c790d36543452c9f1"),
            ),
        ),
        "S34": (
            "Bounded agent-message expiry admission",
            "Improvements",
            (
                ("implementation", "e0d1025e779a16a4e9703c4412ab7a7b197d26b5"),
                ("documentation", "7b3cc726e29d903cafb3ddbfc9a293968bc6c00b"),
            ),
        ),
        "S35": (
            "Fail-closed SQL memory tenant isolation",
            "Improvements",
            (
                ("implementation", "32a9ec3fa745c4119633e8e597eb40c941b62f37"),
                ("documentation", "a201bd55d6a38a0bb1c6629f626437832fa9762c"),
            ),
        ),
        "S36": (
            "Project-isolated persisted tool outputs",
            "Improvements",
            (
                ("implementation", "cb0959465fc940467e826bde3a634c72f21805b6"),
                ("documentation", "7af995fb69e622df5c5a3e17a9aa6b8a275a0c1e"),
            ),
        ),
        "S37": (
            "Dialect-native SQLite and PostgreSQL repository upserts",
            "Improvements",
            (
                ("implementation", "9bf7437dd18a43b5103e09b7b83dc3983b715cbc"),
            ),
        ),
        "S38": (
            "Native SearXNG controller runtime",
            "Features",
            (
                ("implementation", "1b9d79457bd7be583ef5b5ae60a3383be0c4db71"),
                ("task_evidence", "5bea57b1ed220ff28cacd05fa3a005d11db993b3"),
            ),
        ),
        "S39": (
            "Native git-release artifact verification",
            "Improvements",
            (
                ("implementation", "5cc1bb2556f2f9b0ea45c4fd64a335adc7a40fe7"),
            ),
        ),
        "S47": (
            "Exact-SHA resumable, fail-fast local gate admission",
            "Improvements",
            (
                ("implementation", "cba037e37fb1f66a28d76bded20dc708556d9901"),
                (
                    "promoted_namespace_repair",
                    "ee1a73e691c7c7ace9e137c2c572c5bffdbedf3a",
                ),
            ),
        ),
        "S40": (
            "Native Frictionless dataset admission",
            "Features",
            (
                ("implementation", "24fd14135d1bdaf3813650736dce8c58f6efd51e"),
                ("task_evidence", "90d79105b265df060472279e46466f64ca554cd2"),
            ),
        ),
        "S41": (
            "Collection-native chemical lot admission",
            "Features",
            (
                ("implementation", "1cf229c408110e5389f317472da1560c7b852a3f"),
                ("resource_ownership", "7ad01a472f617260e422feddd4d5cf1f9242ad28"),
                ("inventory_refresh", "14643699ec9a740b36b6b44e7687bb8082754bf9"),
            ),
        ),
        "S42": (
            "Native fail-closed JUnit pipeline triage",
            "Features",
            (
                ("implementation", "fd5faf302dd01bf1ab2df958e5112fc70ad17cfa"),
                ("task_evidence", "3a539a9640a9283ca655ab1b46b4cb25e609fdcb"),
            ),
        ),
        "S43": (
            "Native fail-closed materials tolerance analysis",
            "Features",
            (
                ("implementation", "3d86e4854ab4229986d9c9f78dd01e2ed36aa8a9"),
                ("task_evidence", "6eb9893550df86dff1c09cdaf11780e74dd21b29"),
            ),
        ),
        "S44": (
            "Collection-native SearXNG batch consumers",
            "Features",
            (
                ("implementation", "085a76bcac47bc11c00a961d11070cfb49a99d27"),
                ("task_evidence", "c54b128d04d1c6caee20e32c099a3d0b29f62999"),
                ("development_merge", "134566ad2d3186101cc719676210bb13fe1e86a6"),
            ),
        ),
        "S45": (
            "Fail-closed native OpenAPI service registration",
            "Features",
            (
                ("implementation", "a2cde4421c3f9cd221f35b2b82bfda7b57a7bac0"),
                ("task_evidence", "e560ed21dd6fc7a4bf88c90b451c579396b2672e"),
                ("development_merge", "523e2c0632427b68cbb0a6855d5dc04415c31ce3"),
            ),
        ),
    }
    for item_id, (title, category, evidence) in candidate_contracts.items():
        item = next(
            candidate
            for candidate in ledger.implemented_items
            if candidate.item_id == item_id
        )
        assert item.title == title
        assert item.category == category
        assert tuple(
            (receipt.role, receipt.sha) for receipt in item.evidence_commits
        ) == evidence
        assert expected.count(f"{item_id} — {title}") == 1
    assert "Formally completed backlog items: 6." in expected
    assert (
        "Implemented candidate items pending exact-head/release proof: 37."
        in expected
    )
    release_source = (
        ROOT / "docs" / "features" / "V012_RELEASE_PAGE_NOTES.md"
    ).read_text(encoding="utf-8")
    for required in (
        "S37",
        "SQLAlchemy discussion #7199",
        "SQLAlchemy discussion #7007",
        "schema-free, application-image-only rolling change",
        "354 warning-fatal tests",
        "94% aggregate coverage",
        "S38-S45",
        "Native SearXNG controller runtime",
        "Native git-release artifact verification",
        "Native Frictionless dataset admission",
        "Collection-native chemical lot admission",
        "Native fail-closed JUnit pipeline triage",
        "Native fail-closed materials tolerance analysis",
        "Collection-native SearXNG batch consumers",
        "Fail-closed native OpenAPI service registration",
        "31 raw SearXNG HTTP calls",
        "155 focused",
        "99% aggregate coverage",
        "31 tests",
        "95% aggregate coverage",
        "runtime response validation",
        "16 MiB",
        "100,000 testcases",
        "64 failures or errors",
        "26 focused tests",
        "97% aggregate coverage",
        "121,418-test zero-error collection",
        "exactly six pure operations",
        "256 dimension pairs",
        "64 KiB",
        "32 characters",
        "94% aggregate coverage",
        "121,469-test zero-error collection",
        "digest-addressed canary",
        "Ansible Release 1.2.3",
        "Frictionless discussion #675",
        "ansible/ansible#50579",
        "95% aggregate coverage",
        "Exact-SHA resumable, fail-fast local gate admission",
        "exact 37-item candidate inventory",
    ):
        assert required in release_source
    task_ledger = (ROOT / "TASKS.md").read_text(encoding="utf-8")
    for task_id, evidence_sha in (
        ("S37", "9bf7437dd18a43b5103e09b7b83dc3983b715cbc"),
        ("S38", "1b9d79457bd7be583ef5b5ae60a3383be0c4db71"),
        ("S39", "5cc1bb2556f2f9b0ea45c4fd64a335adc7a40fe7"),
        ("S47", "cba037e37fb1f66a28d76bded20dc708556d9901"),
        ("S40", "24fd14135d1bdaf3813650736dce8c58f6efd51e"),
        ("S41", "1cf229c408110e5389f317472da1560c7b852a3f"),
        ("S42", "fd5faf302"),
        ("S43", "3d86e4854"),
        ("S44", "085a76bca"),
        ("S45", "a2cde4421"),
    ):
        assert f"- [ ] {task_id}" in task_ledger
        assert evidence_sha in task_ledger


def test_release_dry_run_validates_v012_page_without_publishing() -> None:
    makefile = compose_makefile(ROOT / "Makefile")

    assert "release-page-notes:" in makefile
    assert "release-dry-run: _release-page-notes-preview _release-dry-run-guard" in makefile
    preview = makefile.split("_release-page-notes-preview:", 1)[1].split("\n\n", 1)[0]
    assert "RELEASE_PAGE_NOTES_VALIDATE_ONLY=1" in preview
    assert "gh release" not in preview


def test_release_publication_uses_v012_page_and_safe_other_tag_fallback() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8"
    )
    validation = "- name: Validate deterministic v0.1.2 release notes"
    action = "uses: softprops/action-gh-release@"
    assert validation in workflow
    assert workflow.index(validation) < workflow.index(action, workflow.index(validation))

    release_action = workflow.rsplit(action, 1)[1].split("\n      - name:", 1)[0]
    assert (
        "body_path: ${{ github.ref_name == 'v0.1.2' "
        "&& 'docs/releases/v0.1.2.md' || '' }}"
    ) in release_action
    assert (
        "generate_release_notes: ${{ github.ref_name != 'v0.1.2' }}"
        in release_action
    )
    assert "generate_release_notes: true" not in release_action

    makefile = compose_makefile(ROOT / "Makefile")
    release_create = makefile.split("release-create:", 1)[1].split("\n\n", 1)[0]
    assert release_create.startswith(" _release-page-notes-preview")
    assert 'if [ "$(TAG)" = "v0.1.2" ]' in release_create
    assert '--notes-file "docs/releases/v0.1.2.md"' in release_create
    assert '--notes "Release $(TAG) (manual single-binary draft' in release_create
