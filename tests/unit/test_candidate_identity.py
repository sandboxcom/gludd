"""Provider-neutral model candidate identity contracts."""

from __future__ import annotations

import pytest

from general_ludd.models.candidate_identity import (
    CatalogFreeTierCandidateIdentity,
    ModelCandidateProvider,
    stable_identity_digest,
)
from general_ludd.self_improve.model_candidates import (
    CatalogFreeTierCandidateIdentity as SelfImproveCatalogIdentity,
)


def _identity(**updates: object) -> CatalogFreeTierCandidateIdentity:
    values: dict[str, object] = {
        "platform": "groq",
        "model_id": "acme/coder-model",
        "catalog_version": "2026.09.15",
        "catalog_payload_sha256": "a" * 64,
    }
    values.update(updates)
    return CatalogFreeTierCandidateIdentity(**values)  # type: ignore[arg-type]


def test_catalog_identity_is_owned_by_the_provider_neutral_model_layer() -> None:
    identity = _identity()

    assert SelfImproveCatalogIdentity is CatalogFreeTierCandidateIdentity
    assert identity.provider is ModelCandidateProvider.CATALOG_FREE_TIER
    assert identity.evidence_identity_digest == identity.identity_digest
    assert len(identity.identity_digest) == 64


def test_stable_identity_digest_is_key_order_independent() -> None:
    assert stable_identity_digest({"provider": "groq", "model": "a"}) == (
        stable_identity_digest({"model": "a", "provider": "groq"})
    )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"platform": "Groq"}, "platform"),
        ({"platform": ""}, "platform"),
        ({"model_id": ""}, "model_id"),
        ({"model_id": " model"}, "model_id"),
        ({"model_id": "bad\nmodel"}, "model_id"),
        ({"catalog_version": "latest"}, "catalog_version"),
        ({"catalog_payload_sha256": "A" * 64}, "catalog_payload_sha256"),
    ],
)
def test_catalog_identity_rejects_mutable_or_noncanonical_evidence(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _identity(**updates)
