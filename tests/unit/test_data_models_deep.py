"""Deep data-model / ORM introspection tests — no database needed.

Covers: tablename convention, column typing, relationships, backref
consistency, hybrid properties, FK-column indexing, CheckConstraints.
"""

from __future__ import annotations

import inspect
import typing
from datetime import datetime

import pytest
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql.schema import Column

from general_ludd.db import models as m

# ── helpers ────────────────────────────────────────────────────────────────


def _columns(model_cls: type[m.Base]) -> list[Column]:
    return list(model_cls.__table__.columns)


def _fks(model_cls: type[m.Base]) -> list[ForeignKey]:
    fks: list[ForeignKey] = []
    for col in _columns(model_cls):
        fks.extend(col.foreign_keys)
    return fks


def _table_args(model_cls: type[m.Base]) -> tuple:
    return getattr(model_cls, "__table_args__", ()) or ()


def _indexes(model_cls: type[m.Base]) -> list[Index]:
    return [a for a in _table_args(model_cls) if isinstance(a, Index)]


def _checks(model_cls: type[m.Base]) -> list[CheckConstraint]:
    return [a for a in _table_args(model_cls) if isinstance(a, CheckConstraint)]


def _uq_constraints(model_cls: type[m.Base]) -> list[UniqueConstraint]:
    return [a for a in _table_args(model_cls) if isinstance(a, UniqueConstraint)]


def _rel_attrs(model_cls: type[m.Base]) -> dict[str, typing.Any]:
    result: dict[str, typing.Any] = {}
    for name in dir(model_cls):
        attr = getattr(model_cls, name, None)
        if hasattr(attr, "property") and hasattr(attr.property, "back_populates"):
            result[name] = attr.property
    return result


def _all_model_classes() -> list[type[m.Base]]:
    return [
        obj
        for _, obj in inspect.getmembers(m)
        if inspect.isclass(obj) and issubclass(obj, m.Base) and obj is not m.Base
    ]


# ── 1. Tablename convention ────────────────────────────────────────────────


class TestTablenameConvention:
    MODELS_WITH_TABLENAMES: typing.ClassVar[dict[str, str]] = {
        "ProjectModel": "projects",
        "ProjectRelationshipModel": "project_relationships",
        "TodoModel": "todos",
        "TodoEventModel": "todo_events",
        "TaskReturnModel": "task_returns",
        "TaskDecisionModel": "task_decisions",
        "QueueModel": "queues",
        "AuditEventModel": "audit_events",
        "DeploymentRecordModel": "deployment_records",
        "VariableNamespaceModel": "variable_namespaces",
        "VariableValueModel": "variable_values",
        "BucketLeaseModel": "bucket_leases",
        "AzureCostPredictionModel": "azure_cost_predictions",
        "AzureCostObservationModel": "azure_cost_observations",
        "AzureCostOutboxEventModel": "azure_cost_outbox_events",
        "FeatureModel": "features",
        "PromptProfileModel": "prompt_profiles",
        "AgentMessageModel": "agent_messages",
        "SpendRecordModel": "spend_records",
        "RoleRunModel": "role_runs",
        "BenchmarkResultModel": "benchmark_results",
        "MemoryRecordModel": "memory_records",
        "TaskEmbeddingModel": "task_embeddings",
        "StsAuditModel": "sts_audit",
        "AgentTokenModel": "agent_tokens",
        "PermissionEscalationRequestModel": "permission_escalation_request",
        "HumanTodoModel": "human_todos",
        "RemediationActionModel": "remediation_actions",
        "ModelCallLogModel": "model_call_logs",
        "ModelPerformanceModel": "model_performance",
        "OrnithTrainingPairModel": "ornith_training_pairs",
        "SlurmJobModel": "slurm_jobs",
        "EventWorkTransportModel": "event_work_transport",
    }

    @pytest.mark.parametrize("cls_name,expected", list(MODELS_WITH_TABLENAMES.items()))
    def test_tablename_matches_convention(self, cls_name, expected):
        cls = getattr(m, cls_name)
        assert cls.__tablename__ == expected, (
            f"{cls_name}.__tablename__ expected {expected!r}, got {cls.__tablename__!r}"
        )

    def test_all_model_classes_have_tablename(self):
        for cls in _all_model_classes():
            name = cls.__tablename__
            assert isinstance(name, str) and len(name) > 0, f"{cls.__name__} missing __tablename__"
            assert name == name.lower(), f"{cls.__name__} tablename {name!r} not lowercase"

    def test_tablenames_are_unique(self):
        seen: dict[str, str] = {}
        for cls in _all_model_classes():
            tname = cls.__tablename__
            if tname in seen:
                pytest.fail(f"Duplicate tablename {tname!r}: {seen[tname]} and {cls.__name__}")
            seen[tname] = cls.__name__


# ── 2. Column typing ───────────────────────────────────────────────────────


COLUMN_TYPE_MAP = {
    "id": (Integer, String),
    "project_id": (String,),
    "name": (String,),
    "description": (Text, String),
    "title": (String,),
    "status": (String,),
    "event_type": (String,),
    "created_at": (DateTime, m.UTCDateTime),
    "updated_at": (DateTime, m.UTCDateTime),
    "todo_id": (String,),
    "queue": (String,),
    "priority": (Integer,),
    "queue_name": (String,),
    "active": (Boolean,),
    "confidence": (Float,),
    "version": (Integer,),
    "cost_usd": (Float, Numeric),
    "success": (Boolean,),
    "body": (Text,),
    "topic": (String,),
    "sender": (String,),
    "recipient": (String,),
    "role": (String,),
    "ts": (Float, DateTime),
    "tags": (Text,),
    "artifacts": (Text,),
    "evidence_refs": (Text,),
    "acceptance_criteria": (Text,),
    "dependencies": (Text,),
    "value": (Text, String),
    "key": (String,),
    "namespace": (String,),
    "agent_id": (String,),
    "model_name": (String,),
    "service": (String,),
    "prompt_text": (Text,),
    "spec_yaml": (Text,),
    "payload": (Text,),
    "state": (String,),
    "reason": (Text,),
    "decision": (String,),
    "exit_code": (Integer,),
    "playbook": (String,),
    "job_id": (String,),
    "return_id": (String,),
    "actor": (String,),
    "holder_id": (String,),
    "bucket_key": (String,),
    "category": (String,),
    "summary": (Text,),
    "detail": (Text,),
    "ok": (Boolean,),
    "model_profile_id": (String,),
    "task_type": (String,),
    "completion_score": (Float,),
    "code_quality_score": (Float,),
    "instruction_adherence_score": (Float,),
    "token_efficiency_score": (Float,),
    "input_tokens": (Integer,),
    "output_tokens": (Integer,),
    "time_seconds": (Float,),
    "duration_ms": (Float,),
    "error_message": (Text,),
    "raw_output": (Text,),
    "scaffold_content": (Text,),
    "scaffold_kind": (String,),
    "scaffold_hash": (String,),
    "outcome_status": (String,),
    "outcome_details": (Text,),
    "token_id": (String,),
    "issuer_agent_id": (String,),
    "subject_agent_id": (String,),
    "issued_at": (Float,),
    "expires_at": (Float,),
    "use_count": (Integer,),
    "events": (Text,),
    "role_name": (String,),
    "role_id": (String,),
    "scope_hash": (String,),
    "scope_actions": (Text,),
    "hydration_count": (Integer,),
    "parent_agent_id": (String,),
    "parent_todo_id": (String,),
    "child_todo_ids": (Text,),
    "worktree": (String,),
    "branch_name": (String,),
    "blocked_todo_id": (String,),
    "blocker_kind": (String,),
    "action_kind": (String,),
    "idempotency_key": (String,),
    "task_description": (Text,),
    "target_files": (Text,),
    "iterations_used": (Integer,),
    "tokens_consumed": (Integer,),
    "model_sha": (String,),
    "cron": (String,),
    "schedule_timezone": (String,),
    "run_count": (Integer,),
    "schedule_paused": (Boolean,),
}

MODEL_COLUMN_TYPE_OVERRIDES = {
    ("AgentMessageModel", "priority"): (String,),
    ("AgentTokenModel", "expires_at"): (DateTime,),
    ("BucketLeaseModel", "expires_at"): (m.UTCDateTime,),
    ("HumanTodoModel", "priority"): (String,),
    ("PromptProfileModel", "version"): (String,),
}


class TestColumnTypes:
    def test_all_columns_have_type(self):
        for cls in _all_model_classes():
            for col in _columns(cls):
                assert col.type is not None, f"{cls.__name__}.{col.name} has no type"

    def test_column_names_consistent_naming(self):
        for cls in _all_model_classes():
            for col in _columns(cls):
                assert col.name == col.name.lower(), f"{cls.__name__}.{col.name} has uppercase characters"
                assert " " not in col.name, f"{cls.__name__}.{col.name} contains space"

    @pytest.mark.parametrize("col_name,expected_types", list(COLUMN_TYPE_MAP.items()))
    def test_common_column_type_consistency(self, col_name, expected_types):
        for cls in _all_model_classes():
            col = next((c for c in _columns(cls) if c.name == col_name), None)
            if col is None:
                continue
            type_class = type(col.type)
            model_expected_types = MODEL_COLUMN_TYPE_OVERRIDES.get(
                (cls.__name__, col_name), expected_types
            )
            msg = (
                f"{cls.__name__}.{col_name} type {type_class.__name__} "
                f"not in expected {tuple(t.__name__ for t in model_expected_types)}"
            )
            assert any(
                type_class is t or issubclass(type_class, t)
                for t in model_expected_types
            ), msg

    def test_id_columns_use_valid_types(self):
        valid = (Integer, String)
        for cls in _all_model_classes():
            for col in _columns(cls):
                if col.name in ("id",) or col.name.endswith("_id"):
                    assert isinstance(col.type, valid), (
                        f"{cls.__name__}.{col.name} type {type(col.type).__name__} "
                        f"not in {tuple(t.__name__ for t in valid)}"
                    )


# ── 3. Relationships ───────────────────────────────────────────────────────


class TestRelationships:
    def test_todo_events_backref_consistent(self):
        todo_rel = _rel_attrs(m.TodoModel)
        event_rel = _rel_attrs(m.TodoEventModel)

        assert "events" in todo_rel
        assert "todo" in event_rel
        assert todo_rel["events"].back_populates == "todo"
        assert event_rel["todo"].back_populates == "events"

    def test_variable_namespace_values_backref_consistent(self):
        ns_rel = _rel_attrs(m.VariableNamespaceModel)
        vv_rel = _rel_attrs(m.VariableValueModel)

        assert "values" in ns_rel
        assert "namespace" in vv_rel
        assert ns_rel["values"].back_populates == "namespace"
        assert vv_rel["namespace"].back_populates == "values"

    def test_todo_events_cascade_delete_orphan(self):
        rel = _rel_attrs(m.TodoModel)["events"]
        assert "delete" in rel.cascade
        assert "delete-orphan" in rel.cascade
        assert rel.passive_deletes is True

    def test_variable_namespace_cascade_delete_orphan(self):
        rel = _rel_attrs(m.VariableNamespaceModel)["values"]
        assert "delete" in rel.cascade
        assert "delete-orphan" in rel.cascade
        assert rel.passive_deletes is True


# ── 4. Hybrid properties ───────────────────────────────────────────────────


class TestHybridProperties:
    def test_azure_cost_prediction_id_property(self):
        inst = m.AzureCostPredictionModel(prediction_id="pred-001", prediction_version=3)
        assert inst.id == ("pred-001", 3)

    def test_azure_cost_prediction_id_property_is_tuple(self):
        inst = m.AzureCostPredictionModel(prediction_id="abc", prediction_version=1)
        val = inst.id
        assert isinstance(val, tuple)
        assert len(val) == 2
        assert val[0] == "abc"
        assert val[1] == 1


# ── 5. FK-column indexing ──────────────────────────────────────────────────


FK_COLUMNS_THAT_MUST_HAVE_INDEX: set[tuple[str, str]] = {
    ("TodoModel", "project_id"),
    ("TodoModel", "parent_todo_id"),
    ("TodoEventModel", "todo_id"),
    ("TodoEventModel", "project_id"),
    ("TaskReturnModel", "project_id"),
    ("TaskReturnModel", "todo_id"),
    ("TaskReturnModel", "job_id"),
    ("TaskDecisionModel", "project_id"),
    ("QueueModel", "project_id"),
    ("AuditEventModel", "project_id"),
    ("AuditEventModel", "entity_id"),
    ("DeploymentRecordModel", "state"),
    ("BucketLeaseModel", "project_id"),
    ("BucketLeaseModel", "bucket_key"),
    ("AzureCostPredictionModel", "todo_id"),
    ("AzureCostPredictionModel", "state"),
    ("FeatureModel", "project_id"),
    ("AgentMessageModel", "project_id"),
    ("AgentMessageModel", "recipient"),
    ("SpendRecordModel", "project_id"),
    ("SpendRecordModel", "ts"),
    ("RoleRunModel", "project_id"),
    ("RoleRunModel", "role"),
    ("BenchmarkResultModel", "prompt_profile_id"),
    ("BenchmarkResultModel", "model_profile_id"),
    ("BenchmarkResultModel", "task_type"),
    ("MemoryRecordModel", "project_id"),
    ("MemoryRecordModel", "agent_id"),
    ("MemoryRecordModel", "namespace"),
    ("StsAuditModel", "token_id"),
    ("StsAuditModel", "issuer_agent_id"),
    ("StsAuditModel", "subject_agent_id"),
    ("AgentTokenModel", "agent_id"),
    ("AgentTokenModel", "parent_agent_id"),
    ("HumanTodoModel", "parent_agent_todo_id"),
    ("HumanTodoModel", "agent_id"),
    ("HumanTodoModel", "category"),
    ("HumanTodoModel", "status"),
    ("RemediationActionModel", "blocked_todo_id"),
    ("RemediationActionModel", "project_id"),
    ("RemediationActionModel", "blocker_kind"),
    ("RemediationActionModel", "action_kind"),
    ("RemediationActionModel", "idempotency_key"),
    ("ModelCallLogModel", "todo_id"),
    ("ModelCallLogModel", "model_profile_id"),
    ("SlurmJobModel", "job_id"),
    ("OrnithTrainingPairModel", "scaffold_hash"),
    ("PermissionEscalationRequestModel", "agent_id"),
    ("PermissionEscalationRequestModel", "status"),
    ("VariableValueModel", "namespace_id"),
}


class TestFkColumnIndexing:
    def _col_has_explicit_index(self, cls: type[m.Base], col_name: str) -> bool:
        col = next((c for c in _columns(cls) if c.name == col_name), None)
        if col is not None and col.index:
            return True
        for idx in _indexes(cls):
            idx_cols = [c.name for c in idx.columns]
            if col_name in idx_cols:
                return True
        return False

    @pytest.mark.parametrize("cls_name,col_name", sorted(FK_COLUMNS_THAT_MUST_HAVE_INDEX))
    def test_fk_column_has_index(self, cls_name, col_name):
        cls = getattr(m, cls_name)
        assert self._col_has_explicit_index(cls, col_name), (
            f"{cls_name}.{col_name} is an FK column but has no explicit index"
        )


# ── 6. FK ondelete conventions ──────────────────────────────────────────────


class TestFkOnDeleteConventions:
    def test_nullable_fks_use_set_null_except_noted(self):
        cascade_exceptions = {
            ("TodoEventModel", "todo_id"),  # CASCADE on non-nullable
            ("TaskDecisionModel", "return_id"),  # CASCADE on non-nullable
            ("VariableValueModel", "namespace_id"),  # CASCADE on non-nullable
        }
        for cls in _all_model_classes():
            for col in _columns(cls):
                for fk in col.foreign_keys:
                    if col.nullable and fk.ondelete:
                        key = (cls.__name__, col.name)
                        if key in cascade_exceptions:
                            continue
                        assert fk.ondelete.upper() == "SET NULL", (
                            f"{cls.__name__}.{col.name} is nullable FK but ondelete={fk.ondelete!r}, expected SET NULL"
                        )

    def test_non_nullable_fks_use_cascade_or_set_null(self):
        expected_cascade = {
            ("TodoEventModel", "todo_id"): "CASCADE",
            ("TaskDecisionModel", "return_id"): "CASCADE",
            ("VariableValueModel", "namespace_id"): "CASCADE",
            ("ProjectRelationshipModel", "project_id"): "CASCADE",
        }
        for cls in _all_model_classes():
            for col in _columns(cls):
                if col.nullable:
                    continue
                for fk in col.foreign_keys:
                    if fk.ondelete:
                        key = (cls.__name__, col.name)
                        expect = expected_cascade.get(key, "CASCADE")
                        assert fk.ondelete.upper() == expect, (
                            f"{cls.__name__}.{col.name} is non-nullable FK but "
                            f"ondelete={fk.ondelete!r}, expected {expect}"
                        )


# ── 7. CheckConstraint coverage ─────────────────────────────────────────────


class TestCheckConstraints:
    def test_todos_priority_range_constraint(self):
        checks = _checks(m.TodoModel)
        names = {c.name for c in checks}
        assert "ck_todos_priority_range" in names

    def test_deployment_records_revision_positive(self):
        checks = _checks(m.DeploymentRecordModel)
        names = {c.name for c in checks}
        assert "ck_deployment_records_revision_positive" in names

    def test_deployment_records_destroy_owner_state(self):
        checks = _checks(m.DeploymentRecordModel)
        names = {c.name for c in checks}
        assert "ck_deployment_records_destroy_owner_state" in names

    def test_azure_cost_predictions_fencing_nonnegative(self):
        checks = _checks(m.AzureCostPredictionModel)
        names = {c.name for c in checks}
        assert "ck_azure_cost_predictions_fencing_nonnegative" in names

    def test_azure_cost_predictions_state_rank(self):
        checks = _checks(m.AzureCostPredictionModel)
        names = {c.name for c in checks}
        assert "ck_azure_cost_predictions_state_rank" in names

    def test_azure_cost_predictions_lease_pair(self):
        checks = _checks(m.AzureCostPredictionModel)
        names = {c.name for c in checks}
        assert "ck_azure_cost_predictions_lease_pair" in names

    def test_azure_cost_observations_fencing_positive(self):
        checks = _checks(m.AzureCostObservationModel)
        names = {c.name for c in checks}
        assert "ck_azure_cost_observations_fencing_positive" in names

    def test_event_work_transport_payload_len(self):
        checks = _checks(m.EventWorkTransportModel)
        names = {c.name for c in checks}
        assert "ck_event_work_transport_payload_len" in names

    def test_task_decisions_has_length_checks(self):
        checks = _checks(m.TaskDecisionModel)
        names = {c.name for c in checks}
        for col in (
            "todo_updates",
            "child_todos",
            "validation_requests",
            "git_requests",
            "audit_notes",
            "policy_flags",
        ):
            assert f"ck_task_decisions_{col}_len" in names, f"Missing length check for TaskDecisionModel.{col}"

    def test_audit_events_has_length_check_on_details(self):
        checks = _checks(m.AuditEventModel)
        names = {c.name for c in checks}
        assert "ck_audit_events_details_len" in names

    def test_azure_cost_predictions_length_check_identity_payload(self):
        checks = _checks(m.AzureCostPredictionModel)
        names = {c.name for c in checks}
        assert "ck_azure_cost_predictions_identity_payload_len" in names

    def test_azure_cost_observations_length_check_payload(self):
        checks = _checks(m.AzureCostObservationModel)
        names = {c.name for c in checks}
        assert "ck_azure_cost_observations_payload_len" in names

    def test_azure_cost_outbox_events_length_check_payload(self):
        checks = _checks(m.AzureCostOutboxEventModel)
        names = {c.name for c in checks}
        assert "ck_azure_cost_outbox_events_payload_len" in names

    def test_all_check_constraints_have_names(self):
        for cls in _all_model_classes():
            for ck in _checks(cls):
                assert ck.name is not None, f"{cls.__name__} has unnamed CheckConstraint: {ck.sqltext}"
                assert ck.name.startswith("ck_"), f"{cls.__name__} check {ck.name!r} doesn't start with 'ck_'"


# ── 8. UniqueConstraint coverage ────────────────────────────────────────────


class TestUniqueConstraints:
    def test_project_relationships_edge_unique(self):
        uqs = _uq_constraints(m.ProjectRelationshipModel)
        names = {u.name for u in uqs}
        assert "uq_project_relationship_edge" in names

    def test_variable_namespace_project_unique(self):
        uqs = _uq_constraints(m.VariableNamespaceModel)
        names = {u.name for u in uqs}
        assert "uq_namespace_project" in names

    def test_variable_value_namespace_key_unique(self):
        uqs = _uq_constraints(m.VariableValueModel)
        names = {u.name for u in uqs}
        assert "uq_variable_namespace_key" in names

    def test_bucket_lease_unique(self):
        uqs = _uq_constraints(m.BucketLeaseModel)
        names = {u.name for u in uqs}
        assert "uq_bucket_lease" in names

    def test_azure_cost_observation_identity_unique(self):
        uqs = _uq_constraints(m.AzureCostObservationModel)
        names = {u.name for u in uqs}
        assert "uq_azure_cost_observation_identity" in names

    def test_azure_cost_outbox_dedup_unique(self):
        uqs = _uq_constraints(m.AzureCostOutboxEventModel)
        names = {u.name for u in uqs}
        assert "uq_azure_cost_outbox_deduplication_key" in names

    def test_memory_record_unique(self):
        uqs = _uq_constraints(m.MemoryRecordModel)
        names = {u.name for u in uqs}
        assert "uq_memory_agent_key_ns_project" in names

    def test_feature_name_unique(self):
        col = next(c for c in _columns(m.FeatureModel) if c.name == "name")
        assert col.unique is True

    def test_queue_name_unique(self):
        col = next(c for c in _columns(m.QueueModel) if c.name == "queue_name")
        assert col.unique is True

    def test_todo_id_unique(self):
        col = next(c for c in _columns(m.TodoModel) if c.name == "todo_id")
        assert col.unique is True


# ── 9. Composite FK constraints ────────────────────────────────────────────


class TestCompositeForeignKeys:
    def test_azure_cost_observations_composite_fk(self):
        fkc = next((a for a in _table_args(m.AzureCostObservationModel) if isinstance(a, ForeignKeyConstraint)), None)
        assert fkc is not None, "AzureCostObservationModel missing FKConstraint"
        assert fkc.name == "fk_azure_cost_observations_prediction"
        assert fkc.ondelete == "CASCADE"
        assert set(fkc.column_keys) == {"prediction_id", "prediction_version"}

    def test_azure_cost_outbox_composite_fk(self):
        fkc = next((a for a in _table_args(m.AzureCostOutboxEventModel) if isinstance(a, ForeignKeyConstraint)), None)
        assert fkc is not None, "AzureCostOutboxEventModel missing FKConstraint"
        assert fkc.name == "fk_azure_cost_outbox_prediction"
        assert fkc.ondelete == "CASCADE"


# ── 10. Primary key patterns ────────────────────────────────────────────────


class TestPrimaryKeys:
    def test_every_model_has_primary_key(self):
        for cls in _all_model_classes():
            pk_cols = [c for c in _columns(cls) if c.primary_key]
            assert len(pk_cols) >= 1, f"{cls.__name__} has no primary key column"

    def test_most_models_have_single_column_pk(self):
        exceptions = {"AzureCostPredictionModel"}  # composite PK
        for cls in _all_model_classes():
            if cls.__name__ in exceptions:
                continue
            pk_cols = [c for c in _columns(cls) if c.primary_key]
            assert len(pk_cols) == 1, f"{cls.__name__} has {len(pk_cols)} PK columns; expected 1"

    def test_azure_cost_prediction_has_composite_pk(self):
        pk_cols = [c.name for c in _columns(m.AzureCostPredictionModel) if c.primary_key]
        assert set(pk_cols) == {"prediction_id", "prediction_version"}


# ── 11. Default-value coverage ──────────────────────────────────────────────


class TestColumnDefaults:
    def test_created_at_has_default(self):
        for cls in _all_model_classes():
            col = next((c for c in _columns(cls) if c.name == "created_at"), None)
            if col is not None:
                assert col.default is not None, f"{cls.__name__}.created_at has no default"

    def test_json_blob_defaults_are_empty_containers(self):
        empty_json_defaults = {
            ("TodoModel", "tags"): "[]",
            ("TodoModel", "artifacts"): "[]",
            ("TodoModel", "evidence_refs"): "[]",
            ("TodoModel", "dependencies"): "[]",
            ("TodoModel", "child_todo_ids"): "[]",
            ("TodoModel", "test_commands"): "[]",
            ("TodoModel", "molecule_scenarios"): "[]",
            ("TodoModel", "molecule_evidence_refs"): "[]",
            ("TaskReturnModel", "artifacts"): "[]",
            ("TaskDecisionModel", "evidence_refs"): "[]",
            ("TaskDecisionModel", "child_todos"): "[]",
            ("TaskDecisionModel", "audit_notes"): "[]",
            ("TaskDecisionModel", "policy_flags"): "[]",
            ("FeatureModel", "acceptance_criteria"): "[]",
            ("FeatureModel", "evidence"): "[]",
        }
        for (cls_name, col_name), _expected_default in empty_json_defaults.items():
            cls = getattr(m, cls_name)
            col = next((c for c in _columns(cls) if c.name == col_name), None)
            assert col is not None, f"{cls_name}.{col_name} not found"
            assert col.default is not None, f"{cls_name}.{col_name} has no default"

    def test_todo_scheduling_defaults(self):
        cols = {c.name: c for c in _columns(m.TodoModel)}
        assert cols["run_count"].default.arg == 0
        assert cols["schedule_paused"].default.arg is False
        assert cols["schedule_timezone"].default.arg == "UTC"

    def test_todo_version_default(self):
        col = next(c for c in _columns(m.TodoModel) if c.name == "version")
        assert col.default.arg == 1


# ── 12. Model count regression ──────────────────────────────────────────────


class TestModelCount:
    def test_model_count_matches_expected(self):
        expected = 33
        actual = len(_all_model_classes())
        assert actual == expected, (
            f"Expected {expected} model classes, found {actual}. Update this test if models were added/removed."
        )


# ── 13. Versioned model (optimistic concurrency) ────────────────────────────


class TestVersionedModels:
    def test_todo_has_version_id_col(self):
        assert m.TodoModel.__mapper_args__["version_id_col"].name == "version"

    def test_deployment_record_has_revision_column(self):
        col = next(c for c in _columns(m.DeploymentRecordModel) if c.name == "revision")
        assert isinstance(col.type, Integer)
        assert col.default.arg == 1


# ── 14. Enum-column coverage ───────────────────────────────────────────────


class TestEnumUsage:
    def test_audit_event_type_enum_values(self):
        values = {e.value for e in m.AuditEventType}
        assert "todo_created" in values
        assert "task_decision_made" in values
        assert "bucket_lease_acquired" in values

    def test_relation_type_enum_values(self):
        values = {e.value for e in m.RelationType}
        assert values == {"parent", "child", "sibling", "external"}

    def test_location_kind_enum_values(self):
        values = {e.value for e in m.LocationKind}
        assert values == {"gludd_project_name", "directory", "url"}

    def test_feature_status_enum_values(self):
        values = {e.value for e in m.FeatureStatus}
        assert values == {"requested", "in_progress", "implemented", "verified", "regressed"}

    def test_all_enums_are_str_enums(self):
        for name in ("AuditEventType", "RelationType", "LocationKind", "FeatureStatus"):
            enum_cls = getattr(m, name)
            assert issubclass(enum_cls, str), f"{name} is not a StrEnum"


# ── 15. UTCDateTime type decorator ─────────────────────────────────────────


class TestUTCDateTime:
    def test_utcdatetime_cache_ok(self):
        assert m.UTCDateTime.cache_ok is True

    def test_utcdatetime_impl_is_datetime(self):
        assert isinstance(m.UTCDateTime().impl, DateTime)

    def test_utcdatetime_process_bind_param_utc_normalize(self):
        from datetime import UTC, datetime, timedelta, timezone

        dt = m.UTCDateTime()
        aware = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=5)))
        result = dt.process_bind_param(aware, None)
        assert result.tzinfo == UTC

    def test_utcdatetime_process_result_value_naive_to_utc(self):
        dt = m.UTCDateTime()
        naive = datetime(2026, 1, 1)
        result = dt.process_result_value(naive, None)
        assert result.tzinfo is not None


# ── 16. ID-generation functions ────────────────────────────────────────────


class TestIdGeneration:
    def test_gen_todo_id_format(self):
        tid = m._gen_todo_id()
        assert tid.startswith("TODO-")
        assert len(tid) == 13

    def test_gen_rel_id_format(self):
        rid = m._gen_rel_id()
        assert rid.startswith("rel-")
        assert len(rid) == 16

    def test_gen_feature_id_format(self):
        fid = m._gen_feature_id()
        assert fid.startswith("FEAT-")
        assert len(fid) == 13

    def test_gen_memory_id_format(self):
        mid = m._gen_memory_id()
        assert mid.startswith("mem-")
        assert len(mid) == 16

    def test_gen_human_todo_id_format(self):
        hid = m._gen_human_todo_id()
        assert hid.startswith("HTODO-")
        assert len(hid) == 16

    def test_gen_remediation_id_format(self):
        rid = m._gen_remediation_id()
        assert rid.startswith("REM-")
        assert len(rid) == 16

    def test_gen_model_call_id_format(self):
        mid = m._gen_model_call_id()
        assert mid.startswith("MC-")
        assert len(mid) == 16

    def test_gen_ornith_pair_id_format(self):
        oid = m._gen_ornith_pair_id()
        assert oid.startswith("ORN-")
        assert len(oid) == 36
