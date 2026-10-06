"""Legacy per-run replay capture with a bounded redaction boundary."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast

from general_ludd.filestore.store import FileStore
from general_ludd.replay.telemetry import ReplayTelemetry
from general_ludd.security.redaction import (
    RedactionLimits,
    RedactionMetadata,
    redact_for_persistence,
)

CaptureState = Literal["complete", "redacted", "truncated", "redacted_truncated"]

_CAPTURE_SCHEMA: Final[str] = "gludd.legacy-capture/v1"
_CAPTURE_STATES: Final[frozenset[str]] = frozenset(
    {"complete", "redacted", "truncated", "redacted_truncated"}
)


@dataclass(frozen=True, slots=True)
class CaptureMetadata:
    """Typed, content-free evidence for one legacy event capture."""

    sequence: int
    state: CaptureState
    redaction_count: int
    redaction_kinds: tuple[str, ...]
    truncation_count: int
    truncation_kinds: tuple[str, ...]
    stored_bytes: int
    schema: str = field(default=_CAPTURE_SCHEMA, init=False)

    @classmethod
    def from_redaction(
        cls,
        *,
        sequence: int,
        redaction: RedactionMetadata,
        stored_bytes: int,
    ) -> CaptureMetadata:
        """Build capture state from canonical redaction counters."""
        if redaction.redaction_count and redaction.truncation_count:
            state: CaptureState = "redacted_truncated"
        elif redaction.redaction_count:
            state = "redacted"
        elif redaction.truncation_count:
            state = "truncated"
        else:
            state = "complete"
        return cls(
            sequence=sequence,
            state=state,
            redaction_count=redaction.redaction_count,
            redaction_kinds=redaction.redaction_kinds,
            truncation_count=redaction.truncation_count,
            truncation_kinds=redaction.truncation_kinds,
            stored_bytes=stored_bytes,
        )

    def as_dict(self) -> dict[str, object]:
        """Return the stable JSON sidecar representation."""
        return {
            "schema": self.schema,
            "sequence": self.sequence,
            "state": self.state,
            "redaction": {
                "redaction_count": self.redaction_count,
                "redaction_kinds": list(self.redaction_kinds),
                "truncation_count": self.truncation_count,
                "truncation_kinds": list(self.truncation_kinds),
                "truncated": self.truncation_count > 0,
                "stored_bytes": self.stored_bytes,
            },
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> CaptureMetadata:
        """Parse one recorder-owned sidecar without accepting source content."""
        if payload.get("schema") != _CAPTURE_SCHEMA:
            raise ValueError("capture metadata schema is invalid")
        sequence = payload.get("sequence")
        state = payload.get("state")
        redaction = payload.get("redaction")
        if type(sequence) is not int or state not in _CAPTURE_STATES or not isinstance(redaction, dict):
            raise ValueError("capture metadata shape is invalid")

        redaction_count = redaction.get("redaction_count")
        redaction_kinds = redaction.get("redaction_kinds")
        truncation_count = redaction.get("truncation_count")
        truncation_kinds = redaction.get("truncation_kinds")
        stored_bytes = redaction.get("stored_bytes")
        if (
            type(redaction_count) is not int
            or type(truncation_count) is not int
            or type(stored_bytes) is not int
            or not isinstance(redaction_kinds, list)
            or not all(type(item) is str for item in redaction_kinds)
            or not isinstance(truncation_kinds, list)
            or not all(type(item) is str for item in truncation_kinds)
        ):
            raise ValueError("capture redaction metadata is invalid")
        return cls(
            sequence=sequence,
            state=cast(CaptureState, state),
            redaction_count=redaction_count,
            redaction_kinds=tuple(cast(list[str], redaction_kinds)),
            truncation_count=truncation_count,
            truncation_kinds=tuple(cast(list[str], truncation_kinds)),
            stored_bytes=stored_bytes,
        )


class RunRecorder:
    """Record redacted legacy events and replay them for audit or debugging.

    Event JSON remains in the historical ``runs/<id>/events/<n>.json`` shape.
    Content-free capture metadata lives beside it under ``capture/`` so valid
    callers of :meth:`replay` and :meth:`list_runs` retain their old contract.
    """

    def __init__(
        self,
        store: FileStore | None = None,
        *,
        redaction_limits: RedactionLimits | None = None,
        telemetry: ReplayTelemetry | None = None,
    ) -> None:
        """Initialize a recorder with an optional store and bounded limits."""
        self._store = store if store is not None else FileStore(root_path=".gludd/replays")
        self._redaction_limits = redaction_limits or RedactionLimits()
        self._telemetry = telemetry if telemetry is not None else ReplayTelemetry()

    def record(self, run_id: str, event: dict[str, Any]) -> None:
        """Atomically publish one sanitized event and its capture metadata."""
        try:
            event_type, metadata = self._record(run_id, event)
        except Exception as exc:
            reason = "storage" if isinstance(exc, OSError) else "redaction"
            self._telemetry.record_failure(reason)
            self._telemetry.operation("record", "failure")
            raise
        self._telemetry.events_recorded("legacy-v0", event_type)
        for kind in metadata.redaction_kinds:
            self._telemetry.redactions(kind)
        if metadata.truncation_count:
            self._telemetry.truncations("event", count=metadata.truncation_count)
        self._telemetry.operation("record", "success")

    def _record(
        self,
        run_id: str,
        event: dict[str, Any],
    ) -> tuple[object, CaptureMetadata]:
        """Publish one event and return only bounded telemetry evidence."""
        events_dir = f"runs/{run_id}/events"
        capture_dir = f"runs/{run_id}/capture"
        sequence = self._next_seq(events_dir)
        event_path = f"{events_dir}/{sequence}.json"
        capture_path = f"{capture_dir}/{sequence}.json"

        event_type = dict.get(event, "type")
        redacted = redact_for_persistence(event, limits=self._redaction_limits)
        # The total-byte fail-closed marker is scalar; keep the historical
        # replay return type while the sidecar explains why content is gone.
        event_bytes = (
            redacted.canonical_json_bytes()
            if isinstance(redacted.value, dict)
            else b"{}"
        )
        metadata = CaptureMetadata.from_redaction(
            sequence=sequence,
            redaction=redacted.metadata,
            stored_bytes=len(event_bytes),
        )
        metadata_text = json.dumps(
            metadata.as_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

        try:
            # Publish metadata first and the event last. A reader can therefore
            # never observe an event whose content-safety evidence is absent.
            self._atomic_write_text(capture_path, metadata_text)
            self._atomic_write_text(event_path, event_bytes.decode("utf-8"))
        except Exception:
            self._remove_if_exists(capture_path)
            raise
        return event_type, metadata

    def replay(self, run_id: str) -> list[dict[str, Any]]:
        """Return captured events in sequence order using the legacy shape."""
        try:
            events = self._replay(run_id)
        except Exception:
            self._telemetry.operation("show", "failure")
            raise
        self._telemetry.legacy_read()
        self._telemetry.operation("show", "success")
        return events

    def _replay(self, run_id: str) -> list[dict[str, Any]]:
        """Read legacy events without announcing a partial read as successful."""
        events_dir = f"runs/{run_id}/events"
        events: list[dict[str, Any]] = []
        for _, name in self._numeric_json_entries(events_dir):
            decoded: object = json.loads(self._store.read_text(f"{events_dir}/{name}"))
            if isinstance(decoded, dict):
                events.append(cast(dict[str, Any], decoded))
        return events

    def capture_metadata(self, run_id: str) -> list[CaptureMetadata]:
        """Return typed content-free capture metadata in event sequence order."""
        try:
            captures = self._capture_metadata(run_id)
        except Exception:
            self._telemetry.operation("show", "failure")
            raise
        self._telemetry.legacy_read()
        self._telemetry.operation("show", "success")
        return captures

    def _capture_metadata(self, run_id: str) -> list[CaptureMetadata]:
        """Read all sidecars before emitting a successful legacy-read metric."""
        capture_dir = f"runs/{run_id}/capture"
        captures: list[CaptureMetadata] = []
        for _, name in self._numeric_json_entries(capture_dir):
            decoded: object = json.loads(self._store.read_text(f"{capture_dir}/{name}"))
            if not isinstance(decoded, dict):
                raise ValueError("capture metadata root is invalid")
            captures.append(CaptureMetadata.from_dict(cast(dict[str, object], decoded)))
        return captures

    def list_runs(self) -> list[str]:
        """Return recorded run identifiers in lexical order."""
        try:
            runs_dir = "runs"
            if not self._store.exists(runs_dir):
                runs: list[str] = []
            else:
                entries = self._store.list_dir(runs_dir)
                runs = sorted(e["name"] for e in entries if e["is_dir"])
        except Exception:
            self._telemetry.operation("list", "failure")
            raise
        self._telemetry.operation("list", "success")
        return runs

    def _numeric_json_entries(self, directory: str) -> list[tuple[int, str]]:
        if not self._store.exists(directory):
            return []
        numbered: list[tuple[int, str]] = []
        for entry in self._store.list_dir(directory):
            name = entry.get("name")
            if entry.get("is_dir") or not isinstance(name, str) or not name.endswith(".json"):
                continue
            try:
                sequence = int(name.removesuffix(".json"))
            except ValueError:
                continue
            numbered.append((sequence, name))
        return sorted(numbered)

    def _next_seq(self, events_dir: str) -> int:
        entries = self._numeric_json_entries(events_dir)
        return 0 if not entries else entries[-1][0] + 1

    def _atomic_write_text(self, path: str, content: str) -> None:
        temporary_path = f"{path}.{uuid.uuid4().hex}.tmp"
        try:
            self._store.write_text(temporary_path, content)
            self._store.move(temporary_path, path)
        except Exception:
            self._remove_if_exists(temporary_path)
            raise

    def _remove_if_exists(self, path: str) -> None:
        try:
            if self._store.exists(path):
                self._store.remove(path)
        except Exception:
            # Cleanup is best-effort and must not replace the original failure.
            return


__all__ = ["CaptureMetadata", "RunRecorder"]
