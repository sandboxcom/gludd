"""Ownership contract for the frozen-corpus proof."""

from general_ludd.models.freellmapi_release_proof import run_frozen_corpus_proof


def test_frozen_corpus_proof_is_owned_by_the_corpus_module() -> None:
    assert run_frozen_corpus_proof.__module__ == (
        "general_ludd.models.freellmapi_release_corpus"
    )
