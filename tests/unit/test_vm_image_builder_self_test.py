"""Self-test for the image_builder module's structural properties.

Verifies import hygiene, manifest immutability, and function signatures.
"""

from __future__ import annotations

import inspect
from dataclasses import is_dataclass

import pytest


def test_all_exported_names_are_importable():
    import general_ludd.security.sandboxes.vm.image_builder as ib

    for name in ib.__all__:
        assert hasattr(ib, name), f"image_builder.__all__ names {name} but object missing"
        obj = getattr(ib, name)
        assert obj is not None, f"image_builder.{name} resolves to None"


def test_image_manifest_is_frozen_and_hashable():
    from general_ludd.security.sandboxes.vm.image_builder import ImageManifest

    assert is_dataclass(ImageManifest), "ImageManifest must be a dataclass"
    assert ImageManifest.__dataclass_params__.frozen, "ImageManifest must be frozen"

    m1 = ImageManifest(name="alpine", packages=("python3", "ansible"))
    m2 = ImageManifest(name="alpine", packages=("python3", "ansible"))
    assert m1 == m2, "equal manifests must compare equal"
    assert hash(m1) == hash(m2), "equal manifests must have equal hashes"
    assert isinstance(hash(m1), int), "hash must return int"

    with pytest.raises(AttributeError):
        m1.name = "debian"


def test_build_rootfs_signature_accepts_expected_params():
    from general_ludd.security.sandboxes.vm.image_builder import build_rootfs

    sig = inspect.signature(build_rootfs)
    param_names = set(sig.parameters.keys())

    assert "manifest" in param_names, "build_rootfs must have manifest param"
    assert "output_path" in param_names, "build_rootfs must have output_path param"
    assert "image_type" in param_names, "build_rootfs must have image_type param"
