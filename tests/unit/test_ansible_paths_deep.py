"""Deep edge-case tests for ansible/paths — mutation, encoding, ordering, and boundary conditions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from general_ludd.ansible.paths import (
    CollectionsPathEntry,
    CollectionsPathMutationError,
    CollectionVersionInfo,
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

# ---------------------------------------------------------------------------
# CollectionsPathEntry — mutation edge cases
# ---------------------------------------------------------------------------


class TestCollectionsPathEntryDeep:
    def test_delattr_raises(self):
        entry = CollectionsPathEntry(source="p", path=Path("/"), precedence=0)
        with pytest.raises(CollectionsPathMutationError, match="cannot delete"):
            del entry.source

    def test_setattr_after_construction_raises(self):
        entry = CollectionsPathEntry(source="p", path=Path("/"), precedence=0)
        with pytest.raises(CollectionsPathMutationError):
            entry.path = Path("/other")

    def test_slots_prevents_new_attributes(self):
        entry = CollectionsPathEntry(source="p", path=Path("/"), precedence=0)
        assert not hasattr(entry, "extra")
        with pytest.raises(AttributeError):
            object.__setattr__(entry, "extra", 42)

    def test_empty_source(self):
        entry = CollectionsPathEntry(source="", path=Path("/tmp"), precedence=0)
        assert entry.source == ""

    def test_negative_precedence(self):
        entry = CollectionsPathEntry(source="z", path=Path("/z"), precedence=-5)
        assert entry.precedence == -5

    def test_multiple_mutations_raises_sequentially(self):
        entry = CollectionsPathEntry(source="p", path=Path("/"), precedence=0)
        with pytest.raises(CollectionsPathMutationError):
            entry.source = "x"
        with pytest.raises(CollectionsPathMutationError):
            entry.precedence = 99

    def test_mutation_error_is_valueerror(self):
        entry = CollectionsPathEntry(source="p", path=Path("/"), precedence=0)
        with pytest.raises(ValueError):
            entry.source = "x"


# ---------------------------------------------------------------------------
# _user_collections_root — boundary cases
# ---------------------------------------------------------------------------


class TestUserCollectionsRootDeep:
    def test_xdg_empty_string(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "")
        result = _user_collections_root()
        assert str(result).endswith("gludd/collections")

    def test_xdg_relative_path(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/cfg")
        result = _user_collections_root()
        assert str(result).endswith("relative/cfg/gludd/collections")


# ---------------------------------------------------------------------------
# resolve_collections_paths — precedence and string project_root
# ---------------------------------------------------------------------------


class TestResolveCollectionsPathsDeep:
    def test_project_root_as_string(self, tmp_path):
        proj_col = tmp_path / ".gludd" / "collections"
        proj_col.mkdir(parents=True)
        entries = resolve_collections_paths(project_root=str(tmp_path))
        sources = [e.source for e in entries]
        assert "project" in sources

    def test_project_root_none(self, tmp_path):
        entries = resolve_collections_paths(project_root=None)
        sources = [e.source for e in entries]
        assert "project" not in sources

    def test_precedence_values_when_user_missing(self, tmp_path):
        proj_col = tmp_path / ".gludd" / "collections"
        proj_col.mkdir(parents=True)
        entries = resolve_collections_paths(project_root=tmp_path)
        precs = {e.source: e.precedence for e in entries}
        assert precs["project"] == 0
        assert precs["bundled"] >= 1

    def test_no_dirs_exist_bundled_still_present(self):
        entries = resolve_collections_paths()
        assert len(entries) >= 1
        assert entries[-1].source == "bundled"

    def test_all_three_tiers_present(self, tmp_path, monkeypatch):
        proj_col = tmp_path / ".gludd" / "collections"
        proj_col.mkdir(parents=True)
        user_col = tmp_path / "xdg" / "gludd" / "collections"
        user_col.mkdir(parents=True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        entries = resolve_collections_paths(project_root=tmp_path)
        assert len(entries) == 3


# ---------------------------------------------------------------------------
# to_ansible_env — pathsep, duplicates, emptiness
# ---------------------------------------------------------------------------


class TestToAnsibleEnvDeep:
    def test_empty_entries(self):
        env = to_ansible_env([])
        assert env["ANSIBLE_COLLECTIONS_PATH"] == ""
        assert env["ANSIBLE_ROLES_PATH"] == ""

    def test_existing_pathsep_multiple(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", "/a:/b:/c")
        entries = [CollectionsPathEntry("bundled", Path("/bundled"), 0)]
        env = to_ansible_env(entries)
        parts = env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)
        assert parts[0] == "/bundled"
        assert "/a" in parts
        assert "/b" in parts
        assert "/c" in parts

    def test_existing_empty_parts_skipped(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", ":/x::/y:")
        entries = [CollectionsPathEntry("bundled", Path("/bundled"), 0)]
        env = to_ansible_env(entries)
        parts = env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)
        assert "" not in parts

    def test_both_env_vars_preserved(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", "/ext-cp")
        monkeypatch.setenv("ANSIBLE_ROLES_PATH", "/ext-rp")
        entries = [CollectionsPathEntry("bundled", Path("/bundled"), 0)]
        env = to_ansible_env(entries)
        assert "/ext-cp" in env["ANSIBLE_COLLECTIONS_PATH"]
        assert "/ext-rp" in env["ANSIBLE_ROLES_PATH"]

    def test_tier_duplicate_with_existing_removed(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", "/bundled:/other")
        entries = [CollectionsPathEntry("bundled", Path("/bundled"), 0)]
        env = to_ansible_env(entries)
        assert env["ANSIBLE_COLLECTIONS_PATH"].count("/bundled") == 1

    def test_roles_env_duplicate_with_tier_removed(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_ROLES_PATH", "/bundled:/extra")
        entries = [CollectionsPathEntry("bundled", Path("/bundled"), 0)]
        env = to_ansible_env(entries)
        assert env["ANSIBLE_ROLES_PATH"].count("/bundled") == 1


# ---------------------------------------------------------------------------
# to_ansible_cfg — edge cases
# ---------------------------------------------------------------------------


class TestToAnsibleCfgDeep:
    def test_empty_entries(self):
        cfg = to_ansible_cfg([])
        assert cfg == "collections_path = "

    def test_paths_with_spaces(self):
        entries = [CollectionsPathEntry("x", Path("/has spaces/here"), 0)]
        cfg = to_ansible_cfg(entries)
        assert "/has spaces/here" in cfg


# ---------------------------------------------------------------------------
# _split_fqcn — boundary cases
# ---------------------------------------------------------------------------


class TestSplitFqcnDeep:
    def test_empty_string(self):
        assert _split_fqcn("") is None

    def test_only_dots(self):
        assert _split_fqcn("...") == ("", "", ".")
        assert _split_fqcn("....") == ("", "", "..")

    def test_leading_dot(self):
        result = _split_fqcn(".ns.coll")
        assert result is not None
        assert result[0] == ""

    def test_trailing_dot(self):
        result = _split_fqcn("ns.coll.")
        assert result == ("ns", "coll", "")

    def test_exactly_three_parts(self):
        assert _split_fqcn("a.b.c") == ("a", "b", "c")

    def test_four_parts(self):
        assert _split_fqcn("a.b.c.d") == ("a", "b", "c.d")

    def test_one_part(self):
        assert _split_fqcn("single") is None

    def test_two_parts(self):
        assert _split_fqcn("one.two") is None

    def test_fqcn_with_spaces(self):
        result = _split_fqcn("ns . coll . r")
        assert result == ("ns ", " coll ", " r")


# ---------------------------------------------------------------------------
# find_resource — precedence corner cases
# ---------------------------------------------------------------------------


class TestFindResourceDeep:
    def test_role_and_module_both_exist_role_wins(self, tmp_path):
        tier = tmp_path / "tier"
        (tier / "ansible_collections" / "ns" / "coll" / "roles" / "r").mkdir(parents=True)
        mod_dir = tier / "ansible_collections" / "ns" / "coll" / "plugins" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "r.py").touch()
        entries = [CollectionsPathEntry("test", tier, 0)]
        result = find_resource("ns.coll.r", entries)
        assert result is not None
        assert result.name == "r"
        assert result.is_dir()

    def test_tier_skipped_when_ns_missing(self, tmp_path):
        tier = tmp_path / "tier"
        tier.mkdir()
        entries = [CollectionsPathEntry("test", tier, 0)]
        assert find_resource("ns.coll.r", entries) is None

    def test_second_tier_wins_when_first_lacks_resource(self, tmp_path):
        t1 = tmp_path / "t1"
        t2 = tmp_path / "t2"
        target = t2 / "ansible_collections" / "ns" / "coll" / "roles" / "r"
        target.mkdir(parents=True)
        entries = [
            CollectionsPathEntry("t1", t1, 0),
            CollectionsPathEntry("t2", t2, 1),
        ]
        result = find_resource("ns.coll.r", entries)
        assert result == target

    def test_module_in_lower_tier_ignored_when_higher_has_role(self, tmp_path):
        t1 = tmp_path / "t1"
        t2 = tmp_path / "t2"
        (t1 / "ansible_collections" / "ns" / "coll" / "roles" / "r").mkdir(parents=True)
        mod_dir = t2 / "ansible_collections" / "ns" / "coll" / "plugins" / "modules"
        mod_dir.mkdir(parents=True)
        (mod_dir / "r.py").touch()
        entries = [
            CollectionsPathEntry("t1", t1, 0),
            CollectionsPathEntry("t2", t2, 1),
        ]
        result = find_resource("ns.coll.r", entries)
        assert str(t1) in str(result)


# ---------------------------------------------------------------------------
# CollectionVersionInfo — semver regex corner cases
# ---------------------------------------------------------------------------


class TestCollectionVersionInfoDeep:
    def test_empty_version(self):
        info = CollectionVersionInfo("ns", "coll", "", Path("/tmp"))
        assert info.is_semver is False
        assert info.is_latest is False

    def test_semver_two_part(self):
        info = CollectionVersionInfo("ns", "coll", "1.2", Path("/tmp"))
        assert info.is_semver is False

    def test_semver_four_part(self):
        info = CollectionVersionInfo("ns", "coll", "1.2.3.4", Path("/tmp"))
        assert info.is_semver is True

    def test_semver_with_build_metadata(self):
        info = CollectionVersionInfo("ns", "coll", "1.0.0+build.1", Path("/tmp"))
        assert info.is_semver is False

    def test_semver_leading_zero(self):
        info = CollectionVersionInfo("ns", "coll", "01.02.03", Path("/tmp"))
        assert info.is_semver is True

    def test_semver_single_digit(self):
        info = CollectionVersionInfo("ns", "coll", "0.0.1", Path("/tmp"))
        assert info.is_semver is True

    def test_not_latest_nor_semver(self):
        info = CollectionVersionInfo("ns", "coll", "edge", Path("/tmp"))
        assert info.is_latest is False
        assert info.is_semver is False


# ---------------------------------------------------------------------------
# scan_collection_versions — filter, mixed entries, edge cases
# ---------------------------------------------------------------------------


class TestScanCollectionVersionsDeep:
    def test_files_inside_ac_ignored(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        ac.mkdir()
        (ac / "readme.txt").touch()
        (ac / "ns@1.0").mkdir()
        result = scan_collection_versions(tmp_path)
        assert result == []

    def test_version_dir_with_no_collections(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@1.0").mkdir(parents=True)
        result = scan_collection_versions(tmp_path)
        assert result == []

    def test_version_dir_with_file_not_dir(self, tmp_path):
        ac = tmp_path / "ansible_collections" / "ns@1.0"
        ac.mkdir(parents=True)
        (ac / "readme.txt").touch()
        result = scan_collection_versions(tmp_path)
        assert result == []

    def test_non_version_directories_skipped(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "plain_ns" / "coll").mkdir(parents=True)
        result = scan_collection_versions(tmp_path)
        assert result == []

    def test_sorted_by_name_naturally(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "z_ns@1.0" / "coll").mkdir(parents=True)
        (ac / "a_ns@1.0" / "coll").mkdir(parents=True)
        result = scan_collection_versions(tmp_path)
        assert result[0].namespace == "a_ns"
        assert result[1].namespace == "z_ns"

    def test_multiple_collections_same_namespace_version(self, tmp_path):
        ac = tmp_path / "ansible_collections" / "ns@1.0"
        (ac / "coll_a").mkdir(parents=True)
        (ac / "coll_b").mkdir(parents=True)
        result = scan_collection_versions(tmp_path)
        assert len(result) == 2

    def test_both_filters_no_match(self, tmp_path):
        ac = tmp_path / "ansible_collections" / "ns@1.0" / "coll"
        ac.mkdir(parents=True)
        result = scan_collection_versions(tmp_path, namespace="other", collection="missing")
        assert result == []

    def test_collection_filter_no_namespace_filter(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns_a@1.0" / "shared").mkdir(parents=True)
        (ac / "ns_b@1.0" / "shared").mkdir(parents=True)
        result = scan_collection_versions(tmp_path, collection="shared")
        assert len(result) == 2

    def test_version_with_at_sign_in_name_but_no_version(self, tmp_path):
        ac = tmp_path / "ansible_collections" / "ns@"
        ac.mkdir(parents=True)
        result = scan_collection_versions(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# _semver_key — sorting nuance
# ---------------------------------------------------------------------------


class TestSemverKeyDeep:
    def test_same_major_different_minor(self):
        assert _semver_key("1.3.0") < _semver_key("1.2.0")

    def test_same_major_minor_different_patch(self):
        assert _semver_key("1.0.5") < _semver_key("1.0.4")

    def test_pre_release_semver_sorting(self):
        assert _semver_key("1.0.0") < _semver_key("1.0.0-alpha.1")

    def test_four_part_parsed_as_semver_skips_fourth(self):
        assert _semver_key("1.2.3.4") < _semver_key("1.0.0")

    def test_alpha_tagged(self):
        assert _semver_key("alpha") > _semver_key("1.0.0")

    def test_semver_with_leading_letter(self):
        k = _semver_key("v1.0.0")
        assert k > _semver_key("1.0.0")

    def test_stability_across_multiple(self):
        versions = ["1.0.0", "2.0.0", "0.9.0", "beta"]
        result = sorted(versions, key=_semver_key)
        assert result[0] == "2.0.0"
        assert result[-1] == "beta"


# ---------------------------------------------------------------------------
# list_collection_versions — dedup, cross-collection
# ---------------------------------------------------------------------------


class TestListCollectionVersionsDeep:
    def test_deduplicates_identical_versions(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@1.0.0" / "coll_a").mkdir(parents=True)
        (ac / "ns@1.0.0" / "coll_b").mkdir(parents=True)
        versions = list_collection_versions(tmp_path, "ns")
        assert versions.count("1.0.0") == 1

    def test_different_collections_same_namespace_same_version(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@1.0.0" / "coll_a").mkdir(parents=True)
        (ac / "ns@2.0.0" / "coll_b").mkdir(parents=True)
        versions = list_collection_versions(tmp_path, "ns")
        assert len(versions) == 2
        assert versions[0] == "2.0.0"

    def test_filtered_by_collection(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@1.0.0" / "coll_a").mkdir(parents=True)
        (ac / "ns@2.0.0" / "coll_b").mkdir(parents=True)
        versions = list_collection_versions(tmp_path, "ns", collection="coll_a")
        assert versions == ["1.0.0"]

    def test_non_semver_mixed_with_semver_ordering(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@3.0.0" / "coll").mkdir(parents=True)
        (ac / "ns@beta" / "coll").mkdir(parents=True)
        (ac / "ns@alpha" / "coll").mkdir(parents=True)
        versions = list_collection_versions(tmp_path, "ns")
        assert versions[0] == "3.0.0"
        assert "alpha" in versions
        assert "beta" in versions
        assert versions.index("alpha") < versions.index("beta")


# ---------------------------------------------------------------------------
# resolve_collection_version — exact-match vs fallback interplay
# ---------------------------------------------------------------------------


class TestResolveCollectionVersionDeep:
    def test_exact_match_wins_over_latest(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        target = ac / "ns@2.0.0" / "coll"
        target.mkdir(parents=True)
        (ac / "ns@latest" / "coll").mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "ns", "coll", requested_version="2.0.0")
        assert result == target

    def test_exact_match_not_found_does_not_fallback(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@latest" / "coll").mkdir(parents=True)
        (ac / "ns@1.0.0" / "coll").mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "ns", "coll", requested_version="9.9.9")
        assert result is None

    def test_no_versioned_no_bare(self, tmp_path, monkeypatch):
        result = resolve_collection_version(tmp_path, "ns", "coll")
        assert result is None

    def test_non_semver_no_latest_no_bare_picks_first(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@edge" / "coll").mkdir(parents=True)
        (ac / "ns@dev" / "coll").mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "ns", "coll")
        assert result is not None

    def test_exact_match_empty_string_version_scan_skips_empty_tag(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        (ac / "ns@" / "coll").mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "ns", "coll", requested_version="")
        assert result is None

    def test_semver_with_bare_also_present_latest_wins(self, tmp_path):
        ac = tmp_path / "ansible_collections"
        latest = ac / "ns@latest" / "coll"
        latest.mkdir(parents=True)
        (ac / "ns" / "coll").mkdir(parents=True)
        (ac / "ns@1.0.0" / "coll").mkdir(parents=True)
        result = resolve_collection_version(tmp_path, "ns", "coll")
        assert result == latest


# ---------------------------------------------------------------------------
# activate_collection_version — symlink replacement, missing version
# ---------------------------------------------------------------------------


class TestActivateCollectionVersionDeep:
    def test_reuses_supplied_temp_dir(self, tmp_path):
        ac = tmp_path / "base" / "ansible_collections"
        target = ac / "ns@1.0" / "coll"
        target.mkdir(parents=True)
        temp = tmp_path / "activ"
        temp.mkdir()
        activation_root, cleanup = activate_collection_version(tmp_path / "base", "ns", "coll", temp_dir=temp)
        assert activation_root == temp
        assert cleanup is None

    def test_specific_version(self, tmp_path):
        ac = tmp_path / "base" / "ansible_collections"
        (ac / "ns@1.0" / "coll").mkdir(parents=True)
        (ac / "ns@2.0" / "coll").mkdir(parents=True)
        activation_root, _ = activate_collection_version(tmp_path / "base", "ns", "coll", version="2.0")
        link = activation_root / "ansible_collections" / "ns" / "coll"
        assert link.is_symlink()
        assert "2.0" in str(link.resolve())

    def test_version_not_found_raises(self, tmp_path):
        ac = tmp_path / "base" / "ansible_collections"
        (ac / "ns@1.0" / "coll").mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="No collection found"):
            activate_collection_version(tmp_path / "base", "ns", "coll", version="99.99")

    def test_replaces_existing_link(self, tmp_path):
        ac = tmp_path / "base" / "ansible_collections"
        t1 = ac / "ns@1.0" / "coll"
        t1.mkdir(parents=True)
        t2 = ac / "ns@2.0" / "coll"
        t2.mkdir(parents=True)
        temp = tmp_path / "activ"
        temp.mkdir()
        activate_collection_version(tmp_path / "base", "ns", "coll", temp_dir=temp, version="1.0")
        activation_root, _ = activate_collection_version(tmp_path / "base", "ns", "coll", temp_dir=temp, version="2.0")
        link = activation_root / "ansible_collections" / "ns" / "coll"
        assert link.is_symlink()
        assert "2.0" in str(link.resolve())


# ---------------------------------------------------------------------------
# list_all_collections — files, symlinks, non-existent
# ---------------------------------------------------------------------------


class TestListAllCollectionsDeep:
    def test_files_mixed_with_dirs(self, tmp_path):
        ns_dir = tmp_path / "ansible_collections" / "general_ludd"
        ns_dir.mkdir(parents=True)
        (ns_dir / "coll_a").mkdir()
        (ns_dir / "coll_b").mkdir()
        (ns_dir / "readme.txt").touch()
        result = list_all_collections(tmp_path)
        assert result == ["coll_a", "coll_b"]

    def test_empty_namespace_dir(self, tmp_path):
        ns_dir = tmp_path / "ansible_collections" / "empty_ns"
        ns_dir.mkdir(parents=True)
        result = list_all_collections(tmp_path, namespace="empty_ns")
        assert result == []

    def test_custom_namespace_not_exists(self, tmp_path):
        result = list_all_collections(tmp_path, namespace="no_such_ns")
        assert result == []

    def test_symlinked_collection_dir(self, tmp_path):
        real = tmp_path / "real_coll"
        real.mkdir()
        ns_dir = tmp_path / "ansible_collections" / "general_ludd"
        ns_dir.mkdir(parents=True)
        link = ns_dir / "linked_coll"
        link.symlink_to(real, target_is_directory=True)
        result = list_all_collections(tmp_path)
        assert "linked_coll" in result

    def test_sorted_output(self, tmp_path):
        ns_dir = tmp_path / "ansible_collections" / "general_ludd"
        ns_dir.mkdir(parents=True)
        (ns_dir / "zzz").mkdir()
        (ns_dir / "aaa").mkdir()
        (ns_dir / "mmm").mkdir()
        result = list_all_collections(tmp_path)
        assert result == ["aaa", "mmm", "zzz"]


# ---------------------------------------------------------------------------
# End-to-integration: full resolution chain
# ---------------------------------------------------------------------------


class TestEndToEndIntegration:
    def test_full_resolution_chain(self, tmp_path):
        role_dir = tmp_path / "ansible_collections" / "general_ludd" / "agent" / "roles" / "project_init"
        role_dir.mkdir(parents=True)
        entries = [CollectionsPathEntry("bundled", tmp_path, 0)]
        resource = find_resource("general_ludd.agent.project_init", entries)
        assert resource is not None
        assert resource.name == "project_init"

    def test_activate_then_find_resource_through_activation(self, tmp_path):
        base = tmp_path / "base"
        ac = base / "ansible_collections"
        target = ac / "ns@1.0.0" / "coll"
        mod = target / "plugins" / "modules" / "hello.py"
        mod.parent.mkdir(parents=True)
        mod.touch()
        activation_root, _ = activate_collection_version(base, "ns", "coll", version="1.0.0")
        link = activation_root / "ansible_collections" / "ns" / "coll"
        assert link.is_symlink()
        assert link.resolve() == target
        entries = [CollectionsPathEntry("activated", activation_root, 0)]
        result = find_resource("ns.coll.hello", entries)
        assert result is not None
        assert result.name == "hello.py"

    def test_resolve_collections_paths_then_list_versions(self, tmp_path):
        proj_col = tmp_path / ".gludd" / "collections" / "ansible_collections"
        (proj_col / "general_ludd@2.0.0" / "agent").mkdir(parents=True)
        entries = resolve_collections_paths(project_root=tmp_path)
        proj_paths = [e.path for e in entries if e.source == "project"]
        assert len(proj_paths) == 1
        versions = list_collection_versions(proj_paths[0], "general_ludd", "agent")
        assert versions == ["2.0.0"]

    def test_env_and_cfg_agree_on_tiers(self):
        entries = [
            CollectionsPathEntry("a", Path("/a"), 0),
            CollectionsPathEntry("b", Path("/b"), 1),
        ]
        env = to_ansible_env(entries)
        cfg = to_ansible_cfg(entries)
        assert os.pathsep.join(["/a", "/b"]) in env["ANSIBLE_COLLECTIONS_PATH"]
        assert os.pathsep.join(["/a", "/b"]) in cfg

    def test_bundled_root_consistency(self):
        from general_ludd.ansible.paths import _bundled_collections_root as bcr

        root = bcr()
        assert isinstance(root, Path)
        assert root.name == "collections"
