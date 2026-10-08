"""PostgreSQL-backed, artifact-authenticated generation state for many hosts."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from secrets import token_hex
from typing import Any, Final, cast

from pydantic import ValidationError
from sqlalchemy import delete, select, text
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from general_ludd.db.models import (
    BucketLeaseModel,
    ProjectModel,
    VariableNamespaceModel,
    VariableValueModel,
)
from general_ludd.decision_codification.artifact_store import (
    ArtifactStoreError,
    DecisionArtifactStore,
)
from general_ludd.decision_codification.rollout import (
    GenerationPointer,
    RolloutError,
    artifacts_have_exact_bindings,
)
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionApplicationOutcomeV1,
    DecisionKind,
    DecisionRuleBundleV1,
    RolloutStage,
    VerifiedOutcome,
    canonical_decision_json,
)

_STATE_SCHEMA: Final[str] = "gludd.decision-shared-generation-state/v1"
_HEAD_NAMESPACE: Final[str] = "decision-shared-generation-v1"
_CANDIDATE_NAMESPACE_PREFIX: Final[str] = "decision-generation-candidate:"
_LEASE_DOMAIN: Final[bytes] = b"general_ludd.decision_generation.lease.v1\x00"
_SAFE_IDENTIFIER: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)
_SHA256: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_RECORD_BYTES: Final[int] = 64 * 1024
_MAX_HISTORY: Final[int] = 256
_MAX_APPLICATIONS: Final[int] = 100_000
_MAX_RECENT_OUTCOMES: Final[int] = 100


class SharedGenerationStoreError(RolloutError):
    """Refuse a shared-state operation whose identity cannot be trusted."""


class PostgresGenerationStore:
    """Serialize exact generation transitions through existing SQLAlchemy rows.

    PostgreSQL's unique ``bucket_leases.bucket_key`` constraint provides bounded
    cross-host admission. Project-scoped ``variable_values`` rows hold only
    HMAC-authenticated, digest-only state. Every transition, fencing lease, and
    compare-and-swap check is committed or rolled back in one transaction.
    """

    def __init__(
        self,
        engine: Engine,
        artifacts: DecisionArtifactStore,
        *,
        lease_ttl_seconds: int = 30,
        lock_timeout_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
        allow_sqlite_for_tests: bool = False,
    ) -> None:
        """Bind one shared database and the existing artifact trust root."""
        dialect = engine.dialect.name
        if dialect != "postgresql" and not (
            allow_sqlite_for_tests and dialect == "sqlite"
        ):
            raise ValueError("shared generation state requires PostgreSQL")
        if (
            type(lease_ttl_seconds) is not int
            or not 1 <= lease_ttl_seconds <= 60
        ):
            raise ValueError("generation lease TTL must be between 1 and 60 seconds")
        if (
            isinstance(lock_timeout_seconds, bool)
            or not isinstance(lock_timeout_seconds, (int, float))
            or not 0 < lock_timeout_seconds <= 60
        ):
            raise ValueError("generation lock timeout must be between zero and 60 seconds")
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._artifacts = artifacts
        self._lease_ttl = lease_ttl_seconds
        self._lock_timeout = float(lock_timeout_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def engine(self) -> Engine:
        """Return the SQLAlchemy engine used for shared state."""
        return self._engine

    def coordination_key(self, project_id: str) -> str:
        """Return one opaque project lease key without exposing scope text."""
        self._validate_scope(project_id, DecisionKind.REVIEW)
        digest = hashlib.sha256(
            _LEASE_DOMAIN + project_id.encode("utf-8")
        ).hexdigest()
        return f"decision-generation:{digest}"

    def verify_ready(self, project_id: str) -> None:
        """Fail startup unless the migrated shared scope is reachable."""
        self._validate_scope(project_id, DecisionKind.REVIEW)
        try:
            with self._sessions() as session, session.begin():
                self._set_transaction_timeout(session)
                project = session.scalar(
                    select(ProjectModel.project_id).where(
                        ProjectModel.project_id == project_id
                    )
                )
                session.scalar(
                    select(BucketLeaseModel.id)
                    .where(BucketLeaseModel.bucket_key == self.coordination_key(project_id))
                    .limit(1)
                )
                session.scalar(
                    select(VariableNamespaceModel.id)
                    .where(
                        VariableNamespaceModel.namespace == _HEAD_NAMESPACE,
                        VariableNamespaceModel.project_id == project_id,
                    )
                    .limit(1)
                )
                if project != project_id:
                    raise SharedGenerationStoreError(
                        "shared generation project scope is unavailable"
                    )
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation database is unavailable"
            ) from None

    def current(
        self,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> GenerationPointer | None:
        """Load and authenticate the newest committed exact-scope pointer."""
        self._validate_scope(project_id, decision_kind)
        try:
            with self._sessions() as session:
                head = self._read_head(session, project_id, decision_kind)
                pointer = head[0]
                if pointer is not None:
                    self._verify_shared_pointer(pointer)
                    self._require_epoch(session, pointer)
                return pointer
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def compare_and_swap(
        self,
        pointer: GenerationPointer,
        *,
        expected_candidate_digest: str | None,
        expected_generation: GenerationPointer | None = None,
    ) -> GenerationPointer | None:
        """Publish only from the exact current fenced generation identity."""
        self._validate_pointer(pointer)
        if expected_candidate_digest is not None:
            self._validate_digest(expected_candidate_digest)
            if expected_generation is None:
                raise SharedGenerationStoreError(
                    "expected generation identity is required"
                )
        if expected_generation is not None:
            self._validate_pointer(expected_generation)
            if (
                expected_candidate_digest is None
                or expected_generation.candidate_digest
                != expected_candidate_digest
                or expected_generation.project_id != pointer.project_id
                or expected_generation.decision_kind is not pointer.decision_kind
            ):
                raise SharedGenerationStoreError(
                    "expected generation identity is invalid"
                )
        self._verify_pointer(pointer)
        try:
            with self._mutation(pointer.project_id, pointer.decision_kind) as session:
                namespace = self._head_namespace(
                    session,
                    pointer.project_id,
                    create=True,
                    lock=True,
                )
                assert namespace is not None
                current, _reason = self._read_head(
                    session,
                    pointer.project_id,
                    pointer.decision_kind,
                    namespace=namespace,
                    lock=True,
                )
                current_digest = (
                    None if current is None else current.candidate_digest
                )
                if current is not None:
                    self._verify_shared_pointer(current)
                if current_digest != expected_candidate_digest:
                    return None
                if (
                    expected_generation is not None
                    and current != expected_generation
                ):
                    return None
                epoch = self._next_epoch(
                    session,
                    namespace,
                    pointer.project_id,
                    pointer.decision_kind,
                )
                if (
                    current is not None
                    and current.candidate_digest != pointer.candidate_digest
                ):
                    self._write_history(session, namespace, current)
                installed = replace(pointer, epoch=epoch)
                self._ensure_candidate_scope(session, installed)
                self._write_head(
                    session,
                    namespace,
                    installed,
                    decision_kind=pointer.decision_kind,
                    inactive_reason=None,
                )
                return installed
        except SharedGenerationStoreError:
            raise
        except Exception:
            raise SharedGenerationStoreError(
                "shared generation state write failed"
            ) from None

    def mark_drift_hold(self, candidate_digest: str, reason: str) -> None:
        """Persist the first closed drift reason across every host."""
        self._validate_digest(candidate_digest)
        if _SAFE_IDENTIFIER.fullmatch(reason) is None:
            raise SharedGenerationStoreError("generation drift reason is invalid")
        project_id, decision_kind = self._candidate_scope(candidate_digest)
        with self._mutation(project_id, decision_kind) as session:
            namespace = self._candidate_namespace(
                session,
                candidate_digest,
                project_id=project_id,
                create=True,
                lock=True,
            )
            assert namespace is not None
            existing = self._value_row(session, namespace.id, "hold", lock=True)
            if existing is None:
                self._set_record(
                    session,
                    namespace,
                    "hold",
                    project_id,
                    decision_kind,
                    {"candidate_digest": candidate_digest, "reason": reason},
                )
            else:
                payload = self._decode_record(
                    existing.value,
                    key="hold",
                    project_id=project_id,
                    decision_kind=decision_kind,
                )
                if payload.get("candidate_digest") != candidate_digest:
                    raise SharedGenerationStoreError(
                        "shared generation hold is invalid"
                    )

    def is_drift_held(self, candidate_digest: str) -> bool:
        """Return whether the authenticated candidate scope is held."""
        return self._candidate_flag(candidate_digest, "hold")

    def revoke(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        *,
        remove_pointer: bool,
        expected_generation: GenerationPointer | None = None,
    ) -> bool:
        """Revoke only the exact committed candidate and optionally tombstone it."""
        self._validate_scope(project_id, decision_kind)
        self._validate_digest(expected_candidate_digest)
        if expected_generation is None:
            raise SharedGenerationStoreError(
                "expected generation identity is required"
            )
        self._validate_pointer(expected_generation)
        try:
            with self._mutation(project_id, decision_kind) as session:
                namespace = self._head_namespace(
                    session,
                    project_id,
                    create=False,
                    lock=True,
                )
                if namespace is None:
                    return False
                current, _reason = self._read_head(
                    session,
                    project_id,
                    decision_kind,
                    namespace=namespace,
                    lock=True,
                )
                if (
                    current is None
                    or current.candidate_digest != expected_candidate_digest
                    or current != expected_generation
                ):
                    return False
                candidate_namespace = self._candidate_namespace(
                    session,
                    expected_candidate_digest,
                    project_id=project_id,
                    create=True,
                    lock=True,
                )
                assert candidate_namespace is not None
                self._set_record(
                    session,
                    candidate_namespace,
                    "revoked",
                    project_id,
                    decision_kind,
                    {"candidate_digest": expected_candidate_digest},
                )
                if remove_pointer:
                    self._next_epoch(
                        session,
                        namespace,
                        project_id,
                        decision_kind,
                    )
                    self._write_head(
                        session,
                        namespace,
                        None,
                        decision_kind=decision_kind,
                        inactive_reason="revoked",
                    )
                return True
        except SharedGenerationStoreError:
            raise
        except Exception:
            raise SharedGenerationStoreError(
                "shared generation state write failed"
            ) from None

    def inactive_reason(
        self,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> str | None:
        """Read the authenticated closed reason for an absent pointer."""
        self._validate_scope(project_id, decision_kind)
        try:
            with self._sessions() as session:
                pointer, reason = self._read_head(
                    session,
                    project_id,
                    decision_kind,
                )
                if pointer is not None and reason is not None:
                    raise SharedGenerationStoreError(
                        "shared generation head is invalid"
                    )
                return reason
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def is_revoked(self, candidate_digest: str) -> bool:
        """Return whether a candidate carries an authenticated revocation."""
        return self._candidate_flag(candidate_digest, "revoked")

    def force_revoke(self, candidate_digest: str) -> None:
        """Persist an emergency revocation from the durable candidate scope."""
        self._validate_digest(candidate_digest)
        project_id, decision_kind = self._candidate_scope(
            candidate_digest,
            require_artifact=False,
        )
        with self._mutation(project_id, decision_kind) as session:
            namespace = self._candidate_namespace(
                session,
                candidate_digest,
                project_id=project_id,
                create=True,
                lock=True,
            )
            assert namespace is not None
            self._set_record(
                session,
                namespace,
                "revoked",
                project_id,
                decision_kind,
                {"candidate_digest": candidate_digest},
            )

    def reserve_use(
        self,
        candidate_digest: str,
        application_id: str,
        maximum_use_count: int,
    ) -> bool:
        """Reserve one globally idempotent application below the approval cap."""
        self._validate_digest(candidate_digest)
        self._validate_digest(application_id)
        if (
            type(maximum_use_count) is not int
            or not 1 <= maximum_use_count <= _MAX_APPLICATIONS
        ):
            raise SharedGenerationStoreError("generation use count is invalid")
        project_id, decision_kind = self._candidate_scope(candidate_digest)
        app_key = self._application_key(application_id)
        with self._mutation(project_id, decision_kind) as session:
            namespace = self._candidate_namespace(
                session,
                candidate_digest,
                project_id=project_id,
                create=False,
                lock=True,
            )
            if namespace is None:
                raise SharedGenerationStoreError(
                    "shared candidate scope is unavailable"
                )
            existing = self._value_row(session, namespace.id, app_key, lock=True)
            if existing is not None:
                self._require_application(
                    existing.value,
                    key=app_key,
                    project_id=project_id,
                    decision_kind=decision_kind,
                    candidate_digest=candidate_digest,
                    application_id=application_id,
                )
                return True
            count = self._read_count(
                session,
                namespace,
                project_id,
                decision_kind,
                lock=True,
            )
            if count >= maximum_use_count or count >= _MAX_APPLICATIONS:
                return False
            self._set_record(
                session,
                namespace,
                app_key,
                project_id,
                decision_kind,
                {
                    "candidate_digest": candidate_digest,
                    "application_id": application_id,
                },
            )
            self._write_count(
                session,
                namespace,
                project_id,
                decision_kind,
                count + 1,
            )
            return True

    def has_application(self, candidate_digest: str, application_id: str) -> bool:
        """Return whether the exact authenticated application was issued."""
        self._validate_digest(candidate_digest)
        self._validate_digest(application_id)
        project_id, decision_kind = self._candidate_scope(candidate_digest)
        try:
            with self._sessions() as session:
                namespace = self._candidate_namespace(
                    session,
                    candidate_digest,
                    project_id=project_id,
                    create=False,
                    lock=False,
                )
                if namespace is None:
                    return False
                key = self._application_key(application_id)
                row = self._value_row(session, namespace.id, key, lock=False)
                if row is None:
                    return False
                self._require_application(
                    row.value,
                    key=key,
                    project_id=project_id,
                    decision_kind=decision_kind,
                    candidate_digest=candidate_digest,
                    application_id=application_id,
                )
                return True
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def use_count(self, candidate_digest: str) -> int:
        """Return the authenticated cross-host distinct application count."""
        self._validate_digest(candidate_digest)
        project_id, decision_kind = self._candidate_scope(candidate_digest)
        try:
            with self._sessions() as session:
                namespace = self._candidate_namespace(
                    session,
                    candidate_digest,
                    project_id=project_id,
                    create=False,
                    lock=False,
                )
                if namespace is None:
                    return 0
                return self._read_count(
                    session,
                    namespace,
                    project_id,
                    decision_kind,
                    lock=False,
                )
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def record_outcome(self, outcome: DecisionApplicationOutcomeV1) -> bool:
        """Idempotently persist terminal feedback for an issued application."""
        try:
            validated = DecisionApplicationOutcomeV1.model_validate(
                outcome.model_dump(mode="python", by_alias=True),
                strict=True,
            )
        except (AttributeError, ValidationError):
            raise SharedGenerationStoreError(
                "generation application outcome is invalid"
            ) from None
        project_id, decision_kind = self._candidate_scope(
            validated.candidate_digest
        )
        if (
            validated.project_id != project_id
            or validated.decision_kind is not decision_kind
        ):
            raise SharedGenerationStoreError(
                "generation application outcome scope is invalid"
            )
        key = self._outcome_key(validated.application_id)
        with self._mutation(project_id, decision_kind) as session:
            namespace = self._candidate_namespace(
                session,
                validated.candidate_digest,
                project_id=project_id,
                create=False,
                lock=True,
            )
            if namespace is None:
                raise SharedGenerationStoreError(
                    "shared candidate scope is unavailable"
                )
            app_key = self._application_key(validated.application_id)
            application = self._value_row(
                session,
                namespace.id,
                app_key,
                lock=True,
            )
            if application is None:
                raise RolloutError(
                    "outcome does not reference an issued application"
                )
            self._require_application(
                application.value,
                key=app_key,
                project_id=project_id,
                decision_kind=decision_kind,
                candidate_digest=validated.candidate_digest,
                application_id=validated.application_id,
            )
            existing = self._value_row(session, namespace.id, key, lock=True)
            if existing is not None:
                stored = self._decode_outcome(
                    existing.value,
                    key=key,
                    project_id=project_id,
                    decision_kind=decision_kind,
                )
                if stored != validated:
                    raise RolloutError(
                        "application has conflicting outcome feedback"
                    )
                return False
            self._set_record(
                session,
                namespace,
                key,
                project_id,
                decision_kind,
                validated.model_dump(mode="json", by_alias=True),
            )
            self._append_outcome_index(
                session,
                namespace,
                project_id,
                decision_kind,
                validated,
            )
            return True

    def recent_outcomes(
        self,
        candidate_digest: str,
        *,
        now: datetime,
    ) -> tuple[VerifiedOutcome, ...]:
        """Return the authenticated bounded seven-day feedback window."""
        self._validate_digest(candidate_digest)
        current_time = self._utc(now)
        project_id, decision_kind = self._candidate_scope(candidate_digest)
        try:
            with self._sessions() as session:
                namespace = self._candidate_namespace(
                    session,
                    candidate_digest,
                    project_id=project_id,
                    create=False,
                    lock=False,
                )
                if namespace is None:
                    return ()
                index = self._read_outcome_index(
                    session,
                    namespace,
                    project_id,
                    decision_kind,
                    lock=False,
                )
                cutoff = current_time - timedelta(days=7)
                outcomes: list[DecisionApplicationOutcomeV1] = []
                for application_id, occurred_at in index:
                    if not cutoff <= occurred_at <= current_time:
                        continue
                    key = self._outcome_key(application_id)
                    row = self._value_row(
                        session,
                        namespace.id,
                        key,
                        lock=False,
                    )
                    if row is None:
                        raise SharedGenerationStoreError(
                            "shared outcome index is orphaned"
                        )
                    outcome = self._decode_outcome(
                        row.value,
                        key=key,
                        project_id=project_id,
                        decision_kind=decision_kind,
                    )
                    if (
                        outcome.candidate_digest != candidate_digest
                        or outcome.application_id != application_id
                        or outcome.occurred_at != occurred_at
                    ):
                        raise SharedGenerationStoreError(
                            "shared outcome index is invalid"
                        )
                    outcomes.append(outcome)
                outcomes.sort(
                    key=lambda item: (item.occurred_at, item.application_id)
                )
                return tuple(item.outcome for item in outcomes)
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def rollback(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        eligible: Callable[[GenerationPointer], bool],
        *,
        expected_generation: GenerationPointer | None = None,
    ) -> GenerationPointer | None:
        """Restore the newest authenticated eligible history entry atomically."""
        self._validate_scope(project_id, decision_kind)
        self._validate_digest(expected_candidate_digest)
        if expected_generation is None:
            raise SharedGenerationStoreError(
                "expected generation identity is required"
            )
        self._validate_pointer(expected_generation)
        try:
            with self._mutation(project_id, decision_kind) as session:
                namespace = self._head_namespace(
                    session,
                    project_id,
                    create=False,
                    lock=True,
                )
                if namespace is None:
                    raise RolloutError(
                        "rollback compare-and-swap expectation failed"
                    )
                current, _reason = self._read_head(
                    session,
                    project_id,
                    decision_kind,
                    namespace=namespace,
                    lock=True,
                )
                if (
                    current is None
                    or current.candidate_digest != expected_candidate_digest
                    or current != expected_generation
                ):
                    raise RolloutError(
                        "rollback compare-and-swap expectation failed"
                    )
                history = self._read_history(
                    session,
                    namespace,
                    project_id,
                    decision_kind,
                    lock=True,
                )
                selected: GenerationPointer | None = None
                for candidate in history:
                    if (
                        not self._candidate_flag_in_session(
                            session,
                            candidate,
                            "revoked",
                        )
                        and not self._candidate_flag_in_session(
                            session,
                            candidate,
                            "hold",
                        )
                        and eligible(candidate)
                    ):
                        selected = candidate
                        break
                epoch = self._next_epoch(
                    session,
                    namespace,
                    project_id,
                    decision_kind,
                )
                if selected is None:
                    self._write_head(
                        session,
                        namespace,
                        None,
                        decision_kind=decision_kind,
                        inactive_reason=None,
                    )
                    return None
                restored = replace(selected, epoch=epoch)
                self._delete_consumed_history(
                    session,
                    namespace,
                    decision_kind,
                    selected.epoch,
                )
                self._write_head(
                    session,
                    namespace,
                    restored,
                    decision_kind=decision_kind,
                    inactive_reason=None,
                )
                return restored
        except (RolloutError, SharedGenerationStoreError):
            raise
        except Exception:
            raise SharedGenerationStoreError(
                "shared generation state write failed"
            ) from None

    @contextlib.contextmanager
    def _mutation(
        self,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> Iterator[Session]:
        self._validate_scope(project_id, decision_kind)
        session = self._sessions()
        try:
            with session.begin():
                self._set_transaction_timeout(session)
                holder_id = f"decision-generation-{token_hex(16)}"
                self._acquire_lease(session, project_id, holder_id)
                yield session
                self._release_lease(session, project_id, holder_id)
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state write failed"
            ) from None
        finally:
            session.close()

    def _set_transaction_timeout(self, session: Session) -> None:
        if self._engine.dialect.name != "postgresql":
            return
        timeout = f"{int(self._lock_timeout * 1_000)}ms"
        session.execute(
            text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": timeout},
        )
        session.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": timeout},
        )

    def _acquire_lease(
        self,
        session: Session,
        project_id: str,
        holder_id: str,
    ) -> None:
        key = self.coordination_key(project_id)
        now = self._now()
        existing = session.scalar(
            select(BucketLeaseModel)
            .where(BucketLeaseModel.bucket_key == key)
            .with_for_update()
        )
        if existing is not None:
            if existing.expires_at > now:
                raise SharedGenerationStoreError(
                    "shared generation coordination is unavailable"
                )
            session.delete(existing)
            session.flush()
        session.add(
            BucketLeaseModel(
                bucket_key=key,
                project_id=project_id,
                holder_id=holder_id,
                expires_at=now + timedelta(seconds=self._lease_ttl),
                heartbeat_at=now,
                updated_at=now,
            )
        )
        session.flush()

    def _release_lease(
        self,
        session: Session,
        project_id: str,
        holder_id: str,
    ) -> None:
        result = session.execute(
            delete(BucketLeaseModel).where(
                BucketLeaseModel.bucket_key == self.coordination_key(project_id),
                BucketLeaseModel.holder_id == holder_id,
            )
        )
        if (cast(CursorResult[Any], result).rowcount or 0) != 1:
            raise SharedGenerationStoreError(
                "shared generation coordination is unavailable"
            )

    def _head_namespace(
        self,
        session: Session,
        project_id: str,
        *,
        create: bool,
        lock: bool,
    ) -> VariableNamespaceModel | None:
        return self._namespace(
            session,
            name=_HEAD_NAMESPACE,
            project_id=project_id,
            description="Authenticated shared decision generation state",
            create=create,
            lock=lock,
        )

    def _candidate_namespace(
        self,
        session: Session,
        candidate_digest: str,
        *,
        project_id: str,
        create: bool,
        lock: bool,
    ) -> VariableNamespaceModel | None:
        return self._namespace(
            session,
            name=self._candidate_namespace_name(candidate_digest),
            project_id=project_id,
            description="Authenticated shared decision candidate state",
            create=create,
            lock=lock,
        )

    @staticmethod
    def _namespace(
        session: Session,
        *,
        name: str,
        project_id: str,
        description: str,
        create: bool,
        lock: bool,
    ) -> VariableNamespaceModel | None:
        statement = select(VariableNamespaceModel).where(
            VariableNamespaceModel.namespace == name,
            VariableNamespaceModel.project_id == project_id,
        )
        if lock:
            statement = statement.with_for_update()
        namespace = session.scalar(statement)
        if namespace is None and create:
            namespace = VariableNamespaceModel(
                namespace=name,
                project_id=project_id,
                description=description,
            )
            session.add(namespace)
            session.flush()
        return namespace

    def _read_head(
        self,
        session: Session,
        project_id: str,
        decision_kind: DecisionKind,
        *,
        namespace: VariableNamespaceModel | None = None,
        lock: bool = False,
    ) -> tuple[GenerationPointer | None, str | None]:
        selected_namespace = namespace or self._head_namespace(
            session,
            project_id,
            create=False,
            lock=lock,
        )
        if selected_namespace is None:
            return None, None
        key = self._head_key(decision_kind)
        row = self._value_row(session, selected_namespace.id, key, lock=lock)
        if row is None:
            return None, None
        payload = self._decode_record(
            row.value,
            key=key,
            project_id=project_id,
            decision_kind=decision_kind,
        )
        reason = payload.get("inactive_reason")
        if reason is not None and (
            not isinstance(reason, str)
            or _SAFE_IDENTIFIER.fullmatch(reason) is None
        ):
            raise SharedGenerationStoreError("shared generation head is invalid")
        pointer_payload = payload.get("pointer")
        if pointer_payload is None:
            if set(payload) != {"pointer", "inactive_reason"}:
                raise SharedGenerationStoreError(
                    "shared generation head is invalid"
                )
            return None, reason
        if reason is not None or not isinstance(pointer_payload, dict):
            raise SharedGenerationStoreError("shared generation head is invalid")
        pointer = self._pointer_from_payload(pointer_payload)
        if (
            pointer.project_id != project_id
            or pointer.decision_kind is not decision_kind
        ):
            raise SharedGenerationStoreError(
                "shared generation pointer scope is invalid"
            )
        return pointer, None

    def _write_head(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        pointer: GenerationPointer | None,
        *,
        decision_kind: DecisionKind,
        inactive_reason: str | None,
    ) -> None:
        if pointer is not None and pointer.decision_kind is not decision_kind:
            raise SharedGenerationStoreError(
                "shared generation pointer scope is invalid"
            )
        project_id = namespace.project_id
        if project_id is None:
            raise SharedGenerationStoreError(
                "shared generation namespace scope is invalid"
            )
        self._set_record(
            session,
            namespace,
            self._head_key(decision_kind),
            project_id,
            decision_kind,
            {
                "pointer": (
                    None if pointer is None else self._pointer_payload(pointer)
                ),
                "inactive_reason": inactive_reason,
            },
        )

    def _next_epoch(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> int:
        key = self._epoch_key(decision_kind)
        row = self._value_row(session, namespace.id, key, lock=True)
        current = 0
        if row is not None:
            payload = self._decode_record(
                row.value,
                key=key,
                project_id=project_id,
                decision_kind=decision_kind,
            )
            raw_current = payload.get("epoch", -1)
            if type(raw_current) is not int or raw_current < 0:
                raise SharedGenerationStoreError(
                    "shared generation epoch is invalid"
                )
            current = raw_current
        updated = current + 1
        self._set_record(
            session,
            namespace,
            key,
            project_id,
            decision_kind,
            {"epoch": updated},
        )
        return updated

    def _require_epoch(
        self,
        session: Session,
        pointer: GenerationPointer,
    ) -> None:
        namespace = self._head_namespace(
            session,
            pointer.project_id,
            create=False,
            lock=False,
        )
        if namespace is None:
            raise SharedGenerationStoreError("shared generation epoch is missing")
        key = self._epoch_key(pointer.decision_kind)
        row = self._value_row(session, namespace.id, key, lock=False)
        if row is None:
            raise SharedGenerationStoreError("shared generation epoch is missing")
        payload = self._decode_record(
            row.value,
            key=key,
            project_id=pointer.project_id,
            decision_kind=pointer.decision_kind,
        )
        if payload.get("epoch") != pointer.epoch:
            raise SharedGenerationStoreError(
                "shared generation identity is stale"
            )

    def _write_history(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        pointer: GenerationPointer,
    ) -> None:
        history = self._read_history(
            session,
            namespace,
            pointer.project_id,
            pointer.decision_kind,
            lock=True,
        )
        if len(history) >= _MAX_HISTORY:
            oldest = history[-1]
            session.execute(
                delete(VariableValueModel).where(
                    VariableValueModel.namespace_id == namespace.id,
                    VariableValueModel.key
                    == self._history_key(pointer.decision_kind, oldest.epoch),
                )
            )
        self._set_record(
            session,
            namespace,
            self._history_key(pointer.decision_kind, pointer.epoch),
            pointer.project_id,
            pointer.decision_kind,
            self._pointer_payload(pointer),
        )

    def _read_history(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
        *,
        lock: bool,
    ) -> tuple[GenerationPointer, ...]:
        prefix = f"history:{decision_kind.value}:"
        statement = (
            select(VariableValueModel)
            .where(
                VariableValueModel.namespace_id == namespace.id,
                VariableValueModel.key.like(f"{prefix}%"),
            )
            .order_by(VariableValueModel.key.desc())
            .limit(_MAX_HISTORY + 1)
        )
        if lock:
            statement = statement.with_for_update()
        rows = tuple(session.scalars(statement).all())
        if len(rows) > _MAX_HISTORY:
            raise SharedGenerationStoreError(
                "shared generation history bound exceeded"
            )
        pointers: list[GenerationPointer] = []
        for row in rows:
            payload = self._decode_record(
                row.value,
                key=row.key,
                project_id=project_id,
                decision_kind=decision_kind,
            )
            pointer = self._pointer_from_payload(payload)
            if row.key != self._history_key(decision_kind, pointer.epoch):
                raise SharedGenerationStoreError(
                    "shared generation history is invalid"
                )
            self._verify_pointer(pointer)
            pointers.append(pointer)
        return tuple(pointers)

    @staticmethod
    def _delete_consumed_history(
        session: Session,
        namespace: VariableNamespaceModel,
        decision_kind: DecisionKind,
        selected_epoch: int,
    ) -> None:
        prefix = f"history:{decision_kind.value}:"
        session.execute(
            delete(VariableValueModel).where(
                VariableValueModel.namespace_id == namespace.id,
                VariableValueModel.key.like(f"{prefix}%"),
                VariableValueModel.key >= f"{prefix}{selected_epoch:020d}",
            )
        )

    def _ensure_candidate_scope(
        self,
        session: Session,
        pointer: GenerationPointer,
    ) -> None:
        namespace = self._candidate_namespace(
            session,
            pointer.candidate_digest,
            project_id=pointer.project_id,
            create=True,
            lock=True,
        )
        assert namespace is not None
        key = "scope"
        row = self._value_row(session, namespace.id, key, lock=True)
        expected = {
            "candidate_digest": pointer.candidate_digest,
            "project_id": pointer.project_id,
            "decision_kind": pointer.decision_kind.value,
        }
        if row is None:
            self._set_record(
                session,
                namespace,
                key,
                pointer.project_id,
                pointer.decision_kind,
                expected,
            )
            return
        payload = self._decode_record(
            row.value,
            key=key,
            project_id=pointer.project_id,
            decision_kind=pointer.decision_kind,
        )
        if payload != expected:
            raise SharedGenerationStoreError(
                "shared candidate scope is invalid"
            )

    def _candidate_scope(
        self,
        candidate_digest: str,
        *,
        require_artifact: bool = True,
    ) -> tuple[str, DecisionKind]:
        self._validate_digest(candidate_digest)
        try:
            with self._sessions() as session:
                namespaces = tuple(
                    session.scalars(
                        select(VariableNamespaceModel).where(
                            VariableNamespaceModel.namespace
                            == self._candidate_namespace_name(candidate_digest)
                        )
                    ).all()
                )
                if len(namespaces) > 1:
                    raise SharedGenerationStoreError(
                        "shared candidate scope is ambiguous"
                    )
                if namespaces:
                    namespace = namespaces[0]
                    if namespace.project_id is None:
                        raise SharedGenerationStoreError(
                            "shared candidate scope is invalid"
                        )
                    row = self._value_row(
                        session,
                        namespace.id,
                        "scope",
                        lock=False,
                    )
                    if row is None:
                        raise SharedGenerationStoreError(
                            "shared candidate scope is orphaned"
                        )
                    unsigned = self._decode_record_unscoped(
                        row.value,
                        expected_key="scope",
                    )
                    payload = unsigned["payload"]
                    project_id = unsigned["project_id"]
                    raw_kind = unsigned["decision_kind"]
                    if (
                        not isinstance(payload, dict)
                        or not isinstance(project_id, str)
                        or project_id != namespace.project_id
                        or not isinstance(raw_kind, str)
                    ):
                        raise SharedGenerationStoreError(
                            "shared candidate scope is invalid"
                        )
                    try:
                        decision_kind = DecisionKind(raw_kind)
                    except ValueError:
                        raise SharedGenerationStoreError(
                            "shared candidate scope is invalid"
                        ) from None
                    expected = {
                        "candidate_digest": candidate_digest,
                        "project_id": project_id,
                        "decision_kind": decision_kind.value,
                    }
                    if payload != expected:
                        raise SharedGenerationStoreError(
                            "shared candidate scope is invalid"
                        )
                    if require_artifact:
                        bundle = self._verified_bundle(candidate_digest)
                        if (
                            bundle.project_id != project_id
                            or bundle.decision_kind is not decision_kind
                        ):
                            raise SharedGenerationStoreError(
                                "shared candidate artifact scope is invalid"
                            )
                    return project_id, decision_kind
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None
        if not require_artifact:
            raise SharedGenerationStoreError(
                "shared candidate scope is unavailable"
            )
        bundle = self._verified_bundle(candidate_digest)
        return bundle.project_id, bundle.decision_kind

    def _candidate_flag(self, candidate_digest: str, key: str) -> bool:
        self._validate_digest(candidate_digest)
        project_id, decision_kind = self._candidate_scope(candidate_digest)
        try:
            with self._sessions() as session:
                namespace = self._candidate_namespace(
                    session,
                    candidate_digest,
                    project_id=project_id,
                    create=False,
                    lock=False,
                )
                if namespace is None:
                    return False
                row = self._value_row(session, namespace.id, key, lock=False)
                if row is None:
                    return False
                payload = self._decode_record(
                    row.value,
                    key=key,
                    project_id=project_id,
                    decision_kind=decision_kind,
                )
                if payload.get("candidate_digest") != candidate_digest:
                    raise SharedGenerationStoreError(
                        "shared candidate flag is invalid"
                    )
                if key == "hold" and (
                    not isinstance(payload.get("reason"), str)
                    or _SAFE_IDENTIFIER.fullmatch(cast(str, payload["reason"]))
                    is None
                ):
                    raise SharedGenerationStoreError(
                        "shared candidate flag is invalid"
                    )
                return True
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def _candidate_flag_in_session(
        self,
        session: Session,
        pointer: GenerationPointer,
        key: str,
    ) -> bool:
        namespace = self._candidate_namespace(
            session,
            pointer.candidate_digest,
            project_id=pointer.project_id,
            create=False,
            lock=True,
        )
        if namespace is None:
            raise SharedGenerationStoreError(
                "shared candidate scope is unavailable"
            )
        row = self._value_row(session, namespace.id, key, lock=True)
        if row is None:
            return False
        payload = self._decode_record(
            row.value,
            key=key,
            project_id=pointer.project_id,
            decision_kind=pointer.decision_kind,
        )
        if payload.get("candidate_digest") != pointer.candidate_digest:
            raise SharedGenerationStoreError("shared candidate flag is invalid")
        return True

    def _read_count(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
        *,
        lock: bool,
    ) -> int:
        row = self._value_row(session, namespace.id, "use-count", lock=lock)
        if row is None:
            return 0
        payload = self._decode_record(
            row.value,
            key="use-count",
            project_id=project_id,
            decision_kind=decision_kind,
        )
        count = payload.get("count")
        if type(count) is not int or not 0 <= count <= _MAX_APPLICATIONS:
            raise SharedGenerationStoreError(
                "shared generation use count is invalid"
            )
        return count

    def _write_count(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
        count: int,
    ) -> None:
        self._set_record(
            session,
            namespace,
            "use-count",
            project_id,
            decision_kind,
            {"count": count},
        )

    def _require_application(
        self,
        value: str,
        *,
        key: str,
        project_id: str,
        decision_kind: DecisionKind,
        candidate_digest: str,
        application_id: str,
    ) -> None:
        payload = self._decode_record(
            value,
            key=key,
            project_id=project_id,
            decision_kind=decision_kind,
        )
        if payload != {
            "candidate_digest": candidate_digest,
            "application_id": application_id,
        }:
            raise SharedGenerationStoreError(
                "shared generation application is invalid"
            )

    def _append_outcome_index(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
        outcome: DecisionApplicationOutcomeV1,
    ) -> None:
        existing = list(
            self._read_outcome_index(
                session,
                namespace,
                project_id,
                decision_kind,
                lock=True,
            )
        )
        existing.append((outcome.application_id, outcome.occurred_at))
        deduplicated = {
            application_id: occurred_at
            for application_id, occurred_at in existing
        }
        bounded = sorted(
            deduplicated.items(),
            key=lambda item: (item[1], item[0]),
        )[-_MAX_RECENT_OUTCOMES:]
        self._set_record(
            session,
            namespace,
            "outcome-index",
            project_id,
            decision_kind,
            {
                "items": [
                    {
                        "application_id": application_id,
                        "occurred_at": occurred_at.isoformat(),
                    }
                    for application_id, occurred_at in bounded
                ]
            },
        )

    def _read_outcome_index(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
        *,
        lock: bool,
    ) -> tuple[tuple[str, datetime], ...]:
        row = self._value_row(
            session,
            namespace.id,
            "outcome-index",
            lock=lock,
        )
        if row is None:
            return ()
        payload = self._decode_record(
            row.value,
            key="outcome-index",
            project_id=project_id,
            decision_kind=decision_kind,
        )
        items = payload.get("items")
        if not isinstance(items, list) or len(items) > _MAX_RECENT_OUTCOMES:
            raise SharedGenerationStoreError(
                "shared outcome index is invalid"
            )
        parsed: list[tuple[str, datetime]] = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {
                "application_id",
                "occurred_at",
            }:
                raise SharedGenerationStoreError(
                    "shared outcome index is invalid"
                )
            application_id = item.get("application_id")
            occurred = item.get("occurred_at")
            self._validate_digest(application_id)
            if not isinstance(occurred, str):
                raise SharedGenerationStoreError(
                    "shared outcome index is invalid"
                )
            try:
                occurred_at = self._utc(datetime.fromisoformat(occurred))
            except ValueError:
                raise SharedGenerationStoreError(
                    "shared outcome index is invalid"
                ) from None
            parsed.append((cast(str, application_id), occurred_at))
        if parsed != sorted(parsed, key=lambda item: (item[1], item[0])) or len(
            {item[0] for item in parsed}
        ) != len(parsed):
            raise SharedGenerationStoreError(
                "shared outcome index is invalid"
            )
        return tuple(parsed)

    def _decode_outcome(
        self,
        value: str,
        *,
        key: str,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> DecisionApplicationOutcomeV1:
        payload = self._decode_record(
            value,
            key=key,
            project_id=project_id,
            decision_kind=decision_kind,
        )
        try:
            return DecisionApplicationOutcomeV1.model_validate_json(
                canonical_decision_json(payload),
                strict=True,
            )
        except ValidationError:
            raise SharedGenerationStoreError(
                "shared generation application outcome is invalid"
            ) from None

    def _verify_pointer(self, pointer: GenerationPointer) -> None:
        self._validate_pointer(pointer)
        try:
            bundle = self._artifacts.read_rule_bundle(pointer.candidate_digest)
            chain = self._artifacts.verify_receipt_chain(pointer.receipt_digest)
            receipt = chain[-1]
        except (ArtifactStoreError, IndexError):
            raise SharedGenerationStoreError(
                "shared generation artifact is unavailable or invalid"
            ) from None
        self._validate_artifact_bindings(bundle, receipt)
        if (
            bundle.project_id != pointer.project_id
            or bundle.decision_kind is not pointer.decision_kind
            or receipt.receipt_digest != pointer.receipt_digest
            or receipt.lifecycle_state.value != pointer.stage.value
        ):
            raise SharedGenerationStoreError(
                "shared generation artifact scope is invalid"
            )

    def _verify_shared_pointer(self, pointer: GenerationPointer) -> None:
        self._verify_pointer(pointer)
        project_id, decision_kind = self._candidate_scope(
            pointer.candidate_digest
        )
        if (
            project_id != pointer.project_id
            or decision_kind is not pointer.decision_kind
        ):
            raise SharedGenerationStoreError(
                "shared generation pointer scope is invalid"
            )

    def _verified_bundle(self, candidate_digest: str) -> DecisionRuleBundleV1:
        try:
            return self._artifacts.read_rule_bundle(candidate_digest)
        except ArtifactStoreError:
            raise SharedGenerationStoreError(
                "shared generation artifact is unavailable or invalid"
            ) from None

    @staticmethod
    def _validate_artifact_bindings(
        bundle: DecisionRuleBundleV1,
        receipt: ApprovalReceiptV1,
    ) -> None:
        if not artifacts_have_exact_bindings(bundle, receipt):
            raise SharedGenerationStoreError(
                "shared generation artifact binding is invalid"
            )

    def _set_record(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        key: str,
        project_id: str,
        decision_kind: DecisionKind,
        payload: Mapping[str, object],
    ) -> None:
        value = self._encode_record(
            key=key,
            project_id=project_id,
            decision_kind=decision_kind,
            payload=payload,
        )
        row = self._value_row(session, namespace.id, key, lock=True)
        if row is None:
            session.add(
                VariableValueModel(
                    namespace_id=namespace.id,
                    key=key,
                    value=value,
                    value_type="decision_generation_v1",
                )
            )
        else:
            row.value = value
            row.value_type = "decision_generation_v1"
        session.flush()

    def _encode_record(
        self,
        *,
        key: str,
        project_id: str,
        decision_kind: DecisionKind,
        payload: Mapping[str, object],
    ) -> str:
        unsigned: dict[str, object] = {
            "schema": _STATE_SCHEMA,
            "key": key,
            "project_id": project_id,
            "decision_kind": decision_kind.value,
            "payload": dict(payload),
        }
        authentication_tag = self._artifacts.decision_generation_state_hmac(
            unsigned
        )
        encoded = canonical_decision_json(
            {**unsigned, "authentication_tag": authentication_tag}
        )
        if len(encoded.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise SharedGenerationStoreError(
                "shared generation record exceeds the bounded size"
            )
        return encoded

    def _decode_record(
        self,
        value: str,
        *,
        key: str,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> dict[str, object]:
        unsigned = self._decode_record_unscoped(value, expected_key=key)
        stored_project = unsigned["project_id"]
        stored_kind = unsigned["decision_kind"]
        if (
            not isinstance(stored_project, str)
            or not hmac.compare_digest(stored_project, project_id)
            or stored_kind != decision_kind.value
        ):
            raise SharedGenerationStoreError(
                "shared generation record scope is invalid"
            )
        payload = unsigned["payload"]
        if not isinstance(payload, dict):
            raise SharedGenerationStoreError(
                "shared generation record payload is invalid"
            )
        return cast(dict[str, object], payload)

    def _decode_record_unscoped(
        self,
        value: str,
        *,
        expected_key: str,
    ) -> dict[str, object]:
        if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise SharedGenerationStoreError(
                "shared generation record is invalid"
            )
        try:
            loaded = json.loads(value)
        except (TypeError, ValueError):
            raise SharedGenerationStoreError(
                "shared generation record is invalid"
            ) from None
        if not isinstance(loaded, dict) or set(loaded) != {
            "schema",
            "key",
            "project_id",
            "decision_kind",
            "payload",
            "authentication_tag",
        }:
            raise SharedGenerationStoreError(
                "shared generation record is invalid"
            )
        if loaded.get("schema") != _STATE_SCHEMA or loaded.get("key") != expected_key:
            raise SharedGenerationStoreError(
                "shared generation record identity is invalid"
            )
        authentication_tag = loaded.pop("authentication_tag")
        try:
            self._artifacts.verify_decision_generation_state_hmac(
                loaded,
                cast(str, authentication_tag),
            )
        except ArtifactStoreError:
            raise SharedGenerationStoreError(
                "shared generation record authentication is invalid"
            ) from None
        return cast(dict[str, object], loaded)

    @staticmethod
    def _value_row(
        session: Session,
        namespace_id: int,
        key: str,
        *,
        lock: bool,
    ) -> VariableValueModel | None:
        statement = select(VariableValueModel).where(
            VariableValueModel.namespace_id == namespace_id,
            VariableValueModel.key == key,
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @classmethod
    def _pointer_from_payload(cls, payload: Mapping[str, object]) -> GenerationPointer:
        if set(payload) != {
            "project_id",
            "decision_kind",
            "candidate_digest",
            "receipt_digest",
            "stage",
            "epoch",
        }:
            raise SharedGenerationStoreError(
                "shared generation pointer is invalid"
            )
        try:
            pointer = GenerationPointer(
                project_id=cast(str, payload["project_id"]),
                decision_kind=DecisionKind(cast(str, payload["decision_kind"])),
                candidate_digest=cast(str, payload["candidate_digest"]),
                receipt_digest=cast(str, payload["receipt_digest"]),
                stage=RolloutStage(cast(str, payload["stage"])),
                epoch=cast(int, payload["epoch"]),
            )
            cls._validate_pointer(pointer)
            return pointer
        except (KeyError, TypeError, ValueError):
            raise SharedGenerationStoreError(
                "shared generation pointer is invalid"
            ) from None

    @staticmethod
    def _pointer_payload(pointer: GenerationPointer) -> dict[str, object]:
        return {
            "project_id": pointer.project_id,
            "decision_kind": pointer.decision_kind.value,
            "candidate_digest": pointer.candidate_digest,
            "receipt_digest": pointer.receipt_digest,
            "stage": pointer.stage.value,
            "epoch": pointer.epoch,
        }

    @classmethod
    def _validate_pointer(cls, pointer: GenerationPointer) -> None:
        if not isinstance(pointer, GenerationPointer):
            raise SharedGenerationStoreError(
                "shared generation pointer is invalid"
            )
        cls._validate_scope(pointer.project_id, pointer.decision_kind)
        cls._validate_digest(pointer.candidate_digest)
        cls._validate_digest(pointer.receipt_digest)
        if not isinstance(pointer.stage, RolloutStage):
            raise SharedGenerationStoreError(
                "shared generation pointer stage is invalid"
            )
        if type(pointer.epoch) is not int or pointer.epoch < 0:
            raise SharedGenerationStoreError(
                "shared generation pointer epoch is invalid"
            )

    @staticmethod
    def _validate_scope(project_id: str, decision_kind: DecisionKind) -> None:
        if not isinstance(project_id, str) or _SAFE_IDENTIFIER.fullmatch(project_id) is None:
            raise SharedGenerationStoreError(
                "shared generation project scope is invalid"
            )
        if not isinstance(decision_kind, DecisionKind):
            raise SharedGenerationStoreError(
                "shared generation decision scope is invalid"
            )

    @staticmethod
    def _validate_digest(value: object) -> None:
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise SharedGenerationStoreError(
                "shared generation digest is invalid"
            )

    def _now(self) -> datetime:
        return self._utc(self._clock())

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise SharedGenerationStoreError(
                "shared generation timestamp is invalid"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _head_key(decision_kind: DecisionKind) -> str:
        return f"head:{decision_kind.value}"

    @staticmethod
    def _epoch_key(decision_kind: DecisionKind) -> str:
        return f"epoch:{decision_kind.value}"

    @staticmethod
    def _history_key(decision_kind: DecisionKind, epoch: int) -> str:
        return f"history:{decision_kind.value}:{epoch:020d}"

    @staticmethod
    def _candidate_namespace_name(candidate_digest: str) -> str:
        return _CANDIDATE_NAMESPACE_PREFIX + candidate_digest.removeprefix(
            "sha256:"
        )

    @staticmethod
    def _application_key(application_id: str) -> str:
        return "application:" + application_id.removeprefix("sha256:")

    @staticmethod
    def _outcome_key(application_id: str) -> str:
        return "outcome:" + application_id.removeprefix("sha256:")


__all__ = ["PostgresGenerationStore", "SharedGenerationStoreError"]
