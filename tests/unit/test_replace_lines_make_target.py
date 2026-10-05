"""Regression tests for the Make-backed line editing facility."""

from pathlib import Path


def _target_body(name: str) -> str:
    text = Path("Makefile").read_text(encoding="utf-8")
    marker = f"{name}:\n"
    start = text.index(marker) + len(marker)
    end = text.find("\n\n", start)
    return text[start:] if end < 0 else text[start:end]


def test_replace_lines_target_forwards_all_documented_variables() -> None:
    body = _target_body("replace-lines")
    for token in ('"$(FILE)"', '"$(START)"', '"$(END)"', '"$(NEW_FILE)"'):
        assert token in body
    assert 'cp "$(FILE)" "$$TMP"' in body
    assert 'mv "$$TMP" "$(FILE)"' in body
    assert "/tmp/gludd-replace-lines-atomic.txt" not in body
