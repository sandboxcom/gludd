"""Regression tests for conservative observation contradiction recognition."""

from __future__ import annotations

from general_ludd.memory.observation_consolidator import (
    MemoryFact,
    ObservationConsolidator,
)


def _facts(*contents: str) -> list[MemoryFact]:
    return [
        MemoryFact(fact_id=f"fact-{index}", content=content, timestamp=float(index))
        for index, content in enumerate(contents)
    ]


def test_replacement_claim_is_a_contradiction_in_either_input_order() -> None:
    consolidator = ObservationConsolidator(similarity_threshold=0.5)

    for contents in (
        (
            "Alice uses React for frontend development",
            "Alice switched to Vue for frontend work",
        ),
        (
            "Alice switched to Vue for frontend work",
            "Alice uses React for frontend development",
        ),
    ):
        observations = consolidator.consolidate(_facts(*contents))

        assert any(observation.contradictions for observation in observations)


def test_competing_single_value_claims_are_contradictory() -> None:
    consolidator = ObservationConsolidator()

    observations = consolidator.consolidate(
        _facts("Alice uses React", "Alice uses Vue", "Alice prefers Angular")
    )

    assert any(observation.contradictions for observation in observations)


def test_same_relation_in_distinct_contexts_remains_compatible() -> None:
    consolidator = ObservationConsolidator()

    observations = consolidator.consolidate(
        _facts(
            "Alice uses Python for backend services",
            "Alice uses Terraform for infrastructure automation",
        )
    )

    assert all(not observation.contradictions for observation in observations)

