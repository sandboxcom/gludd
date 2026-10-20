#!/usr/bin/env python3
"""Replace exact UTF-8 text and validate literal base64 text inputs.

The existing three-path command remains:
    python3 scripts/replace_text.py <file> <old_text_file> <new_text_file>

Text inputs must be UTF-8 without NUL. Base64 entry functions accept standard,
strict base64 (including the empty string), never arbitrary binary payloads.
"""

from __future__ import annotations

import base64
import binascii
import sys
from io import StringIO
from pathlib import Path


def _validate_text(text: str, label: str) -> str:
    if "\x00" in text:
        raise ValueError(f"{label} contains NUL")
    return text


def _decode_text_b64(encoded: str, label: str) -> str:
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{label} must be valid base64") from exc
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must decode as valid UTF-8") from exc
    return _validate_text(text, label)


def write_text_b64(target: Path | str, encoded: str) -> None:
    """Validate before opening; retain binary-write bytes, mode and path semantics."""
    text = _decode_text_b64(encoded, "TEXT_B64")
    Path(target).write_bytes(text.encode("utf-8"))


def _replace_text(target: Path, old_text: str, new_text: str) -> int:
    content = _validate_text(target.read_text(encoding="utf-8"), str(target))
    if old_text not in content:
        print(f"ERROR: old text not found in {target}", file=sys.stderr)
        return 1

    count = content.count(old_text)
    if count > 1:
        print(
            f"ERROR: old text found {count} times in {target} (expected 1)",
            file=sys.stderr,
        )
        return 1

    target.write_text(content.replace(old_text, new_text), encoding="utf-8")
    print(f"Replaced 1 occurrence in {target}")
    return 0


def replace_text_b64(target: Path | str, old_encoded: str, new_encoded: str) -> int:
    """Validate both fields before destination IO; retain exact-one-match policy."""
    old_text = _decode_text_b64(old_encoded, "OLD_B64")
    new_text = _decode_text_b64(new_encoded, "NEW_B64")
    # The old temporary-file CLI used universal-newline text reads for both fields.
    with StringIO(old_text, newline=None) as old_input, StringIO(new_text, newline=None) as new_input:
        return _replace_text(Path(target), old_input.read(), new_input.read())


def main() -> int:
    if len(sys.argv) != 4:
        print(
            f"Usage: {sys.argv[0]} <file> <old_text_file> <new_text_file>",
            file=sys.stderr,
        )
        return 2

    target = Path(sys.argv[1])
    try:
        old_text = _validate_text(Path(sys.argv[2]).read_text(encoding="utf-8"), "old text")
        new_text = _validate_text(Path(sys.argv[3]).read_text(encoding="utf-8"), "new text")
        return _replace_text(target, old_text, new_text)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
