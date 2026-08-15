"""Pin the HTML-comment escaper in scripts/fix_docs_drift.py.

Regression pin for the 2026-08-15 incident: ``_escape_html_comments``
rewrote functional marker comments (``<!-- STATUS-TABLE:START -->`` and the
``gate:begin``/``gate:end`` pair) into HTML-escaped text, breaking
``scripts/gen_status_table.py``, ``scripts/status_snapshot.py``, and
``src/general_ludd/quality/preflight.py`` marker detection. Functional
markers must stay literal; prose that merely discusses comment delimiters
must still be escaped.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import fix_docs_drift  # noqa: E402


def test_escape_preserves_functional_markers() -> None:
    text = "\n".join(
        [
            "# Heading",
            "",
            "<!-- STATUS-TABLE:START -->",
            "| A | B |",
            "<!-- STATUS-TABLE:END -->",
            "",
            "<!-- gate:begin -->",
            "- lint: PASS",
            "<!-- gate:end -->",
            "",
            "  <!-- STATUS-TABLE:START -->",
            "| C | D |",
            "  <!-- STATUS-TABLE:END -->",
        ]
    )
    escaped = fix_docs_drift._escape_html_comments(text)
    for marker in (
        "<!-- STATUS-TABLE:START -->",
        "<!-- STATUS-TABLE:END -->",
        "<!-- gate:begin -->",
        "<!-- gate:end -->",
    ):
        assert marker in escaped, f"functional marker escaped: {marker}"
    assert "&lt;!--" not in escaped


def test_escape_still_escapes_prose_mentioning_delimiters() -> None:
    text = "\n".join(
        [
            "Prose that discusses the ``<!--`` and ``-->`` delimiters",
            "mid-line: wrap <!-- raw --> inline.",
            "",
            "```md",
            "<!-- fenced comments stay literal -->",
            "```",
        ]
    )
    escaped = fix_docs_drift._escape_html_comments(text)
    assert "&lt;!--" in escaped
    assert "--&gt;" in escaped
    assert "<!-- fenced comments stay literal -->" in escaped
    assert "wrap &lt;!-- raw --&gt; inline." in escaped
