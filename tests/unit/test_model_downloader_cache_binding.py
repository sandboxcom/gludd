"""Focused cache-isolation tests for :class:`ModelDownloader`."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.small_models.download import DownloadSource, ModelDownloader

_REVISION = "a" * 40
_GGUF_BYTES = b"GGUF\x03\x00\x00\x00cached-model"


def _cached_artifact(
    cache_dir: Path,
    *,
    repo_id: str = "org/model",
    filename: str = "model.gguf",
    revision: str = _REVISION,
    contents: bytes = _GGUF_BYTES,
    incomplete: bool = False,
) -> tuple[Path, Path]:
    repo_cache = cache_dir / f"models--{repo_id.replace('/', '--')}"
    digest = hashlib.sha256(contents).hexdigest()
    blob_name = f"{digest}.incomplete" if incomplete else digest
    blob = repo_cache / "blobs" / blob_name
    blob.parent.mkdir(parents=True)
    blob.write_bytes(contents)

    snapshot = repo_cache / "snapshots" / revision / filename
    snapshot.parent.mkdir(parents=True)
    snapshot.symlink_to(blob)
    refs = repo_cache / "refs"
    refs.mkdir()
    (refs / "main").write_text(revision, encoding="utf-8")
    return snapshot, blob


def test_every_hub_download_path_binds_the_configured_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "models"
    single_file = tmp_path / "weights.bin"
    single_file.write_bytes(b"weights")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    gguf = tmp_path / "weights.gguf"
    gguf.write_bytes(_GGUF_BYTES)
    downloader = ModelDownloader(cache_dir=str(cache_dir), hf_token="token")

    with (
        patch("huggingface_hub.try_to_load_from_cache", return_value=None),
        patch("huggingface_hub.hf_hub_download", return_value=str(single_file)) as hub_file,
    ):
        downloader.download_huggingface("org/model", filename="weights.bin", revision=_REVISION)

    assert hub_file.call_args.kwargs["cache_dir"] == str(cache_dir)

    with patch("huggingface_hub.snapshot_download", return_value=str(snapshot)) as hub_snapshot:
        downloader.download_huggingface("org/model", revision=_REVISION)

    assert hub_snapshot.call_args.kwargs["cache_dir"] == str(cache_dir)

    with (
        patch("huggingface_hub.try_to_load_from_cache", return_value=None),
        patch("huggingface_hub.hf_hub_download", return_value=str(gguf)) as hub_gguf,
    ):
        downloader.download_gguf("org/model", "weights.gguf", revision=_REVISION)

    assert hub_gguf.call_args.kwargs["cache_dir"] == str(cache_dir)

    with patch("huggingface_hub.snapshot_download", return_value=str(snapshot)) as ollama_snapshot:
        downloader.pull_ollama("org/model:latest", revision=_REVISION)

    assert ollama_snapshot.call_args.kwargs["cache_dir"] == str(cache_dir)


def test_anonymous_hub_download_explicitly_disables_implicit_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "models"
    downloaded = tmp_path / "weights.gguf"
    downloaded.write_bytes(_GGUF_BYTES)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    downloader = ModelDownloader(cache_dir=str(cache_dir))

    with (
        patch("huggingface_hub.try_to_load_from_cache", return_value=None),
        patch(
            "huggingface_hub.hf_hub_download",
            return_value=str(downloaded),
        ) as hub_download,
    ):
        downloader.download_gguf("org/public", "weights.gguf", revision=_REVISION)

    assert hub_download.call_args.kwargs["token"] is False


def test_exact_cached_gguf_is_reused_without_network(tmp_path: Path) -> None:
    cache_dir = tmp_path / "models"
    cached_path, _ = _cached_artifact(cache_dir)
    downloader = ModelDownloader(cache_dir=str(cache_dir))

    with patch("huggingface_hub.hf_hub_download") as network_download:
        result = downloader.download_gguf("org/model", "model.gguf", revision=_REVISION)

    network_download.assert_not_called()
    assert result.local_path == str(cached_path)
    assert result.source is DownloadSource.CACHE
    assert result.revision == _REVISION
    assert result.size_bytes == len(_GGUF_BYTES)


def test_exact_cached_huggingface_file_skips_all_auth_resolution(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "models"
    cached_path, _ = _cached_artifact(cache_dir)
    downloader = ModelDownloader(cache_dir=str(cache_dir))

    with (
        patch.object(
            downloader,
            "_resolve_token",
            side_effect=AssertionError("cache hit must not resolve authentication"),
        ) as auth_resolution,
        patch("huggingface_hub.hf_hub_download") as network_download,
    ):
        result = downloader.download_huggingface(
            "org/model",
            filename="model.gguf",
            revision=_REVISION,
        )

    auth_resolution.assert_not_called()
    network_download.assert_not_called()
    assert result.local_path == str(cached_path)
    assert result.source is DownloadSource.CACHE


def test_exact_cached_gguf_skips_all_auth_resolution(tmp_path: Path) -> None:
    cache_dir = tmp_path / "models"
    cached_path, _ = _cached_artifact(cache_dir)
    downloader = ModelDownloader(cache_dir=str(cache_dir))

    with (
        patch.object(
            downloader,
            "_resolve_token",
            side_effect=AssertionError("cache hit must not resolve authentication"),
        ) as auth_resolution,
        patch("huggingface_hub.hf_hub_download") as network_download,
    ):
        result = downloader.download_gguf(
            "org/model",
            "model.gguf",
            revision=_REVISION,
        )

    auth_resolution.assert_not_called()
    network_download.assert_not_called()
    assert result.local_path == str(cached_path)
    assert result.source is DownloadSource.CACHE


def test_mutable_cached_ref_is_recorded_as_immutable_revision(tmp_path: Path) -> None:
    cache_dir = tmp_path / "models"
    cached_path, _ = _cached_artifact(cache_dir)
    downloader = ModelDownloader(cache_dir=str(cache_dir))

    with patch("huggingface_hub.hf_hub_download") as network_download:
        result = downloader.download_gguf("org/model", "model.gguf", revision="main")

    network_download.assert_not_called()
    assert result.local_path == str(cached_path)
    assert result.revision == _REVISION


@pytest.mark.parametrize(
    ("filename", "revision"),
    [("other.gguf", _REVISION), ("model.gguf", "b" * 40)],
)
def test_cache_lookup_requires_exact_filename_and_revision(
    tmp_path: Path,
    filename: str,
    revision: str,
) -> None:
    cache_dir = tmp_path / "models"
    _cached_artifact(cache_dir)
    downloaded = tmp_path / filename
    downloaded.write_bytes(_GGUF_BYTES)
    downloader = ModelDownloader(cache_dir=str(cache_dir))

    with patch("huggingface_hub.hf_hub_download", return_value=str(downloaded)) as network_download:
        result = downloader.download_gguf("org/model", filename, revision=revision)

    network_download.assert_called_once()
    assert result.source is DownloadSource.GGUF
    assert result.revision == revision


def test_network_result_resolves_mutable_ref_from_snapshot_path(tmp_path: Path) -> None:
    cache_dir = tmp_path / "models"
    snapshot, _ = _cached_artifact(cache_dir)
    downloader = ModelDownloader(cache_dir=str(cache_dir))

    with (
        patch("huggingface_hub.try_to_load_from_cache", return_value=None),
        patch("huggingface_hub.hf_hub_download", return_value=str(snapshot)),
    ):
        result = downloader.download_gguf("org/model", "model.gguf", revision="main")

    assert result.source is DownloadSource.GGUF
    assert result.revision == _REVISION


@pytest.mark.parametrize("failure", ["corrupt", "incomplete"])
def test_invalid_cached_gguf_fails_closed(tmp_path: Path, failure: str) -> None:
    cache_dir = tmp_path / "models"
    _, blob = _cached_artifact(cache_dir, incomplete=failure == "incomplete")
    if failure == "corrupt":
        blob.write_bytes(b"tampered")
    downloader = ModelDownloader(cache_dir=str(cache_dir))

    with (
        patch("huggingface_hub.hf_hub_download") as network_download,
        pytest.raises(RuntimeError, match="cached model artifact failed integrity validation"),
    ):
        downloader.download_gguf(
            "org/model",
            "model.gguf",
            revision=_REVISION,
            local_files_only=True,
        )

    network_download.assert_not_called()
    assert downloader.get_downloaded("org/model") is None


def test_configured_cache_never_falls_back_to_or_mutates_ambient_hf_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ambient_cache = tmp_path / "ambient"
    _, ambient_blob = _cached_artifact(ambient_cache)
    configured_cache = tmp_path / "configured"
    before = ambient_blob.read_bytes()
    monkeypatch.setenv("HF_HUB_CACHE", str(ambient_cache))
    downloader = ModelDownloader(cache_dir=str(configured_cache))

    with (
        patch("huggingface_hub.hf_hub_download", side_effect=RuntimeError("offline")) as hub_download,
        pytest.raises(RuntimeError, match="offline"),
    ):
        downloader.download_gguf(
            "org/model",
            "model.gguf",
            revision=_REVISION,
            local_files_only=True,
        )

    assert hub_download.call_args.kwargs["cache_dir"] == str(configured_cache)
    assert ambient_blob.read_bytes() == before
    assert list(configured_cache.iterdir()) == []
