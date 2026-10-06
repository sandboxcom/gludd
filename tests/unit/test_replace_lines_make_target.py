"""Regression tests for the Make-backed line editing facility."""

from pathlib import Path

from scripts.makefile_layout import compose_makefile


def _target_body(name: str) -> str:
    text = compose_makefile(Path("Makefile"))
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
