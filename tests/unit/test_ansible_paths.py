"""Tests for ansible/paths: path resolution, env building, version scanning, activation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from general_ludd.ansible.paths import (
    CollectionsPathEntry,
    CollectionVersionInfo,
    _bundled_collections_root,
    _semver_key,
    _split_fqcn,
    _user_collections_root,
    activate_collection_version,
    find_resource,
    list_all_collections,
    list_collection_versions,
    resolve_collection_version,
    resolve_collections_paths,
    scan_collection_versions,
    to_ansible_cfg,
    to_ansible_env,
)


class TestCollectionsPathEntry:
    def test_construction(self):
        entry = CollectionsPathEntry(source="project", path=Path("/tmp"), precedence=0)
        assert entry.source == "project"
        assert entry.path == Path("/tmp")
        assert entry.precedence == 0

    def test_frozen(self):
        entry = CollectionsPathEntry(source="project", path=Path("/tmp"), precedence=0)
        with pytest.raises(FrozenInstanceError):
            entry.source = "user"  # type: ignore[misc]


class TestUserCollectionsRoot:
    def test_xdg_env_set(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
        result = _user_collections_root()
        assert result == Path("/custom/xdg/gludd/collections")

    def test_home_fallback(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        result = _user_collections_root()
        assert str(result).endswith("gludd/collections")
        assert ".config" in str(result)


class TestBundledCollectionsRoot:
    def test_returns_path(self):
        result = _bundled_collections_root()
        assert result.name == "collections"

    def test_frozen_runtime_uses_pyinstaller_bundle_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)

        assert _bundled_collections_root() == tmp_path / "collections"


class TestResolveCollectionsPaths:
    def test_always_includes_bundled(self):
        entries = resolve_collections_paths()
        sources = [e.source for e in entries]
        assert "bundled" in sources

    def test_missing_project_skipped(self, tmp_path):
        entries = resolve_collections_paths(project_root=tmp_path / "nonexistent")
        sources = [e.source for e in entries]
        assert "project" not in sources

    def test_project_included_when_exists(self, tmp_path):
        proj_col = tmp_path / ".gludd" / "collections"
        proj_col.mkdir(parents=True)
        entries = resolve_collections_paths(project_root=tmp_path)
        sources = [e.source for e in entries]
        assert "project" in sources

    def test_user_included_when_exists(self, tmp_path, monkeypatch):
        user_col = tmp_path / "user-gludd" / "gludd" / "collections"
        user_col.mkdir(parents=True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-gludd"))
        entries = resolve_collections_paths()
        sources = [e.source for e in entries]
        assert "user" in sources

    def test_precedence_order(self, tmp_path):
        proj_col = tmp_path / ".gludd" / "collections"
        proj_col.mkdir(parents=True)
        user_col = tmp_path / "xdg" / "gludd" / "collections"
        user_col.mkdir(parents=True)
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        entries = resolve_collections_paths(project_root=tmp_path)
        precs = [(e.source, e.precedence) for e in entries]
        proj_prec = next(p for s, p in precs if s == "project")
        user_prec = next(p for s, p in precs if s == "user")
        bundled_prec = next(p for s, p in precs if s == "bundled")
        assert proj_prec < user_prec < bundled_prec


class TestToAnsibleEnv:
    def test_basic(self):
        entries = [
            CollectionsPathEntry("project", Path("/proj/col"), 0),
            CollectionsPathEntry("bundled", Path("/bundled/col"), 1),
        ]
        env = to_ansible_env(entries)
        assert "ANSIBLE_COLLECTIONS_PATH" in env
        assert "ANSIBLE_ROLES_PATH" in env
        assert env["ANSIBLE_COLLECTIONS_PATH"].startswith("/proj/col")

    def test_preserves_existing_env(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", "/existing/col")
        entries = [CollectionsPathEntry("bundled", Path("/bundled/col"), 0)]
        env = to_ansible_env(entries)
        assert "/existing/col" in env["ANSIBLE_COLLECTIONS_PATH"]

    def test_no_duplicates(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", "/bundled/col")
        entries = [CollectionsPathEntry("bundled", Path("/bundled/col"), 0)]
        env = to_ansible_env(entries)
        assert env["ANSIBLE_COLLECTIONS_PATH"].count("/bundled/col") == 1


class TestToAnsibleCfg:
    def test_single_entry(self):
        entries = [CollectionsPathEntry("bundled", Path("/bundled"), 0)]
        cfg = to_ansible_cfg(entries)
        assert cfg == "collections_path = /bundled"

    def test_multiple_entries(self):
        entries = [
            CollectionsPathEntry("project", Path("/proj"), 0),
            CollectionsPathEntry("bundled", Path("/bundled"), 1),
        ]
        cfg = to_ansible_cfg(entries)
        assert "/proj" in cfg
        assert "/bundled" in cfg


class TestSplitFqcn:
    def test_valid_fqcn(self):
        result = _split_fqcn("general_ludd.agent.project_init")
        assert result == ("general_ludd", "agent", "project_init")

    def test_valid_fqcn_with_dots_in_resource(self):
        result = _split_fqcn("ns.coll.sub.resource")
        assert result == ("ns", "coll", "sub.resource")

    def test_too_short(self):
        assert _split_fqcn("general_ludd") is None

    def test_two_parts(self):
        assert _split_fqcn("general_ludd.agent") is None


class TestFindResource:
    def test_role_exists(self, tmp_path):
        role_dir = tmp_path / "ansible_collections" / "ns" / "coll" / "roles" / "my_role"
        role_dir.mkdir(parents=True)
        entries = [CollectionsPathEntry("test", tmp_path, 0)]
        result = find_resource("ns.coll.my_role", entries)
        assert result == role_dir

    def test_module_exists(self, tmp_path):
        mod_dir = tmp_path / "ansible_collections" / "ns" / "coll" / "plugins" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "my_module.py").touch()
        entries = [CollectionsPathEntry("test", tmp_path, 0)]
        result = find_resource("ns.coll.my_module", entries)
        assert result == mod_dir / "my_module.py"

    def test_not_found(self, tmp_path):
        entries = [CollectionsPathEntry("test", tmp_path, 0)]
        assert find_resource("ns.coll.missing", entries) is None

    def test_invalid_fqcn_returns_none(self, tmp_path):
        entries = [CollectionsPathEntry("test", tmp_path, 0)]
        assert find_resource("bad", entries) is None

    def test_precedence_first_tier_wins(self, tmp_path):
        tier1 = tmp_path / "tier1"
        tier2 = tmp_path / "tier2"
        (tier1 / "ansible_collections" / "ns" / "coll" / "roles" / "r").mkdir(parents=True)
        (tier2 / "ansible_collections" / "ns" / "coll" / "roles" / "r").mkdir(parents=True)
        entries = [
            CollectionsPathEntry("tier1", tier1, 0),
            CollectionsPathEntry("tier2", tier2, 1),
        ]
        result = find_resource("ns.coll.r", entries)
        assert result is not None
        assert str(tier1) in str(result)


class TestCollectionVersionInfo:
    def test_semver_detection(self):
        info = CollectionVersionInfo("ns", "coll", "1.2.3", Path("/tmp"))
        assert info.is_semver is True
        assert info.is_latest is False

    def test_latest_detection(self):
        info = CollectionVersionInfo("ns", "coll", "latest", Path("/tmp"))
        assert info.is_latest is True
        assert info.is_semver is False

    def test_prerelease_semver(self):
        info = CollectionVersionInfo("ns", "coll", "1.0.0-alpha.1", Path("/tmp"))
        assert info.is_semver is True

    def test_non_semver_tag(self):
        info = CollectionVersionInfo("ns", "coll", "beta.2", Path("/tmp"))
        assert info.is_semver is False
        assert info.is_latest is False


class TestScanCollectionVersions:
    def test_empty_when_no_dir(self, tmp_path):
        result = scan_collection_versions(tmp_path)
        assert result == []

    def test_scans_versioned_dirs(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        coll_dir = ac / "general_ludd@1.0.0" / "agent"
        coll_dir.mkdir(parents=True)
        result = scan_collection_versions(tmp_path)
        assert len(result) == 1
        assert result[0].namespace == "general_ludd"
        assert result[0].collection == "agent"
        assert result[0].version == "1.0.0"

    def test_filters_by_namespace(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns_a@1.0" / "coll").mkdir(parents=True)
        (ac / "ns_b@1.0" / "coll").mkdir(parents=True)
        result = scan_collection_versions(tmp_path, namespace="ns_a")
        assert len(result) == 1
        assert result[0].namespace == "ns_a"

    def test_filters_by_collection(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@1.0" / "coll_a").mkdir(parents=True)
        (ac / "ns@1.0" / "coll_b").mkdir(parents=True)
        result = scan_collection_versions(tmp_path, namespace="ns", collection="coll_a")
        assert len(result) == 1
        assert result[0].collection == "coll_a"


class TestSemverKey:
    def test_semver_sorts_higher(self):
        assert _semver_key("1.0.0") < _semver_key("latest")

    def test_lower_semver_after_higher(self):
        k1 = _semver_key("2.0.0")
        k2 = _semver_key("1.0.0")
        assert k1 < k2

    def test_non_semver_after_semver(self):
        assert _semver_key("beta") > _semver_key("1.0.0")


class TestListCollectionVersions:
    def test_returns_sorted_unique(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@2.0.0" / "coll").mkdir(parents=True)
        (ac / "ns@1.0.0" / "coll").mkdir(parents=True)
        (ac / "ns@latest" / "coll").mkdir(parents=True)
        versions = list_collection_versions(tmp_path, "ns")
        assert versions[0] == "2.0.0"
        assert "latest" in versions[-1]


class TestResolveCollectionVersion:
    def test_exact_match(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        target = ac / "ns@2.0.0" / "coll"
        target.mkdir(parents=True)
        (ac / "ns@1.0.0" / "coll").mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "ns", "coll", requested_version="2.0.0")
        assert result == target

    def test_latest_preference(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        latest = ac / "ns@latest" / "coll"
        latest.mkdir(parents=True)
        (ac / "ns@1.0.0" / "coll").mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "ns", "coll")
        assert result == latest

    def test_bare_fallback(self, tmp_path):
        bare = tmp_path / "ansible_collections" / "ns" / "coll"
        bare.mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "ns", "coll")
        assert result == bare

    def test_highest_semver_fallback(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@2.0.0" / "coll").mkdir(parents=True)
        (ac / "ns@1.0.0" / "coll").mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "ns", "coll")
        assert "2.0.0" in str(result)

    def test_none_when_no_match(self, tmp_path):
        result = resolve_collection_version(tmp_path, "ns", "coll")
        assert result is None

    def test_exact_match_not_found_returns_none(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@1.0.0" / "coll").mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "ns", "coll", requested_version="9.9.9")
        assert result is None


class TestActivateCollectionVersion:
    def test_creates_symlink(self, tmp_path):
        ac = tmp_path / "base" / "ansible_collections"
        resolved = ac / "ns@1.0.0" / "coll"
        resolved.mkdir(parents=True)
        activation_root, _cleanup = activate_collection_version(
            tmp_path / "base", "ns", "coll"
        )
        link = activation_root / "ansible_collections" / "ns" / "coll"
        assert link.is_symlink() or link.exists()

    def test_raises_when_no_collection_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No collection found"):
            activate_collection_version(tmp_path, "ns", "coll")

    def test_supplied_temp_dir(self, tmp_path):
        ac = tmp_path / "base" / "ansible_collections"
        (ac / "ns@latest" / "coll").mkdir(parents=True)
        temp = tmp_path / "my-activation"
        temp.mkdir()
        activation_root, cleanup = activate_collection_version(
            tmp_path / "base", "ns", "coll", temp_dir=temp
        )
        assert cleanup is None
        assert activation_root == temp


class TestListAllCollections:
    def test_empty_when_no_dir(self, tmp_path):
        assert list_all_collections(tmp_path) == []

    def test_lists_collections(self, tmp_path):
        ns_dir = tmp_path / "ansible_collections" / "general_ludd"
        ns_dir.mkdir(parents=True)
        (ns_dir / "agent").mkdir()
        (ns_dir / "runner").mkdir()
        result = list_all_collections(tmp_path)
        assert result == ["agent", "runner"]

    def test_custom_namespace(self, tmp_path):
        ns_dir = tmp_path / "ansible_collections" / "custom_ns"
        ns_dir.mkdir(parents=True)
        (ns_dir / "my_coll").mkdir()
        result = list_all_collections(tmp_path, namespace="custom_ns")
        assert result == ["my_coll"]
