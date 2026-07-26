#!/usr/bin/env python3
"""Codex project hook entrypoint; delegates to the versioned implementation."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.codex_stop_hook import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
