"""Identity-bound diagnostic snapshots, never gate or release attestations."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stream_command import _atomic_write_json
else:
    if __package__ in {None, ""}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.stream_command import _atomic_write_json

_PRODUCERS = frozenset({"ci-await", "pipeline-status"})
_FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MAX_BYTES = 2 * 1024 * 1024


def _validate_record(record: object, *, producer: str) -> dict[str, Any]:
    """Reject an unknown owner, malformed identity, or fabricated authority."""
    if not isinstance(record, dict):
        raise ValueError("observation schema must be an object")
    if (record.get("schema_version") != 1
            or record.get("kind") != "proof_observation"
            or producer not in _PRODUCERS
            or record.get("producer") != producer):
        raise ValueError("observation schema or producer owner mismatch")
    if record.get("release_ready") is not False or record.get("gate_evidence") is not False:
        raise ValueError("diagnostic observations cannot assert release or gate authority")
    identity = record.get("identity")
    if not isinstance(identity, dict) or set(identity) != {"repo", "branch", "sha"}:
        raise ValueError("observation identity must bind repo, branch and SHA")
    if any(not isinstance(identity[key], str) or not identity[key].strip()
           for key in ("repo", "branch")):
        raise ValueError("observation identity has an empty repo or branch")
    sha = identity["sha"]
    if sha is not None and (not isinstance(sha, str) or _FULL_SHA.fullmatch(sha) is None):
        raise ValueError("observation identity SHA must be a full lowercase commit or unknown")
    if not isinstance(record.get("state"), str) or not record["state"]:
        raise ValueError("observation state is missing")
    if sha is None and record["state"] == "success":
        raise ValueError("unknown identity cannot assert success")
    if not isinstance(record.get("evidence"), dict):
        raise ValueError("observation evidence must be an object")
    timestamp = record.get("generated_at")
    if not isinstance(timestamp, str):
        raise ValueError("observation timestamp is missing")
    try:
        generated = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError("observation timestamp is invalid") from exc
    if generated.tzinfo is None:
        raise ValueError("observation timestamp must have a timezone")
    return record


def _load(path: Path, *, producer: str) -> dict[str, Any]:
    """Read one bounded producer-owned snapshot without following a symlink."""
    if path.is_symlink():
        raise ValueError("observation target cannot be a symlink")
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError("observation exceeds the 2 MiB bound")
    record: object = json.loads(path.read_text(encoding="utf-8"))
    return _validate_record(record, producer=producer)


def write_snapshot(
    path: Path,
    *,
    producer: str,
    identity: Mapping[str, object],
    state: str,
    evidence: Mapping[str, object],
) -> None:
    """Atomically refresh a derived record, preserving foreign/manual history."""
    if path.is_symlink():
        raise ValueError("observation target cannot be a symlink")
    if path.exists():
        previous = _load(path, producer=producer)
        if any(previous["identity"][key] != identity.get(key) for key in ("repo", "branch")):
            raise ValueError("observation identity owner differs from the existing target")
    record = {
        "schema_version": 1,
        "kind": "proof_observation",
        "producer": producer,
        "generated_at": datetime.now(UTC).isoformat(),
        "identity": dict(identity),
        "state": state,
        "evidence": dict(evidence),
        "release_ready": False,
        "gate_evidence": False,
    }
    _validate_record(record, producer=producer)
    if len(json.dumps(record).encode("utf-8")) > _MAX_BYTES:
        raise ValueError("observation exceeds the 2 MiB bound")
    _atomic_write_json(path, record)


def read_snapshot(
    path: Path,
    *,
    producer: str,
    identity: Mapping[str, object],
    max_age_seconds: float = 600,
) -> dict[str, Any]:
    """Reject stale, future, foreign-SHA, or fabricated operational state."""
    if max_age_seconds <= 0:
        raise ValueError("observation maximum age must be positive")
    record = _load(path, producer=producer)
    if record["identity"] != dict(identity):
        raise ValueError("observation identity does not match the requested checkout")
    age = (datetime.now(UTC) - datetime.fromisoformat(record["generated_at"])).total_seconds()
    if age < -60 or age > max_age_seconds:
        raise ValueError("observation timestamp is stale or in the future")
    return record
