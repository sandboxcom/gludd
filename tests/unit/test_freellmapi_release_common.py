"""Ownership contract for shared FreeLLMAPI proof types."""

from general_ludd.models.freellmapi_release_proof import (
    FreeLLMAPIReleaseProofError,
)


def test_release_error_is_owned_by_the_common_module() -> None:
    assert FreeLLMAPIReleaseProofError.__module__ == (
        "general_ludd.models.freellmapi_release_common"
    )
