"""Unit tests for MarkdownTodoSource — real temp files, no network.

Covers the issue-source contract:
- parse -> normalize (checkbox open/done states)
- explicit ids (<!--id:ABC--> and (#id)) and stable derived ids
- update_status writes the checkbox back; a re-read reflects it
- add_comment appends an indented note under the item
- health() never raises and reports readability
- path outside the configured root is REFUSED (ValueError)
- symlink escape / absolute-outside-root / ../ escape rejected
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from general_ludd.issue_sources.markdown_todo import MarkdownTodoSource


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path / "root"
    base.mkdir()
    return base


def _src(root: Path, rel: str) -> MarkdownTodoSource:
    return MarkdownTodoSource({"path": rel, "root": str(root)})


def test_system_and_name_attrs(root: Path) -> None:
    _write(root / "todo.md", "- [ ] hello\n")
    src = _src(root, "todo.md")
    assert src.SYSTEM == "markdown"
    assert isinstance(src.name, str)
    assert src.name


def test_parse_normalizes_open_and_done(root: Path) -> None:
    _write(
        root / "todo.md",
        "# Tasks\n"
        "- [ ] write the parser\n"
        "- [x] read the spec\n"
        "some prose, not a task\n"
        "- [ ] ship it\n",
    )
    src = _src(root, "todo.md")
    issues = src.fetch_issues({})
    assert len(issues) == 3
    by_title = {i["title"]: i for i in issues}
    assert by_title["write the parser"]["status"] == "open"
    assert by_title["read the spec"]["status"] == "done"
    assert by_title["ship it"]["status"] == "open"
    # normalized contract fields present
    sample = issues[0]
    for key in (
        "external_id",
        "source",
        "title",
        "description",
        "status",
        "assignee",
        "labels",
        "priority",
        "url",
        "updated_ts",
        "raw",
    ):
        assert key in sample
    assert sample["source"] == "markdown"
    assert isinstance(sample["labels"], list)
    # url is the file path + a locator (line)
    assert str(root) in sample["url"]


def test_explicit_html_comment_id(root: Path) -> None:
    _write(root / "todo.md", "- [ ] task one <!--id:ABC-123-->\n")
    src = _src(root, "todo.md")
    (issue,) = src.fetch_issues({})
    assert issue["external_id"] == "ABC-123"
    assert issue["title"] == "task one"


def test_explicit_paren_hash_id(root: Path) -> None:
    _write(root / "todo.md", "- [ ] another task (#42)\n")
    src = _src(root, "todo.md")
    (issue,) = src.fetch_issues({})
    assert issue["external_id"] == "42"
    assert issue["title"] == "another task"


def test_derived_id_is_stable_across_reads(root: Path) -> None:
    _write(root / "todo.md", "- [ ] no explicit id here\n")
    src = _src(root, "todo.md")
    first = src.fetch_issues({})[0]["external_id"]
    second = src.fetch_issues({})[0]["external_id"]
    assert first == second
    assert first  # non-empty


def test_derived_id_distinct_per_line(root: Path) -> None:
    _write(root / "todo.md", "- [ ] alpha\n- [ ] beta\n")
    src = _src(root, "todo.md")
    ids = [i["external_id"] for i in src.fetch_issues({})]
    assert len(set(ids)) == 2


def test_update_status_writes_checkbox_back(root: Path) -> None:
    _write(root / "todo.md", "- [ ] flip me <!--id:X1-->\n- [ ] leave me <!--id:X2-->\n")
    src = _src(root, "todo.md")
    result = src.update_status("X1", "done")
    assert result["external_id"] == "X1"
    assert result["status"] == "done"

    # re-read from disk reflects the change
    reread = {i["external_id"]: i for i in _src(root, "todo.md").fetch_issues({})}
    assert reread["X1"]["status"] == "done"
    assert reread["X2"]["status"] == "open"
    raw = (root / "todo.md").read_text(encoding="utf-8")
    assert "- [x] flip me" in raw
    assert "- [ ] leave me" in raw


def test_update_status_back_to_open(root: Path) -> None:
    _write(root / "todo.md", "- [x] done already <!--id:D1-->\n")
    src = _src(root, "todo.md")
    src.update_status("D1", "open")
    raw = (root / "todo.md").read_text(encoding="utf-8")
    assert "- [ ] done already" in raw


def test_update_status_with_comment_marker(root: Path) -> None:
    _write(root / "todo.md", "- [ ] work item <!--id:W1-->\n")
    src = _src(root, "todo.md")
    src.update_status("W1", "done", comment="picked up by gludd")
    raw = (root / "todo.md").read_text(encoding="utf-8")
    assert "- [x] work item" in raw
    assert "picked up by gludd" in raw


def test_update_status_unknown_id_raises(root: Path) -> None:
    _write(root / "todo.md", "- [ ] only one <!--id:O1-->\n")
    src = _src(root, "todo.md")
    with pytest.raises(KeyError):
        src.update_status("NOPE", "done")


def test_add_comment_appends_indented_note(root: Path) -> None:
    _write(root / "todo.md", "- [ ] commentable <!--id:C1-->\n- [ ] sibling <!--id:C2-->\n")
    src = _src(root, "todo.md")
    result = src.add_comment("C1", "this is a note")
    assert result["external_id"] == "C1"
    raw = (root / "todo.md").read_text(encoding="utf-8")
    lines = raw.splitlines()
    idx = next(i for i, line in enumerate(lines) if "commentable" in line)
    # the note is indented and appears directly under the item
    assert lines[idx + 1].startswith("  ")
    assert "this is a note" in lines[idx + 1]
    # sibling not disturbed
    assert "- [ ] sibling" in raw


def test_health_ok_for_readable_file(root: Path) -> None:
    _write(root / "todo.md", "- [ ] x\n")
    src = _src(root, "todo.md")
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_never_raises_for_missing_file(root: Path) -> None:
    src = _src(root, "absent.md")
    h = src.health()
    assert h["ok"] is False
    assert isinstance(h["detail"], str)


def test_path_escape_with_dotdot_refused(root: Path) -> None:
    with pytest.raises(ValueError):
        MarkdownTodoSource({"path": "../escape.md", "root": str(root)})


def test_absolute_path_outside_root_refused(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    _write(outside, "- [ ] secret\n")
    with pytest.raises(ValueError):
        MarkdownTodoSource({"path": str(outside), "root": str(root)})


def test_symlink_escape_refused(root: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.md"
    _write(secret, "- [ ] secret\n")
    link = root / "link.md"
    os.symlink(secret, link)
    with pytest.raises(ValueError):
        MarkdownTodoSource({"path": "link.md", "root": str(root)})


def test_absolute_path_inside_root_allowed(root: Path) -> None:
    target = root / "ok.md"
    _write(target, "- [ ] fine\n")
    src = MarkdownTodoSource({"path": str(target), "root": str(root)})
    assert src.health()["ok"] is True
