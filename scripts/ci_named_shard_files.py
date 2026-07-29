"""Expand the GitHub Actions named test-shard matrix locally."""

from __future__ import annotations

import argparse
import fnmatch
import shlex
from pathlib import Path

SHARDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "unit-1a1": (("tests/unit/test_a[a-m]*.py",), ("*/test_*_e2e.py",)),
    "unit-1a2": (
        ("tests/unit/test_a[n-z]*.py", "tests/unit/test_a[0-9]*.py"),
        ("*/test_*_e2e.py",),
    ),
    "unit-1b": (
        ("tests/unit/test_[ce]*.py",),
        ("*/test_connector*.py", "*/test_*_e2e.py"),
    ),
    "unit-1d": (("tests/unit/test_[bd]*.py",), ("*/test_*_e2e.py",)),
    "unit-2": (("tests/unit/test_[f-m]*.py",), ("*/test_*_e2e.py",)),
    "unit-3": (
        ("tests/unit/test_[n-z]*.py", "tests/unit/secrets/"),
        ("*/test_*_e2e.py",),
    ),
    "other": (
        (
            "tests/integration/",
            "tests/e2e/",
            "tests/live/",
            "tests/security/",
            "tests/test_*.py",
            "tests/unit/test_connector*.py",
            "tests/unit/test_*_e2e.py",
            "tests/unit/sts/",
            "tests/unit/test_e2e_test_generation/",
        ),
        (),
    ),
}


def _expand(pattern: str) -> list[str]:
    path = Path(pattern)
    if path.is_dir():
        return [pattern]
    return sorted(str(match) for match in Path().glob(pattern))


def _excluded(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def expand_shard(shard: str) -> list[str]:
    try:
        patterns, excludes = SHARDS[shard]
    except KeyError as exc:
        valid = ", ".join(sorted(SHARDS))
        raise SystemExit(f"unknown shard {shard!r}; valid shards: {valid}") from exc

    seen: set[str] = set()
    selected: list[str] = []
    for pattern in patterns:
        for path in _expand(pattern):
            if _excluded(path, excludes) or path in seen:
                continue
            seen.add(path)
            selected.append(path)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", required=True)
    parser.add_argument("--shell", action="store_true")
    args = parser.parse_args()

    paths = expand_shard(args.shard)
    if not paths:
        raise SystemExit(f"shard {args.shard!r} expanded to no test paths")
    if args.shell:
        print(" ".join(shlex.quote(path) for path in paths))
    else:
        print("\n".join(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
