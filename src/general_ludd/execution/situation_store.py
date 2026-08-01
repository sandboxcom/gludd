"""BadCallSituationStore — persists blocked-tool-call situations."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from general_ludd.security.state import project_state, secure_directory, secure_write_text

logger = logging.getLogger(__name__)

_MAC_KEY = b"gludd-situation-store-integrity"


@dataclass
class BadCallSituation:
    """Snapshot of a blocked tool call for prompt enhancement."""

    tool_name: str
    tool_args: dict[str, Any]
    classification: str
    reason: str
    task_excerpt: str = ""
    recent_calls: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = 0.0
    work_type: str = ""


def _compute_mac(data: bytes) -> str:
    return hmac.new(_MAC_KEY, data, hashlib.sha256).hexdigest()


def _serialize(situation: BadCallSituation) -> dict[str, object]:
    return {
        "tool_name": situation.tool_name,
        "tool_args": situation.tool_args,
        "classification": situation.classification,
        "reason": situation.reason,
        "task_excerpt": situation.task_excerpt,
        "recent_calls": situation.recent_calls,
        "timestamp": situation.timestamp,
        "work_type": situation.work_type,
    }


def _deserialize(data: dict[str, Any]) -> BadCallSituation:
    return BadCallSituation(
        tool_name=data.get("tool_name", ""),
        tool_args=data.get("tool_args", {}),
        classification=data.get("classification", "unknown"),
        reason=data.get("reason", ""),
        task_excerpt=data.get("task_excerpt", ""),
        recent_calls=data.get("recent_calls", []),
        timestamp=data.get("timestamp", 0.0),
        work_type=data.get("work_type", ""),
    )


class BadCallSituationStore:
    """Persists BadCallSituation records for later analysis.

    Disk-based persistence with MAC verification for integrity.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._dir: Path | None = secure_directory(base_dir) if base_dir else None
        self._inmemory: list[BadCallSituation] = []

    def save(self, situation: BadCallSituation) -> Path:
        """Save a BadCallSituation to disk. Returns the path written."""
        if situation.timestamp == 0.0:
            situation.timestamp = time.time()
        self._inmemory.append(situation)

        ts = int(situation.timestamp * 1_000_000)

        if self._dir is None:
            dummy = project_state().path(
                "situations",
                f"situation_{ts}.json",
            )
            return dummy

        data = _serialize(situation)
        raw = json.dumps(data, sort_keys=True, default=str)
        mac = _compute_mac(raw.encode())

        fname = f"situation_{ts}.json"
        fpath = self._dir / fname
        secure_write_text(fpath, raw)

        mac_path = self._dir / (fname + ".mac")
        secure_write_text(mac_path, mac)

        return fpath

    def load(self, filename: str) -> BadCallSituation | None:
        """Load a situation from its filename. Returns None if not found or tampered."""
        if self._dir is None:
            return None
        if Path(filename).name != filename:
            logger.warning("refusing unsafe situation filename: %r", filename)
            return None
        fpath = self._dir / filename
        mac_path = self._dir / (filename + ".mac")
        if not fpath.exists() or not mac_path.exists():
            return None

        try:
            raw = fpath.read_text()
            expected_mac = mac_path.read_text().strip()
            actual_mac = _compute_mac(raw.encode())
            if not hmac.compare_digest(actual_mac, expected_mac):
                logger.warning("MAC mismatch for %s — file tampered", filename)
                return None
        except Exception:
            return None

        try:
            return _deserialize(json.loads(raw))
        except (json.JSONDecodeError, KeyError):
            return None

    def list_recent(self, limit: int = 10) -> list[BadCallSituation]:
        """Return the most recent situations, newest first."""
        if self._dir is None:
            return sorted(
                self._inmemory,
                key=lambda s: s.timestamp,
                reverse=True,
            )[:limit]
        situations: list[BadCallSituation] = []
        for fpath in sorted(self._dir.glob("*.json")):
            mac_path = self._dir / (fpath.name + ".mac")
            if not mac_path.exists():
                continue
            try:
                raw = fpath.read_text()
                expected_mac = mac_path.read_text().strip()
                if not hmac.compare_digest(_compute_mac(raw.encode()), expected_mac):
                    continue
                situations.append(_deserialize(json.loads(raw)))
            except Exception:
                continue
        situations.sort(key=lambda s: s.timestamp, reverse=True)
        return situations[:limit]

    def list_by_classification(self, classification: str, limit: int | None = None) -> list[BadCallSituation]:
        """Return situations matching the given classification."""
        result = [
            s for s in self.list_recent(limit=10_000)
            if s.classification == classification
        ]
        if limit is not None:
            return result[:limit]
        return result

    def list_by_tool(self, tool_name: str, limit: int | None = None) -> list[BadCallSituation]:
        """Return situations for the given tool name."""
        result = [
            s for s in self.list_recent(limit=10_000)
            if s.tool_name == tool_name
        ]
        if limit is not None:
            return result[:limit]
        return result

    def prune(self, max_age_seconds: float) -> int:
        """Remove situations older than max_age_seconds. Returns count removed."""
        if self._dir is None:
            return 0
        cutoff = time.time() - max_age_seconds
        removed = 0
        for fpath in list(self._dir.glob("*.json")):
            try:
                raw = fpath.read_text()
                data = json.loads(raw)
                ts = data.get("timestamp", 0)
                if ts < cutoff:
                    fpath.unlink()
                    mac_path = self._dir / (fpath.name + ".mac")
                    if mac_path.exists():
                        mac_path.unlink()
                    removed += 1
            except Exception:
                continue
        return removed

    def count(self) -> int:
        """Return the total number of saved situations."""
        if self._dir is None:
            return len(self._inmemory)
        return len(list(self._dir.glob("*.json")))
