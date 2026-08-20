"""Regression tests for source-tree agent collection imports."""

from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_AGENT_COLLECTION_ROOT = (
    _REPOSITORY_ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
).resolve()


def test_agent_collection_namespace_resolves_to_source_tree() -> None:
    """The source checkout must provide the same namespace as Galaxy installs."""
    spec = find_spec("ansible_collections.general_ludd.agent")

    assert spec is not None
    locations = {
        Path(location).resolve()
        for location in (spec.submodule_search_locations or ())
    }
    assert _AGENT_COLLECTION_ROOT in locations


@pytest.mark.parametrize(
    "module_name",
    ("gludd_facts", "gludd_message", "gludd_metrics", "gludd_traces"),
)
def test_agent_modules_keep_galaxy_fqcn_imports(module_name: str) -> None:
    """Agent modules must import through their packaged Galaxy namespace."""
    module = import_module(
        f"ansible_collections.general_ludd.agent.plugins.modules.{module_name}"
    )

    assert callable(module.main)
