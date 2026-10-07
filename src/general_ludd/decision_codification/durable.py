"""SQLite-backed multiworker generation and application-outcome state."""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
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
    VerifiedOutcome,
)

_SCHEMA_VERSION: Final[str] = "1"
_MAX_IDEMPOTENCY_KEYS: Final[int] = 100_000
_SAFE_IDENTIFIER: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)
_SHA256: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")


class DurableGenerationStoreError(RolloutError):
    """Raised when durable generation state cannot be trusted or persisted."""


class DurableGenerationStore:
    """Cross-process SQLite implementation of the generation-store contract.

    Every mutation uses ``BEGIN IMMEDIATE`` and every reader opens a fresh
    connection, so independent daemon workers observe committed pointer, use,
    drift, revocation, rollback, and outcome state without process-local caches.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        busy_timeout_seconds: float = 10.0,
    ) -> None:
        """Open or create one fail-closed, versioned durable state database."""
        if (
            isinstance(busy_timeout_seconds, bool)
            or not isinstance(busy_timeout_seconds, (int, float))
            or not 0 < busy_timeout_seconds <= 60
        ):
            raise ValueError("busy timeout must be between zero and 60 seconds")
        raw_path = Path(path)
        if raw_path.exists() and raw_path.is_symlink():
            raise DurableGenerationStoreError("durable state path must not be a symlink")
        raw_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path = raw_path.absolute()
        self._timeout = float(busy_timeout_seconds)
        self._initialize()

    @property
    def path(self) -> Path:
        """Return the configured shared state path."""
        return self._path

    def current(
        self, project_id: str, decision_kind: DecisionKind
    ) -> GenerationPointer | None:
        """Read the currently published complete pointer for one exact scope."""
        self._validate_scope(project_id, decision_kind)
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT project_id, decision_kind, candidate_digest,
                       receipt_digest, stage, epoch
                FROM generation_pointers
                WHERE project_id = ? AND decision_kind = ?
                """,
                (project_id, decision_kind.value),
            ).fetchone()
        return None if row is None else self._pointer_from_row(row)

    def compare_and_swap(
        self,
        pointer: GenerationPointer,
        *,
        expected_candidate_digest: str | None,
    ) -> GenerationPointer | None:
        """Atomically publish one pointer only when the exact expectation holds."""
        self._validate_pointer(pointer)
        if expected_candidate_digest is not None:
            self._validate_digest(expected_candidate_digest)
        with self._write_connection() as connection:
            current_row = connection.execute(
                """
                SELECT project_id, decision_kind, candidate_digest,
                       receipt_digest, stage, epoch
                FROM generation_pointers
                WHERE project_id = ? AND decision_kind = ?
                """,
                (pointer.project_id, pointer.decision_kind.value),
            ).fetchone()
            current = (
                None if current_row is None else self._pointer_from_row(current_row)
            )
            current_digest = current.candidate_digest if current is not None else None
            if current_digest != expected_candidate_digest:
                return None
            if current is not None and current.candidate_digest != pointer.candidate_digest:
                connection.execute(
                    """
                    INSERT INTO generation_history (
                        project_id, decision_kind, candidate_digest,
                        receipt_digest, stage, epoch
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    self._pointer_values(current),
                )
            epoch = self._next_epoch(connection, pointer.project_id, pointer.decision_kind)
            installed = GenerationPointer(
                project_id=pointer.project_id,
                decision_kind=pointer.decision_kind,
                candidate_digest=pointer.candidate_digest,
                receipt_digest=pointer.receipt_digest,
                stage=pointer.stage,
                epoch=epoch,
            )
            connection.execute(
                """
                INSERT INTO generation_pointers (
                    project_id, decision_kind, candidate_digest,
                    receipt_digest, stage, epoch
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, decision_kind) DO UPDATE SET
                    candidate_digest = excluded.candidate_digest,
                    receipt_digest = excluded.receipt_digest,
                    stage = excluded.stage,
                    epoch = excluded.epoch
                """,
                self._pointer_values(installed),
            )
            connection.execute(
                "DELETE FROM inactive_reasons WHERE project_id = ? AND decision_kind = ?",
                (pointer.project_id, pointer.decision_kind.value),
            )
        return installed

    def mark_drift_hold(self, candidate_digest: str, reason: str) -> None:
        """Persist the first bounded drift reason for a candidate."""
        self._validate_digest(candidate_digest)
        if _SAFE_IDENTIFIER.fullmatch(reason) is None:
            raise DurableGenerationStoreError("drift reason is invalid")
        with self._write_connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO drift_holds(candidate_digest, reason) VALUES (?, ?)",
                (candidate_digest, reason),
            )

    def is_drift_held(self, candidate_digest: str) -> bool:
        """Return whether durable feedback has held a candidate."""
        self._validate_digest(candidate_digest)
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM drift_holds WHERE candidate_digest = ?",
                (candidate_digest,),
            ).fetchone()
        return row is not None

    def revoke(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        *,
        remove_pointer: bool,
    ) -> bool:
        """Atomically revoke the exact current generation and optional pointer."""
        self._validate_scope(project_id, decision_kind)
        self._validate_digest(expected_candidate_digest)
        with self._write_connection() as connection:
            row = connection.execute(
                """
                SELECT candidate_digest FROM generation_pointers
                WHERE project_id = ? AND decision_kind = ?
                """,
                (project_id, decision_kind.value),
            ).fetchone()
            if row is None or row["candidate_digest"] != expected_candidate_digest:
                return False
            connection.execute(
                "INSERT OR IGNORE INTO revoked_candidates(candidate_digest) VALUES (?)",
                (expected_candidate_digest,),
            )
            if remove_pointer:
                connection.execute(
                    "DELETE FROM generation_pointers WHERE project_id = ? AND decision_kind = ?",
                    (project_id, decision_kind.value),
                )
                connection.execute(
                    """
                    INSERT INTO inactive_reasons(project_id, decision_kind, reason)
                    VALUES (?, ?, 'revoked')
                    ON CONFLICT(project_id, decision_kind) DO UPDATE SET
                        reason = excluded.reason
                    """,
                    (project_id, decision_kind.value),
                )
        return True

    def inactive_reason(
        self, project_id: str, decision_kind: DecisionKind
    ) -> str | None:
        """Read the bounded tombstone retained after pointer removal."""
        self._validate_scope(project_id, decision_kind)
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT reason FROM inactive_reasons
                WHERE project_id = ? AND decision_kind = ?
                """,
                (project_id, decision_kind.value),
            ).fetchone()
        return None if row is None else str(row["reason"])

    def is_revoked(self, candidate_digest: str) -> bool:
        """Return whether a candidate is durably revoked."""
        self._validate_digest(candidate_digest)
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM revoked_candidates WHERE candidate_digest = ?",
                (candidate_digest,),
            ).fetchone()
        return row is not None

    def force_revoke(self, candidate_digest: str) -> None:
        """Persist an emergency integrity revocation without a pointer lookup."""
        self._validate_digest(candidate_digest)
        with self._write_connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO revoked_candidates(candidate_digest) VALUES (?)",
                (candidate_digest,),
            )

    def reserve_use(
        self,
        candidate_digest: str,
        application_id: str,
        maximum_use_count: int,
    ) -> bool:
        """Idempotently reserve one application across all daemon workers."""
        self._validate_digest(candidate_digest)
        self._validate_digest(application_id)
        if type(maximum_use_count) is not int or maximum_use_count < 1:
            raise DurableGenerationStoreError("maximum use count is invalid")
        with self._write_connection() as connection:
            existing = connection.execute(
                """
                SELECT 1 FROM applications
                WHERE candidate_digest = ? AND application_id = ?
                """,
                (candidate_digest, application_id),
            ).fetchone()
            if existing is not None:
                return True
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM applications WHERE candidate_digest = ?",
                    (candidate_digest,),
                ).fetchone()[0]
            )
            if count >= min(maximum_use_count, _MAX_IDEMPOTENCY_KEYS):
                return False
            connection.execute(
                "INSERT INTO applications(candidate_digest, application_id) VALUES (?, ?)",
                (candidate_digest, application_id),
            )
        return True

    def has_application(self, candidate_digest: str, application_id: str) -> bool:
        """Return whether an exact application was durably issued."""
        self._validate_digest(candidate_digest)
        self._validate_digest(application_id)
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM applications
                WHERE candidate_digest = ? AND application_id = ?
                """,
                (candidate_digest, application_id),
            ).fetchone()
        return row is not None

    def use_count(self, candidate_digest: str) -> int:
        """Return the durable distinct-application count."""
        self._validate_digest(candidate_digest)
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM applications WHERE candidate_digest = ?",
                (candidate_digest,),
            ).fetchone()
        return int(row[0])

    def record_outcome(self, outcome: DecisionApplicationOutcomeV1) -> bool:
        """Idempotently append terminal feedback for an issued application."""
        validated = DecisionApplicationOutcomeV1.model_validate(
            outcome.model_dump(mode="python", by_alias=True)
        )
        with self._write_connection() as connection:
            issued = connection.execute(
                """
                SELECT 1 FROM applications
                WHERE candidate_digest = ? AND application_id = ?
                """,
                (validated.candidate_digest, validated.application_id),
            ).fetchone()
            if issued is None:
                raise RolloutError("outcome does not reference an issued application")
            existing = connection.execute(
                """
                SELECT project_id, decision_kind, candidate_digest,
                       application_id, rollout_stage, outcome, occurred_at,
                       terminal_event_id, evidence_digest
                FROM application_outcomes
                WHERE candidate_digest = ? AND application_id = ?
                """,
                (validated.candidate_digest, validated.application_id),
            ).fetchone()
            if existing is not None:
                stored = self._outcome_from_row(existing)
                if stored != validated:
                    raise RolloutError("application has conflicting outcome feedback")
                return False
            connection.execute(
                """
                INSERT INTO application_outcomes (
                    project_id, decision_kind, candidate_digest, application_id,
                    rollout_stage, outcome, occurred_at, terminal_event_id,
                    evidence_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._outcome_values(validated),
            )
        return True

    def recent_outcomes(
        self,
        candidate_digest: str,
        *,
        now: datetime,
    ) -> tuple[VerifiedOutcome, ...]:
        """Return at most 100 outcomes from the bounded seven-day window."""
        self._validate_digest(candidate_digest)
        normalized_now = self._utc(now)
        cutoff = normalized_now - timedelta(days=7)
        with self._read_connection() as connection:
            rows = connection.execute(
                """
                SELECT outcome FROM application_outcomes
                WHERE candidate_digest = ? AND occurred_at >= ? AND occurred_at <= ?
                ORDER BY occurred_at DESC, application_id DESC
                LIMIT 100
                """,
                (
                    candidate_digest,
                    cutoff.isoformat(),
                    normalized_now.isoformat(),
                ),
            ).fetchall()
        try:
            return tuple(VerifiedOutcome(str(row["outcome"])) for row in reversed(rows))
        except ValueError as exc:
            raise DurableGenerationStoreError(
                "durable application outcome is invalid"
            ) from exc

    def rollback(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        eligible: Callable[[GenerationPointer], bool],
    ) -> GenerationPointer | None:
        """Atomically restore the newest eligible durable history entry."""
        self._validate_scope(project_id, decision_kind)
        self._validate_digest(expected_candidate_digest)
        with self._write_connection() as connection:
            current_row = connection.execute(
                """
                SELECT project_id, decision_kind, candidate_digest,
                       receipt_digest, stage, epoch
                FROM generation_pointers
                WHERE project_id = ? AND decision_kind = ?
                """,
                (project_id, decision_kind.value),
            ).fetchone()
            if current_row is None:
                raise RolloutError("rollback compare-and-swap expectation failed")
            current = self._pointer_from_row(current_row)
            if current.candidate_digest != expected_candidate_digest:
                raise RolloutError("rollback compare-and-swap expectation failed")
            history_rows = connection.execute(
                """
                SELECT project_id, decision_kind, candidate_digest,
                       receipt_digest, stage, epoch
                FROM generation_history
                WHERE project_id = ? AND decision_kind = ?
                ORDER BY epoch DESC
                """,
                (project_id, decision_kind.value),
            ).fetchall()
            selected: GenerationPointer | None = None
            for row in history_rows:
                candidate = self._pointer_from_row(row)
                if (
                    not self._is_revoked_connection(connection, candidate.candidate_digest)
                    and not self._is_held_connection(connection, candidate.candidate_digest)
                    and eligible(candidate)
                ):
                    selected = candidate
                    break
            epoch = self._next_epoch(connection, project_id, decision_kind)
            if selected is None:
                connection.execute(
                    "DELETE FROM generation_pointers WHERE project_id = ? AND decision_kind = ?",
                    (project_id, decision_kind.value),
                )
                connection.execute(
                    "DELETE FROM inactive_reasons WHERE project_id = ? AND decision_kind = ?",
                    (project_id, decision_kind.value),
                )
                return None
            restored = GenerationPointer(
                project_id=selected.project_id,
                decision_kind=selected.decision_kind,
                candidate_digest=selected.candidate_digest,
                receipt_digest=selected.receipt_digest,
                stage=selected.stage,
                epoch=epoch,
            )
            connection.execute(
                """
                UPDATE generation_pointers
                SET candidate_digest = ?, receipt_digest = ?, stage = ?, epoch = ?
                WHERE project_id = ? AND decision_kind = ?
                """,
                (
                    restored.candidate_digest,
                    restored.receipt_digest,
                    restored.stage.value,
                    restored.epoch,
                    project_id,
                    decision_kind.value,
                ),
            )
            connection.execute(
                """
                DELETE FROM generation_history
                WHERE project_id = ? AND decision_kind = ? AND epoch >= ?
                """,
                (project_id, decision_kind.value, selected.epoch),
            )
            connection.execute(
                "DELETE FROM inactive_reasons WHERE project_id = ? AND decision_kind = ?",
                (project_id, decision_kind.value),
            )
        return restored

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
            raise DurableGenerationStoreError("durable generation state read failed") from exc
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
    def _validate_digest(value: str) -> None:
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise DurableGenerationStoreError("generation digest is invalid")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise DurableGenerationStoreError("outcome timestamp must be timezone-aware")
        return value.astimezone(UTC)


__all__ = ["DurableGenerationStore", "DurableGenerationStoreError"]
