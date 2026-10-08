"""Read-only adapter for the unversioned legacy replay layout."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from general_ludd.filestore.store import FileStore
from general_ludd.replay.schema import (
    SafeRunId,
    decode_replay_json_object,
    validate_replay_json_value,
    validate_run_id,
)

_LEGACY_EVENT_FILENAME = re.compile(r"([0-9]+)\.json\Z")
LEGACY_SCHEMA_V0 = "legacy-v0"
LEGACY_INTEGRITY_UNVERIFIED = "unverified"


class LegacyReplayError(ValueError):
    """Raised when legacy replay storage is ambiguous or malformed."""


class _StrictLegacyModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        strict=True,
    )


class LegacyEvent(_StrictLegacyModel):
    """One legacy payload paired with its original filename sequence."""

    sequence: int = Field(ge=0)
    payload: dict[str, object]

    @field_validator("payload")
    @classmethod
    def _payload_is_json(cls, value: dict[str, object]) -> dict[str, object]:
        validate_replay_json_value(value, path="$.legacy.payload")
        return value


class LegacyRun(_StrictLegacyModel):
    """Typed, explicitly unverified view over one legacy replay run."""

    schema_version: Literal["legacy-v0"] = Field(default="legacy-v0", alias="schema")
    integrity: Literal["unverified"] = "unverified"
    run_id: SafeRunId
    events: tuple[LegacyEvent, ...]


class LegacyReplayReader:
    """Read legacy event files in numeric order without mutating the store."""

    def __init__(self, store: FileStore) -> None:
        """Bind the read-only adapter to an existing replay store."""
        self._store = store

    def read(self, run_id: str) -> LegacyRun:
        """Return a typed legacy view while preserving numeric event order."""
        safe_run_id = validate_run_id(run_id)
        events_dir = f"runs/{safe_run_id}/events"
        if not self._store.exists(events_dir):
            return LegacyRun(run_id=safe_run_id, events=())
        if not self._store.is_dir(events_dir):
            raise LegacyReplayError("legacy events path must be a directory")

        indexed_names: list[tuple[int, str]] = []
        seen_sequences: set[int] = set()
        for entry in self._store.list_dir(events_dir):
            if entry.get("is_dir") is True:
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                raise LegacyReplayError("legacy event filename metadata is invalid")
            match = _LEGACY_EVENT_FILENAME.fullmatch(name)
            if match is None:
                raise LegacyReplayError(f"legacy event filename is invalid: {name!r}")
            sequence = int(match.group(1))
            if sequence in seen_sequences:
                raise LegacyReplayError(f"duplicate legacy event sequence: {sequence}")
            seen_sequences.add(sequence)
            indexed_names.append((sequence, name))

        events: list[LegacyEvent] = []
        for sequence, name in sorted(indexed_names):
            try:
                payload = decode_replay_json_object(
                    self._store.read_text(f"{events_dir}/{name}")
                )
            except (TypeError, ValueError) as exc:
                raise LegacyReplayError(
                    f"legacy event {name!r} contains invalid JSON: {exc}"
                ) from exc
            events.append(LegacyEvent(sequence=sequence, payload=payload))
        return LegacyRun(run_id=safe_run_id, events=tuple(events))


def read_legacy_run(store: FileStore, run_id: str) -> LegacyRun:
    """Read one legacy run through the compatibility adapter."""
    return LegacyReplayReader(store).read(run_id)


__all__ = [
    "LEGACY_INTEGRITY_UNVERIFIED",
    "LEGACY_SCHEMA_V0",
    "LegacyEvent",
    "LegacyReplayError",
    "LegacyReplayReader",
    "LegacyRun",
    "read_legacy_run",
]
