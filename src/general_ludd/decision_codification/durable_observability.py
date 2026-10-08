"""Fixed-cardinality durable aggregation for decision-reuse observations."""

from __future__ import annotations

import hmac
import sqlite3
from collections import defaultdict
from datetime import datetime

from pydantic import ValidationError

from general_ludd.decision_codification.durable_feedback import (
    _DurableFeedbackStore,
)
from general_ludd.decision_codification.durable_storage import (
    DurableGenerationStoreError,
)
from general_ludd.decision_codification.observability import (
    MAX_OBSERVABILITY_COUNTER,
    DecisionAbstentionCount,
    DecisionResolutionPath,
    DecisionReuseAggregate,
    DecisionReuseObservation,
    is_drift_reason,
)
from general_ludd.decision_codification.schema import DecisionKind, FallbackReason


class _DurableObservabilityStore(_DurableFeedbackStore):
    """Store one aggregate row per closed kind/reason under ``BEGIN IMMEDIATE``."""

    def bind_decision_observability(
        self,
        project_id: str,
        policy_digest: str,
    ) -> None:
        """Bind this database to one immutable project and policy scope."""
        self._validate_scope(project_id, DecisionKind.REVIEW)
        self._validate_digest(policy_digest)
        with self._write_connection() as connection:
            row = connection.execute(
                """
                SELECT project_id, policy_digest
                FROM decision_observability_scope WHERE singleton = 1
                """
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO decision_observability_scope(
                        singleton, project_id, policy_digest
                    ) VALUES (1, ?, ?)
                    """,
                    (project_id, policy_digest),
                )
                return
            if not self._scope_matches(row, project_id, policy_digest):
                raise DurableGenerationStoreError(
                    "decision observability scope does not match durable state"
                )

    def record_decision_observation(
        self,
        observation: DecisionReuseObservation,
    ) -> None:
        """Atomically aggregate one validated observation across workers."""
        try:
            validated = DecisionReuseObservation.model_validate(
                observation.model_dump(mode="python", by_alias=True),
                strict=True,
            )
        except (AttributeError, ValidationError):
            raise DurableGenerationStoreError(
                "decision observation is invalid"
            ) from None
        with self._write_connection() as connection:
            self._require_bound_scope(
                connection,
                validated.project_id,
                validated.policy_digest,
            )
            existing = connection.execute(
                """
                SELECT sequence, exact_rule_hits, typed_abstentions,
                       fallback_calls, avoided_agent_calls, latency_observations,
                       latency_total_us, latency_max_us, rule_version_changes,
                       drift_events, last_candidate_digest, last_observed_at
                FROM decision_observability_totals
                WHERE decision_kind = ?
                """,
                (validated.decision_kind.value,),
            ).fetchone()
            values = self._updated_values(existing, validated)
            connection.execute(
                """
                INSERT INTO decision_observability_totals (
                    decision_kind, sequence, exact_rule_hits, typed_abstentions,
                    fallback_calls, avoided_agent_calls, latency_observations,
                    latency_total_us, latency_max_us, rule_version_changes,
                    drift_events, last_candidate_digest, last_observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_kind) DO UPDATE SET
                    sequence = excluded.sequence,
                    exact_rule_hits = excluded.exact_rule_hits,
                    typed_abstentions = excluded.typed_abstentions,
                    fallback_calls = excluded.fallback_calls,
                    avoided_agent_calls = excluded.avoided_agent_calls,
                    latency_observations = excluded.latency_observations,
                    latency_total_us = excluded.latency_total_us,
                    latency_max_us = excluded.latency_max_us,
                    rule_version_changes = excluded.rule_version_changes,
                    drift_events = excluded.drift_events,
                    last_candidate_digest = excluded.last_candidate_digest,
                    last_observed_at = excluded.last_observed_at
                """,
                (validated.decision_kind.value, *values),
            )
            if validated.abstention_reason is not None:
                self._increment_reason(
                    connection,
                    validated.decision_kind,
                    validated.abstention_reason,
                )

    def decision_observability_aggregates(
        self,
        project_id: str,
        policy_digest: str,
    ) -> tuple[DecisionReuseAggregate, ...]:
        """Load and fully revalidate the bounded aggregate snapshot."""
        self._validate_scope(project_id, DecisionKind.REVIEW)
        self._validate_digest(policy_digest)
        try:
            with self._read_connection() as connection:
                self._require_bound_scope(connection, project_id, policy_digest)
                rows = connection.execute(
                    """
                    SELECT decision_kind, sequence, exact_rule_hits,
                           typed_abstentions, fallback_calls, avoided_agent_calls,
                           latency_observations, latency_total_us, latency_max_us,
                           rule_version_changes, drift_events,
                           last_candidate_digest, last_observed_at
                    FROM decision_observability_totals
                    ORDER BY decision_kind
                    """
                ).fetchall()
                reason_rows = connection.execute(
                    """
                    SELECT decision_kind, reason, count
                    FROM decision_observability_abstentions
                    ORDER BY decision_kind, reason
                    """
                ).fetchall()
            reasons: defaultdict[DecisionKind, list[DecisionAbstentionCount]] = (
                defaultdict(list)
            )
            for row in reason_rows:
                kind = DecisionKind(str(row["decision_kind"]))
                reasons[kind].append(
                    DecisionAbstentionCount(
                        reason=FallbackReason(str(row["reason"])),
                        count=int(row["count"]),
                    )
                )
            total_kinds = {
                DecisionKind(str(row["decision_kind"])) for row in rows
            }
            if set(reasons) - total_kinds:
                raise DurableGenerationStoreError(
                    "durable decision observability state is invalid"
                )
            return tuple(
                self._aggregate_from_row(
                    row,
                    tuple(reasons[DecisionKind(str(row["decision_kind"]))]),
                )
                for row in rows
            )
        except DurableGenerationStoreError:
            raise
        except (KeyError, TypeError, ValueError, ValidationError):
            raise DurableGenerationStoreError(
                "durable decision observability state is invalid"
            ) from None

    @classmethod
    def _updated_values(
        cls,
        row: sqlite3.Row | None,
        observation: DecisionReuseObservation,
    ) -> tuple[object, ...]:
        current = {
            "sequence": 0,
            "exact_rule_hits": 0,
            "typed_abstentions": 0,
            "fallback_calls": 0,
            "avoided_agent_calls": 0,
            "latency_observations": 0,
            "latency_total_us": 0,
            "latency_max_us": 0,
            "rule_version_changes": 0,
            "drift_events": 0,
        }
        previous_candidate: str | None = None
        previous_observed: datetime | None = None
        if row is not None:
            for field in current:
                current[field] = int(row[field])
            previous_candidate = row["last_candidate_digest"]
            if row["last_observed_at"] is not None:
                previous_observed = datetime.fromisoformat(row["last_observed_at"])

        increments = {
            "sequence": 1,
            "exact_rule_hits": int(
                observation.path is DecisionResolutionPath.EXACT_RULE
            ),
            "typed_abstentions": int(
                observation.path is DecisionResolutionPath.AGENT_FALLBACK
            ),
            "fallback_calls": int(
                observation.path is DecisionResolutionPath.AGENT_FALLBACK
            ),
            "avoided_agent_calls": int(
                observation.path is DecisionResolutionPath.EXACT_RULE
            ),
            "latency_observations": 1,
            "latency_total_us": observation.latency_us,
            "rule_version_changes": int(
                previous_candidate is not None
                and observation.candidate_digest is not None
                and previous_candidate != observation.candidate_digest
            ),
            "drift_events": int(is_drift_reason(observation.abstention_reason)),
        }
        for field, increment in increments.items():
            current[field] = cls._checked_add(current[field], increment)
        current["latency_max_us"] = max(
            current["latency_max_us"],
            observation.latency_us,
        )
        candidate = observation.candidate_digest or previous_candidate
        observed_at = max(
            item
            for item in (previous_observed, observation.observed_at)
            if item is not None
        )
        return (
            current["sequence"],
            current["exact_rule_hits"],
            current["typed_abstentions"],
            current["fallback_calls"],
            current["avoided_agent_calls"],
            current["latency_observations"],
            current["latency_total_us"],
            current["latency_max_us"],
            current["rule_version_changes"],
            current["drift_events"],
            candidate,
            observed_at.isoformat(),
        )

    @classmethod
    def _increment_reason(
        cls,
        connection: sqlite3.Connection,
        kind: DecisionKind,
        reason: FallbackReason,
    ) -> None:
        row = connection.execute(
            """
            SELECT count FROM decision_observability_abstentions
            WHERE decision_kind = ? AND reason = ?
            """,
            (kind.value, reason.value),
        ).fetchone()
        count = cls._checked_add(0 if row is None else int(row["count"]), 1)
        connection.execute(
            """
            INSERT INTO decision_observability_abstentions(
                decision_kind, reason, count
            ) VALUES (?, ?, ?)
            ON CONFLICT(decision_kind, reason) DO UPDATE SET count = excluded.count
            """,
            (kind.value, reason.value, count),
        )

    @staticmethod
    def _aggregate_from_row(
        row: sqlite3.Row,
        reasons: tuple[DecisionAbstentionCount, ...],
    ) -> DecisionReuseAggregate:
        observed = row["last_observed_at"]
        return DecisionReuseAggregate(
            decision_kind=DecisionKind(str(row["decision_kind"])),
            sequence=int(row["sequence"]),
            exact_rule_hits=int(row["exact_rule_hits"]),
            typed_abstentions=int(row["typed_abstentions"]),
            fallback_calls=int(row["fallback_calls"]),
            avoided_agent_calls=int(row["avoided_agent_calls"]),
            latency_observations=int(row["latency_observations"]),
            latency_total_us=int(row["latency_total_us"]),
            latency_max_us=int(row["latency_max_us"]),
            rule_version_changes=int(row["rule_version_changes"]),
            drift_events=int(row["drift_events"]),
            abstentions=reasons,
            last_candidate_digest=row["last_candidate_digest"],
            last_observed_at=(
                None if observed is None else datetime.fromisoformat(str(observed))
            ),
        )

    @staticmethod
    def _scope_matches(
        row: sqlite3.Row,
        project_id: str,
        policy_digest: str,
    ) -> bool:
        stored_project = row["project_id"]
        stored_policy = row["policy_digest"]
        return (
            isinstance(stored_project, str)
            and isinstance(stored_policy, str)
            and hmac.compare_digest(stored_project, project_id)
            and hmac.compare_digest(stored_policy, policy_digest)
        )

    @classmethod
    def _require_bound_scope(
        cls,
        connection: sqlite3.Connection,
        project_id: str,
        policy_digest: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT project_id, policy_digest
            FROM decision_observability_scope WHERE singleton = 1
            """
        ).fetchone()
        if row is None or not cls._scope_matches(row, project_id, policy_digest):
            raise DurableGenerationStoreError(
                "decision observability scope does not match durable state"
            )

    @staticmethod
    def _checked_add(current: int, increment: int) -> int:
        updated = current + increment
        if current < 0 or increment < 0 or updated > MAX_OBSERVABILITY_COUNTER:
            raise DurableGenerationStoreError(
                "decision observability counter bound exceeded"
            )
        return updated


__all__ = ["_DurableObservabilityStore"]
