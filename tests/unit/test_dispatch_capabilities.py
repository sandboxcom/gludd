"""Tests for src/general_ludd/dispatch/capabilities.py and router.py."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from general_ludd.dispatch.capabilities import (
    CapabilityRegistry,
    CollectionMeta,
    discover_capabilities,
)
from general_ludd.dispatch.router import CapabilityRouter, RouteResult

# ── Fixtures ────────────────────────────────────────────────────────────


def _write_galaxy_yml(parent: Path, name: str, tags: list[str], description: str = "") -> None:
    galaxy = {
        "namespace": "general_ludd",
        "name": name,
        "version": "0.1.0",
        "description": description or f"{name} collection",
        "tags": tags,
        "license": ["MIT"],
        "authors": ["Test"],
        "dependencies": {},
    }
    parent.mkdir(parents=True, exist_ok=True)
    (parent / "galaxy.yml").write_text(yaml.dump(galaxy))


def _write_role_meta(parent: Path, description: str, role_name: str | None = None) -> None:
    meta: dict[str, Any] = {
        "galaxy_info": {
            "author": "Test",
            "description": description,
            "license": "MIT",
            "min_ansible_version": "2.14",
        },
        "dependencies": [],
    }
    if role_name:
        meta["galaxy_info"]["role_name"] = role_name
    parent.mkdir(parents=True, exist_ok=True)
    (parent / "main.yml").write_text(yaml.dump(meta))


def _make_collections_dir() -> Path:
    base = Path(tempfile.mkdtemp(prefix="gludd-test-collections-"))
    colls = base / "ansible_collections"
    return colls


# ── CollectionMeta ──────────────────────────────────────────────────────


def test_collectionmeta_from_galaxy_yml_with_tags() -> None:
    data = {
        "namespace": "general_ludd",
        "name": "chemistry",
        "version": "0.2.0",
        "description": "chemistry expert",
        "tags": ["chemistry", "reactions", "safety"],
        "license": ["MIT"],
    }
    meta = CollectionMeta.from_galaxy(data)
    assert meta.name == "chemistry"
    assert meta.namespace == "general_ludd"
    assert meta.version == "0.2.0"
    assert "chemistry" in meta.tags
    assert "reactions" in meta.tags
    assert "safety" in meta.tags


def test_collectionmeta_from_galaxy_missing_tags() -> None:
    data = {
        "namespace": "general_ludd",
        "name": "minimal",
        "version": "0.1.0",
    }
    meta = CollectionMeta.from_galaxy(data)
    assert meta.name == "minimal"
    assert meta.tags == frozenset()


def test_collectionmeta_from_galaxy_missing_version() -> None:
    data = {
        "namespace": "general_ludd",
        "name": "noversion",
        "tags": ["test"],
    }
    meta = CollectionMeta.from_galaxy(data)
    assert meta.version == "unknown"


# ── CapabilityRegistry ──────────────────────────────────────────────────


def test_registry_empty_on_init() -> None:
    reg = CapabilityRegistry()
    assert reg.collections == {}
    assert reg.tag_index == {}


def test_registry_add_collection_increments_tag_index() -> None:
    reg = CapabilityRegistry()
    meta = CollectionMeta(
        name="agent",
        namespace="general_ludd",
        version="0.2.0",
        description="agent collection",
        tags=frozenset(["agentic", "sdlc", "automation"]),
    )
    reg.add_collection(meta)
    assert "agent" in reg.collections
    assert reg.collections["agent"] is meta
    assert "agentic" in reg.tag_index
    assert "sdlc" in reg.tag_index
    assert "automation" in reg.tag_index
    assert "agent" in reg.tag_index["agentic"]
    assert "agent" in reg.tag_index["sdlc"]


def test_registry_add_collection_with_no_tags_creates_no_index_entries() -> None:
    reg = CapabilityRegistry()
    meta = CollectionMeta(
        name="minimal",
        namespace="general_ludd",
        version="0.1.0",
        description="no tags",
        tags=frozenset(),
    )
    reg.add_collection(meta)
    assert "minimal" in reg.collections
    assert reg.tag_index == {}
    assert reg.lookup_by_tag("minimal") == frozenset()


def test_registry_lookup_by_tag_returns_matching_collections() -> None:
    reg = CapabilityRegistry()
    reg.add_collection(CollectionMeta("a", "ns", "0.1", "desc", frozenset(["web", "html"])))
    reg.add_collection(CollectionMeta("b", "ns", "0.1", "desc", frozenset(["web", "css"])))
    reg.add_collection(CollectionMeta("c", "ns", "0.1", "desc", frozenset(["ml", "ai"])))

    assert reg.lookup_by_tag("web") == frozenset(["a", "b"])
    assert reg.lookup_by_tag("html") == frozenset(["a"])
    assert reg.lookup_by_tag("nonexistent") == frozenset()


def test_registry_lookup_by_tag_is_case_sensitive() -> None:
    reg = CapabilityRegistry()
    reg.add_collection(CollectionMeta("x", "ns", "0.1", "desc", frozenset(["Web"])))
    assert reg.lookup_by_tag("web") == frozenset()
    assert reg.lookup_by_tag("Web") == frozenset(["x"])


def test_registry_to_dict_roundtrip() -> None:
    reg = CapabilityRegistry()
    meta_a = CollectionMeta("a", "ns", "0.1", "desc a", frozenset(["tag1"]))
    meta_b = CollectionMeta("b", "ns", "0.2", "desc b", frozenset(["tag2"]))
    reg.add_collection(meta_a)
    reg.add_collection(meta_b)

    data = reg.to_dict()
    assert isinstance(data, dict)
    assert "collections" in data
    assert "tag_index" in data
    collections = data["collections"]
    tag_index = data["tag_index"]
    assert isinstance(collections, dict)
    assert isinstance(collections["a"], dict)
    assert isinstance(collections["b"], dict)
    assert isinstance(tag_index, dict)
    assert collections["a"]["name"] == "a"
    assert collections["b"]["version"] == "0.2"
    assert tag_index["tag1"] == ["a"]
    assert tag_index["tag2"] == ["b"]


def test_registry_from_dict_rebuilds_tag_index() -> None:
    data = {
        "collections": {
            "chemistry": {
                "name": "chemistry",
                "namespace": "general_ludd",
                "version": "0.2.0",
                "description": "chem",
                "tags": ["chemistry", "reactions"],
            },
            "agent": {
                "name": "agent",
                "namespace": "general_ludd",
                "version": "0.2.0",
                "description": "agent",
                "tags": ["agentic", "sdlc"],
            },
        },
        "tag_index": {"chemistry": ["chemistry"], "reactions": ["chemistry"], "agentic": ["agent"], "sdlc": ["agent"]},
    }
    reg = CapabilityRegistry.from_dict(data)
    assert reg.lookup_by_tag("chemistry") == frozenset(["chemistry"])
    assert reg.lookup_by_tag("agentic") == frozenset(["agent"])


# ── discover_capabilities ───────────────────────────────────────────────


def test_discover_from_single_collection() -> None:
    colls = _make_collections_dir()
    _write_galaxy_yml(colls / "general_ludd" / "chemistry", "chemistry", ["chemistry", "reactions", "safety"])

    reg = discover_capabilities(colls)
    assert "chemistry" in reg.collections
    assert reg.lookup_by_tag("chemistry") == frozenset(["chemistry"])
    assert reg.lookup_by_tag("reactions") == frozenset(["chemistry"])


def test_discover_loads_canonical_collection_capabilities(tmp_path: Path) -> None:
    colls = tmp_path / "ansible_collections"
    collection = colls / "general_ludd" / "language"
    _write_galaxy_yml(collection, "language", ["language", "translation"])
    (collection / "capabilities.yml").write_text(
        yaml.safe_dump(
            {
                "model_capabilities": [
                    {
                        "name": "translation",
                        "description": "Translate text",
                        "roles": ["translate"],
                        "quality_class": "high",
                    }
                ],
                "role_capabilities": {"translate": ["translation"]},
            }
        ),
        encoding="utf-8",
    )

    meta = discover_capabilities(colls).collections["language"]
    assert meta.model_capabilities[0]["name"] == "translation"
    assert meta.role_capabilities == {"translate": ["translation"]}


def test_discover_preserves_legacy_inline_collection_capabilities(tmp_path: Path) -> None:
    colls = tmp_path / "ansible_collections"
    collection = colls / "general_ludd" / "legacy"
    _write_galaxy_yml(collection, "legacy", ["legacy"])
    galaxy_path = collection / "galaxy.yml"
    galaxy = yaml.safe_load(galaxy_path.read_text(encoding="utf-8"))
    galaxy["model_capabilities"] = [
        {"name": "legacy_action", "roles": ["legacy_role"]}
    ]
    galaxy["role_capabilities"] = {"legacy_role": ["legacy_action"]}
    galaxy_path.write_text(yaml.safe_dump(galaxy), encoding="utf-8")

    meta = discover_capabilities(colls).collections["legacy"]
    assert len(meta.model_capabilities) == 1
    assert meta.model_capabilities[0]["name"] == "legacy_action"
    assert meta.model_capabilities[0]["roles"] == ["legacy_role"]
    assert meta.role_capabilities == {"legacy_role": ["legacy_action"]}


@pytest.mark.parametrize("contents", ["not: [valid: yaml: }", "- unexpected-list"])
def test_discover_rejects_invalid_capability_extensions(
    tmp_path: Path, contents: str
) -> None:
    colls = tmp_path / "ansible_collections"
    collection = colls / "general_ludd" / "language"
    _write_galaxy_yml(collection, "language", ["language"])
    (collection / "capabilities.yml").write_text(contents, encoding="utf-8")

    meta = discover_capabilities(colls).collections["language"]
    assert meta.tags == frozenset({"language"})
    assert meta.model_capabilities == []
    assert meta.role_capabilities == {}


def test_discover_from_multiple_collections() -> None:
    colls = _make_collections_dir()
    _write_galaxy_yml(colls / "general_ludd" / "web", "web", ["web", "html", "css"])
    _write_galaxy_yml(colls / "general_ludd" / "ai_ml", "ai_ml", ["ai", "ml", "research"])
    _write_galaxy_yml(colls / "general_ludd" / "security", "security", ["security", "audit"])

    reg = discover_capabilities(colls)
    assert len(reg.collections) == 3
    assert reg.lookup_by_tag("web") == frozenset(["web"])
    assert reg.lookup_by_tag("ml") == frozenset(["ai_ml"])
    assert reg.lookup_by_tag("security") == frozenset(["security"])


def test_discover_skips_missing_galaxy_yml() -> None:
    colls = _make_collections_dir()
    _write_galaxy_yml(colls / "general_ludd" / "web", "web", ["web"])
    # Create an empty directory without galaxy.yml
    (colls / "general_ludd" / "no_galaxy").mkdir(parents=True, exist_ok=True)
    (colls / "not_a_collection").mkdir(parents=True, exist_ok=True)

    reg = discover_capabilities(colls)
    assert len(reg.collections) == 1
    assert "web" in reg.collections


def test_discover_skips_malformed_yaml() -> None:
    colls = _make_collections_dir()
    _write_galaxy_yml(colls / "general_ludd" / "good", "good", ["good"])
    bad_dir = colls / "general_ludd" / "bad"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "galaxy.yml").write_text("not: [valid: yaml: }}}")

    reg = discover_capabilities(colls)
    assert len(reg.collections) == 1
    assert "good" in reg.collections


def test_discover_empty_directory() -> None:
    colls = _make_collections_dir()
    reg = discover_capabilities(colls)
    assert reg.collections == {}


def test_discover_with_custom_collections_root() -> None:
    colls = _make_collections_dir()
    _write_galaxy_yml(colls / "general_ludd" / "custom", "custom", ["custom-tag"])

    reg = discover_capabilities(colls_root=colls)
    assert "custom" in reg.collections
    assert reg.lookup_by_tag("custom-tag") == frozenset(["custom"])


def test_discover_indexes_roles() -> None:
    colls = _make_collections_dir()
    _write_galaxy_yml(colls / "general_ludd" / "agent", "agent", ["agentic", "sdlc", "automation"])
    role_dir = colls / "general_ludd" / "agent" / "roles" / "agent_task" / "meta"
    _write_role_meta(role_dir, "Full agent task runner", role_name="agent_task")

    reg = discover_capabilities(colls)
    assert "agent" in reg.collections
    meta = reg.collections["agent"]
    assert len(meta.roles) == 1
    assert meta.roles[0]["name"] == "agent_task"
    assert "agent task" in meta.roles[0]["description"].lower()


def test_discover_collection_without_namespace_subdir() -> None:
    """Directories that don't follow the namespace/collection pattern are skipped."""
    colls = _make_collections_dir()
    (colls / "orphan_no_namespace").mkdir(parents=True, exist_ok=True)
    (colls / "orphan_no_namespace" / "galaxy.yml").write_text(
        yaml.dump(
            {
                "namespace": "unknown",
                "name": "orphan",
                "version": "0.1.0",
                "tags": ["test"],
            }
        )
    )

    reg = discover_capabilities(colls)
    assert "orphan" not in reg.collections


# ── CapabilityRouter ────────────────────────────────────────────────────


def _make_sample_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.add_collection(
        CollectionMeta("agent", "general_ludd", "0.2.0", "agent collection", frozenset(["agentic", "sdlc"]))
    )
    reg.add_collection(
        CollectionMeta("chemistry", "general_ludd", "0.2.0", "chemistry expert", frozenset(["chemistry", "reactions"]))
    )
    reg.add_collection(CollectionMeta("web", "general_ludd", "0.1.0", "web collection", frozenset(["web", "html"])))
    reg.add_collection(CollectionMeta("ai_ml", "general_ludd", "0.2.0", "ai/ml", frozenset(["ai", "ml", "research"])))
    reg.add_collection(
        CollectionMeta("language", "general_ludd", "0.1.0", "language", frozenset(["language", "unicode", "text"]))
    )
    return reg


def test_router_finds_single_match_by_capability() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    result = router.route(capability="chemistry", payload={"task": "balance equation"})
    assert result.ok is True
    assert len(result.matches) == 1
    assert result.matches[0].name == "chemistry"
    assert result.payload == {"task": "balance equation"}


def test_router_finds_multiple_matches_by_capability() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    result = router.route(capability="research", payload={"query": "find tools"})
    assert result.ok is True
    assert len(result.matches) == 1
    assert "ai_ml" in {m.name for m in result.matches}


def test_router_no_match_for_unknown_capability() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    result = router.route(capability="nonexistent", payload={})
    assert result.ok is False
    assert len(result.matches) == 0
    assert result.error is not None
    assert "nonexistent" in result.error


def test_router_empty_registry_always_no_match() -> None:
    reg = CapabilityRegistry()
    router = CapabilityRouter(reg)
    result = router.route(capability="anything", payload={})
    assert result.ok is False
    assert len(result.matches) == 0


def test_router_route_by_collection_name_finds_direct_match() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    result = router.route_by_collection(collection="agent", payload={})
    assert result.ok is True
    assert len(result.matches) == 1
    assert result.matches[0].name == "agent"


def test_router_route_by_collection_missing() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    result = router.route_by_collection(collection="bogus", payload={})
    assert result.ok is False
    assert result.error is not None
    assert "bogus" in result.error


def test_router_capability_matches_across_collections() -> None:
    """When a capability appears in multiple collections (e.g., 'research' across
    ai_ml and chemistry), the router returns all matches."""
    reg = CapabilityRegistry()
    reg.add_collection(CollectionMeta("ai_ml", "general_ludd", "0.2.0", "ai", frozenset(["research", "ai"])))
    reg.add_collection(
        CollectionMeta("chemistry", "general_ludd", "0.2.0", "chem", frozenset(["research", "chemistry"]))
    )
    router = CapabilityRouter(reg)
    result = router.route(capability="research", payload={})
    assert result.ok is True
    assert len(result.matches) == 2
    names = {m.name for m in result.matches}
    assert names == {"ai_ml", "chemistry"}


def test_router_preserves_payload_structure() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    payload: dict[str, Any] = {
        "task": "write e2e tests",
        "options": {"framework": "pytest", "timeout": 300},
        "tags": ["urgent", "ci-blocker"],
    }
    result = router.route(capability="agentic", payload=payload)
    assert result.ok is True
    assert result.payload == payload


def test_router_get_collection_meta_existing() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    meta = router.get_collection("agent")
    assert meta is not None
    assert meta.name == "agent"
    assert meta.version == "0.2.0"


def test_router_get_collection_meta_missing() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    assert router.get_collection("nope") is None


def test_router_list_all_capabilities() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    caps = router.list_capabilities()
    assert "agentic" in caps
    assert "chemistry" in caps
    assert "web" in caps
    assert "ai" in caps
    assert "language" in caps


def test_router_route_with_empty_capability_string() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    result = router.route(capability="", payload={})
    assert result.ok is False
    assert result.error is not None


def test_router_route_with_none_payload_defaults_to_empty() -> None:
    reg = _make_sample_registry()
    router = CapabilityRouter(reg)
    result = router.route(capability="agentic", payload=None)
    assert result.ok is True
    assert result.payload == {}


def test_route_match_repr() -> None:
    meta = CollectionMeta("agent", "general_ludd", "0.2.0", "agent desc", frozenset(["agentic"]))
    match = RouteResult.RouteMatch(collection=meta, score=1.0)
    r = repr(match)
    assert "agent" in r
    assert "general_ludd" in r


# ── Integration: discover + route ───────────────────────────────────────


def test_end_to_end_discover_then_route() -> None:
    colls = _make_collections_dir()
    _write_galaxy_yml(colls / "general_ludd" / "agent", "agent", ["agentic", "sdlc", "automation"])
    _write_galaxy_yml(colls / "general_ludd" / "web", "web", ["web", "html", "css"])
    _write_galaxy_yml(colls / "general_ludd" / "chemistry", "chemistry", ["chemistry", "reactions", "safety"])

    reg = discover_capabilities(colls)
    router = CapabilityRouter(reg)

    r1 = router.route(capability="web", payload={"url": "https://example.com"})
    assert r1.ok is True
    assert len(r1.matches) == 1
    assert r1.matches[0].name == "web"

    r2 = router.route(capability="chemistry", payload={"formula": "H2O"})
    assert r2.ok is True
    assert r2.matches[0].name == "chemistry"

    r3 = router.route(capability="security", payload={})
    assert r3.ok is False

    caps = router.list_capabilities()
    assert "agentic" in caps
    assert "web" in caps
    assert "chemistry" in caps


def test_end_to_end_capability_resolves_ambiguous() -> None:
    """When a tag like 'security' exists in multiple collections, route returns all."""
    colls = _make_collections_dir()
    _write_galaxy_yml(colls / "general_ludd" / "security", "security", ["security", "audit"])
    _write_galaxy_yml(
        colls / "general_ludd" / "binary_re", "binary_re", ["security", "reverse-engineering", "debugging"]
    )

    reg = discover_capabilities(colls)
    router = CapabilityRouter(reg)

    result = router.route(capability="security", payload={})
    assert result.ok is True
    assert len(result.matches) == 2


def test_end_to_end_discover_real_collections() -> None:
    """Validate against the real collections directory (if it exists)."""
    project_root = Path(__file__).resolve().parents[2]
    colls_root = project_root / "collections" / "ansible_collections"

    if not colls_root.is_dir():
        pytest.skip("Real collections directory not found")

    reg = discover_capabilities(colls_root=colls_root)

    assert len(reg.collections) >= 15
    assert "agent" in reg.collections
    assert "chemistry" in reg.collections
    assert "web" in reg.collections
    assert "ai_ml" in reg.collections
    assert "travel" in reg.collections

    assert len(reg.tag_index) > 0
    assert "travel" in reg.tag_index
    assert "flights" in reg.tag_index
    assert "hotels" in reg.tag_index
    assert "itinerary" in reg.tag_index
    assert "planning" in reg.tag_index

    for name, meta in reg.collections.items():
        assert meta.name == name
        assert meta.namespace == "general_ludd"
        assert isinstance(meta.tags, (set, frozenset))
