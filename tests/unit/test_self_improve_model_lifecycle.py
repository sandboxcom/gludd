"""Contracts for the owned self-improvement GGUF cache lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from general_ludd.local_model._local_model_configs import LocalModelConfig
from general_ludd.self_improve.model_lifecycle import ModelLeaseManager
from general_ludd.small_models.download import DownloadedModel, DownloadSource

_REV_A = "a" * 40
_REV_B = "b" * 40
_ARTIFACT = b"immutable gguf"


def _coding_config(
    name: str = "coder-test-0.5b",
    repo: str = "example/coder-test-GGUF",
) -> LocalModelConfig:
    return LocalModelConfig(
        name=name,
        repo=repo,
        filename=f"{name}.Q4_K_M.gguf",
        size_mb=1,
        category="coding",
        ci_safe=True,
    )


class _Downloader:
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root
        self.calls: list[tuple[str, str, str | None]] = []

    def download_gguf(
        self,
        model_id: str,
        filename: str,
        revision: str | None = None,
        *,
        local_files_only: bool = False,
    ) -> DownloadedModel:
        del local_files_only
        assert revision is not None
        artifact = (
            self.cache_root
            / f"models--{model_id.replace('/', '--')}"
            / "snapshots"
            / revision
            / filename
        )
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(_ARTIFACT + model_id.encode("utf-8"))
        self.calls.append((model_id, filename, revision))
        return DownloadedModel(
            model_id=model_id,
            local_path=str(artifact),
            source=DownloadSource.GGUF,
            filename=filename,
            revision=revision,
            size_bytes=artifact.stat().st_size,
        )


def _manager(
    tmp_path: Path,
    downloader: _Downloader,
    *,
    selector: Callable[[str], LocalModelConfig] | None = None,
    revision_resolver: Callable[[str], str] | None = None,
    quota_bytes: int = 64 * 1024 * 1024,
    reserve_bytes: int = 0,
    disk_free: Callable[[Path], int] | None = None,
    revision_deleter: Callable[[Path, str], None] | None = None,
) -> ModelLeaseManager:
    return ModelLeaseManager(
        cache_root=tmp_path / "self-improve-cache",
        quota_bytes=quota_bytes,
        reserve_bytes=reserve_bytes,
        model_selector=selector or (lambda _task: _coding_config()),
        revision_resolver=revision_resolver or (lambda _repo: _REV_A),
        downloader_factory=lambda cache: downloader,
        disk_free=disk_free,
        revision_deleter=revision_deleter,
    )


def test_managed_acquisition_binds_cache_revision_digest_manifest_and_lease(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "self-improve-cache"
    downloader = _Downloader(cache_root)
    observed_cache_roots: list[Path] = []

    def downloader_factory(cache: Path) -> _Downloader:
        observed_cache_roots.append(cache)
        return downloader

    manager = ModelLeaseManager(
        cache_root=cache_root,
        quota_bytes=64 * 1024 * 1024,
        reserve_bytes=0,
        model_selector=lambda _task: _coding_config(),
        revision_resolver=lambda _repo: _REV_A,
        downloader_factory=downloader_factory,
    )

    with manager.acquire("repair Python code") as model:
        assert model.path.is_file()
        assert model.model_id == "coder-test-0.5b"
        assert model.resolved_revision == _REV_A
        assert len(model.artifact_sha256) == 64
        assert model.manifest_path.is_file()
        assert model.lease_path.is_file()
        assert observed_cache_roots == [cache_root]
        assert downloader.calls == [
            (
                "example/coder-test-GGUF",
                "coder-test-0.5b.Q4_K_M.gguf",
                _REV_A,
            )
        ]

    assert not model.lease_path.exists()


def test_explicit_path_override_is_hashed_and_never_managed_or_downloaded(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "operator.gguf"
    explicit.write_bytes(b"operator supplied")
    downloader = _Downloader(tmp_path / "self-improve-cache")
    manager = _manager(tmp_path, downloader)

    with manager.acquire("coding", explicit_path=explicit) as model:
        assert model.path == explicit
        assert model.source == "explicit"
        assert model.resolved_revision is None
        assert len(model.artifact_sha256) == 64
        assert model.lease_path.is_file()
        assert not model.manifest_path.exists()

    assert downloader.calls == []
    assert not model.lease_path.exists()


@pytest.mark.parametrize("raised", [RuntimeError("worker failed"), KeyboardInterrupt()])
def test_failure_and_cancellation_always_release_lease(
    tmp_path: Path,
    raised: BaseException,
) -> None:
    downloader = _Downloader(tmp_path / "self-improve-cache")
    manager = _manager(tmp_path, downloader)

    with pytest.raises(type(raised)), manager.acquire("coding") as model:
        lease_path = model.lease_path
        raise raised

    assert not lease_path.exists()


def test_concurrent_leases_reuse_one_verified_cached_artifact(tmp_path: Path) -> None:
    downloader = _Downloader(tmp_path / "self-improve-cache")
    manager = _manager(tmp_path, downloader)

    with manager.acquire("coding") as first:
        with manager.acquire("coding") as second:
            assert first.path == second.path
            assert first.lease_path != second.lease_path
            assert first.lease_path.is_file()
            assert second.lease_path.is_file()
            assert len(downloader.calls) == 1
        assert first.lease_path.is_file()

    assert not first.lease_path.exists()


def test_quota_evicts_only_oldest_owned_unleased_revision(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "self-improve-cache"
    downloader = _Downloader(cache_root)
    configs = {
        "old": _coding_config("old-coder", "example/old"),
        "active": _coding_config("active-coder", "example/active"),
    }
    revisions = {"example/old": _REV_A, "example/active": _REV_B}
    manager = _manager(
        tmp_path,
        downloader,
        selector=lambda task: configs[task],
        revision_resolver=lambda repo: revisions[repo],
    )
    with manager.acquire("old") as old:
        old_path = old.path
    unrelated = cache_root / "models--someone-else--model" / "blob.bin"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"not owned by gludd")
    deleted: list[str] = []

    def delete_revision(_cache: Path, revision: str) -> None:
        deleted.append(revision)
        if revision == _REV_A:
            old_path.unlink()

    with manager.acquire("active") as active:
        pressure_manager = _manager(
            tmp_path,
            downloader,
            selector=lambda task: configs[task],
            revision_resolver=lambda repo: revisions[repo],
            quota_bytes=active.path.stat().st_size,
            revision_deleter=delete_revision,
        )
        removed = pressure_manager.reclaim(required_bytes=0)
        assert active.path.is_file()
        assert active.lease_path.is_file()

    assert removed == (old_path,)
    assert deleted == [_REV_A]
    assert not old_path.exists()
    assert unrelated.read_bytes() == b"not owned by gludd"


def test_insufficient_headroom_fails_before_download(tmp_path: Path) -> None:
    downloader = _Downloader(tmp_path / "self-improve-cache")
    manager = _manager(
        tmp_path,
        downloader,
        quota_bytes=2 * 1024 * 1024,
        reserve_bytes=2 * 1024 * 1024,
        disk_free=lambda _root: 1024,
    )

    with pytest.raises(RuntimeError, match="insufficient model cache headroom"), manager.acquire("coding"):
        pytest.fail("acquisition must not start")

    assert downloader.calls == []


def test_corrupt_ownership_manifest_blocks_eviction(tmp_path: Path) -> None:
    cache_root = tmp_path / "self-improve-cache"
    manifest = cache_root / ".gludd" / "models" / "broken.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{broken", encoding="utf-8")
    deletes: list[str] = []
    manager = _manager(
        tmp_path,
        _Downloader(cache_root),
        quota_bytes=1,
        revision_deleter=lambda _root, revision: deletes.append(revision),
    )

    with pytest.raises(RuntimeError, match="model ownership manifest"):
        manager.reclaim(required_bytes=0)

    assert deletes == []


def test_leased_only_cache_fails_closed_instead_of_deleting_active_model(
    tmp_path: Path,
) -> None:
    downloader = _Downloader(tmp_path / "self-improve-cache")
    manager = _manager(tmp_path, downloader)
    deletes: list[str] = []
    pressure_manager = _manager(
        tmp_path,
        downloader,
        quota_bytes=1,
        revision_deleter=lambda _root, revision: deletes.append(revision),
    )

    with manager.acquire("coding") as active:
        with pytest.raises(RuntimeError, match="insufficient model cache headroom"):
            pressure_manager.reclaim(required_bytes=1)
        assert active.path.is_file()

    assert deletes == []
