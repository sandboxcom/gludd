"""Ownership contract for live-provider proof receipts."""

import general_ludd.models.freellmapi_release_provider as release_provider
from general_ludd.models.freellmapi_release_proof import build_live_provider_receipt


def test_live_receipt_builder_is_owned_by_the_provider_module() -> None:
    assert build_live_provider_receipt is release_provider.build_live_provider_receipt
    assert build_live_provider_receipt.__module__ == (
        "general_ludd.models.freellmapi_release_provider"
    )
