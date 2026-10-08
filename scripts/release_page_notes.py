#!/usr/bin/env python3
"""Build a bounded, deterministic GitHub release-page preview from a ledger."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

MAX_LEDGER_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_COMPLETED_ITEMS = 64
MAX_EXCLUDED_ITEMS = 64
MAX_EVIDENCE_COMMITS = 8
RELEASE_CATEGORIES = ("Features", "Improvements")

_RELEASE_RE = re.compile(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\Z")
_TASK_RE = re.compile(r"S\d+(?:\.[0-9A-Za-z]+)+\Z")
_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_ROLE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_REPOSITORY_RE = re.compile(r"[0-9A-Za-z_.-]+/[0-9A-Za-z_.-]+\Z")


class ReleaseNotesError(ValueError):
    """Raised when ledger evidence cannot produce safe release-page notes."""


@dataclass(frozen=True)
class EvidenceCommit:
    """One immutable implementation receipt linked from release notes."""

    role: str
    sha: str


@dataclass(frozen=True)
class CompletedItem:
    """One completed task and its public release-page classification."""

    task_id: str
    title: str
    category: str
    evidence_commits: tuple[EvidenceCommit, ...]


@dataclass(frozen=True)
class ReleaseLedger:
    """Validated release-page projection of the completed-backlog ledger."""

    schema_version: int
    release: str
    status: str
    repository: str
    baseline_ref: str
    baseline_commit: str
    completed_items: tuple[CompletedItem, ...]
    excluded_open_tasks: tuple[str, ...]


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ReleaseNotesError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, label: str, maximum: int) -> Sequence[object]:
    if not isinstance(value, list):
        raise ReleaseNotesError(f"{label} must be an array")
    if not value or len(value) > maximum:
        raise ReleaseNotesError(f"{label} must contain between 1 and {maximum} entries")
    return cast(Sequence[object], value)


def _text(
    record: Mapping[str, object],
    key: str,
    label: str,
    *,
    maximum: int,
) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReleaseNotesError(f"{label}.{key} must be a non-empty trimmed string")
    if len(value) > maximum or any(ord(character) < 32 for character in value):
        raise ReleaseNotesError(f"{label}.{key} exceeds its safe text boundary")
    return value


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    decoded: dict[str, object] = {}
    for key, value in pairs:
        if key in decoded:
            raise ReleaseNotesError(f"duplicate JSON key is not allowed: {key}")
        decoded[key] = value
    return decoded


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _load_json(path: Path) -> Mapping[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ReleaseNotesError(f"ledger must be a regular non-symlink file: {path}")
    before = path.stat()
    if before.st_size > MAX_LEDGER_BYTES:
        raise ReleaseNotesError(
            f"ledger size {before.st_size} exceeds {MAX_LEDGER_BYTES} bytes"
        )
    raw = path.read_bytes()
    try:
        after = path.stat()
    except OSError as exc:
        raise ReleaseNotesError("ledger changed while it was being read") from exc
    if (
        path.is_symlink()
        or len(raw) != before.st_size
        or _file_identity(before) != _file_identity(after)
    ):
        raise ReleaseNotesError("ledger changed while it was being read")
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseNotesError(f"ledger is not valid UTF-8 JSON: {exc}") from exc
    return _mapping(cast(object, decoded), "ledger")


def _parse_evidence(value: object, item_label: str) -> tuple[EvidenceCommit, ...]:
    records = _sequence(
        value,
        f"{item_label}.evidence_commits",
        MAX_EVIDENCE_COMMITS,
    )
    evidence: list[EvidenceCommit] = []
    seen: set[str] = set()
    for index, raw_record in enumerate(records):
        label = f"{item_label}.evidence_commits[{index}]"
        record = _mapping(raw_record, label)
        role = _text(record, "role", label, maximum=32)
        sha = _text(record, "sha", label, maximum=40)
        if not _ROLE_RE.fullmatch(role):
            raise ReleaseNotesError(f"{label}.role has an invalid identifier")
        if not _SHA_RE.fullmatch(sha):
            raise ReleaseNotesError(f"{label}.sha must be 40 lowercase hexadecimal characters")
        if sha in seen:
            raise ReleaseNotesError(f"{item_label} evidence commits must be unique")
        seen.add(sha)
        evidence.append(EvidenceCommit(role=role, sha=sha))
    return tuple(evidence)


def _parse_completed_items(value: object) -> tuple[CompletedItem, ...]:
    records = _sequence(value, "completed_items", MAX_COMPLETED_ITEMS)
    items: list[CompletedItem] = []
    task_ids: set[str] = set()
    categories: set[str] = set()
    for index, raw_record in enumerate(records):
        label = f"completed_items[{index}]"
        record = _mapping(raw_record, label)
        task_id = _text(record, "task_id", label, maximum=32)
        title = _text(record, "title", label, maximum=180)
        category = _text(record, "release_page_category", label, maximum=16)
        if not _TASK_RE.fullmatch(task_id):
            raise ReleaseNotesError(f"{label}.task_id is invalid")
        if category not in RELEASE_CATEGORIES:
            raise ReleaseNotesError(
                f"{label}.release_page_category must be Features or Improvements"
            )
        if task_id in task_ids:
            raise ReleaseNotesError("completed task identifiers must be unique")
        task_ids.add(task_id)
        categories.add(category)
        items.append(
            CompletedItem(
                task_id=task_id,
                title=title,
                category=category,
                evidence_commits=_parse_evidence(record.get("evidence_commits"), label),
            )
        )
    if categories != set(RELEASE_CATEGORIES):
        raise ReleaseNotesError("completed items must include both Features and Improvements")
    return tuple(items)


def _parse_excluded(value: object, completed: set[str]) -> tuple[str, ...]:
    records = _sequence(value, "excluded_open_tasks", MAX_EXCLUDED_ITEMS)
    excluded: list[str] = []
    for index, raw_task_id in enumerate(records):
        if not isinstance(raw_task_id, str) or not _TASK_RE.fullmatch(raw_task_id):
            raise ReleaseNotesError(f"excluded_open_tasks[{index}] is invalid")
        if raw_task_id in completed or raw_task_id in excluded:
            raise ReleaseNotesError("excluded task identifiers must be unique and open")
        excluded.append(raw_task_id)
    return tuple(excluded)


def load_release_ledger(path: Path, expected_release: str) -> ReleaseLedger:
    """Load and fully validate a completed-backlog ledger for one release."""
    if not _RELEASE_RE.fullmatch(expected_release):
        raise ReleaseNotesError("expected release must be a v-prefixed semantic version")
    root = _load_json(path)
    schema_version = root.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ReleaseNotesError("ledger.schema_version must be 1")
    release = _text(root, "release", "ledger", maximum=64)
    if release != expected_release:
        raise ReleaseNotesError(
            f"ledger release {release!r} does not match expected release {expected_release!r}"
        )
    page = _mapping(root.get("release_page"), "ledger.release_page")
    status = _text(page, "status", "ledger.release_page", maximum=16)
    if status != "unreleased":
        raise ReleaseNotesError("release_page.status must remain unreleased")
    repository = _text(page, "repository", "ledger.release_page", maximum=128)
    if not _REPOSITORY_RE.fullmatch(repository):
        raise ReleaseNotesError("release_page.repository must be an owner/name pair")
    baseline = _mapping(root.get("baseline"), "ledger.baseline")
    baseline_ref = _text(baseline, "ref", "ledger.baseline", maximum=64)
    baseline_commit = _text(baseline, "commit", "ledger.baseline", maximum=40)
    if not _RELEASE_RE.fullmatch(baseline_ref) or not _SHA_RE.fullmatch(baseline_commit):
        raise ReleaseNotesError("ledger baseline must contain a release ref and full commit")
    completed_items = _parse_completed_items(root.get("completed_items"))
    excluded = _parse_excluded(
        root.get("excluded_open_tasks"),
        {item.task_id for item in completed_items},
    )
    return ReleaseLedger(
        schema_version=schema_version,
        release=release,
        status=status,
        repository=repository,
        baseline_ref=baseline_ref,
        baseline_commit=baseline_commit,
        completed_items=completed_items,
        excluded_open_tasks=excluded,
    )


def build_release_page_notes(ledger: ReleaseLedger) -> str:
    """Render stable Markdown accepted by ``gh release --notes-file``."""
    lines = [
        f"# Gludd {ledger.release}",
        "",
        "> **Status: Unreleased.** This deterministic preview comes from the",
        "> completed-backlog ledger and does not create or publish a GitHub release.",
        "",
    ]
    for category in RELEASE_CATEGORIES:
        lines.extend((f"## {category}", ""))
        for item in ledger.completed_items:
            if item.category != category:
                continue
            links = "; ".join(
                "["
                + evidence.role.replace("_", " ")
                + f" `{evidence.sha[:9]}`](https://github.com/{ledger.repository}/commit/{evidence.sha})"
                for evidence in item.evidence_commits
            )
            lines.append(f"- **{item.task_id} — {item.title}.** Evidence: {links}.")
        lines.append("")
    excluded = ", ".join(f"`{task_id}`" for task_id in ledger.excluded_open_tasks)
    lines.extend(
        (
            "## Release scope",
            "",
            f"- Completed backlog items: {len(ledger.completed_items)}.",
            f"- Excluded open work: {excluded}.",
            "- Baseline: "
            f"[`{ledger.baseline_ref}`](https://github.com/{ledger.repository}/releases/tag/{ledger.baseline_ref}) "
            f"at `{ledger.baseline_commit}`.",
            "",
            "<!-- release-page-source: completed-backlog-ledger; "
            f"schema={ledger.schema_version}; status={ledger.status} -->",
            "",
        )
    )
    rendered = "\n".join(lines)
    size = len(rendered.encode("utf-8"))
    if size > MAX_OUTPUT_BYTES:
        raise ReleaseNotesError(
            f"release-page notes size {size} exceeds {MAX_OUTPUT_BYTES} bytes"
        )
    return rendered


def _atomic_write(path: Path, content: str) -> None:
    if not path.parent.is_dir() or path.is_symlink():
        raise ReleaseNotesError("output parent must exist and output must not be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sync_release_page_notes(
    ledger_path: Path,
    output_path: Path,
    expected_release: str,
    *,
    validate_only: bool,
) -> str:
    """Write notes atomically or prove an existing preview is byte-exact."""
    rendered = build_release_page_notes(
        load_release_ledger(ledger_path, expected_release)
    )
    if validate_only:
        if output_path.is_symlink() or not output_path.is_file():
            raise ReleaseNotesError(f"release-page preview is missing: {output_path}")
        current = output_path.read_bytes()
        if len(current) > MAX_OUTPUT_BYTES or current != rendered.encode("utf-8"):
            raise ReleaseNotesError(f"release-page preview is out of date: {output_path}")
    else:
        _atomic_write(output_path, rendered)
    return rendered


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", help="v-prefixed release version")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the bounded file builder without contacting GitHub."""
    args = _parser().parse_args(argv)
    try:
        sync_release_page_notes(
            args.ledger,
            args.output,
            args.release,
            validate_only=args.validate_only,
        )
    except (OSError, ReleaseNotesError) as exc:
        print(f"release-page-notes: ERROR: {exc}")
        return 2
    action = "validated" if args.validate_only else "wrote"
    print(f"release-page-notes: {action} {args.output} for {args.release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
