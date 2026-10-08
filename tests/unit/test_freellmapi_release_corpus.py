"""Ownership contract for the frozen-corpus proof."""

import general_ludd.models.freellmapi_release_corpus as release_corpus
from general_ludd.models.freellmapi_release_proof import run_frozen_corpus_proof


def test_frozen_corpus_proof_is_owned_by_the_corpus_module() -> None:
    assert run_frozen_corpus_proof is release_corpus.run_frozen_corpus_proof
    assert run_frozen_corpus_proof.__module__ == (
        "general_ludd.models.freellmapi_release_corpus"
    )
