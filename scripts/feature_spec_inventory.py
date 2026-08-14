#!/usr/bin/env python3
"""Inventory every documented Gludd feature spec and OpenCode behavioral spec.

The inventory deliberately does not use ``docs/features.yml`` as an allow-list.
It recursively accounts for every Markdown, YAML, and JSON file below ``docs/``
and extracts specification units from stable document structures:

* explicit feature/work-item IDs and ID ranges;
* implementation-plan phase tables;
* YAML/JSON ``features`` and ``specs`` collections;
* MCP tool schema manifests; and
* document-level feature/design/specification files without embedded IDs.

OpenCode-only enforcement material is excluded from the Gludd feature count and
reported separately from the canonical behavioral-spec source.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import audit_spec_effectiveness as effectiveness_audit
import check_spec_enforcement_coverage as enforcement_coverage_audit
import spec_generator_loop as generator_audit
import yaml

DOC_SUFFIXES = {".md", ".yml", ".yaml", ".json"}
STATUSES = ("implemented", "partial", "unimplemented", "unknown")


def _behavioral_group(spec_id: object) -> str:
    """Return the alphabetic prefix of a behavioral specification ID."""
    match = re.match(r"[A-Z]+", str(spec_id))
    return match.group() if match is not None else ""
STATUS_IMPLEMENTED_RE = re.compile(
    r"\b(?:implemented|complete(?:d)?|done|landed|shipped|delivered|resolved)\b",
    re.IGNORECASE,
)
STATUS_PARTIAL_RE = re.compile(
    r"\b(?:partial(?:ly)?|in[ -]progress|in flight|pending verification|"
    r"landed\s*[—-]\s*verify|not done|remaining)\b",
    re.IGNORECASE,
)
STATUS_UNIMPLEMENTED_RE = re.compile(
    r"\b(?:not implemented|unimplemented|not started|planned|proposed|"
    r"future work|deferred|todo|draft|stub)\b",
    re.IGNORECASE,
)
STATUS_FIELD_RE = re.compile(
    r"(?:\*\*)?status(?:\*\*)?\s*[:|]\s*(?P<status>[^|\n]+)",
    re.IGNORECASE,
)
FEATURE_ID_FIELD_RE = re.compile(
    r"(?:\*\*)?feature\s+id(?:\*\*)?\s*:\s*(?:\*\*)?"
    r"(?P<id>[A-Z][A-Z0-9]*(?:[.-]\d+)+(?:\s*[-\u2013]\s*"
    r"(?:[A-Z][A-Z0-9]*[.-]?)?\d+(?:\.\d+)*)?)",
    re.IGNORECASE,
)
EXPLICIT_UNIT_RE = re.compile(
    r"^(?P<id>"
    r"[A-Z][A-Z0-9]{0,8}(?:[.-]?\d+)(?:\.\d+)*"
    r"(?:\s*[-\u2013]\s*(?:[A-Z][A-Z0-9]{0,8}[.-]?)?\d+(?:\.\d+)*)?"
    r")\s*(?:—|\u2013|:|\s+-\s+)\s*(?P<title>.+?)\s*$"
)
TABLE_UNIT_RE = re.compile(
    r"^\|\s*(?P<id>[A-Z][A-Z0-9]{0,8}(?:[.-]?\d+)(?:\.\d+)*"
    r"(?:\s*[-\u2013]\s*(?:[A-Z][A-Z0-9]{0,8}[.-]?)?\d+(?:\.\d+)*)?)"
    r"\s*\|\s*(?P<title>[^|]+?)(?:\s*\|(?P<tail>.*))?$"
)
BEHAVIORAL_HEADER_RE = re.compile(
    r"^###\s+(?P<id>[A-Z]{1,3}\d{2,4})\s+(?:—|\u2013|:|-)\s+"
    r"(?P<title>.+?)\s*$"
)
PATH_TOKEN_RE = re.compile(r"`([^`\n]+)`")
RANGE_RE = re.compile(
    r"^(?P<prefix>[A-Z][A-Z0-9]*)(?:[.-]?)(?P<start>\d+)"
    r"(?P<decimal>(?:\.\d+)*)\s*[-\u2013]\s*"
    r"(?:(?P<prefix2>[A-Z][A-Z0-9]*)(?:[.-]?))?"
    r"(?P<end>\d+)(?P<decimal2>(?:\.\d+)*)$",
    re.IGNORECASE,
)
SINGLE_ID_RE = re.compile(
    r"^(?P<prefix>[A-Z][A-Z0-9]*)(?:[.-]?)(?P<number>\d+)"
    r"(?P<decimal>(?:\.\d+)*)$",
    re.IGNORECASE,
)

DOCUMENT_NAME_TOKENS = {
    "ARCHITECTURE",
    "DESIGN",
    "FEATURE",
    "IMPLEMENTATION",
    "INTEGRATION",
    "PLAN",
    "REMEDIATION",
    "ROADMAP",
    "SANDBOX",
    "SPEC",
    "STRUCTURE",
    "SYSTEM",
}
DOCUMENT_MARKERS = (
    "implementation plan",
    "acceptance criteria",
    "test plan",
    "requirements",
    "architecture",
    "files to",
)
OPENCODE_PATH_MARKERS = (
    "behavioral_specs",
    "enforcement",
    "guardrail",
    "opencode",
    "nf8_multitask",
    "nf10_stop",
    "task_tracking_enforcement",
)
OPENCODE_CONTENT_MARKERS = (
    ".opencode/plugin",
    "opencode plugin",
    "enforce-stop.ts",
    "enforce-multitask.ts",
    "enforce-delegate.ts",
)
TITLE_STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "feature",
    "implementation",
    "of",
    "phase",
    "spec",
    "specification",
    "system",
    "the",
    "to",
    "with",
}
RECURRENCE_STOP_WORDS = {
    "agent",
    "agents",
    "behavior",
    "blocking",
    "enforcement",
    "failure",
    "guard",
    "mechanism",
    "spec",
}


@dataclass
class _Record:
    """Mutable accumulator used while aliases are being merged."""

    canonical_id: str
    title: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    aliases: set[str] = field(default_factory=set)
    claim_statuses: set[str] = field(default_factory=set)
    evidence_existing: set[str] = field(default_factory=set)
    evidence_missing: set[str] = field(default_factory=set)
    evidence_code: set[str] = field(default_factory=set)
    evidence_tests: set[str] = field(default_factory=set)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _title_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 1 and token not in TITLE_STOP_WORDS
    }


def _normalize_single_id(raw_id: str) -> str:
    compact = re.sub(r"\s+", "", raw_id).lower().replace(".", "")
    return compact.replace("-", "")


def _expand_ids(raw_id: str) -> list[str]:
    """Expand a simple numeric ID range while preserving the prefix."""

    compact = re.sub(r"\s+", "", raw_id)
    range_match = RANGE_RE.fullmatch(compact)
    if range_match:
        prefix = range_match.group("prefix")
        prefix2 = range_match.group("prefix2")
        start = int(range_match.group("start"))
        end = int(range_match.group("end"))
        decimal = range_match.group("decimal")
        decimal2 = range_match.group("decimal2")
        if (
            (not prefix2 or prefix2.lower() == prefix.lower())
            and decimal == decimal2
            and start <= end
            and end - start <= 100
        ):
            return [
                _normalize_single_id(f"{prefix}{number}{decimal}")
                for number in range(start, end + 1)
            ]
    return [_normalize_single_id(compact)]


def _claim_status(text: str) -> str:
    """Return the explicit claim status in text, never a percentage inference."""

    status_match = STATUS_FIELD_RE.search(text)
    candidate = status_match.group("status") if status_match else text
    if STATUS_UNIMPLEMENTED_RE.search(candidate):
        return "unimplemented"
    if STATUS_PARTIAL_RE.search(candidate):
        return "partial"
    if STATUS_IMPLEMENTED_RE.search(candidate):
        return "implemented"
    return "unknown"


def _document_claim_status(text: str) -> str:
    """Read only an explicit document status field.

    Words such as "planned" and "implemented" in requirement prose are not a
    status claim for the document that contains them.
    """

    match = STATUS_FIELD_RE.search("\n".join(text.splitlines()[:40]))
    return _claim_status(match.group(0)) if match else "unknown"


def _first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return re.sub(r"[`*_]", "", line[2:]).strip()
    return fallback


def _is_opencode_only(path: Path, text: str) -> bool:
    normalized_path = path.as_posix().lower()
    path_signal = any(marker in normalized_path for marker in OPENCODE_PATH_MARKERS)
    content_signal = any(marker in text.lower() for marker in OPENCODE_CONTENT_MARKERS)
    product_signal = any(
        marker in text
        for marker in (
            "src/general_ludd/",
            "collections/ansible_collections/general_ludd/",
            "/api/",
        )
    )
    return (path_signal and content_signal) or (
        path_signal and not product_signal and "behavioral" in normalized_path
    )


def _is_document_spec(path: Path, text: str) -> bool:
    stem_tokens = set(re.findall(r"[A-Z0-9]+", path.stem.upper()))
    title = _first_heading(text, "")
    title_signal = bool(
        re.search(
            r"\b(feature|design|spec(?:ification)?|architecture|roadmap|"
            r"implementation plan|system)\b",
            title,
            re.IGNORECASE,
        )
    )
    name_signal = bool(stem_tokens & DOCUMENT_NAME_TOKENS)
    marker_count = sum(marker in text.lower() for marker in DOCUMENT_MARKERS)
    strong_name_signal = bool(
        stem_tokens & {"ARCHITECTURE", "DESIGN", "FEATURE", "ROADMAP", "SPEC"}
    )
    return (
        "specs" in path.parts
        or strong_name_signal
        or ((name_signal or title_signal) and marker_count >= 1)
    )


def _document_feature_id(text: str) -> str | None:
    match = FEATURE_ID_FIELD_RE.search(text)
    if match:
        return _expand_ids(match.group("id"))[0]
    title = _first_heading(text, "")
    match = EXPLICIT_UNIT_RE.match(re.sub(r"^(?:feature|spec)\s*:\s*", "", title))
    if match:
        return _expand_ids(match.group("id"))[0]
    return None


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _strip_path_suffix(token: str) -> str:
    value = token.strip().strip("'\"")
    value = value.split("::", 1)[0]
    value = re.sub(r":\d+(?:-\d+)?$", "", value)
    value = value.rstrip(".,;)")
    return value


def _resolve_evidence_token(root: Path, token: str) -> tuple[str, bool, str] | None:
    """Resolve one path-like evidence token.

    Returns ``(display_path, exists, kind)`` or ``None`` for prose/code symbols.
    """

    raw = _strip_path_suffix(token)
    for prefix in ("file:", "test:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    if raw.startswith("role:"):
        role = raw[len("role:") :]
        matches = list(
            (root / "collections/ansible_collections/general_ludd").glob(
                f"*/roles/{role}"
            )
        )
        display = f"role:{role}"
        return display, any(path.is_dir() for path in matches), "code"
    if raw.startswith("module:"):
        module = raw[len("module:") :]
        matches = list(
            (root / "collections/ansible_collections/general_ludd").glob(
                f"*/plugins/modules/{module}.py"
            )
        )
        display = f"module:{module}"
        return display, any(path.is_file() for path in matches), "code"
    if raw.startswith("molecule:"):
        scenario = raw[len("molecule:") :]
        path = root / "molecule/playbooks" / scenario
        return f"molecule:{scenario}", path.is_dir(), "test"

    raw = raw[2:] if raw.startswith("./") else raw
    if (
        raw.startswith("/")
        or
        any(character in raw for character in (" ", "*", "{", "}", "<", ">", "(", ")"))
        or raw.startswith(("make ", "node ", "http://", "https://"))
        or "@" in raw
    ):
        return None
    likely_path = (
        "/" in raw
        or raw.endswith((".py", ".pyi", ".ts", ".mjs", ".yml", ".yaml", ".json"))
    )
    if not likely_path:
        return None

    candidates = [root / raw]
    if raw.startswith(("tests/", "src/", "docs/", "config/", ".opencode/")):
        pass
    elif raw.endswith(".py"):
        candidates.extend(
            [
                root / "src/general_ludd" / raw,
                root / "tests/unit" / raw,
                root / "scripts" / raw,
            ]
        )
    existing = next((candidate for candidate in candidates if candidate.exists()), None)
    display_path = (
        _relative_path(root, existing) if existing is not None else raw
    )
    kind = "test" if raw.startswith("tests/") else "code"
    return display_path, existing is not None, kind


def _extract_path_evidence(root: Path, text: str) -> list[tuple[str, bool, str]]:
    evidence: list[tuple[str, bool, str]] = []
    seen: set[str] = set()
    for token in PATH_TOKEN_RE.findall(text):
        resolved = _resolve_evidence_token(root, token)
        if resolved is None or resolved[0] in seen:
            continue
        seen.add(resolved[0])
        evidence.append(resolved)
    return evidence


def _add_record(
    records: dict[str, _Record],
    *,
    canonical_id: str,
    title: str,
    source_path: str,
    line: int | None,
    source_kind: str,
    raw_id: str,
    claim_status: str,
    evidence: Iterable[tuple[str, bool, str]] = (),
) -> None:
    record = records.get(canonical_id)
    if record is None:
        record = _Record(canonical_id=canonical_id, title=title.strip())
        records[canonical_id] = record
    elif len(title.strip()) > len(record.title):
        record.title = title.strip()

    source: dict[str, Any] = {
        "path": source_path,
        "kind": source_kind,
        "raw_id": raw_id,
    }
    if line is not None:
        source["line"] = line
    if source not in record.sources:
        record.sources.append(source)
    record.aliases.add(raw_id)
    if claim_status != "unknown":
        record.claim_statuses.add(claim_status)
    for evidence_path, exists, kind in evidence:
        if exists:
            record.evidence_existing.add(evidence_path)
            if kind == "test":
                record.evidence_tests.add(evidence_path)
            else:
                record.evidence_code.add(evidence_path)
        else:
            record.evidence_missing.add(evidence_path)


def _manifest_claim(item: Mapping[str, Any]) -> str:
    status = item.get("status")
    if isinstance(status, str):
        return _claim_status(f"Status: {status}")
    pct = item.get("pct")
    # pct is recorded as a claim, not accepted as verification evidence.
    if isinstance(pct, (int, float)):
        if pct >= 100:
            return "implemented"
        if pct <= 0:
            return "unimplemented"
        return "partial"
    return "unknown"


def _iter_feature_collections(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"features", "specs"} and isinstance(child, list):
                for item in child:
                    if isinstance(item, Mapping):
                        yield item
            yield from _iter_feature_collections(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_feature_collections(child)


def _scan_structured_feature_file(
    root: Path,
    path: Path,
    data: Any,
    records: dict[str, _Record],
) -> int:
    source_path = _relative_path(root, path)
    added = 0
    for item in _iter_feature_collections(data):
        raw_id_value = item.get("id") or item.get("name")
        title_value = item.get("title") or item.get("description") or raw_id_value
        if not isinstance(raw_id_value, str) or not isinstance(title_value, str):
            continue
        explicit_title_id = EXPLICIT_UNIT_RE.match(title_value)
        ids = (
            _expand_ids(explicit_title_id.group("id"))
            if explicit_title_id
            else [_normalize_single_id(raw_id_value)]
        )
        evidence: list[tuple[str, bool, str]] = []
        refs = item.get("evidence_refs")
        if isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, str):
                    resolved = _resolve_evidence_token(root, ref)
                    if resolved is not None:
                        evidence.append(resolved)
        for canonical_id in ids:
            _add_record(
                records,
                canonical_id=canonical_id,
                title=title_value,
                source_path=source_path,
                line=None,
                source_kind="structured-feature-manifest",
                raw_id=raw_id_value,
                claim_status=_manifest_claim(item),
                evidence=evidence,
            )
            added += 1
    return added


def _scan_mcp_manifest(
    root: Path,
    path: Path,
    data: Any,
    records: dict[str, _Record],
) -> int:
    """Treat each JSON-schema MCP tool entry as a product capability spec."""

    if path.name != "MCP_TOOLS_MANIFEST.json" or not isinstance(data, list):
        return 0
    source_path = _relative_path(root, path)
    added = 0
    for item in data:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        description = item.get("description")
        schema = item.get("input_schema")
        if not isinstance(name, str) or not isinstance(description, str):
            continue
        if not isinstance(schema, Mapping):
            continue
        module = name.rsplit(".", 1)[-1]
        evidence_path = (
            root
            / "collections/ansible_collections/general_ludd/agent/plugins/modules"
            / f"{module}.py"
        )
        evidence = [
            (
                _relative_path(root, evidence_path)
                if evidence_path.exists()
                else evidence_path.relative_to(root).as_posix(),
                evidence_path.exists(),
                "code",
            )
        ]
        _add_record(
            records,
            canonical_id=f"mcp-tool:{_slug(name)}",
            title=description.split(" — ", 1)[0],
            source_path=source_path,
            line=None,
            source_kind="mcp-json-schema",
            raw_id=name,
            claim_status="unknown",
            evidence=evidence,
        )
        added += 1
    return added


def _scan_mcp_topics(
    root: Path,
    path: Path,
    data: Any,
    records: dict[str, _Record],
) -> int:
    if path.name != "MCP_TOOLS_TOPICS.yml" or not isinstance(data, Mapping):
        return 0
    source_path = _relative_path(root, path)
    added = 0
    for name, value in data.items():
        if not isinstance(name, str) or not name.startswith("general_ludd."):
            continue
        title = name
        if isinstance(value, Mapping):
            documentation = value.get("DOCUMENTATION")
            if isinstance(documentation, Mapping):
                short = documentation.get("short_description")
                if isinstance(short, str):
                    title = short
        canonical_id = f"mcp-tool:{_slug(name)}"
        if canonical_id not in records:
            continue
        _add_record(
            records,
            canonical_id=canonical_id,
            title=title,
            source_path=source_path,
            line=None,
            source_kind="mcp-yaml-contract",
            raw_id=name,
            claim_status="unknown",
        )
        added += 1
    return added


def _best_manifest_alias(
    title: str,
    stem: str,
    records: Mapping[str, _Record],
) -> str | None:
    doc_tokens = _title_tokens(f"{title} {stem.replace('_', ' ')}")
    if not doc_tokens:
        return None
    best_id: str | None = None
    best_score = 0.0
    for canonical_id, record in records.items():
        if canonical_id.startswith("mcp-tool:"):
            continue
        manifest_tokens = _title_tokens(record.title)
        if not manifest_tokens:
            continue
        overlap = len(doc_tokens & manifest_tokens)
        denominator = min(len(doc_tokens), len(manifest_tokens))
        score = overlap / denominator if denominator else 0.0
        if overlap >= 2 and score > best_score:
            best_id = canonical_id
            best_score = score
    return best_id if best_score >= 0.5 else None


def _clean_unit_line(line: str) -> str:
    cleaned = line.strip()
    cleaned = re.sub(r"^>\s*", "", cleaned)
    cleaned = re.sub(r"^#{2,6}\s+", "", cleaned)
    cleaned = re.sub(r"^[-*]\s+", "", cleaned)
    cleaned = cleaned.strip()
    if cleaned.startswith("**"):
        cleaned = cleaned[2:]
    cleaned = re.sub(r"\*\*.*$", "", cleaned).strip()
    return cleaned


def _extract_markdown_units(
    text: str,
    *,
    document_id: str | None,
) -> list[tuple[str, str, int, str, str]]:
    """Return ``(id, title, line, raw_id, claim)`` specification units."""

    units: list[tuple[str, str, int, str, str]] = []
    in_implementation_plan = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if re.match(r"^#{2,6}\s+", line):
            heading = re.sub(r"^#{2,6}\s+", "", line).lower()
            in_implementation_plan = any(
                marker in heading
                for marker in ("implementation plan", "work items", "roadmap", "phases")
            )

        table_match = TABLE_UNIT_RE.match(line)
        match: re.Match[str] | None = None
        tail = ""
        if table_match and in_implementation_plan:
            match = table_match
            tail = table_match.group("tail") or ""
        else:
            cleaned = _clean_unit_line(line)
            match = EXPLICIT_UNIT_RE.match(cleaned)
        if match is None:
            continue

        raw_id = match.group("id")
        title = re.sub(r"[`*_]", "", match.group("title")).strip().rstrip(".")
        if title.lower() in {"scope", "title", "description", "work item"}:
            continue
        expanded = _expand_ids(raw_id)
        local_phase = bool(
            re.fullmatch(
                r"p\d+(?:\s*[-\u2013]\s*p?\d+)?", raw_id, re.IGNORECASE
            )
        )
        claim = _claim_status(f"{title} {tail}")
        for expanded_id in expanded:
            canonical_id = (
                f"{document_id}:{expanded_id}" if local_phase and document_id else expanded_id
            )
            units.append((canonical_id, title, line_number, raw_id, claim))
    return units


def _title_similarity(left: str, right: str) -> float:
    left_tokens = _title_tokens(left)
    right_tokens = _title_tokens(right)
    denominator = min(len(left_tokens), len(right_tokens))
    if denominator == 0:
        return 0.0
    return len(left_tokens & right_tokens) / denominator


def _resolve_markdown_unit_id(
    records: Mapping[str, _Record],
    *,
    canonical_id: str,
    title: str,
    document_id: str,
    path: Path,
) -> str:
    """Namespace ambiguous short IDs reused by unrelated documents."""

    if ":" in canonical_id or canonical_id not in records:
        return canonical_id
    if path.name == "AGENTIC_IMPLEMENTATION_SPEC.md":
        return canonical_id
    existing = records[canonical_id]
    if _title_similarity(existing.title, title) >= 0.5:
        return canonical_id
    if re.fullmatch(r"[a-z]{1,2}\d+(?:\d+)?", canonical_id):
        return f"{document_id}:{canonical_id}"
    return canonical_id


def _unit_context(text: str, line: int, unit_lines: Sequence[int]) -> str:
    lines = text.splitlines()
    next_lines = [candidate for candidate in unit_lines if candidate > line]
    end = min(next_lines) - 1 if next_lines else len(lines)
    return "\n".join(lines[line - 1 : end])


def _scan_markdown_file(
    root: Path,
    path: Path,
    text: str,
    records: dict[str, _Record],
) -> tuple[int, str, str]:
    source_path = _relative_path(root, path)
    if path.name == "BEHAVIORAL_SPECS.md":
        return 0, "excluded", "opencode-behavioral-separate"
    if _is_opencode_only(path, text):
        return 0, "excluded", "opencode-or-enforcement-only"

    explicit_document_id = _document_feature_id(text)
    is_document_spec = _is_document_spec(path, text) or explicit_document_id is not None
    title = _first_heading(text, path.stem.replace("_", " ").title())
    alias = explicit_document_id or _best_manifest_alias(title, path.stem, records)
    document_id = alias or f"doc:{_slug(path.relative_to(root / 'docs').with_suffix('').as_posix())}"
    units = _extract_markdown_units(text, document_id=document_id)
    document_evidence = _extract_path_evidence(root, text)
    document_claim = _document_claim_status(text)
    unit_lines = sorted({unit[2] for unit in units})

    added = 0
    if is_document_spec and (explicit_document_id or not units):
        _add_record(
            records,
            canonical_id=document_id,
            title=title,
            source_path=source_path,
            line=1,
            source_kind="markdown-document-spec",
            raw_id=explicit_document_id or path.stem,
            claim_status=document_claim,
            evidence=document_evidence,
        )
        added += 1
    elif is_document_spec and alias and alias in records:
        _add_record(
            records,
            canonical_id=alias,
            title=title,
            source_path=source_path,
            line=1,
            source_kind="markdown-document-alias",
            raw_id=path.stem,
            claim_status=document_claim,
            evidence=document_evidence,
        )

    for canonical_id, unit_title, line, raw_id, unit_claim in units:
        canonical_id = _resolve_markdown_unit_id(
            records,
            canonical_id=canonical_id,
            title=unit_title,
            document_id=document_id,
            path=path,
        )
        context = _unit_context(text, line, unit_lines)
        context_claim = _document_claim_status(context)
        claim = next(
            (
                status
                for status in (unit_claim, context_claim, document_claim)
                if status != "unknown"
            ),
            "unknown",
        )
        local_phase = canonical_id.startswith(f"{document_id}:p")
        evidence = (
            document_evidence
            if local_phase
            else _extract_path_evidence(root, context)
        )
        _add_record(
            records,
            canonical_id=canonical_id,
            title=unit_title,
            source_path=source_path,
            line=line,
            source_kind="markdown-embedded-spec",
            raw_id=raw_id,
            claim_status=claim,
            evidence=evidence,
        )
        added += 1

    if added:
        return added, "included", "feature-specification-units"
    return 0, "unrecognized", "no-feature-spec-grammar-match"


def _finalize_records(records: Mapping[str, _Record]) -> list[dict[str, Any]]:
    finalized: list[dict[str, Any]] = []
    for canonical_id in sorted(records):
        record = records[canonical_id]
        claim_conflict = len(record.claim_statuses) > 1
        claim_status = (
            "unknown"
            if claim_conflict or not record.claim_statuses
            else next(iter(record.claim_statuses))
        )

        has_code = bool(record.evidence_code)
        has_tests = bool(record.evidence_tests)
        has_existing = bool(record.evidence_existing)
        has_missing = bool(record.evidence_missing)
        if claim_conflict:
            verified_status = "unknown"
        elif has_code and has_tests and not has_missing:
            verified_status = "implemented"
        elif (has_existing and has_missing) or (has_code and not has_tests):
            verified_status = "partial"
        else:
            verified_status = "unknown"

        finalized.append(
            {
                "id": canonical_id,
                "title": record.title,
                "aliases": sorted(record.aliases),
                "sources": sorted(
                    record.sources,
                    key=lambda item: (
                        str(item["path"]),
                        int(item.get("line", 0)),
                        str(item["kind"]),
                    ),
                ),
                "claim_status": claim_status,
                "claim_conflict": claim_conflict,
                "verified_status": verified_status,
                "evidence": {
                    "existing": sorted(record.evidence_existing),
                    "missing": sorted(record.evidence_missing),
                    "code": sorted(record.evidence_code),
                    "tests": sorted(record.evidence_tests),
                },
            }
        )
    return finalized


def _status_counts(records: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts = Counter(str(record[key]) for record in records)
    return {status: counts.get(status, 0) for status in STATUSES}


def _parse_behavioral_specs(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    specs: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in path.read_text().splitlines():
        match = BEHAVIORAL_HEADER_RE.match(line)
        if match:
            if current is not None:
                specs.append(current)
            current = {
                "id": match.group("id"),
                "title": match.group("title").strip(),
                "enforcement": "",
                "test": "",
                "behavior": "",
            }
            continue
        if current is None:
            continue
        for field_name, label in (
            ("enforcement", "**Enforcement:**"),
            ("test", "**Test:**"),
            ("behavior", "**Behavior:**"),
        ):
            if line.startswith(label):
                current[field_name] = line[len(label) :].strip()
    if current is not None:
        specs.append(current)
    return specs


def _is_template_enforcement(enforcement: str) -> bool:
    lowered = enforcement.lower()
    return (
        len(enforcement) > 400
        or "automated unique mechanism" in lowered
        or "generic mechanism" in lowered
    )


def _mechanism_exists(root: Path, text: str) -> bool:
    candidates: list[Path] = []
    for token in PATH_TOKEN_RE.findall(text):
        stripped = _strip_path_suffix(token)
        if stripped.startswith("make "):
            target = stripped.split()[1]
            makefile = (root / "Makefile").read_text() if (root / "Makefile").exists() else ""
            if re.search(rf"^{re.escape(target)}\s*:", makefile, re.MULTILINE):
                return True
        if stripped in {"AGENTS.md", "Makefile"}:
            candidates.append(root / stripped)
        elif "/" in stripped:
            relative = stripped[2:] if stripped.startswith("./") else stripped
            candidates.append(root / relative)
        elif stripped.endswith(".ts"):
            candidates.append(root / ".opencode/plugin" / stripped)
        elif stripped.endswith(".py"):
            candidates.extend([root / "scripts" / stripped, root / stripped])
    return any(candidate.exists() for candidate in candidates)


def _test_evidence_exists(root: Path, text: str) -> bool:
    if not text:
        return True
    path_tokens = PATH_TOKEN_RE.findall(text)
    if not path_tokens:
        return False
    return any(
        (resolved := _resolve_evidence_token(root, token)) is not None and resolved[1]
        for token in path_tokens
    )


def _canonical_behavioral_audits(
    root: Path,
) -> tuple[set[str], generator_audit.EnforcementStats, set[str]]:
    """Run the existing behavioral audit logic without subprocesses."""

    specs_path = root / "docs/specs/BEHAVIORAL_SPECS.md"
    coverage_paths = (
        enforcement_coverage_audit.ROOT,
        enforcement_coverage_audit.SPECS_FILE,
        enforcement_coverage_audit.MAKEFILE,
        enforcement_coverage_audit.AGENTS_FILE,
        enforcement_coverage_audit.PLUGIN_DIR,
        enforcement_coverage_audit.SCRIPTS_DIR,
    )
    effectiveness_paths = (
        effectiveness_audit.ROOT,
        effectiveness_audit.SPECS_FILE,
        effectiveness_audit.BUGS_FILE,
        effectiveness_audit.RATCHET_FILE,
    )
    try:
        enforcement_coverage_audit.ROOT = root
        enforcement_coverage_audit.SPECS_FILE = specs_path
        enforcement_coverage_audit.MAKEFILE = root / "Makefile"
        enforcement_coverage_audit.AGENTS_FILE = root / "AGENTS.md"
        enforcement_coverage_audit.PLUGIN_DIR = root / ".opencode/plugin"
        enforcement_coverage_audit.SCRIPTS_DIR = root / "scripts"
        core_specs = enforcement_coverage_audit._parse_specs()
        covered_core_ids = {
            str(spec["id"])
            for spec in core_specs
            if len(_behavioral_group(spec["id"])) > 1
            and str(spec["enforcement"]).strip().lower()
            not in {"agents.md", "makefile"}
            and enforcement_coverage_audit._enforcement_exists(
                str(spec["enforcement"])
            )
        }

        generated_specs = generator_audit.parse_specs_raw(specs_path)
        generated_stats = generator_audit.compute_stats(generated_specs)

        effectiveness_audit.ROOT = root
        effectiveness_audit.SPECS_FILE = specs_path
        effectiveness_audit.BUGS_FILE = root / "BUGS.md"
        effectiveness_audit.RATCHET_FILE = root / "config/ratchet.yml"
        ineffective_ids = {
            str(spec["id"])
            for spec in effectiveness_audit.parse_specs()
            if effectiveness_audit.check_recurrences(spec)
        }
    finally:
        (
            enforcement_coverage_audit.ROOT,
            enforcement_coverage_audit.SPECS_FILE,
            enforcement_coverage_audit.MAKEFILE,
            enforcement_coverage_audit.AGENTS_FILE,
            enforcement_coverage_audit.PLUGIN_DIR,
            enforcement_coverage_audit.SCRIPTS_DIR,
        ) = coverage_paths
        (
            effectiveness_audit.ROOT,
            effectiveness_audit.SPECS_FILE,
            effectiveness_audit.BUGS_FILE,
            effectiveness_audit.RATCHET_FILE,
        ) = effectiveness_paths
    return covered_core_ids, generated_stats, ineffective_ids


def _behavioral_inventory(root: Path) -> dict[str, Any]:
    path = root / "docs/specs/BEHAVIORAL_SPECS.md"
    specs = _parse_behavioral_specs(path)
    core = [spec for spec in specs if len(_behavioral_group(spec["id"])) > 1]
    generated = [
        spec for spec in specs if len(_behavioral_group(spec["id"])) == 1
    ]
    covered_core_ids, generated_stats, ineffective_ids = _canonical_behavioral_audits(
        root
    )
    claimed = [spec for spec in specs if spec["enforcement"]]
    generated_real_ids = {
        str(spec["spec_id"])
        for spec in generator_audit.parse_specs_raw(path)
        if not generator_audit.is_template_enforcement(spec)
    }
    generated_missing_ids = {
        spec["id"] for spec in generated if not spec["enforcement"]
    }
    generated_template_ids = {
        spec["id"]
        for spec in generated
        if spec["id"] not in generated_real_ids
        and spec["id"] not in generated_missing_ids
    }
    real_ids = covered_core_ids | generated_real_ids
    missing_ids = (
        {spec["id"] for spec in core if spec["id"] not in covered_core_ids}
        | generated_missing_ids
    )

    records = [
        {
            "id": spec["id"],
            "title": spec["title"],
            "source": {
                "path": "docs/specs/BEHAVIORAL_SPECS.md",
                "kind": "core" if spec in core else "generated",
            },
            "documented": True,
            "claimed_enforcement": bool(spec["enforcement"]),
            "enforcement_quality": (
                "missing"
                if spec["id"] in missing_ids
                else (
                    "template"
                    if spec["id"] in generated_template_ids
                    else "real"
                )
            ),
            "verified_enforcement": spec["id"] in covered_core_ids,
            "ineffective": spec["id"] in ineffective_ids,
        }
        for spec in specs
    ]
    return {
        "source": "docs/specs/BEHAVIORAL_SPECS.md",
        "counts": {
            "documented": len(specs),
            "core": len(core),
            "generated": len(generated),
            "claimed_enforcement": len(claimed),
            "real_enforcement": len(real_ids),
            "template_enforcement": len(generated_template_ids),
            "missing_enforcement": len(missing_ids),
            "verified_enforcement": len(covered_core_ids),
            "ineffective": len(ineffective_ids),
            "core_verified_enforcement": len(covered_core_ids),
            "core_missing_enforcement": len(core) - len(covered_core_ids),
            "generated_real_enforcement": int(
                generated_stats["real_enforcement"]
            ),
            "generated_template_enforcement": int(
                generated_stats["template_enforcement"]
            ),
        },
        "records": records,
    }


def build_inventory(root: Path) -> dict[str, Any]:
    """Build the complete inventory for ``root``."""

    root = root.resolve()
    docs_root = root / "docs"
    paths = sorted(
        path
        for path in docs_root.rglob("*")
        if path.is_file() and path.suffix.lower() in DOC_SUFFIXES
    )
    records: dict[str, _Record] = {}
    coverage: dict[str, dict[str, Any]] = {}
    structured_data: dict[Path, Any] = {}

    # Structured manifests are scanned first so Markdown docs can alias their IDs.
    for path in paths:
        source_path = _relative_path(root, path)
        if path.suffix.lower() not in {".yml", ".yaml", ".json"}:
            continue
        try:
            data = (
                json.loads(path.read_text())
                if path.suffix.lower() == ".json"
                else yaml.safe_load(path.read_text())
            )
        except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
            coverage[source_path] = {
                "path": source_path,
                "disposition": "error",
                "reason": f"parse-error:{type(exc).__name__}",
                "spec_units": 0,
            }
            continue
        structured_data[path] = data
        added = _scan_structured_feature_file(root, path, data, records)
        added += _scan_mcp_manifest(root, path, data, records)
        coverage[source_path] = {
            "path": source_path,
            "disposition": "included" if added else "unrecognized",
            "reason": (
                "structured-feature-or-capability-spec"
                if added
                else "no-feature-spec-grammar-match"
            ),
            "spec_units": added,
        }

    # Alias MCP topic contracts after their canonical JSON manifest entries exist.
    for path, data in structured_data.items():
        added = _scan_mcp_topics(root, path, data, records)
        if added:
            source_path = _relative_path(root, path)
            coverage[source_path] = {
                "path": source_path,
                "disposition": "included",
                "reason": "mcp-capability-alias-contract",
                "spec_units": added,
            }

    for path in paths:
        if path.suffix.lower() != ".md":
            continue
        text = path.read_text(errors="replace")
        added, disposition, reason = _scan_markdown_file(root, path, text, records)
        source_path = _relative_path(root, path)
        coverage[source_path] = {
            "path": source_path,
            "disposition": disposition,
            "reason": reason,
            "spec_units": added,
        }

    finalized = _finalize_records(records)
    coverage_files = [coverage[_relative_path(root, path)] for path in paths]
    disposition_counts = Counter(entry["disposition"] for entry in coverage_files)
    behavioral = _behavioral_inventory(root)
    feature_counts = {
        "total": len(finalized),
        "claimed": _status_counts(finalized, "claim_status"),
        "verified": _status_counts(finalized, "verified_status"),
        "claim_conflicts": sum(bool(record["claim_conflict"]) for record in finalized),
    }
    return {
        "schema_version": 1,
        "root": str(root),
        "inclusion_rules": {
            "gludd": [
                "explicit feature/work-item IDs and numeric ID ranges",
                "implementation-plan phase table rows",
                "YAML/JSON features or specs collections",
                "MCP JSON-schema/YAML capability contracts",
                "document-level feature/design/spec files with normative markers",
            ],
            "deduplication": (
                "normalized global ID; document namespace for local P<n> phases; "
                "manifest-title alias matching for document names"
            ),
            "excluded_from_gludd": (
                "OpenCode/enforcement/guardrail-only specs; reported separately"
            ),
            "verification": (
                "claims and percentages are never evidence; verified status requires "
                "resolvable implementation/test references"
            ),
        },
        "gludd_features": {
            "counts": feature_counts,
            "records": finalized,
        },
        "opencode_behavioral": behavioral,
        "source_coverage": {
            "scanned": len(coverage_files),
            "dispositions": {
                key: disposition_counts.get(key, 0)
                for key in ("included", "excluded", "unrecognized", "error")
            },
            "files": coverage_files,
        },
        "grand_total": {
            "documented": len(finalized) + behavioral["counts"]["documented"],
            "gludd_feature_specs": len(finalized),
            "opencode_behavioral_specs": behavioral["counts"]["documented"],
        },
    }


def _format_status_counts(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{status}={counts[status]}" for status in STATUSES)


def render_human(inventory: Mapping[str, Any]) -> str:
    """Render a concise human report; JSON mode carries individual records."""

    gludd = inventory["gludd_features"]
    behavioral = inventory["opencode_behavioral"]
    coverage = inventory["source_coverage"]
    grand = inventory["grand_total"]
    lines = [
        "GLUDD SPECIFICATION INVENTORY",
        "",
        "Gludd enhancement/feature specifications",
        f"  Documented/deduplicated total: {gludd['counts']['total']}",
        f"  Claimed status: {_format_status_counts(gludd['counts']['claimed'])}",
        f"  Verified status: {_format_status_counts(gludd['counts']['verified'])}",
        f"  Conflicting claims (fail-closed unknown): "
        f"{gludd['counts']['claim_conflicts']}",
        "",
        "OpenCode behavioral/enforcement specifications",
        f"  Documented total: {behavioral['counts']['documented']}",
        f"  Core: {behavioral['counts']['core']}",
        f"  Generated: {behavioral['counts']['generated']}",
        f"  Claimed enforcement: {behavioral['counts']['claimed_enforcement']}",
        f"  Real enforcement: {behavioral['counts']['real_enforcement']}",
        f"  Template enforcement: {behavioral['counts']['template_enforcement']}",
        f"  Missing enforcement: {behavioral['counts']['missing_enforcement']}",
        f"  Core enforced (canonical coverage audit): "
        f"{behavioral['counts']['core_verified_enforcement']}/"
        f"{behavioral['counts']['core']}",
        f"  Generated real/template (canonical generator audit): "
        f"{behavioral['counts']['generated_real_enforcement']}/"
        f"{behavioral['counts']['generated_template_enforcement']}",
        f"  Statically verified enforcement: "
        f"{behavioral['counts']['verified_enforcement']}",
        f"  Ineffective (documented recurrence evidence): "
        f"{behavioral['counts']['ineffective']}",
        "",
        "Source coverage",
        f"  Scanned docs files: {coverage['scanned']}",
        "  Dispositions: "
        + ", ".join(
            f"{key}={value}" for key, value in coverage["dispositions"].items()
        ),
        "",
        f"Grand documented total: {grand['documented']}",
        "Use FORMAT=json for every record, alias, source reference, and evidence path.",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root",
    )
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="output format",
    )
    args = parser.parse_args(argv)
    inventory = build_inventory(args.root)
    if args.format == "json":
        print(json.dumps(inventory, indent=2, sort_keys=True))
    else:
        print(render_human(inventory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
