"""Regression tests for the local-model E2E artifact downloader."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import e2e_download_small_model


def test_downloaded_quantized_gguf_is_materialized_without_requantizing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A pre-quantized download should become the directly usable E2E artifact."""
    source = tmp_path / "huggingface-cache" / e2e_download_small_model.MODEL_FILENAME
    source.parent.mkdir()
    source.write_bytes(b"GGUF-ready")
    artifact_dir = tmp_path / "e2e-model"

    class StubDownloader:
        def __init__(self, *, cache_dir: str) -> None:
            assert cache_dir == str(artifact_dir)

        def download_gguf(
            self,
            *,
            model_id: str,
            filename: str,
            revision: str,
        ) -> SimpleNamespace:
            assert model_id == e2e_download_small_model.MODEL_ID
            assert filename == e2e_download_small_model.MODEL_FILENAME
            assert revision == e2e_download_small_model.MODEL_REVISION
            return SimpleNamespace(local_path=str(source), size_bytes=source.stat().st_size)

    monkeypatch.setattr(e2e_download_small_model, "CACHE_DIR", str(artifact_dir))
    monkeypatch.setattr(e2e_download_small_model, "ModelDownloader", StubDownloader)

    assert e2e_download_small_model.main() == 0
    artifact = artifact_dir / e2e_download_small_model.MODEL_FILENAME
    assert artifact.read_bytes() == b"GGUF-ready"
    assert artifact.resolve() != source.resolve()


def test_materialize_artifact_reuses_the_same_existing_file(tmp_path: Path) -> None:
    """Repeated materialization should leave an already-ready artifact untouched."""
    artifact = tmp_path / "model.gguf"
    artifact.write_bytes(b"ready")

    e2e_download_small_model._materialize_artifact(artifact, artifact)

    assert artifact.read_bytes() == b"ready"


def test_materialize_artifact_copies_when_hard_links_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cross-filesystem caches should use the atomic copy fallback."""
    source = tmp_path / "source.gguf"
    destination = tmp_path / "artifact" / "model.gguf"
    destination.parent.mkdir()
    source.write_bytes(b"cross-device")

    def reject_hard_link(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", reject_hard_link)

    e2e_download_small_model._materialize_artifact(source, destination)

    assert destination.read_bytes() == b"cross-device"


def test_materialize_artifact_propagates_unexpected_link_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unexpected filesystem failures must fail closed and clean temporary files."""
    source = tmp_path / "source.gguf"
    destination_dir = tmp_path / "artifact"
    destination = destination_dir / "model.gguf"
    destination_dir.mkdir()
    source.write_bytes(b"model")

    def reject_hard_link(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EIO, "storage failure")

    monkeypatch.setattr(os, "link", reject_hard_link)

    with pytest.raises(OSError, match="storage failure"):
        e2e_download_small_model._materialize_artifact(source, destination)

    assert list(destination_dir.iterdir()) == []
