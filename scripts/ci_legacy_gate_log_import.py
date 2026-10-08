#!/usr/bin/env python3
"""Import one completed legacy gate log as bounded non-reusable evidence."""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.ci_batch_receipts import (
        canonical_json_bytes,
        canonical_json_sha256,
        failure_class_for_returncode,
    )
    from scripts.ci_shards_log_context import _allowed_log_path
else:
    from ci_batch_receipts import (
        canonical_json_bytes,
        canonical_json_sha256,
        failure_class_for_returncode,
    )
    from ci_shards_log_context import _allowed_log_path

IMPORT_SCHEMA_VERSION = 1
MAX_IMPORT_BYTES = 64 * 1024**2
MAX_IMPORT_LINES = 250_000
MAX_IMPORT_FAILURES = 64
MAX_IMPORT_OUTPUT_BYTES = 32 * 1024
MAX_IMPORT_LINE_BYTES = 1024**2
_RUN_ID = r"[A-Za-z0-9][A-Za-z0-9._-]{7,79}"
_SHARD = r"[a-z0-9][a-z0-9-]{0,31}"
_RUN_ID_SEARCH = re.compile(rf"\brun_id=(?P<run_id>{_RUN_ID})\b")
_FINISHED = re.compile(
    rf"^\[gate-background-watch\] outcome=finished "
    rf"pid=[1-9][0-9]{{0,9}} run_id=(?P<run_id>{_RUN_ID})$"
)
_PHASE = re.compile(
    r"^SERIAL-SHARD-PHASE phase="
    r"(?P<phase>[a-z0-9][a-z0-9-]{0,63}"
    r"(?::[a-z0-9][a-z0-9-]{0,63}){0,3}) "
    r"result=(?P<result>0|[1-9][0-9]{0,2}|not-started)$"
)
_PRIMARY_BATCH_PHASE = re.compile(
    rf"^(?P<shard>{_SHARD}):batch-(?P<batch>[0-9]{{3}})$"
)
_BATCH_PASS = re.compile(
    rf"^SHARD-BATCH-PASS shard=(?P<shard>{_SHARD}) "
    r"batch=(?P<batch>[1-9][0-9]{0,3}) rc=0$"
)
_BATCH_FAIL = re.compile(
    rf"^SHARD-FAIL shard=(?P<shard>{_SHARD}) "
    r"batch=(?P<batch>[1-9][0-9]{0,3}) "
    r"rc=(?P<rc>[1-9][0-9]{0,2}); "
    r"later-batches=(?:continuing|not-started)$"
)
_SUMMARY = re.compile(
    r"^SERIAL-SHARD-SUMMARY total=(?P<total>[1-9][0-9]{0,2}) "
    r"failed=(?P<failed>[0-9]{1,3}) failures=\{.*\} phases=\{.*\}$"
)


@dataclass(frozen=True)
class LegacyGateImportResult:
    """One content-free importer decision and its bounded report."""

    accepted: bool
    reason: str
    report: dict[str, object]


def _non_reusable_report() -> dict[str, object]:
    return {
        "reuse_policy": "NON-REUSABLE",
        "skip_authorized": False,
        "skips": 0,
    }


def _rejected(reason: str) -> LegacyGateImportResult:
    return LegacyGateImportResult(False, reason, _non_reusable_report())


def render_import_result(result: LegacyGateImportResult) -> str:
    """Render one deterministic ASCII JSON line without source paths or payloads."""
    return (
        canonical_json_bytes(
            {
                "accepted": result.accepted,
                "reason": result.reason,
                "report": result.report,
                "schema_version": IMPORT_SCHEMA_VERSION,
            }
        ).decode("ascii")
        + "\n"
    )


class LegacyGateLogImporter:
    """Read one confined completed log and derive non-reusable diagnostics."""

    def __init__(
        self,
        allowed_root: Path,
        *,
        max_bytes: int = MAX_IMPORT_BYTES,
        max_lines: int = MAX_IMPORT_LINES,
        max_failures: int = MAX_IMPORT_FAILURES,
        max_output_bytes: int = MAX_IMPORT_OUTPUT_BYTES,
    ) -> None:
        if max_bytes < 1 or max_bytes > MAX_IMPORT_BYTES:
            raise ValueError("legacy import byte bound is outside the hard limit")
        if max_lines < 1 or max_lines > MAX_IMPORT_LINES:
            raise ValueError("legacy import line bound is outside the hard limit")
        if max_failures < 1 or max_failures > MAX_IMPORT_FAILURES:
            raise ValueError("legacy import failure bound is outside the hard limit")
        if max_output_bytes < 256 or max_output_bytes > MAX_IMPORT_OUTPUT_BYTES:
            raise ValueError("legacy import output bound is outside the hard limit")
        self._allowed_root = allowed_root
        self._max_bytes = max_bytes
        self._max_lines = max_lines
        self._max_failures = max_failures
        self._max_output_bytes = max_output_bytes

    def _read_source(self, log_path: Path) -> tuple[bytes | None, str | None]:
        if log_path.is_symlink():
            return None, "source-symlink"
        if ".." in log_path.parts:
            return None, "source-unconfined"
        if not _allowed_log_path(log_path, self._allowed_root):
            return None, "source-unconfined"
        try:
            before = log_path.stat()
            if before.st_size < 1:
                return None, "empty-log"
            if before.st_size > self._max_bytes:
                return None, "log-too-large"
            content = log_path.read_bytes()
            after = log_path.stat()
        except OSError:
            return None, "source-unavailable"
        stable_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        )
        stable_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        )
        if stable_before != stable_after or len(content) != before.st_size:
            return None, "source-mutated"
        if len(content) > self._max_bytes:
            return None, "log-too-large"
        return content, None

    def import_log(self, log_path: Path) -> LegacyGateImportResult:
        """Import one exact file without mutating it or authorizing reuse."""
        content, source_error = self._read_source(log_path)
        if source_error is not None or content is None:
            return _rejected(source_error or "source-unavailable")
        if not content.endswith(b"\n"):
            return _rejected("truncated-log")
        raw_lines = content.removesuffix(b"\n").split(b"\n")
        if len(raw_lines) > self._max_lines:
            return _rejected("line-limit")
        if any(len(line) > MAX_IMPORT_LINE_BYTES for line in raw_lines):
            return _rejected("line-too-large")
        try:
            lines = [line.decode("utf-8", errors="strict") for line in raw_lines]
        except UnicodeDecodeError:
            return _rejected("encoding-invalid")

        run_ids = {
            match.group("run_id")
            for line in lines
            for match in [_RUN_ID_SEARCH.search(line)]
            if match is not None
        }
        if len(run_ids) > 1:
            return _rejected("mixed-run-log")
        if len(run_ids) != 1:
            return _rejected("run-identity-missing")
        run_id = next(iter(run_ids))
        finished = [match for line in lines if (match := _FINISHED.fullmatch(line))]
        if (
            len(finished) != 1
            or finished[0].group("run_id") != run_id
            or _FINISHED.fullmatch(lines[-1]) is None
        ):
            return _rejected("truncated-log")

        failed_terminals = sum(line == "=== GATE: FAILED ===" for line in lines)
        passed_terminals = sum(line == "=== GATE: PASSED ===" for line in lines)
        if passed_terminals:
            return _rejected("mixed-terminal-log")
        if failed_terminals < 1 or failed_terminals > 2:
            return _rejected("truncated-log")

        summaries = [match for line in lines if (match := _SUMMARY.fullmatch(line))]
        if len(summaries) != 1:
            return _rejected("summary-invalid")
        summary = summaries[0]

        phase_results: list[tuple[str, int | str]] = []
        primary_results: dict[tuple[str, int], int] = {}
        pass_coordinates: set[tuple[str, int]] = set()
        failure_results: dict[tuple[str, int], int] = {}
        for line in lines:
            if line.startswith("SERIAL-SHARD-PHASE "):
                phase_match = _PHASE.fullmatch(line)
                if phase_match is None:
                    return _rejected("phase-marker-invalid")
                raw_result = phase_match.group("result")
                phase_result: int | str = (
                    raw_result if raw_result == "not-started" else int(raw_result)
                )
                phase = phase_match.group("phase")
                phase_results.append((phase, phase_result))
                primary_match = _PRIMARY_BATCH_PHASE.fullmatch(phase)
                if primary_match is not None:
                    coordinate = (
                        primary_match.group("shard"),
                        int(primary_match.group("batch")),
                    )
                    if coordinate in primary_results or not isinstance(phase_result, int):
                        return _rejected("ambiguous-batch")
                    primary_results[coordinate] = phase_result
            elif line.startswith("SHARD-BATCH-PASS "):
                pass_match = _BATCH_PASS.fullmatch(line)
                if pass_match is None:
                    return _rejected("batch-marker-invalid")
                coordinate = (
                    pass_match.group("shard"),
                    int(pass_match.group("batch")),
                )
                if coordinate in pass_coordinates:
                    return _rejected("ambiguous-batch")
                pass_coordinates.add(coordinate)
            elif line.startswith("SHARD-FAIL "):
                failure_match = _BATCH_FAIL.fullmatch(line)
                if failure_match is None:
                    return _rejected("failure-marker-invalid")
                coordinate = (
                    failure_match.group("shard"),
                    int(failure_match.group("batch")),
                )
                if coordinate in failure_results:
                    return _rejected("ambiguous-failure")
                failure_results[coordinate] = int(failure_match.group("rc"))
                if len(failure_results) > self._max_failures:
                    return _rejected("failure-limit")

        if not primary_results or not phase_results:
            return _rejected("phase-evidence-missing")
        if pass_coordinates.intersection(failure_results):
            return _rejected("ambiguous-batch")
        if set(primary_results) != pass_coordinates.union(failure_results):
            return _rejected("batch-evidence-incomplete")
        if any(primary_results[coordinate] != 0 for coordinate in pass_coordinates):
            return _rejected("batch-result-mismatch")
        if any(
            primary_results.get(coordinate) != returncode
            for coordinate, returncode in failure_results.items()
        ):
            return _rejected("batch-result-mismatch")
        if int(summary.group("failed")) != len(failure_results):
            return _rejected("summary-mismatch")
        observed_shards = {coordinate[0] for coordinate in primary_results}
        if int(summary.group("total")) != len(observed_shards):
            return _rejected("summary-mismatch")

        run_digest = canonical_json_sha256(
            {"kind": "legacy-gate-run", "run_id": run_id}
        )
        failure_receipts: list[dict[str, object]] = []
        for (shard, batch), returncode in sorted(failure_results.items()):
            receipt = {
                "batch_identity_sha256": canonical_json_sha256(
                    {
                        "batch": batch,
                        "run_sha256": run_digest,
                        "shard": shard,
                    }
                ),
                "failure_class": failure_class_for_returncode(returncode),
                "receipt_kind": "legacy-gate-log-failure",
                "reuse_policy": "NON-REUSABLE",
                "schema_version": IMPORT_SCHEMA_VERSION,
                "skip_authorized": False,
            }
            failure_receipts.append(
                {**receipt, "receipt_sha256": canonical_json_sha256(receipt)}
            )

        numeric_phase_results = [
            result for _phase, result in phase_results if isinstance(result, int)
        ]
        report = {
            "batches": {
                "executed": len(primary_results),
                "failed": len(failure_results),
                "passed": len(pass_coordinates),
            },
            "failure_receipts": failure_receipts,
            "kind": "legacy-gate-log-failure-import",
            "phases": {
                "failed": sum(result != 0 for result in numeric_phase_results),
                "not_started": sum(
                    result == "not-started" for _phase, result in phase_results
                ),
                "observed": len(phase_results),
                "passed": sum(result == 0 for result in numeric_phase_results),
            },
            "planned_shards": int(summary.group("total")),
            "reuse_policy": "NON-REUSABLE",
            "schema_version": IMPORT_SCHEMA_VERSION,
            "skip_authorized": False,
            "skips": 0,
            "source": {
                "byte_count": len(content),
                "line_count": len(lines),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            "terminal_gate_result": "failed",
        }
        result = LegacyGateImportResult(True, "imported-non-reusable", report)
        if len(render_import_result(result).encode("ascii")) > self._max_output_bytes:
            return _rejected("output-limit")
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--allowed-root", required=True, type=Path)
    args = parser.parse_args()
    result = LegacyGateLogImporter(args.allowed_root).import_log(args.log)
    print(render_import_result(result), end="")
    return 0 if result.accepted else 1


__all__ = [
    "IMPORT_SCHEMA_VERSION",
    "MAX_IMPORT_BYTES",
    "MAX_IMPORT_FAILURES",
    "MAX_IMPORT_LINES",
    "MAX_IMPORT_LINE_BYTES",
    "MAX_IMPORT_OUTPUT_BYTES",
    "LegacyGateImportResult",
    "LegacyGateLogImporter",
    "render_import_result",
]


if __name__ == "__main__":
    raise SystemExit(main())
