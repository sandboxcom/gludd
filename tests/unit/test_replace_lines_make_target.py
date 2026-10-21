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
    source = compose_makefile(Path("Makefile"))
    for public_name in ("FILE", "START", "END", "NEW_FILE"):
        private_name = f"GLUDD_LINE_EDIT_{public_name}"
        assert f"override {private_name} := $(value {public_name})" in source
        assert f"export {private_name} := $(value {public_name})" in source
        assert f'"$${private_name}"' in body
        assert f'"$({public_name})"' not in body
    assert "unexport FILE START END NEW_FILE" in source
    assert (
        '$(PYTHON) scripts/replace_lines.py "$$TMP" "$$GLUDD_LINE_EDIT_START" '
        '"$$GLUDD_LINE_EDIT_END" "$$GLUDD_LINE_EDIT_NEW_FILE"'
    ) in body
    assert 'cp -p "$$GLUDD_LINE_EDIT_FILE" "$$TMP"' in body
    assert 'mv "$$TMP" "$$GLUDD_LINE_EDIT_FILE"' in body
    assert "/tmp/gludd-replace-lines-atomic.txt" not in body
