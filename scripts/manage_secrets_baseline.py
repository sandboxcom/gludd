#!/usr/bin/env python3
"""Refresh and compact the detect-secrets baseline without exposing findings.

The scanner remains Yelp's pinned ``detect-secrets`` CLI. This wrapper provides
only the repository-specific safety boundary around it: exact exclusions,
semantic checks, canonical JSON, atomic replacement, and content-free evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

FindingIdentity = tuple[str, str, str]
JsonObject = dict[str, object]
AUDIT_FIELDS = ("is_secret",)


class BaselineError(RuntimeError):
    """Raised when baseline maintenance cannot prove a safe replacement."""


@dataclass(frozen=True)
class Policy:
    """Validated repository policy for the supported detect-secrets artifact."""

    max_physical_lines: int
    exclude_files: str
    required_filters: tuple[str, ...]


@dataclass(frozen=True)
class BaselineSummary:
    """Content-free evidence safe for terminals and CI logs."""

    files: int
    findings: int
    physical_lines: int
    settings_digest: str
    findings_digest: str

    def as_dict(self) -> dict[str, int | str]:
        """Return stable JSON-ready evidence without paths or secret hashes."""
        return {
            "files": self.files,
            "findings": self.findings,
            "physical_lines": self.physical_lines,
            "settings_digest": self.settings_digest,
            "findings_digest": self.findings_digest,
        }


def _load_object(path: Path) -> JsonObject:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"invalid JSON document: {path.name}") from exc
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) for key in payload
    ):
        raise BaselineError(f"JSON root must be an object: {path.name}")
    return cast(JsonObject, payload)


def load_policy(path: Path) -> Policy:
    """Load and validate the declarative scan boundary."""
    payload = _load_object(path)
    if payload.get("schema_version") != 1:
        raise BaselineError("unsupported detect-secrets policy schema")
    max_lines = payload.get("max_physical_lines")
    exclude_files = payload.get("exclude_files")
    required_filters = payload.get("required_filters")
    if not isinstance(max_lines, int) or not 0 < max_lines < 2_500:
        raise BaselineError("policy max_physical_lines must be between 1 and 2499")
    if not isinstance(exclude_files, str) or not exclude_files:
        raise BaselineError("policy exclude_files must be a non-empty regex")
    try:
        re.compile(exclude_files)
    except re.error as exc:
        raise BaselineError("policy exclude_files is not a valid regex") from exc
    if not isinstance(required_filters, list) or not all(
        isinstance(value, str) and value for value in required_filters
    ):
        raise BaselineError("policy required_filters must be non-empty strings")
    return Policy(max_lines, exclude_files, tuple(required_filters))


def _results(payload: Mapping[str, object]) -> dict[str, list[JsonObject]]:
    raw_results = payload.get("results")
    if not isinstance(raw_results, dict):
        raise BaselineError("detect-secrets baseline results must be an object")
    results: dict[str, list[JsonObject]] = {}
    for filename, raw_entries in raw_results.items():
        if not isinstance(filename, str) or not isinstance(raw_entries, list):
            raise BaselineError("detect-secrets baseline results are malformed")
        entries: list[JsonObject] = []
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict) or not all(
                isinstance(key, str) for key in raw_entry
            ):
                raise BaselineError("detect-secrets finding is malformed")
            entries.append(cast(JsonObject, raw_entry))
        results[filename] = entries
    return results


def finding_counts(payload: Mapping[str, object]) -> Counter[FindingIdentity]:
    """Return stable finding identities without exposing their values."""
    findings: Counter[FindingIdentity] = Counter()
    for result_path, entries in _results(payload).items():
        for entry in entries:
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
                raise BaselineError("detect-secrets finding identity is malformed")
            findings[(filename, detector, digest)] += 1
    return findings


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_text(payload: Mapping[str, object]) -> str:
    """Serialize one deterministic, non-slim detect-secrets JSON document."""
    return _canonical_json(payload) + "\n"


def _settings(payload: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"results", "generated_at"}
    }


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def summary(payload: Mapping[str, object], *, physical_lines: int) -> BaselineSummary:
    """Build safe, deterministic evidence for a baseline payload."""
    identities = finding_counts(payload)
    encoded_identities = [
        [*identity, count] for identity, count in sorted(identities.items())
    ]
    return BaselineSummary(
        files=len(_results(payload)),
        findings=sum(identities.values()),
        physical_lines=physical_lines,
        settings_digest=_digest(_settings(payload)),
        findings_digest=_digest(encoded_identities),
    )


def _filter_paths(payload: Mapping[str, object]) -> set[str]:
    raw_filters = payload.get("filters_used")
    if not isinstance(raw_filters, list):
        raise BaselineError("detect-secrets filters_used must be a list")
    paths: set[str] = set()
    for raw_filter in raw_filters:
        if not isinstance(raw_filter, dict):
            raise BaselineError("detect-secrets filter entry is malformed")
        path = raw_filter.get("path")
        if not isinstance(path, str) or not path:
            raise BaselineError("detect-secrets filter path is malformed")
        paths.add(path)
    return paths


def _without_policy_filters(
    payload: Mapping[str, object], policy: Policy
) -> list[object]:
    raw_filters = payload.get("filters_used")
    if not isinstance(raw_filters, list):
        raise BaselineError("detect-secrets filters_used must be a list")
    ignored = set(policy.required_filters)
    ignored.add("detect_secrets.filters.regex.should_exclude_file")
    retained: list[object] = []
    for raw_filter in raw_filters:
        if not isinstance(raw_filter, dict):
            raise BaselineError("detect-secrets filter entry is malformed")
        if raw_filter.get("path") not in ignored:
            retained.append(raw_filter)
    return retained


def validate_payload(payload: Mapping[str, object], policy: Policy) -> None:
    """Fail closed on unsupported or recursively populated baselines."""
    if not isinstance(payload.get("version"), str):
        raise BaselineError("detect-secrets version is missing")
    if not isinstance(payload.get("plugins_used"), list):
        raise BaselineError("detect-secrets plugins_used must be a list")
    missing_filters = set(policy.required_filters) - _filter_paths(payload)
    if missing_filters:
        raise BaselineError("required baseline-file filter is missing")
    excluded = re.compile(policy.exclude_files)
    if any(excluded.search(path) for path in _results(payload)):
        raise BaselineError("baseline contains a policy-excluded result path")
    finding_counts(payload)


def _finding_key(result_path: str, entry: Mapping[str, object]) -> FindingIdentity:
    filename = entry.get("filename", result_path)
    detector = entry.get("type")
    digest = entry.get("hashed_secret")
    if not all(isinstance(value, str) and value for value in (filename, detector, digest)):
        raise BaselineError("detect-secrets finding identity is malformed")
    return cast(tuple[str, str, str], (filename, detector, digest))


def _merge_audit_labels(
    previous: Mapping[str, object], fresh: JsonObject
) -> JsonObject:
    labels: defaultdict[FindingIdentity, deque[dict[str, object]]] = defaultdict(deque)
    for result_path, entries in _results(previous).items():
        for entry in entries:
            saved = {field: entry[field] for field in AUDIT_FIELDS if field in entry}
            labels[_finding_key(result_path, entry)].append(saved)
    for result_path, entries in _results(fresh).items():
        for entry in entries:
            available = labels.get(_finding_key(result_path, entry))
            if available:
                entry.update(available.popleft())
    return fresh


def _entry_sort_key(entry: Mapping[str, object]) -> tuple[int, str, str]:
    line = entry.get("line_number")
    return (
        line if isinstance(line, int) else -1,
        str(entry.get("type", "")),
        str(entry.get("hashed_secret", "")),
    )


def normalize_payload(payload: JsonObject) -> JsonObject:
    """Sort result entries while retaining the supported non-slim schema."""
    normalized_results: dict[str, list[JsonObject]] = {}
    for result_path, entries in sorted(_results(payload).items()):
        normalized_results[result_path] = sorted(entries, key=_entry_sort_key)
    payload["results"] = normalized_results
    return payload


def _write_atomic(path: Path, text: str) -> None:
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temp_path, path.stat().st_mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def compact(path: Path, policy: Policy) -> BaselineSummary:
    """Atomically canonicalize without changing settings or findings."""
    payload = _load_object(path)
    before_settings = _settings(payload)
    before_findings = finding_counts(payload)
    text = canonical_text(normalize_payload(payload))
    round_trip = cast(JsonObject, json.loads(text))
    if _settings(round_trip) != before_settings:
        raise BaselineError("canonical serialization changed plugin settings")
    if finding_counts(round_trip) != before_findings:
        raise BaselineError("canonical serialization changed finding identities")
    validate_payload(round_trip, policy)
    _write_atomic(path, text)
    return summary(round_trip, physical_lines=len(text.splitlines()))


def _scan_command(executable: str, policy: Policy, baseline: Path) -> list[str]:
    # Passing --baseline is the supported way to initialize the official
    # is_baseline_file filter: detect-secrets injects the filename parameter.
    # Adding that filter manually is invalid because it lacks this context.
    return [
        executable,
        "scan",
        "--baseline",
        str(baseline),
        "--exclude-files",
        policy.exclude_files,
    ]


def _scan_failure_evidence(stderr: bytes) -> str:
    """Describe scanner failure without echoing paths, findings, or values."""
    decoded = stderr.decode("utf-8", errors="replace")
    lowered = decoded.lower()
    markers = (
        "no such file",
        "permission denied",
        "unrecognized arguments",
        "traceback",
        "unicode",
        "too many levels of symbolic links",
        "is a directory",
        "broken pipe",
        "invalid filter",
    )
    categories = [marker for marker in markers if marker in lowered]
    exception_types = sorted(
        set(re.findall(r"(?m)^([A-Za-z][A-Za-z0-9_]*(?:Error|Exception)):", decoded))
    )
    stack_functions = re.findall(r'(?m)^\s+File "[^"]+", line \d+, in ([A-Za-z0-9_<>]+)$', decoded)
    return _canonical_json(
        {
            "categories": categories,
            "exception_types": exception_types,
            "stack_functions": stack_functions[-8:],
            "stderr_bytes": len(stderr),
            "stderr_digest": hashlib.sha256(stderr).hexdigest(),
        }
    )


def _scan_fresh(
    *,
    executable: str,
    policy: Policy,
    repository: Path,
    seed: Path,
    destination: Path,
) -> JsonObject:
    # The official baseline-file filter requires detect-secrets' baseline
    # context. Update a disposable copy so upstream supplies that context while
    # retaining its supported audit-label migration behavior.
    shutil.copyfile(seed, destination)
    completed = subprocess.run(
        _scan_command(executable, policy, destination),
        cwd=repository,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise BaselineError(
            "detect-secrets scan failed with "
            f"status {completed.returncode}; {_scan_failure_evidence(completed.stderr)}"
        )
    return _load_object(destination)


def refresh(
    *, baseline: Path, policy: Policy, repository: Path, executable: str
) -> tuple[BaselineSummary, BaselineSummary, int, int]:
    """Scan, prove semantic compatibility, and atomically replace the baseline."""
    previous = _load_object(baseline)
    previous_counts = finding_counts(previous)
    previous_summary = summary(
        previous,
        physical_lines=len(baseline.read_text(encoding="utf-8").splitlines()),
    )
    descriptor, raw_scan = tempfile.mkstemp(
        prefix="gludd-detect-secrets-scan.", suffix=".json"
    )
    os.close(descriptor)
    scan_path = Path(raw_scan)
    try:
        fresh = _scan_fresh(
            executable=executable,
            policy=policy,
            repository=repository,
            seed=baseline,
            destination=scan_path,
        )
    finally:
        scan_path.unlink(missing_ok=True)
    if fresh.get("plugins_used") != previous.get("plugins_used"):
        raise BaselineError("detect-secrets plugin configuration drifted")
    if _without_policy_filters(fresh, policy) != _without_policy_filters(
        previous, policy
    ):
        raise BaselineError("detect-secrets non-policy filter configuration drifted")
    validate_payload(fresh, policy)
    candidate = normalize_payload(_merge_audit_labels(previous, fresh))
    fresh_counts = finding_counts(candidate)
    if (
        _settings(candidate) == _settings(previous)
        and fresh_counts == previous_counts
        and "generated_at" in previous
    ):
        candidate["generated_at"] = previous["generated_at"]
    text = canonical_text(candidate)
    _write_atomic(baseline, text)
    current_summary = summary(candidate, physical_lines=len(text.splitlines()))
    return (
        previous_summary,
        current_summary,
        sum((previous_counts - fresh_counts).values()),
        sum((fresh_counts - previous_counts).values()),
    )


def check(path: Path, policy: Policy) -> BaselineSummary:
    """Validate policy, canonical bytes, and the physical line limit."""
    payload = _load_object(path)
    text = path.read_text(encoding="utf-8")
    validate_payload(payload, policy)
    if text != canonical_text(normalize_payload(payload)):
        raise BaselineError("baseline JSON is not canonical")
    physical_lines = len(text.splitlines())
    if physical_lines > policy.max_physical_lines:
        raise BaselineError("baseline exceeds the configured physical line limit")
    return summary(payload, physical_lines=physical_lines)


def _emit(action: str, evidence: Mapping[str, object]) -> None:
    print(_canonical_json({"action": action, **evidence}))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("check", "compact", "refresh", "summary")
    )
    parser.add_argument("--baseline", type=Path, default=Path(".secrets.baseline"))
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("config/detect_secrets_baseline_policy.json"),
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--executable", default="detect-secrets")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = load_policy(args.policy)
        if args.command == "refresh":
            before, after, removed, added = refresh(
                baseline=args.baseline,
                policy=policy,
                repository=args.repo_root,
                executable=args.executable,
            )
            _emit(
                "refresh",
                {
                    "before": before.as_dict(),
                    "after": after.as_dict(),
                    "removed_findings": removed,
                    "added_findings": added,
                },
            )
        elif args.command == "compact":
            _emit("compact", compact(args.baseline, policy).as_dict())
        elif args.command == "check":
            _emit("check", check(args.baseline, policy).as_dict())
        else:
            payload = _load_object(args.baseline)
            lines = len(args.baseline.read_text(encoding="utf-8").splitlines())
            _emit("summary", summary(payload, physical_lines=lines).as_dict())
    except (BaselineError, OSError) as exc:
        print(f"manage-secrets-baseline: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
