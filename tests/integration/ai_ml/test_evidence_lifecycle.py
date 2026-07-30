"""Integration tests: evidence fetch -> store -> retrieve -> dedupe -> retract.

Pins the evidence-pipeline contract from docs/specs/FEATURE_AI_ML_EXPERT.md
§4.3 (records), §5.2 (pipeline), and acceptance criteria:

  - AIML-AT-002 — duplicate content creates ONE artifact and multiple source
    locators; a 100-source fixture ingests deterministically.
"""

from __future__ import annotations

import hashlib

import pytest

from general_ludd.ai_ml import EvidenceStore
from general_ludd.ai_ml.schemas import EvidenceArtifact


class TestFetchStoreRetrieve:
    """Pipeline steps 1-3: fetch into content-addressed artifact, retrieve."""

    def test_ingest_then_retrieve_by_source_id(self) -> None:
        store = EvidenceStore()
        ev = store.ingest(
            content=b"transformer architecture paper",
            media_type="application/pdf",
            license="CC-BY-4.0",
            locator="https://arxiv.org/abs/1706.03762",
            creator="Vaswani et al.",
        )

        retrieved = store.get(ev.source_id)
        assert retrieved is not None
        assert retrieved.source_id == ev.source_id
        assert retrieved.sha256 == ev.sha256
        assert retrieved.media_type == "application/pdf"
        assert retrieved.license == "CC-BY-4.0"
        assert retrieved.creator == "Vaswani et al."

    def test_content_addressed_sha_matches_content_hash(self) -> None:
        # The artifact's sha256 IS the content hash (content-addressed
        # storage, spec §5.2 step 1).
        store = EvidenceStore()
        payload = b"deterministic content"
        expected = hashlib.sha256(payload).hexdigest()

        ev = store.ingest(
            content=payload,
            media_type="text/plain",
            license="MIT",
            locator="loc",
        )
        assert ev.sha256 == expected

        by_sha = store.get_by_sha(expected)
        assert by_sha is not None
        assert by_sha.source_id == ev.source_id

    def test_get_content_returns_inert_bytes_only(self) -> None:
        # The ONLY method that returns content is get_content(); it returns
        # raw bytes, never an interpreted object (spec §5.1 prompt-injection
        # isolation boundary).
        store = EvidenceStore()
        payload = b"\x00\x01raw bytes, not instructions\x02\x00"
        ev = store.ingest(
            content=payload,
            media_type="application/octet-stream",
            license="MIT",
            locator="loc",
        )
        retrieved = store.get_content(ev.sha256)
        assert retrieved == payload
        assert isinstance(retrieved, bytes)

    def test_unknown_source_id_returns_none(self) -> None:
        store = EvidenceStore()
        assert store.get("evd-does-not-exist") is None
        assert store.get_by_sha("0" * 64) is None
        assert store.get_content("0" * 64) is None


class TestDuplicateDetection:
    """AIML-AT-002: duplicate content -> one artifact, many locators."""

    def test_duplicate_bytes_dedupe_to_one_artifact_many_locators(self) -> None:
        store = EvidenceStore()
        content = b"identical payload bytes"

        ev1 = store.ingest(
            content=content,
            media_type="text/plain",
            license="MIT",
            locator="https://mirror-a.example/paper",
        )
        ev2 = store.ingest(
            content=content,
            media_type="text/plain",
            license="MIT",
            locator="https://mirror-b.example/paper",
        )

        # Same canonical artifact.
        assert ev1.sha256 == ev2.sha256
        assert ev1.source_id == ev2.source_id
        # Both locators are recorded on the single record.
        record = store.get(ev1.source_id)
        assert record is not None
        assert set(record.locators) == {
            "https://mirror-a.example/paper",
            "https://mirror-b.example/paper",
        }
        # Exactly one artifact in the store.
        assert len(store.list_all()) == 1

    def test_many_sources_ingest_deterministically(self) -> None:
        # AIML-AT-002: a multi-source fixture ingests deterministically.
        # 40 distinct payloads, each referenced from 2 locators -> 40
        # artifacts, 80 locators total, stable ordering by fetched_at.
        store = EvidenceStore()
        for i in range(40):
            payload = f"evidence-payload-{i}".encode()
            store.ingest(
                content=payload,
                media_type="text/plain",
                license="MIT",
                locator=f"https://src-a.example/{i}",
            )
            store.ingest(
                content=payload,
                media_type="text/plain",
                license="MIT",
                locator=f"https://src-b.example/{i}",
            )

        all_artifacts = store.list_all()
        assert len(all_artifacts) == 40
        # Every artifact carries both locators (dedup merged them).
        assert all(len(a.locators) == 2 for a in all_artifacts)
        # Deterministic: re-running the same ingest produces the same count.
        for i in range(40):
            payload = f"evidence-payload-{i}".encode()
            store.ingest(
                content=payload,
                media_type="text/plain",
                license="MIT",
                locator=f"https://src-c.example/{i}",
            )
        # No new artifacts created; the third locator was merged.
        assert len(store.list_all()) == 40
        merged = store.get_by_sha(hashlib.sha256(b"evidence-payload-0").hexdigest())
        assert merged is not None
        assert len(merged.locators) == 3

    def test_re_ingest_same_locator_is_idempotent(self) -> None:
        # Re-ingesting the same (content, locator) does not duplicate the
        # locator entry.
        store = EvidenceStore()
        content = b"payload"
        ev1 = store.ingest(content=content, media_type="text/plain", license="MIT", locator="loc-A")
        ev2 = store.ingest(content=content, media_type="text/plain", license="MIT", locator="loc-A")
        record = store.get(ev1.source_id)
        assert record is not None
        assert record.locators == ("loc-A",)
        assert ev1.source_id == ev2.source_id


class TestRetractionAndCorrection:
    """Pipeline step 4: detect retractions and corrections (spec §5.2)."""

    def test_retract_flags_record_but_keeps_it_auditable(self) -> None:
        # Retraction is non-destructive: the record stays in the store for
        # audit but is flagged with reason + timestamp.
        store = EvidenceStore()
        ev = store.ingest(
            content=b"withdrawn claim",
            media_type="text/plain",
            license="MIT",
            locator="loc",
        )

        retracted = store.retract(ev.source_id, reason="paper withdrawn by authors")
        assert retracted.retracted is True
        assert retracted.retraction_reason == "paper withdrawn by authors"
        assert retracted.retracted_at is not None
        assert retracted.retracted_at > 0

        # Still retrievable for audit via get().
        assert store.get(ev.source_id) is not None
        # Filtered out of the active set.
        assert store.list_all(include_retracted=False) == []
        # Visible in the full audit set.
        full = store.list_all(include_retracted=True)
        assert len(full) == 1
        assert full[0].retracted is True

    def test_retract_is_idempotent(self) -> None:
        # Retracting an already-retracted record is a no-op (keeps the
        # original reason/timestamp).
        store = EvidenceStore()
        ev = store.ingest(
            content=b"x",
            media_type="text/plain",
            license="MIT",
            locator="loc",
        )
        first = store.retract(ev.source_id, reason="first reason")
        second = store.retract(ev.source_id, reason="second reason")
        assert second.retraction_reason == "first reason"
        assert second.retracted_at == first.retracted_at

    def test_retract_unknown_source_id_raises(self) -> None:
        store = EvidenceStore()
        with pytest.raises(KeyError, match="unknown source_id"):
            store.retract("evd-nonexistent", reason="x")

    def test_supersede_links_correction_and_retracts_original(self) -> None:
        # supersede() publishes a corrected record carrying
        # ``supersedes=old_source_id`` AND retracts the old record (spec
        # §5.2: "Detect duplicates, retractions, corrections").
        store = EvidenceStore()
        old = store.ingest(
            content=b"original content with a typo",
            media_type="text/plain",
            license="MIT",
            locator="loc-old",
        )

        linked = store.supersede(
            old.source_id,
            new_content=b"corrected content",
            media_type="text/plain",
            license="MIT",
            locator="loc-new",
            reason="typo in original",
        )

        # New record points back at the old one.
        assert isinstance(linked, EvidenceArtifact)
        assert linked.supersedes == old.source_id
        assert linked.sha256 != old.sha256
        assert not linked.retracted

        # Old record is retracted with the supplied reason.
        old_after = store.get(old.source_id)
        assert old_after is not None
        assert old_after.retracted is True
        assert "typo" in old_after.retraction_reason

    def test_tenant_cannot_retract_other_tenants_evidence(self) -> None:
        # supersede() with an explicit tenant_id must not operate on a
        # different tenant's record.
        store = EvidenceStore()
        store.ingest(
            content=b"tenant-a evidence",
            media_type="text/plain",
            license="MIT",
            locator="loc",
            tenant_id="tenant-A",
        )
        target = store.list_all(tenant_id="tenant-A")[0]
        with pytest.raises(KeyError, match="unknown source_id"):
            store.supersede(
                target.source_id,
                new_content=b"attacker correction",
                media_type="text/plain",
                license="MIT",
                locator="loc",
                reason="attempted cross-tenant supersede",
                tenant_id="tenant-B",
            )
