#!/usr/bin/env python3
"""Standalone transliteration script for the transliterate role."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _find_src_root() -> Path:
    """Walk upward from this file to the repo root (pyproject.toml marker)."""
    current = Path(__file__).resolve().parent
    for _ in range(32):
        if (current / "pyproject.toml").is_file():
            return current / "src"
        if current.parent == current:
            break
        current = current.parent
    return Path(__file__).resolve().parents[3] / "src"


_SRC = _find_src_root()
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> None:
    parser = argparse.ArgumentParser(description="Transliterate text between scripts")
    parser.add_argument("--text", required=True, help="Text to transliterate")
    parser.add_argument("--target-script", default="Latin", help="Target script (default: Latin)")
    parser.add_argument("--scheme", default=None, help="Transliteration scheme (auto-detected if not set)")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    args = parser.parse_args()

    from general_ludd.language.transliteration import transliterate

    result = transliterate(args.text, args.target_script, args.scheme)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
