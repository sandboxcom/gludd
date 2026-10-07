"""Low-level SQLite lifecycle and validation for durable decision state."""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from general_ludd.decision_codification.rollout import (
    GenerationPointer,
    RolloutError,
)
from general_ludd.decision_codification.schema import (
    DECISION_APPLICATION_OUTCOME_SCHEMA_V1,
    DecisionApplicationOutcomeV1,
    DecisionKind,
    RolloutStage,
)

_SCHEMA_VERSION: Final[str] = "1"
_SAFE_IDENTIFIER: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)
_SHA256: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")


class DurableGenerationStoreError(RolloutError):
    """Raised when durable generation state cannot be trusted or persisted."""


class _DurableStorage:
    """Own the transactional SQLite boundary shared by higher-level stores."""

    _path: Path
    _timeout: float

    def _initialize(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS state_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS generation_epochs (
                    project_id TEXT NOT NULL,
                    decision_kind TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    PRIMARY KEY(project_id, decision_kind)
                );
                CREATE TABLE IF NOT EXISTS generation_pointers (
                    project_id TEXT NOT NULL,
                    decision_kind TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    receipt_digest TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    PRIMARY KEY(project_id, decision_kind)
                );
                CREATE TABLE IF NOT EXISTS generation_history (
                    project_id TEXT NOT NULL,
                    decision_kind TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    receipt_digest TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    PRIMARY KEY(project_id, decision_kind, epoch)
                );
                CREATE TABLE IF NOT EXISTS drift_holds (
                    candidate_digest TEXT PRIMARY KEY,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS revoked_candidates (
                    candidate_digest TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS inactive_reasons (
                    project_id TEXT NOT NULL,
                    decision_kind TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY(project_id, decision_kind)
                );
                CREATE TABLE IF NOT EXISTS applications (
                    candidate_digest TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    PRIMARY KEY(candidate_digest, application_id)
                );
                CREATE TABLE IF NOT EXISTS application_outcomes (
                    project_id TEXT NOT NULL,
                    decision_kind TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    rollout_stage TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    terminal_event_id TEXT,
                    evidence_digest TEXT,
                    PRIMARY KEY(candidate_digest, application_id)
                );
                CREATE INDEX IF NOT EXISTS ix_application_outcomes_window
                ON application_outcomes(candidate_digest, occurred_at);
                """
            )
            existing = connection.execute(
                "SELECT value FROM state_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO state_metadata(key, value) VALUES ('schema_version', ?)",
                    (_SCHEMA_VERSION,),
                )
            elif existing["value"] != _SCHEMA_VERSION:
                raise DurableGenerationStoreError(
                    "durable state schema version is unsupported"
                )
            os.chmod(self._path, 0o600)
        except DurableGenerationStoreError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise DurableGenerationStoreError(
                "durable generation state initialization failed"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=self._timeout,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextlib.contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            yield connection
        except DurableGenerationStoreError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise DurableGenerationStoreError(
                "durable generation state read failed"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    @contextlib.contextmanager
    def _write_connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except RolloutError:
            if connection is not None:
                connection.rollback()
            raise
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.rollback()
            raise DurableGenerationStoreError(
                "durable generation state write failed"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _next_epoch(
        connection: sqlite3.Connection,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> int:
        row = connection.execute(
            """
            SELECT epoch FROM generation_epochs
            WHERE project_id = ? AND decision_kind = ?
            """,
            (project_id, decision_kind.value),
        ).fetchone()
        epoch = (0 if row is None else int(row["epoch"])) + 1
        connection.execute(
            """
            INSERT INTO generation_epochs(project_id, decision_kind, epoch)
            VALUES (?, ?, ?)
            ON CONFLICT(project_id, decision_kind) DO UPDATE SET epoch = excluded.epoch
            """,
            (project_id, decision_kind.value, epoch),
        )
        return epoch

    @classmethod
    def _pointer_from_row(cls, row: sqlite3.Row) -> GenerationPointer:
        try:
            pointer = GenerationPointer(
                project_id=str(row["project_id"]),
                decision_kind=DecisionKind(str(row["decision_kind"])),
                candidate_digest=str(row["candidate_digest"]),
                receipt_digest=str(row["receipt_digest"]),
                stage=RolloutStage(str(row["stage"])),
                epoch=int(row["epoch"]),
            )
            cls._validate_pointer(pointer)
        except (KeyError, TypeError, ValueError) as exc:
            raise DurableGenerationStoreError(
                "durable generation pointer is invalid"
            ) from exc
        return pointer

    @staticmethod
    def _pointer_values(pointer: GenerationPointer) -> tuple[object, ...]:
        return (
            pointer.project_id,
            pointer.decision_kind.value,
            pointer.candidate_digest,
            pointer.receipt_digest,
            pointer.stage.value,
            pointer.epoch,
        )

    @classmethod
    def _outcome_from_row(cls, row: sqlite3.Row) -> DecisionApplicationOutcomeV1:
        try:
            return DecisionApplicationOutcomeV1(
                schema=DECISION_APPLICATION_OUTCOME_SCHEMA_V1,
                project_id=row["project_id"],
                decision_kind=row["decision_kind"],
                candidate_digest=row["candidate_digest"],
                application_id=row["application_id"],
                rollout_stage=row["rollout_stage"],
                outcome=row["outcome"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                terminal_event_id=row["terminal_event_id"],
                evidence_digest=row["evidence_digest"],
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise DurableGenerationStoreError(
                "durable application outcome is invalid"
            ) from exc

    @staticmethod
    def _outcome_values(outcome: DecisionApplicationOutcomeV1) -> tuple[object, ...]:
        return (
            outcome.project_id,
            outcome.decision_kind.value,
            outcome.candidate_digest,
            outcome.application_id,
            outcome.rollout_stage.value,
            outcome.outcome.value,
            outcome.occurred_at.isoformat(),
            outcome.terminal_event_id,
            outcome.evidence_digest,
        )

    @staticmethod
    def _is_revoked_connection(
        connection: sqlite3.Connection, candidate_digest: str
    ) -> bool:
        return connection.execute(
            "SELECT 1 FROM revoked_candidates WHERE candidate_digest = ?",
            (candidate_digest,),
        ).fetchone() is not None

    @staticmethod
    def _is_held_connection(
        connection: sqlite3.Connection, candidate_digest: str
    ) -> bool:
        return connection.execute(
            "SELECT 1 FROM drift_holds WHERE candidate_digest = ?",
            (candidate_digest,),
        ).fetchone() is not None

    @classmethod
    def _validate_pointer(cls, pointer: GenerationPointer) -> None:
        cls._validate_scope(pointer.project_id, pointer.decision_kind)
        cls._validate_digest(pointer.candidate_digest)
        cls._validate_digest(pointer.receipt_digest)
        if not isinstance(pointer.stage, RolloutStage):
            raise DurableGenerationStoreError("generation stage is invalid")
        if type(pointer.epoch) is not int or pointer.epoch < 0:
            raise DurableGenerationStoreError("generation epoch is invalid")

    @staticmethod
    def _validate_scope(project_id: str, decision_kind: DecisionKind) -> None:
        if _SAFE_IDENTIFIER.fullmatch(project_id) is None:
            raise DurableGenerationStoreError("generation project scope is invalid")
        if not isinstance(decision_kind, DecisionKind):
            raise DurableGenerationStoreError("generation decision kind is invalid")

    @staticmethod
    def _validate_reason(reason: str) -> None:
        if _SAFE_IDENTIFIER.fullmatch(reason) is None:
            raise DurableGenerationStoreError("drift reason is invalid")

    @staticmethod
    def _validate_digest(value: str) -> None:
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise DurableGenerationStoreError("generation digest is invalid")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise DurableGenerationStoreError(
                "outcome timestamp must be timezone-aware"
            )
        return value.astimezone(UTC)


__all__ = ["DurableGenerationStoreError"]
