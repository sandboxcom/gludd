"""Fail-closed, content-free triage for one controller-local JUnit report."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ansible_collections.general_ludd.git_release.plugins.module_utils.provenance import (
    ArtifactVerificationError,
    _file_snapshot,
    _open_beneath,
    _open_root,
    _relative_parts,
)
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_TEST_CASES = 100_000
MAX_FAILURES = 64
READ_CHUNK_BYTES = 1024 * 1024
MAX_TESTCASE_NAME_CHARS = 65_536
MAX_IDENTITY_CHARS = 4_096
_INTEGER = re.compile(r"0|[1-9][0-9]*\Z")
_ALLOWED_ARGS = frozenset({"report_path", "root"})
_OUTCOME_TAGS = frozenset({"error", "failure", "skipped"})
_DECLARED_OUTCOMES = {
    "error": "error",
    "failed": "failure",
    "failure": "failure",
    "notrun": "skipped",
    "passed": "passed",
    "skipped": "skipped",
    "success": "passed",
}


class PipelineTriageError(ValueError):
    """A bounded rejection safe to expose through an Ansible result."""

    def as_result(self) -> dict[str, object]:
        """Return a stable failure without report content or local paths."""
        return {"changed": False, "failed": True, "msg": str(self)}


@dataclass(frozen=True, slots=True)
class TriageLimits:
    """Downward-only limits that make hard ceilings inexpensive to test."""

    max_report_bytes: int = MAX_REPORT_BYTES
    max_test_cases: int = MAX_TEST_CASES
    max_failures: int = MAX_FAILURES

    def validate(self) -> None:
        """Reject invalid bounds and attempts to raise a hard ceiling."""
        bounds = (
            (self.max_report_bytes, MAX_REPORT_BYTES),
            (self.max_test_cases, MAX_TEST_CASES),
            (self.max_failures, MAX_FAILURES),
        )
        if any(value < 1 or value > ceiling for value, ceiling in bounds):
            raise PipelineTriageError("internal triage limits may only lower hard ceilings")


_DEFAULT_LIMITS = TriageLimits()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        raise PipelineTriageError("JUnit report contains a non-text XML tag")
    return tag.rsplit("}", 1)[-1]


def _read_report(
    *,
    root: object,
    report_path: object,
    maximum_bytes: int,
) -> tuple[bytes, str]:
    """Read one stable regular file through the collection's no-follow boundary."""
    try:
        parts = _relative_parts(report_path, "report_path")
        root_fd = _open_root(root)
    except ArtifactVerificationError as exc:
        raise PipelineTriageError(str(exc)) from exc
    descriptor: int | None = None
    try:
        try:
            descriptor = _open_beneath(root_fd, parts, "JUnit report")
        except ArtifactVerificationError as exc:
            raise PipelineTriageError(str(exc)) from exc
        before = _file_snapshot(descriptor)
        if not stat.S_ISREG(before.mode) or before.links != 1:
            raise PipelineTriageError("JUnit report must be one regular file with one link")
        if before.size < 1:
            raise PipelineTriageError("JUnit report must not be empty")
        if before.size > maximum_bytes:
            raise PipelineTriageError("JUnit report exceeds the 16 MiB limit")

        chunks: list[bytes] = []
        total = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, min(READ_CHUNK_BYTES, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise PipelineTriageError("JUnit report exceeds the 16 MiB limit")
            chunks.append(chunk)
            digest.update(chunk)
        after = _file_snapshot(descriptor)
        if before != after or total != before.size:
            raise PipelineTriageError("JUnit report changed during triage")
        return b"".join(chunks), digest.hexdigest()
    except OSError as exc:
        raise PipelineTriageError("JUnit report became unavailable during triage") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(root_fd)


def _parse_report(payload: bytes) -> Any:
    upper = payload.upper()
    if b"<!DOCTYPE" in upper:
        raise PipelineTriageError("JUnit report DTD declarations are forbidden")
    if b"<!ENTITY" in upper:
        raise PipelineTriageError("JUnit report entity declarations are forbidden")
    try:
        root = ElementTree.fromstring(
            payload,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DefusedXmlException, ElementTree.ParseError, ValueError) as exc:
        raise PipelineTriageError("JUnit report is not safe, well-formed XML") from exc
    if _local_name(root.tag) not in {"testsuite", "testsuites"}:
        raise PipelineTriageError("JUnit report root must be testsuite or testsuites")
    return root


def _identity(case: Any) -> tuple[str, dict[str, str]]:
    name = case.attrib.get("name")
    classname = case.attrib.get("classname", "")
    source_file = case.attrib.get("file", "")
    values = (name, classname, source_file)
    if any(not isinstance(value, str) for value in values) or not name:
        raise PipelineTriageError("JUnit testcase identity is missing or invalid")
    if len(name) > MAX_TESTCASE_NAME_CHARS:
        raise PipelineTriageError("JUnit testcase name exceeds the identity limit")
    if len(classname) > MAX_IDENTITY_CHARS or len(source_file) > MAX_IDENTITY_CHARS:
        raise PipelineTriageError("JUnit testcase identity exceeds the supported limit")
    identity = {"classname": classname, "file": source_file, "name": name}
    return _canonical_sha256(identity), identity


def _terminal_outcome(case: Any) -> str:
    terminals = [
        _local_name(child.tag)
        for child in case
        if _local_name(child.tag) in _OUTCOME_TAGS
    ]
    if len(terminals) > 1:
        raise PipelineTriageError("JUnit testcase has ambiguous terminal outcomes")
    observed = terminals[0] if terminals else "passed"
    declared: list[str] = []
    for attribute in ("status", "result"):
        value = case.attrib.get(attribute)
        if value is None:
            continue
        normalized = _DECLARED_OUTCOMES.get(str(value).strip().lower())
        if normalized is None:
            raise PipelineTriageError("JUnit testcase declares an unsupported outcome")
        declared.append(normalized)
    if any(value != observed for value in declared):
        raise PipelineTriageError("JUnit testcase has ambiguous terminal outcomes")
    return observed


def _declared_count(root: Any, field: str) -> int | None:
    value = root.attrib.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or _INTEGER.fullmatch(value) is None:
        raise PipelineTriageError("JUnit summary contains an invalid count")
    parsed = int(value)
    if parsed > MAX_TEST_CASES:
        raise PipelineTriageError("JUnit summary exceeds the 100000-testcase limit")
    return parsed


def _validate_summary(root: Any, counts: Mapping[str, int]) -> None:
    declared = {
        "tests": _declared_count(root, "tests"),
        "failures": _declared_count(root, "failures"),
        "errors": _declared_count(root, "errors"),
        "skipped": _declared_count(root, "skipped"),
    }
    for field, value in declared.items():
        if value is not None and value != counts[field]:
            raise PipelineTriageError("JUnit summary conflicts with testcase outcomes")


def action_arguments(args: Mapping[str, object]) -> dict[str, object]:
    """Select the closed action contract and reject transport-shaped extras."""
    unknown = sorted(set(args) - _ALLOWED_ARGS)
    if unknown:
        raise PipelineTriageError("pipeline triage received an unsupported argument")
    if "root" not in args or "report_path" not in args:
        raise PipelineTriageError("pipeline triage requires root and report_path")
    return {"root": args["root"], "report_path": args["report_path"]}


def triage_junit_report(
    *,
    root: object,
    report_path: object,
    _limits: TriageLimits = _DEFAULT_LIMITS,
) -> dict[str, object]:
    """Reduce one stable JUnit report to content-free actionable evidence."""
    _limits.validate()
    payload, report_sha256 = _read_report(
        root=root,
        report_path=report_path,
        maximum_bytes=_limits.max_report_bytes,
    )
    xml_root = _parse_report(payload)
    counts = {"errors": 0, "failures": 0, "passed": 0, "skipped": 0, "tests": 0}
    identities: list[str] = []
    outcomes: list[dict[str, str]] = []
    actionable: list[dict[str, str]] = []
    seen: set[str] = set()
    element_count = 0

    for element in xml_root.iter():
        element_count += 1
        if element_count > (_limits.max_test_cases * 4) + 4_096:
            raise PipelineTriageError("JUnit report exceeds the bounded XML element limit")
        if _local_name(element.tag) != "testcase":
            continue
        counts["tests"] += 1
        if counts["tests"] > _limits.max_test_cases:
            raise PipelineTriageError("JUnit report exceeds the 100000-testcase limit")
        identity_sha256, _raw_identity = _identity(element)
        if identity_sha256 in seen:
            raise PipelineTriageError("JUnit report contains a duplicate testcase identity")
        seen.add(identity_sha256)
        outcome = _terminal_outcome(element)
        counts[outcome + "s" if outcome in {"error", "failure"} else outcome] += 1
        identities.append(identity_sha256)
        outcomes.append({"identity_sha256": identity_sha256, "outcome": outcome})
        if outcome in {"error", "failure"}:
            actionable.append({"identity_sha256": identity_sha256, "outcome": outcome})
            if len(actionable) > _limits.max_failures:
                raise PipelineTriageError("JUnit report exceeds the 64-failure limit")

    if counts["tests"] == 0:
        raise PipelineTriageError("JUnit report contains no testcases")
    _validate_summary(xml_root, counts)
    if counts["errors"] or counts["failures"]:
        overall = "failed"
    elif counts["passed"]:
        overall = "passed"
    else:
        overall = "skipped"
    return {
        "schema_version": 1,
        "outcome": overall,
        "counts": counts,
        "actionable_failures": actionable,
        "report": {"bytes": len(payload), "sha256": report_sha256},
        "test_identity_sha256": _canonical_sha256(sorted(identities)),
        "terminal_outcome_sha256": _canonical_sha256(outcomes),
    }


__all__ = [
    "MAX_FAILURES",
    "MAX_REPORT_BYTES",
    "MAX_TEST_CASES",
    "PipelineTriageError",
    "TriageLimits",
    "action_arguments",
    "triage_junit_report",
]
