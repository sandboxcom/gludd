"""Verify the collection split — galaxy.yml files, moved roles correct."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
COLLECTIONS_ROOT = ROOT / "collections"
NS_DIR = COLLECTIONS_ROOT / "ansible_collections" / "general_ludd"

EXPECTED_COLLECTIONS = sorted(
    [
        "agent",
        "ai_ml",
        "azure",
        "behavioral",
        "binary_re",
        "business",
        "chat",
        "chemistry",
        "e2e_test_gen",
        "forensics",
        "formal",
        "git_release",
        "governance",
        "infrastructure",
        "language",
        "materials",
        "networking",
        "operations",
        "os_expert",
        "physics",
        "radio",
        "sandbox",
        "security",
        "travel",
        "web",
        "web_server",
        "xml",
    ]
)

AGENT_CORE_ROLES = {"task_splitter", "agent_orchestrate", "implement_change"}

ROLES_MOVED_OUT = {
    "tla_check",
    "tla_parse",
    "tla_pluscal",
    "tla_scaffold",
    "tla_trace_interpret",
    "entity_research",
}


def _bundle_import() -> Path:
    from general_ludd.ansible.paths import _bundled_collections_root

    return _bundled_collections_root()


def _agent_role_names() -> set[str]:
    agent_roles = NS_DIR / "agent" / "roles"
    if not agent_roles.is_dir():
        return set()
    return {d.name for d in agent_roles.iterdir() if d.is_dir()}


def _collection_galaxy_paths() -> dict[str, Path]:
    return {c: NS_DIR / c / "galaxy.yml" for c in EXPECTED_COLLECTIONS}


class TestCollectionGalaxyFiles:
    def test_all_collections_have_galaxy_yml(self):
        for name, path in _collection_galaxy_paths().items():
            assert path.is_file(), f"Collection '{name}' missing galaxy.yml at {path}"

    def test_no_extra_collection_dirs(self):
        actual = sorted(d.name for d in NS_DIR.iterdir() if d.is_dir())
        assert actual == EXPECTED_COLLECTIONS, f"Expected collection dirs {EXPECTED_COLLECTIONS}, got {actual}"


class TestMovedRolesInDestination:
    def test_formal_has_tla_roles(self):
        formal_roles = NS_DIR / "formal" / "roles"
        assert formal_roles.is_dir(), "formal/roles directory missing"
        names = {d.name for d in formal_roles.iterdir() if d.is_dir()}
        expected = {
            "tla_check",
            "tla_parse",
            "tla_pluscal",
            "tla_scaffold",
            "tla_trace_interpret",
        }
        assert names == expected, f"formal roles mismatch: {names}"

    def test_business_has_entity_research(self):
        business_roles = NS_DIR / "business" / "roles"
        assert business_roles.is_dir(), "business/roles directory missing"
        names = {d.name for d in business_roles.iterdir() if d.is_dir()}
        assert "entity_research" in names, f"entity_research missing from business roles: {names}"

    def test_empty_collections_have_roles_dir(self):
        for coll in ["security", "infrastructure", "networking"]:
            roles_dir = NS_DIR / coll / "roles"
            assert roles_dir.is_dir(), f"Collection '{coll}' missing roles directory at {roles_dir}"


class TestListAllCollections:
    def test_returns_all_collections(self, monkeypatch):
        from general_ludd.ansible import paths as paths_mod

        monkeypatch.setattr(
            paths_mod,
            "_bundled_collections_root",
            lambda: COLLECTIONS_ROOT,
            raising=False,
        )
        result = paths_mod.list_all_collections(
            COLLECTIONS_ROOT,
            namespace="general_ludd",
        )
        assert sorted(result) == EXPECTED_COLLECTIONS, (
            f"list_all_collections: expected {EXPECTED_COLLECTIONS}, got {result}"
        )

    def test_returns_empty_for_missing_namespace(self):
        from general_ludd.ansible.paths import list_all_collections

        result = list_all_collections(
            Path("/nonexistent"),
            namespace="general_ludd",
        )
        assert result == [], f"Expected empty list for missing dir, got {result}"


class TestAgentCoreRolesPresent:
    def test_core_roles_exist(self):
        names = _agent_role_names()
        missing = AGENT_CORE_ROLES - names
        assert not missing, f"Agent collection missing core roles: {missing}"

    def test_agent_has_many_roles(self):
        names = _agent_role_names()
        assert len(names) >= 100, f"Expected 100+ roles in agent collection, found {len(names)}"


class TestMovedRolesNotInAgent:
    def test_moved_roles_absent_from_agent(self):
        names = _agent_role_names()
        still_present = ROLES_MOVED_OUT & names
        assert not still_present, f"Roles should be moved out of agent but still present: {still_present}"


class TestIntegration:
    def test_bundled_root_points_at_real_collections(self):
        bundled = _bundle_import()
        ac = bundled / "ansible_collections" / "general_ludd"
        assert ac.is_dir(), f"_bundled_collections_root resolved to {bundled}, but {ac} does not exist"

    def test_galaxy_yml_parsable_as_yaml(self):
        import yaml

        for name, path in _collection_galaxy_paths().items():
            with open(path) as fh:
                data = yaml.safe_load(fh)
            assert isinstance(data, dict), f"galaxy.yml for '{name}' is not a dict: {type(data)}"
            assert "namespace" in data, f"galaxy.yml for '{name}' missing 'namespace' key"
            assert "name" in data, f"galaxy.yml for '{name}' missing 'name' key"
