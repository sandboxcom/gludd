"""Minimize test modules that corrupt state during pytest collection.

The probe collects candidate modules but selects only one known sentinel test.
This makes import-time side effects observable without executing the candidate
tests themselves.  The delta-debugging loop also handles polluters that require
an interaction between multiple modules.
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

DEFAULT_TARGET = (
    "tests/controllers/test_pause_slice3_capture.py"
    "::test_endpoint_pause_project_captures_resources_from_request"
)
POLLUTION_MARKERS = (
    "TypeError: 'Event' object can't be awaited",
    "NotImplementedError: Operator 'getitem' is not supported on this expression",
    "RecursionError: maximum recursion depth exceeded",
)


class PolluterNotReproducedError(RuntimeError):
    """Raised when the supplied candidate set does not trigger the failure."""


Probe = Callable[[Sequence[str]], bool]


def is_collection_pollution(output: str) -> bool:
    """Return whether output contains a known collection-corruption signature."""

    return any(marker in output for marker in POLLUTION_MARKERS)


def slice_candidates(
    candidates: Sequence[str],
    *,
    start: int,
    limit: int,
) -> list[str]:
    """Select a resumable window; a zero limit means through the end."""

    end = None if limit == 0 else start + limit
    return list(candidates[start:end])


def _chunks(items: Sequence[str], count: int) -> list[list[str]]:
    width = max(1, math.ceil(len(items) / count))
    return [list(items[start : start + width]) for start in range(0, len(items), width)]


def minimize_polluting_set(candidates: Sequence[str], probe: Probe) -> list[str]:
    """Return a one-minimal candidate subset that still pollutes collection."""

    current = list(candidates)
    if not current or not probe(current):
        raise PolluterNotReproducedError(
            "candidate set did not reproduce the collection-time failure"
        )

    granularity = 2
    while len(current) >= 2:
        partitions = _chunks(current, granularity)
        reduced = False

        for partition in partitions:
            if probe(partition):
                current = partition
                granularity = 2
                reduced = True
                break
        if reduced:
            continue

        for partition in partitions:
            partition_set = set(partition)
            complement = [item for item in current if item not in partition_set]
            if complement and probe(complement):
                current = complement
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if reduced:
            continue

        if granularity >= len(current):
            break
        granularity = min(len(current), granularity * 2)

    return current


class PytestCollectionProbe:
    """Run the sentinel after collecting a configurable module subset."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.target_name = target.rsplit("::", 1)[-1]
        self.run_count = 0

    def __call__(self, candidates: Sequence[str]) -> bool:
        self.run_count += 1
        print(
            (
                f"[collection-bisect] probe={self.run_count} "
                f"candidates={len(candidates)} "
                f"first={candidates[0]} last={candidates[-1]}"
            ),
            flush=True,
        )
        with tempfile.TemporaryDirectory(
            prefix="gludd-collection-bisect-",
            dir="/tmp",
        ) as basetemp:
            command = [
                sys.executable,
                "-m",
                "pytest",
                *candidates,
                self.target,
                "-n",
                "1",
                "--dist",
                "loadgroup",
                "-q",
                "-k",
                self.target_name,
                "--tb=short",
                "--maxfail=1",
                "--cov=general_ludd",
                "--cov-report=",
                "--cov-fail-under=0",
                f"--basetemp={basetemp}",
            ]
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )

        output = completed.stdout + completed.stderr
        if completed.returncode == 0:
            return False
        if is_collection_pollution(output):
            return True

        tail = "\n".join(output.splitlines()[-80:])
        raise RuntimeError(
            "pytest collection probe failed for an unexpected reason:\n" + tail
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", default="tests/unit")
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    candidate_dir = Path(args.candidate_dir)
    all_candidates = sorted(
        str(path)
        for path in candidate_dir.rglob("test_*.py")
        if path.is_file()
    )
    candidates = slice_candidates(
        all_candidates,
        start=args.start,
        limit=args.limit,
    )
    print(
        (
            f"[collection-bisect] discovered={len(all_candidates)} "
            f"selected={len(candidates)} start={args.start} "
            f"limit={args.limit} dir={candidate_dir}"
        ),
        flush=True,
    )
    try:
        result = minimize_polluting_set(
            candidates,
            PytestCollectionProbe(args.target),
        )
    except PolluterNotReproducedError:
        print(
            "[collection-bisect] PASS: no known collection pollution reproduced",
            flush=True,
        )
        return 0
    print("[collection-bisect] minimal polluting set:", flush=True)
    for path in result:
        print(path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
