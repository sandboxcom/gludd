"""Unit tests for AIML Phase A: registry records (spec §4.3).

Every Source, Dataset, Model, Adapter, Simulator, EvaluationSuite, and
Deployment record includes: stable ID, semantic version, SHA-256 digest,
creator, creation time, license, origin URI, dependency lock digest, input
digests, policy decision, validation state, supersedes relation, and
tombstone state. Mutable names resolve through atomic aliases; immutable
versions never change in place (spec §4.3).

TDD red phase — ``general_ludd.ai_ml.registries`` must satisfy every
assertion below.
"""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

from general_ludd.ai_ml.registries import (
    Adapter,
    Dataset,
    Deployment,
    EvaluationSuite,
    Model,
    Registry,
    RegistryRecord,
    Simulator,
    Source,
    ValidationState,
)


def _sha(content: bytes = b"registry-fixture") -> str:
    return hashlib.sha256(content).hexdigest()


def _base_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "record_id": "rec-001",
        "kind": "source",
        "version": "1.0.0",
        "sha256": _sha(),
        "creator": "research_refresh@tenant-a",
        "license": "MIT",
        "origin_uri": "https://example.com/source",
        "dependency_lock_sha256": _sha(b"lock"),
        "input_digests": (),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Record construction + invariants
# ---------------------------------------------------------------------------


class TestRegistryRecordConstruction:
    def test_source_record_constructs_with_required_fields(self) -> None:
        rec = Source(**_base_kwargs(kind="source"))  # type: ignore[arg-type]
        assert rec.record_id == "rec-001"
        assert rec.kind == "source"
        assert rec.version == "1.0.0"
        assert rec.validation_state is ValidationState.PENDING
        assert rec.tombstone is False

    @pytest.mark.parametrize(
        "factory, kind",
        [
            (Source, "source"),
            (Dataset, "dataset"),
            (Model, "model"),
            (Adapter, "adapter"),
            (Simulator, "simulator"),
            (EvaluationSuite, "evaluation_suite"),
            (Deployment, "deployment"),
        ],
    )
    def test_each_kind_constructs_with_default_validation_state(self, factory: type[RegistryRecord], kind: str) -> None:
        rec = factory(**_base_kwargs(kind=kind))  # type: ignore[arg-type]
        assert rec.kind == kind
        assert rec.validation_state is ValidationState.PENDING
        assert rec.tombstone is False

    def test_registry_record_is_frozen(self) -> None:
        rec = Source(**_base_kwargs())  # type: ignore[arg-type]
        with pytest.raises(dataclasses.FrozenInstanceError):
            rec.license = "GPL-3.0-only"  # type: ignore[misc]

    def test_invalid_semver_rejected(self) -> None:
        with pytest.raises(ValueError, match="version"):
            Source(**_base_kwargs(version="not-a-version"))  # type: ignore[arg-type]

    def test_invalid_sha256_rejected(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            Source(**_base_kwargs(sha256="bad"))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Registry: publish, alias, tombstone, supersede
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_publish_returns_immutable_record(self) -> None:
        reg = Registry()
        rec = Source(**_base_kwargs(record_id="r1"))  # type: ignore[arg-type]
        published = reg.publish(rec)
        assert published.record_id == "r1"
        assert reg.get("r1") is published

    def test_alias_resolves_to_record(self) -> None:
        reg = Registry()
        rec = Source(**_base_kwargs(record_id="r1"))  # type: ignore[arg-type]
        reg.publish(rec)
        reg.set_alias("production-source", "r1")
        resolved = reg.resolve("production-source")
        assert resolved is not None
        assert resolved.record_id == "r1"

    def test_alias_swap_is_atomic(self) -> None:
        reg = Registry()
        v1 = Source(**_base_kwargs(record_id="r1", version="1.0.0"))  # type: ignore[arg-type]
        v2 = Source(**_base_kwargs(record_id="r2", version="2.0.0"))  # type: ignore[arg-type]
        reg.publish(v1)
        reg.publish(v2)
        reg.set_alias("production-source", "r1")
        reg.set_alias("production-source", "r2")
        # After swap, alias resolves to v2 only — no mixed state.
        resolved = reg.resolve("production-source")
        assert resolved is not None
        assert resolved.record_id == "r2"

    def test_tombstone_marks_record_inactive(self) -> None:
        reg = Registry()
        rec = Source(**_base_kwargs(record_id="r1"))  # type: ignore[arg-type]
        reg.publish(rec)
        reg.tombstone("r1", reason="superseded by r2")
        record = reg.get("r1")
        assert record is not None
        assert record.tombstone is True
        assert record.tombstone_reason == "superseded by r2"

    def test_supersede_creates_new_and_tombstones_old(self) -> None:
        reg = Registry()
        v1 = Source(**_base_kwargs(record_id="r1", version="1.0.0"))  # type: ignore[arg-type]
        reg.publish(v1)
        v2_kwargs = _base_kwargs(record_id="r2", version="2.0.0")
        v2 = Source(**v2_kwargs)  # type: ignore[arg-type]
        reg.supersede("r1", v2)
        # Old is tombstoned, new is live, and supersedes link is set.
        old = reg.get("r1")
        new = reg.get("r2")
        assert old is not None and new is not None
        assert old.tombstone is True
        assert new.supersedes == "r1"
        assert new.tombstone is False

    def test_publish_duplicate_id_raises(self) -> None:
        reg = Registry()
        rec = Source(**_base_kwargs(record_id="r1"))  # type: ignore[arg-type]
        reg.publish(rec)
        with pytest.raises(ValueError, match="already exists"):
            reg.publish(rec)

    def test_resolve_unknown_alias_returns_none(self) -> None:
        reg = Registry()
        assert reg.resolve("never-set") is None

    def test_list_filters_by_kind(self) -> None:
        reg = Registry()
        reg.publish(Source(**_base_kwargs(record_id="s1", kind="source")))  # type: ignore[arg-type]
        reg.publish(Model(**_base_kwargs(record_id="m1", kind="model")))  # type: ignore[arg-type]
        sources = reg.list_kind("source")
        models = reg.list_kind("model")
        assert len(sources) == 1
        assert len(models) == 1
        assert sources[0].record_id == "s1"
        assert models[0].record_id == "m1"
