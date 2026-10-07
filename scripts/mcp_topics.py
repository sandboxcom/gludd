#!/usr/bin/env python3
"""Write and load bounded, integrity-checked MCP topic shards."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

SCHEMA = "gludd.mcp-tool-topics.v1"
PARTS_DIRECTORY = "mcp-tool-topics"
DEFAULT_MAX_LINES = 2_000
HARD_MAX_LINES = 2_499
MAX_PART_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TopicsManifestError(ValueError):
    """The MCP topic manifest or one of its shards is unsafe or inconsistent."""


def _dump_topics(topics: Mapping[str, Any]) -> str:
    return str(
        yaml.safe_dump(
            dict(topics),
            sort_keys=True,
            default_flow_style=False,
            allow_unicode=True,
        )
    )


def _line_count(text: str) -> int:
    return len(text.splitlines())


def _topic_chunks(
    topics: Mapping[str, Any], *, max_lines: int = DEFAULT_MAX_LINES
) -> list[dict[str, Any]]:
    if not 1 <= max_lines <= HARD_MAX_LINES:
        raise TopicsManifestError(
            f"max_lines must be between 1 and {HARD_MAX_LINES}",
        )
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    previous = ""
    for name, topic in sorted(topics.items()):
        if not isinstance(name, str) or not name.startswith("general_ludd."):
            raise TopicsManifestError(f"invalid MCP topic name: {name!r}")
        if name <= previous:
            raise TopicsManifestError("MCP topic names must be unique and ordered")
        candidate = {**current, name: topic}
        if current and _line_count(_dump_topics(candidate)) > max_lines:
            chunks.append(current)
            current = {name: topic}
        else:
            current = candidate
        if _line_count(_dump_topics(current)) > max_lines:
            raise TopicsManifestError(f"single MCP topic exceeds {max_lines} lines: {name}")
        previous = name
    if current:
        chunks.append(current)
    return chunks


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_topics(
    manifest_path: Path,
    topics: Mapping[str, Any],
    *,
    max_lines: int = DEFAULT_MAX_LINES,
) -> tuple[Path, ...]:
    """Write deterministic topic shards, then publish their root manifest."""
    if not topics:
        raise TopicsManifestError("MCP topics must not be empty")
    chunks = _topic_chunks(topics, max_lines=max_lines)
    parts: list[dict[str, Any]] = []
    paths: list[Path] = []
    for index, chunk in enumerate(chunks, start=1):
        relative = PurePosixPath(PARTS_DIRECTORY) / f"topics-{index:03d}.yml"
        path = manifest_path.parent / Path(relative)
        text = _dump_topics(chunk)
        encoded = text.encode("utf-8")
        _atomic_write(path, text)
        paths.append(path)
        parts.append(
            {
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "topic_count": len(chunk),
            }
        )
    manifest = {"schema": SCHEMA, "parts": parts}
    _atomic_write(
        manifest_path,
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
    )
    return (manifest_path, *paths)


def _load_manifest(manifest_path: Path) -> list[Mapping[str, Any]]:
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise TopicsManifestError(f"cannot read MCP topics manifest: {exc}") from exc
    if not isinstance(data, Mapping) or data.get("schema") != SCHEMA:
        raise TopicsManifestError(f"MCP topics manifest schema must be {SCHEMA!r}")
    if set(data) != {"schema", "parts"}:
        raise TopicsManifestError("MCP topics manifest contains unknown fields")
    parts = data.get("parts")
    if not isinstance(parts, list) or not parts:
        raise TopicsManifestError("MCP topics manifest parts must be a non-empty list")
    if not all(isinstance(part, Mapping) for part in parts):
        raise TopicsManifestError("MCP topics manifest parts must be mappings")
    return parts


def _resolve_part(manifest_path: Path, part: Mapping[str, Any]) -> Path:
    if set(part) != {"path", "sha256", "topic_count"}:
        raise TopicsManifestError("MCP topic part contains unknown fields")
    raw_path = part.get("path")
    if not isinstance(raw_path, str):
        raise TopicsManifestError("MCP topic part path must be a string")
    relative = PurePosixPath(raw_path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or relative.parts[0] != PARTS_DIRECTORY
        or relative.suffix not in {".yml", ".yaml"}
    ):
        raise TopicsManifestError(f"unsafe MCP topic part path: {raw_path!r}")
    root = manifest_path.parent.resolve()
    path = manifest_path.parent.joinpath(*relative.parts)
    resolved = path.resolve()
    if root not in resolved.parents or path.is_symlink():
        raise TopicsManifestError(f"MCP topic part escapes its manifest: {raw_path!r}")
    return path


def topic_artifact_paths(manifest_path: Path) -> tuple[Path, ...]:
    """Return the root manifest and its safely resolved ordered shards."""
    return (
        manifest_path,
        *(_resolve_part(manifest_path, part) for part in _load_manifest(manifest_path)),
    )


def load_topics(manifest_path: Path) -> dict[str, Any]:
    """Compose a validated manifest into the original ordered topic mapping."""
    topics: dict[str, Any] = {}
    previous = ""
    for part in _load_manifest(manifest_path):
        path = _resolve_part(manifest_path, part)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise TopicsManifestError(f"cannot read MCP topic part {path.name}: {exc}") from exc
        if len(payload) > MAX_PART_BYTES:
            raise TopicsManifestError(f"MCP topic part exceeds byte limit: {path.name}")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TopicsManifestError(
                f"MCP topic part is not valid UTF-8: {path.name}",
            ) from exc
        if _line_count(text) > HARD_MAX_LINES:
            raise TopicsManifestError(f"MCP topic part exceeds line limit: {path.name}")
        expected_hash = part.get("sha256")
        if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
            raise TopicsManifestError(f"invalid MCP topic part digest: {path.name}")
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise TopicsManifestError(f"MCP topic part digest mismatch: {path.name}")
        try:
            data = yaml.safe_load(payload)
        except (UnicodeError, yaml.YAMLError) as exc:
            raise TopicsManifestError(f"invalid MCP topic part YAML: {path.name}") from exc
        if not isinstance(data, Mapping):
            raise TopicsManifestError(f"MCP topic part must be a mapping: {path.name}")
        topic_count = part.get("topic_count")
        if not isinstance(topic_count, int) or isinstance(topic_count, bool) or topic_count != len(data):
            raise TopicsManifestError(f"MCP topic part count mismatch: {path.name}")
        for name, topic in data.items():
            if not isinstance(name, str) or not name.startswith("general_ludd."):
                raise TopicsManifestError(f"invalid MCP topic name in {path.name}: {name!r}")
            if name <= previous or name in topics:
                raise TopicsManifestError("MCP topics must be globally unique and ordered")
            topics[name] = topic
            previous = name
    return topics
