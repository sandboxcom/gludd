#!/usr/bin/env python3
"""Remove stale gludd CI shard scratch directories without touching active runs."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

DEFAULT_MIN_AGE_SECONDS = 6 * 60 * 60
PATTERNS = ("gludd-ci-shard-*", "gludd-unit-shard-*")


def iter_candidates(tmp_root: Path) -> list[Path]:
    candidates: dict[str, Path] = {}
    for pattern in PATTERNS:
        for path in tmp_root.glob(pattern):
            candidates[str(path)] = path
    return [candidates[key] for key in sorted(candidates)]


def is_stale(path: Path, *, now: float, min_age_seconds: int) -> bool:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    return now - stat.st_mtime >= min_age_seconds


def clean_ci_shard_scratch(
    *,
    tmp_root: Path = Path("/tmp"),
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    now = time.time()
    removed: list[str] = []
    skipped: list[str] = []
    for path in iter_candidates(tmp_root):
        if not path.exists():
            continue
        if not path.is_dir():
            skipped.append(f"{path}:not-directory")
            continue
        if not is_stale(path, now=now, min_age_seconds=min_age_seconds):
            skipped.append(f"{path}:recent")
            continue
        if not dry_run:
            shutil.rmtree(path)
        removed.append(str(path))
    return {"removed": removed, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tmp-root", default="/tmp")
    parser.add_argument("--min-age-seconds", type=int, default=DEFAULT_MIN_AGE_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    result = clean_ci_shard_scratch(
        tmp_root=Path(args.tmp_root),
        min_age_seconds=args.min_age_seconds,
        dry_run=args.dry_run,
    )
    for path in result["removed"]:
        print(f"removed {path}")
    for item in result["skipped"]:
        print(f"skipped {item}")

    removed_count = len(result["removed"])
    skipped_count = len(result["skipped"])
    print(
        "Removed stale gludd CI shard scratch directories "
        f"removed={removed_count} skipped={skipped_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
