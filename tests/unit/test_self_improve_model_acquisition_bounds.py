"""Bounded, observable cache-pressure contracts for model acquisition."""

from __future__ import annotations

import multiprocessing
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import general_ludd.self_improve.model_lifecycle as lifecycle
from general_ludd.local_model._local_model_configs import LocalModelConfig
from general_ludd.self_improve.model_lifecycle import (
    ModelAcquisitionEvent,
    ModelAcquisitionPhase,
    ModelLeaseManager,
)
from general_ludd.small_models.download import DownloadedModel, DownloadSource

_REVISION = "a" * 40
_SECRET = "PRIVATE-CACHE-CREDENTIAL"


def _config() -> LocalModelConfig:
    return LocalModelConfig(
        name="bounded-coder",
        repo=f"example/{_SECRET}",
        filename="bounded-coder.Q4_K_M.gguf",
        size_mb=1,
        category="coding",
        ci_safe=True,
    )


class _Downloader:
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root
        self.calls = 0

    def download_gguf(
        self,
        model_id: str,
        filename: str,
        revision: str | None = None,
        *,
        local_files_only: bool = False,
    ) -> DownloadedModel:
        del local_files_only
        self.calls += 1
        assert revision is not None
        artifact = (
            self.cache_root
            / f"models--{model_id.replace('/', '--')}"
            / "snapshots"
            / revision
            / filename
        )
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"GGUF bounded model")
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
    quota_bytes: int,
    event_sink: Callable[[ModelAcquisitionEvent], None] | None = None,
    revision_deleter: Callable[[Path, str], None] | None = None,
    acquisition_timeout_seconds: float = 1.0,
) -> ModelLeaseManager:
    return ModelLeaseManager(
        cache_root=tmp_path / "cache",
        quota_bytes=quota_bytes,
        reserve_bytes=0,
        model_selector=lambda _task: _config(),
        revision_resolver=lambda _repo: _REVISION,
        downloader_factory=lambda _root: downloader,
        disk_free=lambda _root: 1 << 50,
        revision_deleter=revision_deleter,
        process_started=lambda pid: 123.0 if pid == os.getpid() else None,
        event_sink=event_sink,
        heartbeat_interval_seconds=0.01,
        acquisition_timeout_seconds=acquisition_timeout_seconds,
    )


def _create_partial_and_block(cache_root: str) -> None:
    partial = Path(cache_root) / "models--example--bounded" / "blobs" / "new.incomplete"
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"partial transfer")
    time.sleep(60)


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_acquisition_deadline_must_be_positive_and_finite(
    tmp_path: Path,
    timeout: float,
) -> None:
    with pytest.raises(ValueError, match="acquisition deadline"):
        _manager(
            tmp_path,
            _Downloader(tmp_path / "cache"),
            quota_bytes=64 * 1024 * 1024,
            acquisition_timeout_seconds=timeout,
        )


def test_bounded_process_timeout_is_joined_and_cleans_only_new_partials(
    tmp_path: Path,
) -> None:
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        quota_bytes=64 * 1024 * 1024,
        acquisition_timeout_seconds=0.5,
    )
    preexisting = manager.cache_root / "models--example--other" / "blobs" / "old.incomplete"
    preexisting.parent.mkdir(parents=True)
    preexisting.write_bytes(b"resume-safe")
    before_children = {child.pid for child in multiprocessing.active_children()}

    with pytest.raises(TimeoutError, match="acquisition deadline"):
        manager._run_isolated_operation(
            _create_partial_and_block,
            (str(manager.cache_root),),
            operation_name="download",
            clean_partial_transfers=True,
            repository_id="example/bounded",
        )

    assert preexisting.read_bytes() == b"resume-safe"
    assert not (
        manager.cache_root
        / "models--example--bounded"
        / "blobs"
        / "new.incomplete"
    ).exists()
    assert {
        child.pid for child in multiprocessing.active_children()
    } == before_children


def test_existing_partial_payload_counts_toward_predownload_quota(
    tmp_path: Path,
) -> None:
    events: list[ModelAcquisitionEvent] = []
    downloader = _Downloader(tmp_path / "cache")
    manager = _manager(
        tmp_path,
        downloader,
        quota_bytes=2 * 1024 * 1024,
        event_sink=events.append,
    )
    partial = (
        manager.cache_root
        / "models--external--model"
        / "blobs"
        / "external.incomplete"
    )
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"x" * (1536 * 1024))

    with (
        pytest.raises(RuntimeError, match="insufficient model cache headroom"),
        manager.acquire("implement code"),
    ):
        pytest.fail("quota refusal must happen before a download starts")

    assert downloader.calls == 0
    assert partial.is_file()
    assert ModelAcquisitionPhase.EVICTION_REFUSED in {
        event.phase for event in events
    }
    assert _SECRET not in repr(events)


def test_eviction_emits_planned_and_completed_and_preserves_unowned_payload(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    downloader = _Downloader(cache_root)
    owner = _manager(tmp_path, downloader, quota_bytes=64 * 1024 * 1024)
    with owner.acquire("implement code") as acquired:
        owned_path = acquired.path

    unowned = cache_root / "models--external--model" / "blobs" / "foreign.bin"
    unowned.parent.mkdir(parents=True)
    unowned.write_bytes(b"foreign")
    events: list[ModelAcquisitionEvent] = []

    def delete_revision(_cache: Path, revision: str) -> None:
        assert revision == _REVISION
        owned_path.unlink()

    pressure = _manager(
        tmp_path,
        downloader,
        quota_bytes=len(unowned.read_bytes()),
        event_sink=events.append,
        revision_deleter=delete_revision,
    )

    assert pressure.reclaim(required_bytes=0) == (owned_path,)
    assert unowned.read_bytes() == b"foreign"
    phases = [event.phase for event in events]
    assert phases == [
        ModelAcquisitionPhase.EVICTION_PLANNED,
        ModelAcquisitionPhase.EVICTION_COMPLETED,
    ]
    assert _SECRET not in repr(events)


def test_leased_pressure_emits_refused_without_deleting_any_payload(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    downloader = _Downloader(cache_root)
    owner = _manager(tmp_path, downloader, quota_bytes=64 * 1024 * 1024)
    events: list[ModelAcquisitionEvent] = []
    deletes: list[str] = []
    pressure = _manager(
        tmp_path,
        downloader,
        quota_bytes=1,
        event_sink=events.append,
        revision_deleter=lambda _cache, revision: deletes.append(revision),
    )

    with owner.acquire("implement code") as active:
        with pytest.raises(RuntimeError, match="insufficient model cache headroom"):
            pressure.reclaim(required_bytes=0)
        assert active.path.is_file()

    assert deletes == []
    assert [event.phase for event in events] == [
        ModelAcquisitionPhase.EVICTION_REFUSED
    ]


def _return_value(value: str) -> str:
    return value


def _return_revision(_repo: str) -> str:
    return _REVISION


def _raise_value_error() -> None:
    raise ValueError("bounded child failure")


class _UnpicklableError(Exception):
    def __init__(self) -> None:
        super().__init__("unpicklable")
        self.callback = lambda: None


def _raise_unpicklable_error() -> None:
    raise _UnpicklableError


def test_isolated_operation_returns_or_propagates_a_bounded_child_result(
    tmp_path: Path,
) -> None:
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        quota_bytes=64 * 1024 * 1024,
        acquisition_timeout_seconds=5,
    )

    assert (
        manager._run_isolated_operation(
            _return_value,
            ("complete",),
            operation_name="revision",
            clean_partial_transfers=False,
        )
        == "complete"
    )
    with pytest.raises(ValueError, match="bounded child failure"):
        manager._run_isolated_operation(
            _raise_value_error,
            (),
            operation_name="revision",
            clean_partial_transfers=False,
        )
    with pytest.raises(RuntimeError, match="_UnpicklableError"):
        manager._run_isolated_operation(
            _raise_unpicklable_error,
            (),
            operation_name="revision",
            clean_partial_transfers=False,
        )
    with pytest.raises(ValueError, match="unsupported"):
        manager._run_isolated_operation(
            _return_value,
            ("unused",),
            operation_name="secret-name",
            clean_partial_transfers=False,
        )


def test_default_revision_path_uses_the_bounded_worker(
    tmp_path: Path,
) -> None:
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        quota_bytes=64 * 1024 * 1024,
        acquisition_timeout_seconds=5,
    )
    manager._isolate_revision_resolution = True
    manager._resolve_revision = _return_revision

    assert manager.resolve_revision("example/public") == _REVISION


@pytest.mark.parametrize("required", [1.5, "1", True, -1])
def test_reclaim_rejects_non_integer_or_negative_requirements(
    tmp_path: Path,
    required: object,
) -> None:
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        quota_bytes=64 * 1024 * 1024,
    )
    with pytest.raises(ValueError, match="non-negative integer"):
        manager.reclaim(required_bytes=cast(int, required))


def test_payload_measurement_counts_physical_files_once_and_skips_metadata(
    tmp_path: Path,
) -> None:
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        quota_bytes=64 * 1024 * 1024,
    )
    payload = manager.cache_root / "models--example--physical" / "blobs" / "payload"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"physical")
    hardlink = payload.with_name("hardlink")
    os.link(payload, hardlink)
    payload.with_name("symlink").symlink_to(payload)
    metadata = manager.cache_root / ".gludd" / "ignored-payload"
    metadata.write_bytes(b"ignored" * 100)

    assert manager._cache_payload_bytes() == len(b"physical")


def test_eviction_failure_emits_refusal_and_never_claims_completion(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    downloader = _Downloader(cache_root)
    owner = _manager(tmp_path, downloader, quota_bytes=64 * 1024 * 1024)
    with owner.acquire("implement code") as acquired:
        owned_path = acquired.path

    events: list[ModelAcquisitionEvent] = []

    def rejected_delete(_cache: Path, _revision: str) -> None:
        raise OSError("deletion rejected")

    pressure = _manager(
        tmp_path,
        downloader,
        quota_bytes=1,
        event_sink=events.append,
        revision_deleter=rejected_delete,
    )
    with pytest.raises(OSError, match="deletion rejected"):
        pressure.reclaim(required_bytes=0)

    assert owned_path.is_file()
    assert [event.phase for event in events] == [
        ModelAcquisitionPhase.EVICTION_PLANNED,
        ModelAcquisitionPhase.EVICTION_REFUSED,
    ]
    assert ModelAcquisitionPhase.EVICTION_COMPLETED not in {
        event.phase for event in events
    }


def test_timeout_environment_must_also_be_finite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GLUDD_SELF_IMPROVE_MODEL_ACQUISITION_TIMEOUT_SECONDS",
        "not-a-number",
    )
    with pytest.raises(ValueError, match="acquisition deadline"):
        ModelLeaseManager(
            cache_root=tmp_path / "cache",
            quota_bytes=1024,
            reserve_bytes=0,
            model_selector=lambda _task: _config(),
            revision_resolver=lambda _repo: _REVISION,
            downloader_factory=lambda root: _Downloader(root),
            disk_free=lambda _root: 1 << 50,
            process_started=lambda _pid: 123.0,
        )


def _exit_without_result() -> None:
    os._exit(0)


def test_bounded_worker_without_a_result_fails_closed(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        quota_bytes=64 * 1024 * 1024,
        acquisition_timeout_seconds=5,
    )

    with pytest.raises(RuntimeError, match="returned no result"):
        manager._run_isolated_operation(
            _exit_without_result,
            (),
            operation_name="revision",
            clean_partial_transfers=False,
        )


def test_timeout_without_partial_ownership_still_joins_worker(
    tmp_path: Path,
) -> None:
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        quota_bytes=64 * 1024 * 1024,
        acquisition_timeout_seconds=0.5,
    )

    with pytest.raises(TimeoutError, match="acquisition deadline"):
        manager._run_isolated_operation(
            _create_partial_and_block,
            (str(manager.cache_root),),
            operation_name="revision",
            clean_partial_transfers=False,
        )

    assert not any(
        child.name.startswith("gludd-model-revision-")
        for child in multiprocessing.active_children()
    )


def test_partial_snapshot_without_repo_filter_rejects_escaped_symlink(
    tmp_path: Path,
) -> None:
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        quota_bytes=64 * 1024 * 1024,
    )
    partial = manager.cache_root / "models--example--safe" / "blobs" / "safe.incomplete"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"safe")
    outside = tmp_path / "outside.incomplete"
    outside.write_bytes(b"outside")
    partial.with_name("escaped.incomplete").symlink_to(outside)

    assert manager._partial_transfer_paths(None) == {partial}


def test_bounded_json_reader_rejects_non_object_payload(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "array.json"
    payload.write_text("[]", encoding="utf-8")

    with pytest.raises(RuntimeError, match="state is invalid"):
        lifecycle._read_json(payload, "state")


def test_payload_walk_error_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        quota_bytes=64 * 1024 * 1024,
    )

    def failed_walk(
        _root: Path,
        *,
        followlinks: bool,
        onerror: Callable[[OSError], None],
    ) -> list[tuple[str, list[str], list[str]]]:
        assert followlinks is False
        onerror(OSError("walk denied"))
        return []

    monkeypatch.setattr(os, "walk", failed_walk)
    with pytest.raises(RuntimeError, match="cannot inspect model cache payload"):
        manager._cache_payload_bytes()
