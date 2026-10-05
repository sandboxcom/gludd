#!/usr/bin/env python3
"""Run detect-secrets against a disposable copy of the canonical baseline.

The upstream pre-commit hook deliberately refreshes baseline line metadata.
That is useful during explicit baseline maintenance but is unsafe inside a
read-only commit or push gate because it dirties the candidate being checked.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

Runner = Callable[..., subprocess.CompletedProcess[str]]
FindingIdentity = tuple[str, str, str]


def _finding_counts(path: Path) -> Counter[FindingIdentity]:
    """Return baseline findings without volatile line-number metadata."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        raise ValueError("detect-secrets baseline results must be an object")

    findings: Counter[FindingIdentity] = Counter()
    for result_path, entries in payload["results"].items():
        if not isinstance(result_path, str) or not isinstance(entries, list):
            raise ValueError("detect-secrets baseline result entries are malformed")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("detect-secrets baseline finding must be an object")
            filename = entry.get("filename", result_path)
            detector = entry.get("type")
            digest = entry.get("hashed_secret")
            if (
                not isinstance(filename, str)
                or not filename
                or not isinstance(detector, str)
                or not detector
                or not isinstance(digest, str)
                or not digest
            ):
                raise ValueError("detect-secrets baseline finding identity is malformed")
            findings[(filename, detector, digest)] += 1
    return findings


def run_readonly_scan(
    *,
    baseline: Path,
    filenames: Sequence[str],
    executable: str = "detect-secrets-hook",
    runner: Runner = subprocess.run,
) -> int:
    """Return the upstream hook status without permitting baseline mutation."""
    if not baseline.is_file():
        raise FileNotFoundError(f"detect-secrets baseline not found: {baseline}")

    descriptor, raw_path = tempfile.mkstemp(
        prefix="gludd-secrets-baseline.",
        suffix=".json",
    )
    disposable = Path(raw_path)
    try:
        # Close the descriptor before copying so this works on Windows too.
        os.close(descriptor)
        shutil.copyfile(baseline, disposable)
        command = [executable, "--baseline", str(disposable), *filenames]
        completed = runner(command, check=False)
        status = int(completed.returncode)
        if status != 3:
            return status

        # detect-secrets uses status 3 both when it only refreshes volatile
        # baseline metadata and when it adds a finding. Admit the former while
        # failing closed on every new path/detector/hash occurrence.
        try:
            canonical_findings = _finding_counts(baseline)
            disposable_findings = _finding_counts(disposable)
        except (OSError, ValueError):
            return status
        return status if disposable_findings - canonical_findings else 0
    finally:
        disposable.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run detect-secrets without mutating the tracked baseline.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(".secrets.baseline"),
        help="Canonical baseline copied for the scan (default: .secrets.baseline).",
    )
    parser.add_argument("filenames", nargs="*", help="Files supplied by pre-commit.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    executable = shutil.which("detect-secrets-hook")
    if executable is None:
        print("detect-secrets-hook is unavailable", file=sys.stderr)
        return 2
    try:
        return run_readonly_scan(
            baseline=args.baseline,
            filenames=tuple(args.filenames),
            executable=executable,
        )
    except OSError as exc:
        print(f"detect-secrets read-only scan failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
