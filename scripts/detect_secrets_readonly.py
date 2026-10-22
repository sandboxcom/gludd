#!/usr/bin/env python3
"""Run detect-secrets against a disposable copy of the canonical baseline.

The upstream pre-commit hook deliberately refreshes baseline line metadata.
That is useful during explicit baseline maintenance but is unsafe inside a
read-only commit or push gate because it dirties the candidate being checked.
Full scan also refreshes its baseline and can return zero with new findings.
Compare its private pre-scan and post-scan inventories through the same owner.
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
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if TYPE_CHECKING:
    import manage_secrets_baseline as baseline_owner
else:
    from scripts import manage_secrets_baseline as baseline_owner

Runner = Callable[..., subprocess.CompletedProcess[str]]
FindingIdentity = tuple[str, str, str]


def _finding_counts(path: Path) -> Counter[FindingIdentity]:
    """Return baseline findings without volatile line-number metadata."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        raise ValueError("detect-secrets baseline results must be an object")

    try:
        return baseline_owner.finding_counts(payload)
    except baseline_owner.BaselineError as error:
        raise ValueError("detect-secrets baseline finding inventory is malformed") from error


class _ScanParserBuilder(Protocol):
    """Describe the pinned upstream builder without running its postprocessors."""

    _parser: argparse.ArgumentParser

    def add_console_use_arguments(self) -> _ScanParserBuilder:
        """Expose the native console argument grammar."""
        ...


def _validate_scan_command(command: Sequence[str], disposable: Path) -> None:
    """Check native option ownership before baseline or plugin postprocessing."""
    from detect_secrets.core.usage import ParserBuilder

    factory = cast(Callable[[], _ScanParserBuilder], ParserBuilder)
    # Public builder.parse_args also reads baselines and loads custom settings.
    # Validate through its pinned grammar before those postprocessors can run.
    try:
        with StringIO() as diagnostic, redirect_stdout(diagnostic), redirect_stderr(diagnostic):
            arguments = factory().add_console_use_arguments()._parser.parse_args(command[1:])
    except SystemExit as error:
        raise ValueError("native full-scan arguments must select an owned scan") from error
    if vars(arguments).get("action") != "scan":
        raise ValueError("native full-scan arguments must select an owned scan")
    if any(vars(arguments).get(mode) for mode in ("list_all_plugins", "string", "only_allowlisted")):
        raise ValueError("scan arguments must refresh the private inventory")
    selected: object = vars(arguments).get("baseline")
    if selected != [str(disposable)]:
        raise ValueError("full scan must use the owned disposable baseline")


def run_readonly_scan(
    *,
    baseline: Path,
    filenames: Sequence[str],
    executable: str = "detect-secrets-hook",
    runner: Runner = subprocess.run,
    scan_cores: int | None = None,
) -> int:
    """Run the upstream hook or full scan without mutating the canonical baseline."""
    if scan_cores is not None and (type(scan_cores) is not int or scan_cores < 1):
        raise ValueError("scan cores must be a positive integer")
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
        original_findings = _finding_counts(disposable) if scan_cores is not None else Counter()
        command = [executable, "--baseline", str(disposable), *filenames]
        if scan_cores is not None:
            command = [
                executable, "--cores", str(scan_cores), "scan",
                "--baseline", str(disposable), *filenames,
            ]
            _validate_scan_command(command, disposable)
        completed = runner(command, check=False, capture_output=True, text=True)
        status = int(completed.returncode)
        stdout = completed.stdout or ""
        if scan_cores is not None and status == 0:
            if _finding_counts(disposable) - original_findings:
                status = 1
                sys.stderr.write(
                    "detect-secrets: new findings in disposable scan; canonical baseline unchanged.\n",
                )
        elif scan_cores is None and status == 3:
            # detect-secrets uses status 3 both when it only refreshes volatile
            # baseline metadata and when it adds a finding. Admit the former while
            # failing closed on every new path/detector/hash occurrence.
            try:
                canonical_findings = _finding_counts(baseline)
                disposable_findings = _finding_counts(disposable)
            except (OSError, ValueError):
                pass
            else:
                if not disposable_findings - canonical_findings:
                    status = 0
                    native_update = (
                        "The baseline file was updated.\n"
                        "Probably to keep line numbers of secrets up-to-date.\n"
                        f"Please `git add {disposable}`, thank you.\n\n\n"
                    )
                    stdout = stdout.replace(
                        native_update,
                        "detect-secrets: disposable baseline copy updated; no new findings. "
                        "Canonical baseline unchanged.\n",
                    )
        sys.stdout.write(stdout)
        sys.stderr.write(completed.stderr or "")
        return status
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
    parser.add_argument(
        "--scan-cores", type=int, metavar="N",
        help="Run the native full scan with N workers (default: pre-commit hook).",
    )
    parser.add_argument(
        "filenames", nargs="*", help="Scanner arguments; use -- before full-scan options.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run read-only detection while preserving native failures and findings."""
    args = _parser().parse_args(argv)
    scanner = "detect-secrets-hook" if args.scan_cores is None else "detect-secrets"
    executable = shutil.which(scanner)
    if executable is None:
        print(f"{scanner} is unavailable", file=sys.stderr)
        return 2
    try:
        if args.scan_cores is not None:
            return run_readonly_scan(
                baseline=args.baseline, filenames=tuple(args.filenames),
                executable=executable, scan_cores=args.scan_cores,
            )
        return run_readonly_scan(
            baseline=args.baseline,
            filenames=tuple(args.filenames),
            executable=executable,
        )
    except (OSError, ValueError) as exc:
        print(f"detect-secrets read-only scan failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
