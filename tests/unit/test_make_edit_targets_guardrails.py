from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _makefile() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _target_block(target: str) -> str:
    lines = _makefile().splitlines()
    prefix = target + ":"
    start = next((idx for idx, line in enumerate(lines) if line.startswith(prefix)), None)
    assert start is not None, f"{target} target missing"
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        line = lines[idx]
        if line and not line.startswith((" ", chr(9), "#")) and re.match(r"[a-zA-Z0-9_.-]+:", line):
            end = idx
            break
    return chr(10).join(lines[start:end])


def test_make_edit_targets_are_documented() -> None:
    text = _makefile()
    help_block = _target_block("help")
    for target in ["replace-text", "write-text", "append-text"]:
        assert f"{target}:" in text
        assert f"  {target}" in help_block


def test_text_edit_targets_fail_closed_on_missing_or_external_file() -> None:
    for target in ["replace-text", "write-text", "append-text"]:
        block = _target_block(target)
        assert "Usage: make " + target in block
        assert "FILE" in block
        assert "/tmp/gludd-*" in block
        assert "/*|*..*" in block
        assert "Refusing path outside workspace" in block


def test_copy_file_target_guards_source_and_destination_paths() -> None:
    block = _target_block("copy-file")
    assert "Usage: make copy-file" in block
    for variable in ["SRC", "DST"]:
        assert variable in block
    assert "/tmp/gludd-*" in block
    assert block.count("/*|*..*") >= 2
    assert block.count("Refusing path outside workspace") >= 2
