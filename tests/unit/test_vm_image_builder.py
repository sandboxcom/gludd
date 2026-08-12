"""Unit tests for VM image builder — P2: Firecracker ext4 + gVisor OCI bundle.

Tests cover: manifest hashing, Firecracker image build/verify/cache, gVisor OCI
bundle build/verify/cache, cache hit/miss/force-rebuild, cleanup, image_exists,
get_image_path, list_cached_images, custom_files, and graceful handling of
unknown image types.
"""

from __future__ import annotations

import errno
import json
import shutil
from pathlib import Path
from unittest import mock

import pytest

from general_ludd.security.sandboxes.vm.image_builder import (
    BuiltImage,
    ImageManifest,
    build_firecracker_image,
    build_gvisor_image,
    build_rootfs,
    cleanup_cache,
    get_image_path,
    image_exists,
    list_cached_images,
    verify_image,
)


@pytest.fixture(autouse=True)
def _isolate_cache_dir(tmp_path, monkeypatch):
    cache = tmp_path / "sandbox-cache"
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "general_ludd.security.sandboxes.vm.image_builder.CACHE_DIR", cache
    )
    return cache


def _default_manifest(name="test-image"):
    return ImageManifest(
        name=name,
        packages=("python3",),
        architecture="x86_64",
    )


def test_manifest_content_hash_deterministic():
    m1 = ImageManifest(name="a", packages=("python3", "git"))
    m2 = ImageManifest(name="a", packages=("git", "python3"))
    assert m1.content_hash() == m2.content_hash()


def test_manifest_content_hash_changes_with_packages():
    m1 = ImageManifest(name="a", packages=("python3",))
    m2 = ImageManifest(name="a", packages=("python3", "git"))
    assert m1.content_hash() != m2.content_hash()


def test_manifest_content_hash_changes_with_name():
    m1 = ImageManifest(name="a", packages=("python3",))
    m2 = ImageManifest(name="b", packages=("python3",))
    assert m1.content_hash() != m2.content_hash()


def test_manifest_cache_path_uses_hash():
    from general_ludd.security.sandboxes.vm import image_builder as ib_mod
    m = _default_manifest()
    cache_path = m.cache_path()
    assert m.content_hash() in str(cache_path)
    assert str(ib_mod.CACHE_DIR) in str(cache_path)


def test_build_firecracker_image_creates_ext4(_isolate_cache_dir):
    manifest = _default_manifest()
    built = build_firecracker_image(manifest)
    ext4 = built.path / "rootfs.ext4"
    assert ext4.is_file()
    assert built.image_type == "firecracker"
    assert built.size_bytes > 0


def test_build_firecracker_image_verifies_ext4_magic(_isolate_cache_dir):
    manifest = _default_manifest()
    built = build_firecracker_image(manifest)
    ext4 = built.path / "rootfs.ext4"
    assert verify_image(ext4) is True


def test_build_firecracker_image_cache_hit(_isolate_cache_dir):
    manifest = _default_manifest()
    first = build_firecracker_image(manifest)
    second = build_firecracker_image(manifest)
    assert first.manifest_hash == second.manifest_hash
    assert first.path == second.path


def test_build_firecracker_image_force_rebuild(_isolate_cache_dir):
    manifest = _default_manifest()
    first = build_firecracker_image(manifest)
    with mock.patch("time.time", return_value=first.built_at + 10):
        second = build_firecracker_image(manifest, force_rebuild=True)
    assert second.built_at > first.built_at


def test_build_firecracker_diff_manifest_diff_cache(_isolate_cache_dir):
    m1 = ImageManifest(name="img1", packages=("python3",))
    m2 = ImageManifest(name="img2", packages=("python3", "git"))
    b1 = build_firecracker_image(m1)
    b2 = build_firecracker_image(m2)
    assert b1.path != b2.path
    assert b1.manifest_hash != b2.manifest_hash


def test_build_firecracker_includes_manifest_json(_isolate_cache_dir):
    manifest = _default_manifest()
    built = build_firecracker_image(manifest)
    manifest_file = built.path / "manifest.json"
    assert manifest_file.is_file()
    meta = json.loads(manifest_file.read_text())
    assert meta["name"] == "test-image"
    assert "python3" in meta["packages"]


def test_build_gvisor_image_creates_oci_bundle(_isolate_cache_dir):
    manifest = _default_manifest("gvisor-test")
    built = build_gvisor_image(manifest)
    config = built.path / "config.json"
    rootfs = built.path / "rootfs"
    assert config.is_file()
    assert rootfs.is_dir()
    assert built.image_type == "gvisor"


def test_build_gvisor_image_verify_oci(_isolate_cache_dir):
    manifest = _default_manifest()
    built = build_gvisor_image(manifest)
    assert verify_image(built.path) is True


def test_build_gvisor_image_cache_hit(_isolate_cache_dir):
    manifest = _default_manifest()
    first = build_gvisor_image(manifest)
    second = build_gvisor_image(manifest)
    assert first.path == second.path
    assert first.manifest_hash == second.manifest_hash


def test_build_gvisor_image_force_rebuild(_isolate_cache_dir):
    manifest = _default_manifest()
    first = build_gvisor_image(manifest)
    with mock.patch("time.time", return_value=first.built_at + 10):
        second = build_gvisor_image(manifest, force_rebuild=True)
    assert second.built_at > first.built_at


def test_gvisor_bundle_has_agent_executor(_isolate_cache_dir):
    manifest = _default_manifest()
    built = build_gvisor_image(manifest)
    agent = built.path / "rootfs" / "usr" / "bin" / "agent_executor"
    assert agent.is_file()


def test_gvisor_config_is_valid_oci_spec(_isolate_cache_dir):
    manifest = _default_manifest()
    built = build_gvisor_image(manifest)
    config = built.path / "config.json"
    spec = json.loads(config.read_text())
    assert spec["ociVersion"] == "1.1.0"
    assert spec["process"]["args"] == ["/usr/bin/agent_executor"]
    assert spec["root"]["path"] == "rootfs"
    assert spec["root"]["readonly"] is True


def test_build_rootfs_defaults_to_firecracker(_isolate_cache_dir, tmp_path):
    dest = tmp_path / "output-rootfs"
    built = build_rootfs(str(dest))
    assert (dest / "rootfs.ext4").is_file()
    assert built.image_type == "firecracker"


def test_build_rootfs_gvisor(_isolate_cache_dir, tmp_path):
    dest = tmp_path / "oci-output"
    built = build_rootfs(str(dest), image_type="gvisor")
    assert (dest / "config.json").is_file()
    assert built.image_type == "gvisor"


def test_build_rootfs_with_custom_manifest(_isolate_cache_dir, tmp_path):
    manifest = ImageManifest(name="explicit", packages=("bash", "curl"))
    dest = tmp_path / "custom-output"
    built = build_rootfs(str(dest), manifest=manifest)
    assert built.manifest_hash == manifest.content_hash()


def test_build_rootfs_replaces_shared_destination_without_rmtree(
    _isolate_cache_dir, tmp_path
):
    dest = tmp_path / "shared-output"
    dest.mkdir()
    (dest / "stale").write_text("old image")
    real_rmtree = shutil.rmtree

    def _reject_live_destination_rmtree(path, *args, **kwargs):
        if Path(path) == dest:
            raise OSError(errno.ENOTEMPTY, "shared destination changed during cleanup")
        return real_rmtree(path, *args, **kwargs)

    with mock.patch.object(shutil, "rmtree", side_effect=_reject_live_destination_rmtree):
        built = build_rootfs(dest)

    assert built.path == dest
    assert (dest / "rootfs.ext4").is_file()
    assert not (dest / "stale").exists()


def test_build_rootfs_publishes_when_shared_destination_disappears(
    _isolate_cache_dir, tmp_path
):
    dest = tmp_path / "shared-output"
    dest.mkdir()
    (dest / "stale").write_text("old image")
    real_replace = Path.replace

    def _remove_destination_before_displacement(path: Path, target: Path):
        if path == dest:
            shutil.rmtree(dest)
            raise FileNotFoundError(dest)
        return real_replace(path, target)

    with mock.patch.object(Path, "replace", _remove_destination_before_displacement):
        built = build_rootfs(dest)

    assert built.path == dest
    assert (dest / "rootfs.ext4").is_file()


def test_build_rootfs_unknown_type_raises(_isolate_cache_dir, tmp_path):
    with pytest.raises(ValueError, match="Unknown image_type"):
        build_rootfs(str(tmp_path / "bad"), image_type="docker")


def test_verify_image_missing_path():
    assert verify_image("/nonexistent/path/img.ext4") is False


def test_verify_image_empty_file(_isolate_cache_dir, tmp_path):
    empty = tmp_path / "empty.ext4"
    empty.write_bytes(b"")
    assert verify_image(empty) is False


def test_verify_image_valid_ext4(_isolate_cache_dir):
    manifest = _default_manifest()
    built = build_firecracker_image(manifest)
    assert verify_image(built.path / "rootfs.ext4") is True


def test_verify_image_valid_oci_bundle(_isolate_cache_dir):
    manifest = _default_manifest()
    built = build_gvisor_image(manifest)
    assert verify_image(built.path) is True


def test_image_exists_true_for_firecracker(_isolate_cache_dir):
    manifest = _default_manifest()
    build_firecracker_image(manifest)
    assert image_exists(manifest) is True


def test_image_exists_true_for_gvisor(_isolate_cache_dir):
    manifest = _default_manifest()
    build_gvisor_image(manifest)
    assert image_exists(manifest) is True


def test_image_exists_false_for_unknown(_isolate_cache_dir):
    manifest = ImageManifest(name="never-built", packages=("nonexistent",))
    assert image_exists(manifest) is False


def test_get_image_path_firecracker_returns_ext4(_isolate_cache_dir):
    manifest = _default_manifest()
    build_firecracker_image(manifest)
    path = get_image_path(manifest)
    assert path is not None
    assert path.name == "rootfs.ext4"


def test_get_image_path_gvisor_returns_bundle_dir(_isolate_cache_dir):
    manifest = _default_manifest()
    build_gvisor_image(manifest)
    path = get_image_path(manifest)
    assert path is not None
    assert path.is_dir()
    assert (path / "config.json").exists()


def test_get_image_path_missing_returns_none(_isolate_cache_dir):
    manifest = ImageManifest(name="ghost")
    assert get_image_path(manifest) is None


def test_list_cached_images_empty(_isolate_cache_dir):
    assert list_cached_images() == []


def test_list_cached_images_populated(_isolate_cache_dir):
    build_firecracker_image(_default_manifest("img1"))
    build_gvisor_image(ImageManifest(name="img2", packages=("curl",)))
    entries = list_cached_images()
    assert len(entries) == 2
    types = {e["type"] for e in entries}
    assert types == {"firecracker", "gvisor"}


def test_list_cached_images_includes_metadata(_isolate_cache_dir):
    build_firecracker_image(_default_manifest("meta-test"))
    entries = list_cached_images()
    assert len(entries) == 1
    assert entries[0]["name"] == "meta-test"
    assert entries[0]["type"] == "firecracker"
    assert entries[0]["size_bytes"] > 0
    assert entries[0]["built_at"] > 0


def test_cleanup_cache_removes_stale(_isolate_cache_dir):
    manifest = _default_manifest()
    build_firecracker_image(manifest)
    removed = cleanup_cache(max_age_seconds=0)
    assert removed >= 1
    assert image_exists(manifest) is False


def test_cleanup_cache_keeps_fresh(_isolate_cache_dir):
    manifest = _default_manifest()
    build_firecracker_image(manifest)
    removed = cleanup_cache(max_age_seconds=864000)
    assert removed == 0
    assert image_exists(manifest) is True


def test_cleanup_cache_handles_corrupt_manifest(_isolate_cache_dir):
    manifest = _default_manifest()
    build_firecracker_image(manifest)
    manifest_file = manifest.cache_path() / "manifest.json"
    manifest_file.write_text("not valid json {{{")
    removed = cleanup_cache(max_age_seconds=0)
    assert removed >= 1


def test_custom_files_change_hash_and_produce_different_cache(_isolate_cache_dir):
    m1 = ImageManifest(name="cf", custom_files=(("etc/x", b"v1"),))
    m2 = ImageManifest(name="cf", custom_files=(("etc/x", b"v2"),))
    assert m1.content_hash() != m2.content_hash()
    b1 = build_firecracker_image(m1)
    b2 = build_firecracker_image(m2)
    assert b1.path != b2.path


def test_idempotent_rebuild_same_manifest(_isolate_cache_dir):
    manifest = _default_manifest()
    b1 = build_firecracker_image(manifest)
    b2 = build_firecracker_image(manifest)
    b3 = build_firecracker_image(manifest)
    assert b1.path == b2.path == b3.path
    assert len(list_cached_images()) == 1


def test_built_image_dataclass_fields():
    bi = BuiltImage(
        path=Path("/tmp/img"),
        manifest_hash="abc123",
        image_type="firecracker",
        size_bytes=1000,
        files=("rootfs.ext4",),
    )
    assert bi.path == Path("/tmp/img")
    assert bi.manifest_hash == "abc123"
    assert bi.image_type == "firecracker"
    assert bi.size_bytes == 1000
    assert bi.files == ("rootfs.ext4",)
    assert bi.built_at > 0


def test_image_manifest_extra_dict_included_in_hash():
    m1 = ImageManifest(name="e", extra={"key": "a"})
    m2 = ImageManifest(name="e", extra={"key": "b"})
    assert m1.content_hash() != m2.content_hash()


def test_build_rootfs_copies_to_dest_not_in_place(_isolate_cache_dir, tmp_path):
    manifest = _default_manifest()
    dest = tmp_path / "explicit-dest"
    built = build_rootfs(str(dest), manifest=manifest)
    assert dest.exists()
    assert built.path == dest


def test_firecracker_ext4_has_minimum_size(_isolate_cache_dir):
    manifest = _default_manifest()
    built = build_firecracker_image(manifest)
    ext4 = built.path / "rootfs.ext4"
    assert ext4.stat().st_size >= 64 * 1024 * 1024


def test_verify_image_rejects_non_ext4_regular_file(tmp_path):
    fake = tmp_path / "fake.img"
    fake.write_bytes(b"\x00" * 10240)
    assert verify_image(fake) is False


def test_verify_image_rejects_oci_without_config(_isolate_cache_dir):
    bad = _isolate_cache_dir / "bad-bundle"
    bad.mkdir()
    (bad / "rootfs").mkdir()
    assert verify_image(bad) is False


def test_verify_image_rejects_oci_without_rootfs(_isolate_cache_dir):
    bad = _isolate_cache_dir / "no-rootfs"
    bad.mkdir()
    (bad / "config.json").write_text('{"ociVersion": "1.1.0"}')
    assert verify_image(bad) is False
