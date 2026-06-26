"""Bootstrap support for the DeusData codebase-memory-mcp binary.

Covers the new installed-binary path: KNOWN_VERSIONS registration, per-platform
release-asset URL construction, traversal-safe in-archive executable
extraction, and the store+chmod result. Exercises the generalized
``_extract_executable_member`` / ``_TARBALL_BINARIES`` wiring without any
network or real-platform dependency (URLs are built via the static helper with
explicit os/arch; archives are synthesized in memory).
"""

from __future__ import annotations

import io
import os
import tarfile

import pytest

from general_ludd.filestore.bootstrap import (
    CODEBASE_MEMORY_VERSION,
    BinaryBootstrapper,
)
from general_ludd.filestore.store import FileStore

_BIN = "codebase-memory-mcp"


@pytest.fixture
def bootstrapper(tmp_path) -> BinaryBootstrapper:
    # Isolated store root; point the overlay at a nonexistent dir so no real
    # ~/.config overlay is loaded.
    store = FileStore(
        root_path=str(tmp_path / "store"),
        overlay_path=str(tmp_path / "no-overlay"),
    )
    return BinaryBootstrapper(store=store)


def _make_tar_gz(members: dict[str, bytes]) -> bytes:
    """Build an in-memory .tar.gz from {arcname: content}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for arcname, content in members.items():
            info = tarfile.TarInfo(name=arcname)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def test_known_versions_includes_codebase_memory(bootstrapper) -> None:
    assert bootstrapper.get_known_versions()[_BIN] == CODEBASE_MEMORY_VERSION
    assert CODEBASE_MEMORY_VERSION == "0.8.1"


@pytest.mark.parametrize(
    ("os_name", "arch", "expected_suffix"),
    [
        ("darwin", "amd64", "v0.8.1/codebase-memory-mcp-darwin-amd64.tar.gz"),
        ("darwin", "arm64", "v0.8.1/codebase-memory-mcp-darwin-arm64.tar.gz"),
        ("linux", "amd64", "v0.8.1/codebase-memory-mcp-linux-amd64-portable.tar.gz"),
        ("linux", "arm64", "v0.8.1/codebase-memory-mcp-linux-arm64-portable.tar.gz"),
    ],
)
def test_download_url_per_platform(os_name, arch, expected_suffix) -> None:
    url = BinaryBootstrapper._codebase_memory_download_url(os_name, arch)
    assert url is not None
    assert url.startswith("https://github.com/DeusData/codebase-memory-mcp/releases/download/")
    assert url.endswith(expected_suffix)


@pytest.mark.parametrize(
    ("os_name", "arch"),
    [
        ("windows", "amd64"),  # ships a .zip, not a tarball — not bootstrapped here
        ("linux", "riscv64"),  # unmapped arch
        ("freebsd", "amd64"),  # unmapped OS
    ],
)
def test_download_url_unsupported_returns_none(os_name, arch) -> None:
    assert BinaryBootstrapper._codebase_memory_download_url(os_name, arch) is None


def test_extract_pulls_bare_binary_from_archive_root(bootstrapper) -> None:
    payload = b"\x7fELF-codebase-memory-mcp-binary"
    archive = _make_tar_gz({_BIN: payload, "README.md": b"docs"})
    assert bootstrapper._extract_executable_member(archive, _BIN) == payload


def test_extract_rejects_traversal_member(bootstrapper) -> None:
    # The ONLY member whose basename matches sits behind a '..' path; it must be
    # skipped, so extraction falls through and returns the original bytes.
    payload = b"evil"
    archive = _make_tar_gz({f"../../{_BIN}": payload})
    assert bootstrapper._extract_executable_member(archive, _BIN) == archive


def test_extract_non_tarball_returns_input_unchanged(bootstrapper) -> None:
    plain = b"not-a-tarball"
    assert bootstrapper._extract_executable_member(plain, _BIN) == plain


def test_store_and_chmod_extracts_and_marks_executable(bootstrapper) -> None:
    payload = b"#!/bin/sh\necho codebase-memory-mcp\n"
    archive = _make_tar_gz({_BIN: payload})

    bootstrapper._store_binary_and_chmod(_BIN, archive)

    stored = bootstrapper.get_binary_path(_BIN)
    assert stored is not None and os.path.isfile(stored)
    # The extracted executable bytes are stored, not the raw archive.
    with open(stored, "rb") as fh:
        assert fh.read() == payload
    # Executable bit is set on the result.
    assert os.stat(stored).st_mode & 0o111
