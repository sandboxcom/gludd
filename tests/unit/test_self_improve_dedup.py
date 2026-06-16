"""Unit tests for SelfImproveDeduplicator (F8 — no re-filing the same gap)."""

from __future__ import annotations

from general_ludd.self_improve.dedup import (
    SelfImproveDeduplicator,
    proposal_signature,
)


class TestSignature:
    def test_signature_from_gap_type_and_file(self) -> None:
        sig = proposal_signature(
            {"gap_type": "missing_tests", "source_file": "src/a.py", "title": "x"}
        )
        assert sig == "missing_tests::src/a.py"

    def test_signature_ignores_title_when_gap_present(self) -> None:
        a = proposal_signature(
            {"gap_type": "dead_code", "source_file": "src/a.py", "title": "Wire Foo"}
        )
        b = proposal_signature(
            {"gap_type": "dead_code", "source_file": "src/a.py", "title": "totally different title"}
        )
        assert a == b

    def test_signature_falls_back_to_title(self) -> None:
        sig = proposal_signature({"title": "Lone proposal"})
        assert sig == "title::Lone proposal"

    def test_distinct_titles_distinct_signatures(self) -> None:
        a = proposal_signature({"title": "one"})
        b = proposal_signature({"title": "two"})
        assert a != b


class TestFilterNew:
    def test_drops_already_open(self) -> None:
        open_sigs = {"missing_tests::src/a.py"}
        dedup = SelfImproveDeduplicator(open_signatures=open_sigs)
        proposals = [
            {"gap_type": "missing_tests", "source_file": "src/a.py", "title": "dup"},
            {"gap_type": "missing_tests", "source_file": "src/b.py", "title": "new"},
        ]
        fresh = dedup.filter_new(proposals)
        assert len(fresh) == 1
        assert fresh[0]["source_file"] == "src/b.py"

    def test_collapses_duplicates_within_batch(self) -> None:
        dedup = SelfImproveDeduplicator()
        proposals = [
            {"gap_type": "dead_code", "source_file": "src/x.py", "title": "first"},
            {"gap_type": "dead_code", "source_file": "src/x.py", "title": "second"},
        ]
        fresh = dedup.filter_new(proposals)
        assert len(fresh) == 1
        assert fresh[0]["title"] == "first"  # first occurrence kept

    def test_is_duplicate_predicate(self) -> None:
        dedup = SelfImproveDeduplicator(open_signatures={"low_coverage::src/c.py"})
        assert dedup.is_duplicate(
            {"gap_type": "low_coverage", "source_file": "src/c.py"}
        )
        assert not dedup.is_duplicate(
            {"gap_type": "low_coverage", "source_file": "src/d.py"}
        )

    def test_per_call_open_signatures_augment_constructor_set(self) -> None:
        dedup = SelfImproveDeduplicator(open_signatures={"a::1"})
        proposals = [
            {"gap_type": "a", "source_file": "1", "title": "ctor-open"},
            {"gap_type": "b", "source_file": "2", "title": "call-open"},
            {"gap_type": "c", "source_file": "3", "title": "genuinely new"},
        ]
        fresh = dedup.filter_new(proposals, open_signatures={"b::2"})
        assert [p["title"] for p in fresh] == ["genuinely new"]

    def test_empty_proposals_returns_empty(self) -> None:
        assert SelfImproveDeduplicator().filter_new([]) == []
