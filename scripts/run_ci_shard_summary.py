#!/usr/bin/env python3
"""Run one named CI shard and emit the same summary markers as the parallel runner."""

from __future__ import annotations

import argparse
import shlex

from run_ci_shards_parallel import run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", required=True)
    parser.add_argument("--pytest-args", default="")
    parser.add_argument("--workers-per-shard", type=int, default=1)
    args = parser.parse_args()
    return run([args.shard], shlex.split(args.pytest_args), args.workers_per_shard, heartbeat_seconds=30)


if __name__ == "__main__":
    raise SystemExit(main())
