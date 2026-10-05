"""Strict Python-boundary regressions for beta4's small collections."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.check_collection_python_boundary import scan_collections

_COLLECTIONS = (
    "azure",
    "chat",
    "chemistry",
    "operations",
    "formal",
    "security",
    "web_server",
)


@pytest.mark.parametrize("collection", _COLLECTIONS)
def test_small_collection_has_strict_zero_python_boundaries(collection: str) -> None:
    """Each migrated artifact must run without core or ambient Python coupling."""
    repository_root = Path(__file__).resolve().parents[2]
    collection_root = (
        repository_root
        / "collections"
        / "ansible_collections"
        / "general_ludd"
        / collection
    )

    assert scan_collections(collection_root) == []
