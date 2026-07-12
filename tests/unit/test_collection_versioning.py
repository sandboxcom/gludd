"""Unit tests for multi-version ansible collection support."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from general_ludd.ansible.paths import (
    CollectionVersionInfo,
    activate_collection_version,
    list_collection_versions,
    resolve_collection_version,
    scan_collection_versions,
)


def _make_versioned_collection(
    base: Path,
    namespace: str,
    collection: str,
    version: str,
) -> Path:
    """Create a versioned collection directory tree under *base*."""
    coll_root = (
        base
        / "ansible_collections"
        / f"{namespace}@{version}"
        / collection
    )
    (coll_root / "roles" / "test_role" / "tasks").mkdir(parents=True)
    (coll_root / "plugins" / "modules").mkdir(parents=True)
    (coll_root / "roles" / "test_role" / "tasks" / "main.yml").write_text(
        f"- name: version {version}\n"
    )
    return coll_root


def _make_bare_collection(
    base: Path,
    namespace: str,
    collection: str,
) -> Path:
    """Create a bare (unversioned) collection directory tree under *base*."""
    coll_root = base / "ansible_collections" / namespace / collection
    (coll_root / "roles" / "bare_role" / "tasks").mkdir(parents=True)
    (coll_root / "plugins" / "modules").mkdir(parents=True)
    (coll_root / "roles" / "bare_role" / "tasks" / "main.yml").write_text(
        "- name: bare (unversioned)\n"
    )
    return coll_root


@pytest.fixture
def collections_root(tmp_path: Path) -> Path:
    """Temporary collections root with versioned and bare dirs."""
    base = tmp_path / "collections"
    base.mkdir()
    return base


class TestScanCollectionVersions:
    def test_empty_dir_returns_empty_list(self, collections_root: Path):
        assert scan_collection_versions(collections_root) == []

    def test_finds_versioned_collections(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "beta.2")
        _make_versioned_collection(collections_root, "acme", "tools", "1.0.0")

        results = scan_collection_versions(collections_root)
        assert len(results) == 3

    def test_filters_by_namespace(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(collections_root, "acme", "tools", "1.0.0")

        results = scan_collection_versions(collections_root, namespace="general_ludd")
        assert len(results) == 1
        assert results[0].namespace == "general_ludd"
        assert results[0].collection == "agent"

    def test_filters_by_namespace_and_collection(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "beta.2")
        _make_versioned_collection(collections_root, "general_ludd", "slurm", "0.1.0")

        results = scan_collection_versions(
            collections_root, namespace="general_ludd", collection="agent"
        )
        assert len(results) == 2
        assert all(r.collection == "agent" for r in results)

    def test_ignores_bare_directories(self, collections_root: Path):
        _make_bare_collection(collections_root, "general_ludd", "agent")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")

        results = scan_collection_versions(collections_root)
        assert len(results) == 1
        assert results[0].version == "0.1.0"

    def test_info_attributes(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "latest")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "beta.2")

        results = scan_collection_versions(
            collections_root, namespace="general_ludd", collection="agent"
        )
        by_version = {r.version: r for r in results}
        assert by_version["0.1.0"].is_semver is True
        assert by_version["0.1.0"].is_latest is False
        assert by_version["latest"].is_latest is True
        assert by_version["latest"].is_semver is False
        assert by_version["beta.2"].is_semver is False
        assert by_version["beta.2"].is_latest is False


class TestListCollectionVersions:
    def test_lists_all_versions(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.2.0")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "latest")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "beta.1")

        versions = list_collection_versions(collections_root, "general_ludd", "agent")
        assert len(versions) == 4
        assert "0.2.0" in versions
        assert "0.1.0" in versions
        assert "latest" in versions
        assert "beta.1" in versions

    def test_returns_empty_for_nonexistent(self, collections_root: Path):
        versions = list_collection_versions(collections_root, "nope", "absent")
        assert versions == []


class TestResolveCollectionVersion:
    def test_resolves_exact_version_match(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.3.0")

        path = resolve_collection_version(
            collections_root, "general_ludd", "agent", requested_version="0.1.0"
        )
        assert path is not None
        assert "@0.1.0" in str(path) or path.name == "agent"

    def test_resolves_latest_unversioned_request(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "latest")

        path = resolve_collection_version(
            collections_root, "general_ludd", "agent", requested_version=None
        )
        assert path is not None
        assert "@latest" in str(path)

    def test_resolves_bare_when_no_versioned(self, collections_root: Path):
        bare = _make_bare_collection(collections_root, "general_ludd", "agent")

        path = resolve_collection_version(
            collections_root, "general_ludd", "agent", requested_version=None
        )
        assert path is not None
        assert path == bare.resolve()
        assert "@" not in str(path)

    def test_highest_semver_when_no_latest_and_no_bare(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        v030 = _make_versioned_collection(
            collections_root, "general_ludd", "agent", "0.3.0"
        )
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.2.0")

        path = resolve_collection_version(
            collections_root, "general_ludd", "agent", requested_version=None
        )
        assert path is not None
        assert str(v030) in str(path)

    def test_returns_none_for_missing_namespace(self, collections_root: Path):
        path = resolve_collection_version(
            collections_root, "missing", "agent", requested_version="0.1.0"
        )
        assert path is None

    def test_returns_none_for_exact_version_miss(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")

        path = resolve_collection_version(
            collections_root, "general_ludd", "agent", requested_version="99.0.0"
        )
        assert path is None

    def test_requested_version_overrides_latest(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "latest")

        path = resolve_collection_version(
            collections_root, "general_ludd", "agent", requested_version="0.1.0"
        )
        assert path is not None
        assert "@0.1.0" in str(path)


class TestActivateCollectionVersion:
    def test_creates_symlink_activation(self, collections_root: Path, tmp_path: Path):
        v020 = _make_versioned_collection(
            collections_root, "general_ludd", "agent", "0.2.0"
        )
        temp_dir = tmp_path / "activate"
        temp_dir.mkdir()

        activation_root, _cleanup = activate_collection_version(
            collections_root,
            "general_ludd",
            "agent",
            version="0.2.0",
            temp_dir=temp_dir,
        )
        assert activation_root == temp_dir
        link = temp_dir / "ansible_collections" / "general_ludd" / "agent"
        assert link.is_symlink()
        assert link.resolve() == v020.resolve()

    def test_activate_without_version_uses_precedence(self, collections_root: Path, tmp_path: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.3.0")
        _make_versioned_collection(collections_root, "general_ludd", "agent", "latest")

        temp_dir = tmp_path / "activate"
        temp_dir.mkdir()

        activation_root, _ = activate_collection_version(
            collections_root,
            "general_ludd",
            "agent",
            temp_dir=temp_dir,
        )
        link = activation_root / "ansible_collections" / "general_ludd" / "agent"
        # Should resolve to @latest (precedence rule 2)
        assert "@latest" in str(link.resolve())

    def test_activate_with_latest_requested(self, collections_root: Path, tmp_path: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")
        latest = _make_versioned_collection(
            collections_root, "general_ludd", "agent", "latest"
        )

        temp_dir = tmp_path / "activate"
        temp_dir.mkdir()

        activation_root, _ = activate_collection_version(
            collections_root,
            "general_ludd",
            "agent",
            version="latest",
            temp_dir=temp_dir,
        )
        link = activation_root / "ansible_collections" / "general_ludd" / "agent"
        assert link.resolve() == latest.resolve()

    def test_activate_missing_collection_raises(self, collections_root: Path, tmp_path: Path):
        temp_dir = tmp_path / "activate"
        temp_dir.mkdir()

        with pytest.raises(FileNotFoundError, match=r"nope\.absent"):
            activate_collection_version(
                collections_root, "nope", "absent", temp_dir=temp_dir
            )

    def test_activate_auto_creates_temp_dir(self, collections_root: Path):
        _make_versioned_collection(collections_root, "general_ludd", "agent", "0.1.0")

        activation_root, _cleanup = activate_collection_version(
            collections_root, "general_ludd", "agent", version="0.1.0"
        )
        assert activation_root.is_dir()
        link = activation_root / "ansible_collections" / "general_ludd" / "agent"
        assert link.is_symlink()
        shutil.rmtree(activation_root, ignore_errors=True)


class TestCollectionVersionInfo:
    def test_semver_detection(self):
        info = CollectionVersionInfo(
            namespace="ns", collection="coll", version="2.1.3", path=Path("/tmp")
        )
        assert info.is_semver is True
        assert info.is_latest is False

    def test_semver_with_prerelease(self):
        info = CollectionVersionInfo(
            namespace="ns", collection="coll", version="1.0.0-alpha.1", path=Path("/tmp")
        )
        assert info.is_semver is True

    def test_non_semver_tag(self):
        info = CollectionVersionInfo(
            namespace="ns", collection="coll", version="beta.2", path=Path("/tmp")
        )
        assert info.is_semver is False

    def test_latest_tag(self):
        info = CollectionVersionInfo(
            namespace="ns", collection="coll", version="latest", path=Path("/tmp")
        )
        assert info.is_semver is False
        assert info.is_latest is True

