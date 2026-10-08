"""Shared execution-identity schema contracts and replay compatibility."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def _model_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "openai",
        "profile": "review",
        "model": "gpt-6",
        "request_parameters": {"temperature": 0.25},
        "provider_revision": None,
    }
    payload.update(changes)
    return payload


def test_replay_schema_reexports_shared_identity_classes() -> None:
    """The historical replay import path must expose the canonical classes."""
    from general_ludd.replay.schema import (
        ModelIdentityV1 as ReplayModelIdentityV1,
    )
    from general_ludd.replay.schema import (
        RuntimeIdentityV1 as ReplayRuntimeIdentityV1,
    )
    from general_ludd.replay.schema import (
        SourceIdentityV1 as ReplaySourceIdentityV1,
    )
    from general_ludd.schemas.execution_identity import (
        ModelIdentityV1,
        RuntimeIdentityV1,
        SourceIdentityV1,
    )

    assert ReplaySourceIdentityV1 is SourceIdentityV1
    assert ReplayRuntimeIdentityV1 is RuntimeIdentityV1
    assert ReplayModelIdentityV1 is ModelIdentityV1


def test_identity_aliases_have_one_static_owner_with_runtime_compatibility() -> None:
    """Type aliases are imported canonically without breaking runtime callers."""
    from general_ludd.replay import schema as replay_schema
    from general_ludd.schemas.execution_identity import (
        BoundedIdentifier,
        Sha256Digest,
    )

    assert "BoundedIdentifier" not in replay_schema.__all__
    assert "Sha256Digest" not in replay_schema.__all__
    assert vars(replay_schema)["BoundedIdentifier"] is BoundedIdentifier
    assert vars(replay_schema)["Sha256Digest"] is Sha256Digest


def test_shared_identity_models_preserve_strict_frozen_validation() -> None:
    """The extracted contract remains strict, immutable, and extra-forbidding."""
    from general_ludd.schemas.execution_identity import (
        ModelIdentityV1,
        RuntimeIdentityV1,
        SourceIdentityV1,
    )

    source = SourceIdentityV1(
        repository_url_sha256="sha256:" + "1" * 64,
        commit_sha="2" * 40,
        tree_sha="3" * 40,
        branch="development",
        dirty=False,
    )
    runtime = RuntimeIdentityV1(
        gludd_version="0.1.1",
        python_version="3.12.13",
        os="darwin",
        architecture="arm64",
        config_sha256="sha256:" + "4" * 64,
        feature_flags={"decision_codification": True},
    )

    assert source.branch == "development"
    assert runtime.feature_flags == {"decision_codification": True}
    with pytest.raises(ValidationError, match="dirty"):
        SourceIdentityV1.model_validate(
            {**source.model_dump(), "dirty": 0},
        )
    with pytest.raises(ValidationError, match="unexpected"):
        ModelIdentityV1.model_validate(
            {**_model_payload(), "unexpected": True},
        )
    with pytest.raises(ValidationError, match="frozen"):
        source.branch = "main"


@pytest.mark.parametrize(
    "request_parameters",
    [
        {"temperature": float("nan")},
        {"nested": [1, {"score": float("inf")}]},
    ],
)
def test_shared_model_identity_rejects_non_finite_json(
    request_parameters: dict[str, object],
) -> None:
    """Provider request parameters remain finite JSON before persistence."""
    from general_ludd.schemas.execution_identity import ModelIdentityV1

    with pytest.raises(ValidationError, match="finite"):
        ModelIdentityV1.model_validate(
            _model_payload(request_parameters=request_parameters),
        )
