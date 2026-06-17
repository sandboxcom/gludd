"""Regression: MarkdownTodoSource.update_status must escape '-->' and not duplicate markers.

Uses an explicit ``<!--id:T1-->`` so the external_id is STABLE across calls: a
derived id is hash(text) and would change once the gludd marker is appended,
which is irrelevant to what these tests exercise (the escape + dedup of the
gludd marker itself).
"""
from __future__ import annotations

from general_ludd.issue_sources.markdown_todo import MarkdownTodoSource


def _source(tmp_path):
    p = tmp_path / "TODO.md"
    p.write_text("- [ ] First task <!--id:T1-->\n")
    src = MarkdownTodoSource({"root": str(tmp_path), "path": "TODO.md"})
    return src, p


def test_comment_arrow_is_escaped(tmp_path) -> None:
    src, p = _source(tmp_path)
    src.update_status("T1", "done", comment="result-->injected")
    content = p.read_text()
    # The raw '-->' inside the comment body must be escaped so it cannot close
    # the HTML comment early.
    assert "<!--gludd:result--&gt;injected-->" in content
    assert "gludd:result-->injected" not in content


def test_identical_comment_not_duplicated(tmp_path) -> None:
    src, p = _source(tmp_path)
    src.update_status("T1", "done", comment="reviewed")
    src.update_status("T1", "open", comment="reviewed")
    content = p.read_text()
    assert content.count("<!--gludd:reviewed-->") == 1
