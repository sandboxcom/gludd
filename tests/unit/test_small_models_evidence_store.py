"""Tests for CapabilityEvidenceStore — durable JSON file-backed evidence persistence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from typing import cast

import pytest

from general_ludd.routing_roles.small_model_policy import (
    CapabilityEvidence,
    ModelIdentity,
    TaskImpact,
)
from general_ludd.schemas.benchmark import TaskRole, TaskType


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _identity(model_profile_id: str = "local-profile") -> ModelIdentity:
    return ModelIdentity(
        model_profile_id=model_profile_id,
        model_artifact_digest=_digest("weights-v1"),
        runtime_config_digest=_digest("llama.cpp:rev1"),
        prompt_contract_digest=_digest("prompt:v1"),
    )


def _evidence(
    *,
    model_profile_id: str = "local-profile",
    task_kind: str = "context_compaction",
    role: TaskRole = TaskRole.COMPACTOR,
    collection: str = "general_ludd.agent",
    passed_cases: int = 24,
    total_cases: int = 24,
    collection_ok: bool = True,
    local_only: bool = True,
    evidence_id: str = "ev-1",
) -> CapabilityEvidence:
    identity = _identity(model_profile_id)
    return CapabilityEvidence(
        model_profile_id=identity.model_profile_id,
        model_identity_digest=identity.fingerprint,
        task_kind=task_kind,
        role=role,
        collection=collection,
        suite_id="small-model-contract",
        suite_revision="v1",
        acceptance_contract_digest=_digest(f"contract:{task_kind}:{role.value}:{collection}"),
        passed_cases=passed_cases,
        total_cases=total_cases,
        collection_ok=collection_ok,
        local_only=local_only,
        evidence_digest=_digest(f"proof:{model_profile_id}:{task_kind}:{evidence_id}"),
    )


# ---------------- store creation and file persistence ----------------


def test_store_persists_data_to_disk() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        ev = _evidence()
        store.register_evidence(ev)

        with open(path) as f:
            raw = json.load(f)
        assert len(raw) == 1
        assert raw[0]["model_profile_id"] == "local-profile"
    finally:
        os.unlink(path)


def test_store_survives_reopen() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store_a = CapabilityEvidenceStore(path)
        store_a.register_evidence(_evidence(evidence_id="a"))
        store_a.register_evidence(_evidence(evidence_id="b"))

        store_b = CapabilityEvidenceStore(path)
        assert len(store_b.list_all()) == 2
    finally:
        os.unlink(path)


def test_new_store_creates_empty_file() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        with open(path) as f:
            raw = json.load(f)
        assert raw == []
        assert store.list_all() == []
    finally:
        os.unlink(path)


def test_store_rejects_corrupt_file_without_overwriting_it() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(b"not json at all")
        path = f.name

    try:
        with pytest.raises(ValueError, match="valid JSON list"):
            CapabilityEvidenceStore(path)
        with open(path, "rb") as stream:
            assert stream.read() == b"not json at all"
    finally:
        os.unlink(path)


@pytest.mark.parametrize("payload", ({}, ["not-a-record"]))
def test_store_rejects_non_record_collections_without_overwriting_them(
    payload: object,
) -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    encoded = json.dumps(payload).encode("utf-8")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as stream:
        stream.write(encoded)
        path = stream.name

    try:
        with pytest.raises(ValueError, match="valid JSON list"):
            CapabilityEvidenceStore(path)
        with open(path, "rb") as stream:
            assert stream.read() == encoded
    finally:
        os.unlink(path)


def test_store_rejects_symlink_without_touching_its_target() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.TemporaryDirectory() as directory:
        target = os.path.join(directory, "required-artifact.json")
        link = os.path.join(directory, "evidence.json")
        with open(target, "w", encoding="utf-8") as stream:
            stream.write('{"credential":"must-survive"}')
        os.symlink(target, link)

        with pytest.raises(ValueError, match="regular non-symlink"):
            CapabilityEvidenceStore(link)

        with open(target, encoding="utf-8") as stream:
            assert stream.read() == '{"credential":"must-survive"}'


def test_store_atomic_write_never_follows_predictable_temp_symlink() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "evidence.json")
        store = CapabilityEvidenceStore(path)
        protected = os.path.join(directory, "required-artifact.json")
        with open(protected, "w", encoding="utf-8") as stream:
            stream.write('{"credential":"must-survive"}')
        os.symlink(protected, path + ".tmp")

        store.register_evidence(_evidence())

        with open(protected, encoding="utf-8") as stream:
            assert stream.read() == '{"credential":"must-survive"}'
        assert len(store.list_all()) == 1


def test_store_creates_private_evidence_file() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "evidence.json")

        CapabilityEvidenceStore(path)

        assert os.stat(path).st_mode & 0o777 == 0o600


# ---------------- register_evidence ----------------


def test_register_evidence_adds_record() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence())
        store.register_evidence(_evidence(model_profile_id="other-model"))
        assert len(store.list_all()) == 2
    finally:
        os.unlink(path)


def test_register_evidence_assigns_timestamp() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence())
        all_ev = store.list_all()
        assert all_ev[0].get("registered_at") is not None
        assert isinstance(all_ev[0]["registered_at"], (int, float))
    finally:
        os.unlink(path)


def test_register_evidence_returns_count() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        assert store.register_evidence(_evidence()) == 1
        assert store.register_evidence(_evidence(evidence_id="ev-2")) == 2
    finally:
        os.unlink(path)


# ---------------- query_by_model ----------------


def test_query_by_model_returns_matching_records() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence(model_profile_id="model-a"))
        store.register_evidence(_evidence(model_profile_id="model-a", evidence_id="ev-2"))
        store.register_evidence(_evidence(model_profile_id="model-b"))

        results = store.query_by_model("model-a")
        assert len(results) == 2
        assert all(r["model_profile_id"] == "model-a" for r in results)
    finally:
        os.unlink(path)


def test_query_by_model_returns_empty_for_no_match() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence(model_profile_id="model-a"))
        assert store.query_by_model("nonexistent") == []
    finally:
        os.unlink(path)


def test_query_by_model_validates_identifier() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        with pytest.raises(ValueError):
            store.query_by_model("")
    finally:
        os.unlink(path)


def test_task_queries_validate_shape_and_exclude_legacy_records() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as stream:
        path = stream.name

    try:
        store = CapabilityEvidenceStore(path)
        current = {
            "model_profile_id": "model-a",
            "task_kind": "context_compaction",
            "task_type": TaskType.BUG_FIX.value,
        }
        store.register_evidence(current)
        store.register_evidence({key: value for key, value in current.items() if key != "task_type"})

        assert len(store.query_by_task_kind("context_compaction")) == 2
        assert store.query_by_task_shape(TaskType.BUG_FIX, "context_compaction") == [
            {**current, "registered_at": store.list_all()[0]["registered_at"]}
        ]
        assert len(
            store.query_by_task_shape(
                TaskType.BUG_FIX,
                "context_compaction",
                model_profile_id="model-a",
            )
        ) == 1
        with pytest.raises(ValueError, match="task_kind"):
            store.query_by_task_kind("not a task")
        with pytest.raises(ValueError, match="TaskType"):
            store.query_by_task_shape(
                cast(TaskType, "bug_fix"),
                "context_compaction",
            )
        with pytest.raises(ValueError, match="default task contract"):
            store.query_by_task_shape(TaskType.BUG_FIX, "unknown_kind")
        with pytest.raises(ValueError, match="model_profile_id"):
            store.query_by_task_shape(
                TaskType.BUG_FIX,
                "context_compaction",
                model_profile_id="not valid",
            )
    finally:
        os.unlink(path)


def test_loading_identity_ignores_foreign_and_malformed_records() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as stream:
        path = stream.name

    try:
        store = CapabilityEvidenceStore(path)
        evidence = _evidence()
        store.register_evidence(evidence)
        store.register_evidence(
            {
                "model_profile_id": evidence.model_profile_id,
                "model_identity_digest": evidence.model_identity_digest,
            }
        )

        loaded = store.load_evidence_for_identity(
            evidence.model_profile_id,
            evidence.model_identity_digest,
        )
        assert loaded == [evidence]
        assert store.load_evidence_for_identity(
            evidence.model_profile_id,
            _digest("different-identity"),
        ) == []
    finally:
        os.unlink(path)


# ---------------- list_all ----------------


def test_list_all_returns_shallow_copy() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence())
        results = store.list_all()
        results.clear()
        assert len(store.list_all()) == 1
    finally:
        os.unlink(path)


# ---------------- expire_stale ----------------


def test_expire_stale_removes_old_records() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence(evidence_id="old"))
        time.sleep(0.01)

        before = time.time()
        store.register_evidence(_evidence(evidence_id="new"))

        store._records[0]["registered_at"] = before - 3600
        store._save()

        remained, removed = store.expire_stale(max_age_seconds=1800)
        assert removed == 1
        assert remained == 1
        assert store.list_all()[0]["evidence_digest"] == _digest("proof:local-profile:context_compaction:new")
    finally:
        os.unlink(path)


def test_expire_stale_keeps_fresh_records() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence(evidence_id="fresh"))
        remained, removed = store.expire_stale(max_age_seconds=3600)
        assert removed == 0
        assert remained == 1
    finally:
        os.unlink(path)


def test_expire_stale_on_empty_store() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        remained, removed = store.expire_stale(max_age_seconds=3600)
        assert removed == 0
        assert remained == 0
    finally:
        os.unlink(path)


# ---------------- wire: policy uses store ----------------


def test_policy_can_authorize_from_store_evidence() -> None:
    from general_ludd.routing_roles.small_model_policy import (
        SmallModelTaskPolicy,
        SmallModelTaskSpec,
    )
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    identity = _identity()
    task = SmallModelTaskSpec(
        task_id="todo-17",
        task_kind="context_compaction",
        role=TaskRole.COMPACTOR,
        collection="general_ludd.agent",
        input_digest=_digest("input:todo-17"),
        impacts=frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}),
        acceptance_checks=("facts_preserved", "token_budget_met", "schema_valid"),
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        ev = _evidence(
            model_profile_id=identity.model_profile_id,
            task_kind=task.task_kind,
            role=task.role,
            collection=task.collection,
        )

        raw = {
            "model_profile_id": ev.model_profile_id,
            "model_identity_digest": ev.model_identity_digest,
            "task_kind": ev.task_kind,
            "role": ev.role.value,
            "collection": ev.collection,
            "suite_id": ev.suite_id,
            "suite_revision": ev.suite_revision,
            "acceptance_contract_digest": task.acceptance_contract_digest,
            "passed_cases": ev.passed_cases,
            "total_cases": ev.total_cases,
            "collection_ok": ev.collection_ok,
            "local_only": ev.local_only,
            "evidence_digest": ev.evidence_digest,
        }
        store.register_evidence(raw)

        policy = SmallModelTaskPolicy()
        loaded = store.load_evidence_for_identity(identity.model_profile_id, identity.fingerprint)

        decision = policy.authorize(task, model_identity=identity, evidence=loaded)
        assert decision.approved is True
    finally:
        os.unlink(path)


def test_policy_escalates_when_store_has_no_match() -> None:
    from general_ludd.routing_roles.small_model_policy import (
        SmallModelTaskPolicy,
        SmallModelTaskSpec,
    )
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    identity = _identity("model-x")
    task = SmallModelTaskSpec(
        task_id="todo-99",
        task_kind="context_compaction",
        role=TaskRole.COMPACTOR,
        collection="general_ludd.agent",
        input_digest=_digest("input:todo-99"),
        impacts=frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}),
        acceptance_checks=("facts_preserved", "token_budget_met", "schema_valid"),
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        store = CapabilityEvidenceStore(path)
        policy = SmallModelTaskPolicy()
        loaded = store.load_evidence_for_identity(identity.model_profile_id, identity.fingerprint)

        decision = policy.authorize(task, model_identity=identity, evidence=loaded)
        assert decision.approved is False
        assert decision.reason == "capability_evidence_missing"
    finally:
        os.unlink(path)
