"""Print a bounded, read-only view of pytest's prior-failure cache."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_CACHE = Path(".pytest_cache/v/cache/lastfailed")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_CACHE_BYTES = 8 * 1024 * 1024


class InvalidFailureCache(ValueError):
    """Raised when the pytest last-failed cache cannot be trusted."""


def _load_failures(cache_path: Path) -> list[str]:
    """Return sorted failing node IDs without modifying the cache."""
    if not cache_path.exists():
        return []
    try:
        if cache_path.stat().st_size > MAX_CACHE_BYTES:
            raise InvalidFailureCache(f"cache exceeds {MAX_CACHE_BYTES} bytes")
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidFailureCache(str(exc)) from exc
    if not isinstance(payload, dict):
        raise InvalidFailureCache("cache must contain a JSON object")
    if any(
        not isinstance(node_id, str) or not isinstance(failed, bool)
        for node_id, failed in payload.items()
    ):
        raise InvalidFailureCache("cache entries must map node IDs to booleans")
    return sorted(node_id for node_id, failed in payload.items() if failed)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report cached pytest failures without collecting or running tests."
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Report at most ``--limit`` cached node IDs; return 2 on invalid input."""
    args = _parser().parse_args(argv)
    cache_path: Path = args.cache
    limit: int = args.limit
    print(
        f"test-failures: reading prior-failure cache {cache_path} (limit={limit})",
        flush=True,
    )
    if not 1 <= limit <= MAX_LIMIT:
        print(
            f"test-failures: limit must be between 1 and {MAX_LIMIT}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    try:
        failures = _load_failures(cache_path)
    except InvalidFailureCache as exc:
        print(
            f"test-failures: invalid prior-failure cache: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    if not failures:
        print("test-failures: no prior failures recorded", flush=True)
        return 0
    visible = failures[:limit]
    print(
        f"test-failures: showing {len(visible)} of {len(failures)} prior failures",
        flush=True,
    )
    for node_id in visible:
        print(node_id, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
