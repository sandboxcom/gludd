"""Classify collection failures or incrementally triage a streamed pytest log.

With no arguments this preserves the original AA063 collect-only check.  With
``--log`` it consumes only bytes appended since the prior invocation, merges
pytest ``FAILED``/``ERROR`` node IDs into a durable snapshot, and emits one
compact JSON record suitable for an agent or log processor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
RATCHET_FILE = ROOT / "config" / "ratchet.yml"
BASELINE_FILE = ROOT / "BASELINE.md"

STATE_VERSION = 1

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_NODE_PATTERN = r"(?:[^\s:]+/)*test[^\s:]*\.py(?:::[^\r\n]+?)?"
_PREFIX_STATUS_RE = re.compile(
    rf"^\s*(?:\[[^\]]+\]\s*)*(?P<kind>FAILED|ERROR)\s+"
    rf"(?P<nodeid>{_NODE_PATTERN})(?:\s+-\s+(?P<reason>.+))?\s*$"
)
_SUFFIX_STATUS_RE = re.compile(
    rf"^\s*(?P<nodeid>{_NODE_PATTERN})\s+(?P<kind>FAILED|ERROR)"
    r"(?:\s+\[[^\]]+\])?\s*$"
)
_COLLECTION_ERROR_RE = re.compile(
    rf"^\s*(?:\[[^\]]+\]\s*)*ERROR collecting (?P<nodeid>{_NODE_PATTERN})\s*$"
)
_EXCEPTION_RE = re.compile(
    r"\b(?:[A-Za-z_]\w*\.)*(?P<name>[A-Z]\w*(?:Error|Exception|Failure))\b"
)


@dataclass(frozen=True)
class FailureRecord:
    """One unique failed/error pytest node."""

    kind: str
    nodeid: str
    cause: str

    @property
    def file(self) -> str:
        return self.nodeid.split("::", 1)[0]

def _load_known_failures() -> set[str]:
    """Load known/pre-existing test failures from ratchet.yml and BASELINE.md."""
    known: set[str] = set()

    if RATCHET_FILE.exists():
        for line in RATCHET_FILE.read_text().split("\n"):
            match = re.search(r"tests?[/\w.-]+\.py", line)
            if match:
                known.add(match.group(0))

    if BASELINE_FILE.exists():
        for line in BASELINE_FILE.read_text().split("\n"):
            match = re.search(r"tests?[/\w.-]+\.py", line)
            if match:
                known.add(match.group(0))

    return known


def _run_pytest_collect() -> str:
    """Run pytest --collect-only to find failures without running tests."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return result.stdout + result.stderr
    except Exception as error:
        return str(error)


def _normalise_cause(kind: str, reason: str | None) -> str:
    if not reason:
        return "runtime_failure" if kind == "FAILED" else "runtime_error"
    exception = _EXCEPTION_RE.search(reason)
    if exception:
        return exception.group("name")
    lowered = reason.lstrip().lower()
    if lowered.startswith("assert"):
        return "AssertionError"
    if lowered.startswith("failed:"):
        return "TestFailure"
    return "summary_failure" if kind == "FAILED" else "summary_error"


def _cause_rank(cause: str) -> int:
    if cause.startswith("runtime_"):
        return 0
    if cause.startswith("summary_") or cause == "collection_error":
        return 1
    return 2


def _merge_record(
    records: dict[str, FailureRecord], incoming: FailureRecord
) -> tuple[bool, bool]:
    previous = records.get(incoming.nodeid)
    if previous is None:
        records[incoming.nodeid] = incoming
        return True, False

    kind = "ERROR" if "ERROR" in {previous.kind, incoming.kind} else "FAILED"
    cause = previous.cause
    if _cause_rank(incoming.cause) > _cause_rank(previous.cause):
        cause = incoming.cause
    merged = FailureRecord(kind=kind, nodeid=incoming.nodeid, cause=cause)
    changed = merged != previous
    records[incoming.nodeid] = merged
    return False, changed


def parse_runtime_lines(lines: Iterable[str]) -> list[FailureRecord]:
    """Parse and deduplicate pytest failure statuses from lines available so far."""
    records: dict[str, FailureRecord] = {}
    for raw_line in lines:
        line = _ANSI_ESCAPE_RE.sub("", raw_line.rstrip("\r\n"))
        collection_match = _COLLECTION_ERROR_RE.match(line)
        if collection_match:
            record = FailureRecord(
                kind="ERROR",
                nodeid=collection_match.group("nodeid").strip(),
                cause="collection_error",
            )
            _merge_record(records, record)
            continue

        match = _PREFIX_STATUS_RE.match(line) or _SUFFIX_STATUS_RE.match(line)
        if not match:
            continue
        kind = match.group("kind")
        reason = match.groupdict().get("reason")
        record = FailureRecord(
            kind=kind,
            nodeid=match.group("nodeid").strip(),
            cause=_normalise_cause(kind, reason),
        )
        _merge_record(records, record)
    return list(records.values())


def _default_state_path(log_path: Path) -> Path:
    identity = hashlib.blake2s(
        str(log_path.resolve()).encode("utf-8"), digest_size=8
    ).hexdigest()
    return Path(tempfile.gettempdir()) / f"gludd-{identity}-triage-state.json"


def _prefix_fingerprint(path: Path, size: int) -> str:
    if size <= 0:
        return ""
    with path.open("rb") as stream:
        return hashlib.blake2s(stream.read(size), digest_size=16).hexdigest()


def _state_records(raw: object) -> dict[str, FailureRecord]:
    if not isinstance(raw, dict):
        return {}
    records: dict[str, FailureRecord] = {}
    for nodeid, value in raw.items():
        if not isinstance(nodeid, str) or not isinstance(value, dict):
            continue
        kind = value.get("kind")
        cause = value.get("cause")
        if kind not in {"FAILED", "ERROR"} or not isinstance(cause, str):
            continue
        records[nodeid] = FailureRecord(kind=kind, nodeid=nodeid, cause=cause)
    return records


def _load_state(
    log_path: Path, state_path: Path, inode: int, size: int
) -> tuple[int, dict[str, FailureRecord], bool]:
    if not state_path.exists():
        return 0, {}, False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
            raise ValueError("unsupported state")
        offset = state.get("offset")
        previous_inode = state.get("inode")
        previous_path = state.get("log")
        fingerprint_size = state.get("fingerprint_size", 0)
        fingerprint = state.get("fingerprint", "")
        if not isinstance(offset, int) or not isinstance(fingerprint_size, int):
            raise ValueError("invalid cursor")
        if (
            previous_inode != inode
            or previous_path != str(log_path.resolve())
            or size < offset
            or fingerprint_size > size
            or _prefix_fingerprint(log_path, fingerprint_size) != fingerprint
        ):
            return 0, {}, True
        return offset, _state_records(state.get("failures")), False
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0, {}, True


def _save_state(
    log_path: Path,
    state_path: Path,
    inode: int,
    offset: int,
    records: dict[str, FailureRecord],
) -> None:
    fingerprint_size = min(offset, 4096)
    state = {
        "version": STATE_VERSION,
        "log": str(log_path.resolve()),
        "inode": inode,
        "offset": offset,
        "fingerprint_size": fingerprint_size,
        "fingerprint": _prefix_fingerprint(log_path, fingerprint_size),
        "failures": {
            nodeid: {"kind": record.kind, "cause": record.cause}
            for nodeid, record in sorted(records.items())
        },
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(state_path)


def _classification(record: FailureRecord, known: set[str]) -> str:
    return "preexisting" if any(item in record.nodeid for item in known) else "new"


def _record_payload(record: FailureRecord, known: set[str]) -> dict[str, str]:
    return {
        "kind": record.kind,
        "nodeid": record.nodeid,
        "file": record.file,
        "cause": record.cause,
        "classification": _classification(record, known),
    }


def _group_files(
    records: Iterable[FailureRecord], known: set[str]
) -> list[dict[str, str | int]]:
    groups: dict[str, dict[str, str | int]] = {}
    for record in records:
        group = groups.setdefault(
            record.file,
            {
                "file": record.file,
                "total": 0,
                "failed": 0,
                "error": 0,
                "new": 0,
                "preexisting": 0,
            },
        )
        group["total"] = cast(int, group["total"]) + 1
        kind_key = "failed" if record.kind == "FAILED" else "error"
        group[kind_key] = cast(int, group[kind_key]) + 1
        class_key = _classification(record, known)
        group[class_key] = cast(int, group[class_key]) + 1
    return [groups[key] for key in sorted(groups)]


def _group_causes(records: Iterable[FailureRecord]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        group = grouped.setdefault(
            record.cause, {"cause": record.cause, "total": 0, "files": set()}
        )
        group["total"] += 1
        group["files"].add(record.file)
    return [
        {
            "cause": group["cause"],
            "total": group["total"],
            "files": sorted(group["files"]),
        }
        for _, group in sorted(grouped.items())
    ]


def triage_runtime_log(log_path: Path, state_path: Path | None = None) -> dict[str, Any]:
    """Consume the appended portion of *log_path* and return its current triage."""
    resolved_log = log_path.resolve()
    stat = resolved_log.stat()
    resolved_state = (state_path or _default_state_path(resolved_log)).resolve()
    offset, records, reset = _load_state(
        resolved_log, resolved_state, stat.st_ino, stat.st_size
    )

    with resolved_log.open("rb") as stream:
        stream.seek(offset)
        appended = stream.read()
    last_line_break = max(appended.rfind(b"\n"), appended.rfind(b"\r"))
    consumed = appended[: last_line_break + 1]
    next_offset = offset + len(consumed)
    incoming = parse_runtime_lines(consumed.decode("utf-8", errors="replace").splitlines())

    delta_new: list[FailureRecord] = []
    delta_updated: list[FailureRecord] = []
    for record in incoming:
        is_new, is_updated = _merge_record(records, record)
        if is_new:
            delta_new.append(records[record.nodeid])
        elif is_updated:
            delta_updated.append(records[record.nodeid])

    _save_state(resolved_log, resolved_state, stat.st_ino, next_offset, records)
    known = _load_known_failures()
    ordered = [records[nodeid] for nodeid in sorted(records)]
    classifications = [_classification(record, known) for record in ordered]
    return {
        "mode": "runtime_log",
        "log": str(resolved_log),
        "state": str(resolved_state),
        "cursor": {
            "offset": next_offset,
            "bytes_read": len(consumed),
            "reset": reset,
        },
        "counts": {
            "total": len(ordered),
            "failed": sum(record.kind == "FAILED" for record in ordered),
            "error": sum(record.kind == "ERROR" for record in ordered),
            "new": classifications.count("new"),
            "preexisting": classifications.count("preexisting"),
            "delta": len(delta_new),
            "updated": len(delta_updated),
        },
        "delta": {
            "new": [_record_payload(record, known) for record in delta_new],
            "updated": [_record_payload(record, known) for record in delta_updated],
        },
        "files": _group_files(ordered, known),
        "root_causes": _group_causes(ordered),
    }


def _collect_only_main() -> int:
    output = _run_pytest_collect()
    import_errors = re.findall(r"ERROR collecting (tests?[\w/.-]+\.py)", output)
    known = _load_known_failures()
    new_failures: list[str] = []
    preexisting: list[str] = []

    for failure in import_errors:
        clean = failure.strip()
        if any(item in clean for item in known):
            preexisting.append(clean)
        else:
            new_failures.append(clean)

    print("Test failure triage:")
    print(f"  Total collection errors: {len(import_errors)}")
    if preexisting:
        print(f"  PRE-EXISTING (tracked in ratchet/baseline): {len(preexisting)}")
        for failure in preexisting:
            print(f"    {failure}")
    if new_failures:
        print(f"  NEW (must fix immediately): {len(new_failures)}")
        for failure in new_failures:
            print(f"    {failure}")
    if not import_errors:
        print("  PASS: 0 collection errors")
        return 0
    if new_failures:
        print(f"\nACTION REQUIRED: Fix {len(new_failures)} new failure(s) before proceeding.")
        return 1
    print(f"\nNo new failures to fix. Preexisting failures ({len(preexisting)}) tracked separately.")
    return 0


def _print_runtime_human(payload: dict[str, Any]) -> None:
    counts = payload["counts"]
    cursor = payload["cursor"]
    print(
        "Runtime failure triage: "
        f"total={counts['total']} failed={counts['failed']} error={counts['error']} "
        f"delta={counts['delta']} updated={counts['updated']} "
        f"bytes_read={cursor['bytes_read']}"
    )
    for item in payload["delta"]["new"]:
        print(f"  {item['kind']} {item['nodeid']} ({item['cause']})")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, help="streamed pytest log to consume")
    parser.add_argument("--state", type=Path, help="incremental cursor/snapshot path")
    parser.add_argument("--format", choices=("json", "human"), default="json")
    args = parser.parse_args(argv)

    if args.log is None:
        return _collect_only_main()
    try:
        payload = triage_runtime_log(args.log, args.state)
    except FileNotFoundError:
        error = {
            "mode": "runtime_log",
            "error": "log_not_found",
            "log": str(args.log.resolve()),
        }
        print(json.dumps(error, sort_keys=True, separators=(",", ":")))
        return 2

    if args.format == "json":
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        _print_runtime_human(payload)
    counts = cast(dict[str, int], payload["counts"])
    return 1 if counts["new"] else 0


if __name__ == "__main__":
    sys.exit(main())
