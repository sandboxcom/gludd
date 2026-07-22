#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

root = Path.cwd()
git_path = root / ".git"
if git_path.is_dir():
    gitdir = git_path
else:
    raw = git_path.read_text(encoding="utf-8").strip()
    prefix = "gitdir: "
    if not raw.startswith(prefix):
        raise SystemExit(f"unsupported .git file format: {raw!r}")
    gitdir = Path(raw[len(prefix):])
    if not gitdir.is_absolute():
        gitdir = (root / gitdir).resolve()
lock = gitdir / "index.lock"
if lock.exists():
    lock.unlink()
    print(f"removed {lock}")
else:
    print(f"no lock at {lock}")
