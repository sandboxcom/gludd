"""Deep tests for CapabilityEvidenceStore — atomicity, concurrency, query_by_task_kind,
load_evidence_for_identity edge cases, and dict-based registration.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading

import pytest

from general_ludd.routing_roles.small_model_policy import (
    CapabilityEvidence,
    ModelIdentity,
)
from general_ludd.schemas.benchmark import TaskRole


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


def _make_store(path: str):
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    return CapabilityEvidenceStore(path)


# ---------------------------------------------------------------------------
# File persistence edge cases
# ---------------------------------------------------------------------------


def test_empty_file_resets_to_empty_list() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(b"")
        path = f.name
    try:
        store = _make_store(path)
        assert store.list_all() == []
        with open(path) as fh:
            assert json.load(fh) == []
    finally:
        os.unlink(path)


def test_whitespace_only_file_resets_to_empty_list() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(b"   \n \t  \n")
        path = f.name
    try:
        store = _make_store(path)
        assert store.list_all() == []
    finally:
        os.unlink(path)


def test_non_list_json_resets_to_empty_list() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(json.dumps({"not": "a list"}).encode())
        path = f.name
    try:
        store = _make_store(path)
        assert store.list_all() == []
        with open(path) as fh:
            raw = json.load(fh)
        assert raw == []
    finally:
        os.unlink(path)


def test_missing_file_creates_empty_store_on_init() -> None:
    path = os.path.join(tempfile.gettempdir(), f"gludd-test-nonexistent-{os.getpid()}.json")
    assert not os.path.exists(path)
    try:
        store = _make_store(path)
        assert store.list_all() == []
        assert os.path.exists(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_atomic_write_tmp_file_removed_after_save() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        store.register_evidence(_evidence())
        tmp_path = path + ".tmp"
        assert not os.path.exists(tmp_path)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# register_evidence — dict input
# ---------------------------------------------------------------------------


def test_register_evidence_accepts_plain_dict() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        record = {
            "model_profile_id": "dict-model",
            "model_identity_digest": _digest("dict-id"),
            "task_kind": "code_synthesis",
            "role": "coder",
            "collection": "general_ludd.agent",
            "suite_id": "suite-x",
            "suite_revision": "v2",
            "acceptance_contract_digest": _digest("contract-dict"),
            "passed_cases": 10,
            "total_cases": 20,
            "collection_ok": True,
            "local_only": False,
            "evidence_digest": _digest("proof-dict"),
        }
        count = store.register_evidence(record)
        assert count == 1
        results = store.list_all()
        assert results[0]["model_profile_id"] == "dict-model"
        assert "registered_at" in results[0]
    finally:
        os.unlink(path)


def test_register_evidence_dict_preserves_pre_set_registered_at() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        fixed_ts = 1000000.0
        record = {
            "model_profile_id": "dict-model",
            "model_identity_digest": _digest("dict-id"),
            "task_kind": "code_synthesis",
            "role": "coder",
            "collection": "general_ludd.agent",
            "suite_id": "suite-x",
            "suite_revision": "v2",
            "acceptance_contract_digest": _digest("c"),
            "passed_cases": 5,
            "total_cases": 10,
            "collection_ok": True,
            "local_only": False,
            "evidence_digest": _digest("p"),
            "registered_at": fixed_ts,
        }
        store.register_evidence(record)
        assert store.list_all()[0]["registered_at"] == fixed_ts
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# query_by_task_kind
# ---------------------------------------------------------------------------


def test_query_by_task_kind_returns_matching_records() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        store.register_evidence(_evidence(task_kind="context_compaction", evidence_id="a"))
        store.register_evidence(_evidence(task_kind="context_compaction", evidence_id="b"))
        store.register_evidence(_evidence(task_kind="code_synthesis", evidence_id="c"))
        results = store.query_by_task_kind("context_compaction")
        assert len(results) == 2
        assert all(r["task_kind"] == "context_compaction" for r in results)
    finally:
        os.unlink(path)


def test_query_by_task_kind_returns_empty_for_no_match() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        store.register_evidence(_evidence(task_kind="context_compaction"))
        assert store.query_by_task_kind("code_synthesis") == []
    finally:
        os.unlink(path)


def test_query_by_task_kind_empty_store_returns_empty() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        assert store.query_by_task_kind("context_compaction") == []
    finally:
        os.unlink(path)


def test_query_by_task_kind_validates_format() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        with pytest.raises(ValueError):
            store.query_by_task_kind("")
        with pytest.raises(ValueError):
            store.query_by_task_kind("UPPERCASE")
        with pytest.raises(ValueError):
            store.query_by_task_kind("has-dash")
    finally:
        os.unlink(path)


def test_query_by_task_kind_validates_type() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        with pytest.raises(ValueError):
            store.query_by_task_kind(None)  # type: ignore[arg-type]
    finally:
        os.unlink(path)


def test_query_by_task_kind_returns_shallow_copy() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        store.register_evidence(_evidence(task_kind="context_compaction"))
        results = store.query_by_task_kind("context_compaction")
        results.clear()
        assert len(store.query_by_task_kind("context_compaction")) == 1
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# query_by_model deep
# ---------------------------------------------------------------------------


def test_query_by_model_returns_shallow_copy() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        store.register_evidence(_evidence(model_profile_id="model-a"))
        results = store.query_by_model("model-a")
        results.clear()
        assert len(store.query_by_model("model-a")) == 1
    finally:
        os.unlink(path)


def test_query_by_model_rejects_non_string() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        with pytest.raises(ValueError):
            store.query_by_model(None)  # type: ignore[arg-type]
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# expire_stale deep
# ---------------------------------------------------------------------------


def test_expire_stale_zero_max_age_removes_all() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        store.register_evidence(_evidence(evidence_id="a"))
        store.register_evidence(_evidence(evidence_id="b"))
        remained, removed = store.expire_stale(max_age_seconds=0)
        assert removed == 2
        assert remained == 0
        assert store.list_all() == []
    finally:
        os.unlink(path)


def test_expire_stale_missing_registered_at_defaults_to_zero() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        store.register_evidence(_evidence(evidence_id="a"))
        store._records[0].pop("registered_at", None)
        store._save()
        remained, removed = store.expire_stale(max_age_seconds=3600)
        assert removed == 1
        assert remained == 0
    finally:
        os.unlink(path)


def test_expire_stale_persists_result_to_disk() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        store.register_evidence(_evidence(evidence_id="a"))
        store.register_evidence(_evidence(evidence_id="b"))
        store._records[0]["registered_at"] = 0.0
        store._save()
        store.expire_stale(max_age_seconds=1)
        with open(path) as fh:
            raw = json.load(fh)
        assert len(raw) == 1
    finally:
        os.unlink(path)


def test_expire_stale_negative_max_age_removes_all() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        store.register_evidence(_evidence(evidence_id="a"))
        remained, removed = store.expire_stale(max_age_seconds=-100)
        assert removed == 1
        assert remained == 0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# load_evidence_for_identity
# ---------------------------------------------------------------------------


def test_load_evidence_for_identity_returns_matching_records() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        identity = _identity("model-a")
        ev = _evidence(model_profile_id="model-a", evidence_id="a")
        store.register_evidence(ev)
        loaded = store.load_evidence_for_identity("model-a", identity.fingerprint)
        assert len(loaded) == 1
        assert loaded[0].model_profile_id == "model-a"
        assert isinstance(loaded[0], CapabilityEvidence)
    finally:
        os.unlink(path)


def test_load_evidence_for_identity_filters_different_digest() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        _identity("model-a")
        ev = _evidence(model_profile_id="model-a", evidence_id="a")
        store.register_evidence(ev)
        loaded = store.load_evidence_for_identity("model-a", _digest("different-digest"))
        assert loaded == []
    finally:
        os.unlink(path)


def test_load_evidence_for_identity_skips_invalid_records() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        identity = _identity("model-a")
        store.register_evidence(_evidence(model_profile_id="model-a", evidence_id="good"))
        store._records[0].pop("task_kind")
        store._save()
        loaded = store.load_evidence_for_identity("model-a", identity.fingerprint)
        assert loaded == []
    finally:
        os.unlink(path)


def test_load_evidence_for_identity_handles_string_role() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        identity = _identity("model-a")
        store.register_evidence(_evidence(model_profile_id="model-a", evidence_id="a"))
        loaded = store.load_evidence_for_identity("model-a", identity.fingerprint)
        assert len(loaded) == 1
        assert isinstance(loaded[0].role, TaskRole)
    finally:
        os.unlink(path)


def test_load_evidence_for_identity_skips_invalid_task_role() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        identity = _identity("model-a")
        store.register_evidence(_evidence(model_profile_id="model-a", evidence_id="a"))
        store._records[0]["role"] = "nonexistent_role"
        store._save()
        loaded = store.load_evidence_for_identity("model-a", identity.fingerprint)
        assert loaded == []
    finally:
        os.unlink(path)


def test_load_evidence_for_identity_empty_store_returns_empty() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        loaded = store.load_evidence_for_identity("model-x", _digest("any"))
        assert loaded == []
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_registration_no_corruption() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        errors: list[Exception] = []
        thread_count = 8

        def register_one(idx: int) -> None:
            try:
                store.register_evidence(_evidence(evidence_id=f"thread-{idx}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=register_one, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(store.list_all()) == thread_count
    finally:
        os.unlink(path)


def test_concurrent_register_and_query_no_deadlock() -> None:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _make_store(path)
        store.register_evidence(_evidence(model_profile_id="shared", evidence_id="base"))
        barrier = threading.Barrier(3)

        def writer() -> None:
            barrier.wait()
            for i in range(10):
                store.register_evidence(_evidence(model_profile_id="shared", evidence_id=f"w{i}"))

        def reader() -> None:
            barrier.wait()
            for _ in range(10):
                store.list_all()

        def queryer() -> None:
            barrier.wait()
            for _ in range(10):
                store.query_by_model("shared")

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=queryer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(store.list_all()) == 11
    finally:
        os.unlink(path)
