"""Print a bounded slice of pytest node IDs.

This is used when a large xdist run dies by percentage/progress output only.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence

import pytest


class NodeIdCollector:
    def __init__(self, start: int, limit: int) -> None:
        self.start = max(1, start)
        self.limit = max(1, limit)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        end = self.start + self.limit - 1
        for index, item in enumerate(session.items, start=1):
            if index < self.start:
                continue
            if index > end:
                break
            print(f"{index}: {item.nodeid}")
        pytest.exit("node ids listed", returncode=0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=["tests"], help="pytest paths to collect")
    parser.add_argument("--start", type=int, default=1, help="1-based first node id index")
    parser.add_argument("--limit", type=int, default=120, help="maximum node ids to print")
    args = parser.parse_args(argv)

    pytest_args = [*args.paths, "-q", "--disable-warnings"]
    return int(pytest.main(pytest_args, plugins=[NodeIdCollector(args.start, args.limit)]))


if __name__ == "__main__":
    raise SystemExit(main())
