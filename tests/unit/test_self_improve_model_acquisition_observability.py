"""Typed, secret-safe progress contracts for managed model acquisition."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from general_ludd.local_model._local_model_configs import LocalModelConfig
from general_ludd.self_improve.model_lifecycle import (
    ModelAcquisitionEvent,
    ModelAcquisitionFailure,
    ModelAcquisitionPhase,
    ModelLeaseManager,
)
from general_ludd.small_models.download import DownloadedModel, DownloadSource

_REVISION = "a" * 40
_ARTIFACT = b"GGUF observable model"
_SECRET = "TOP-SECRET-MODEL-TOKEN"


def _config(repo: str = "example/observable-coder") -> LocalModelConfig:
    return LocalModelConfig(
        name="observable-coder",
        repo=repo,
        filename="observable-coder.Q4_K_M.gguf",
        size_mb=1,
        category="coding",
        ci_safe=True,
    )


class _Downloader:
    def __init__(
        self,
        cache_root: Path,
        *,
        release: threading.Event | None = None,
        failure: BaseException | None = None,
    ) -> None:
        self.cache_root = cache_root
        self.release = release
        self.failure = failure
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
        if self.release is not None:
            assert self.release.wait(timeout=0.5), "download heartbeat was not emitted"
        if self.failure is not None:
            raise self.failure
        assert revision is not None
        artifact = (
            self.cache_root
            / f"models--{model_id.replace('/', '--')}"
            / "snapshots"
            / revision
            / filename
        )
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(_ARTIFACT)
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
    event_sink: Callable[[ModelAcquisitionEvent], None],
    revision_resolver: Callable[[str], str] | None = None,
    heartbeat_interval_seconds: float = 0.001,
    repo: str = "example/observable-coder",
) -> ModelLeaseManager:
    return ModelLeaseManager(
        cache_root=tmp_path / "cache",
        quota_bytes=64 * 1024 * 1024,
        reserve_bytes=0,
        model_selector=lambda _task: _config(repo),
        revision_resolver=revision_resolver or (lambda _repo: _REVISION),
        downloader_factory=lambda _root: downloader,
        disk_free=lambda _root: 1 << 50,
        process_started=lambda _pid: 123.0,
        event_sink=event_sink,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )


def test_revision_resolution_emits_bounded_progress_without_identifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    repo = f"https://user:{_SECRET}@example.invalid/private-model"
    release = threading.Event()
    events: list[ModelAcquisitionEvent] = []

    def sink(event: ModelAcquisitionEvent) -> None:
        events.append(event)
        if event.phase is ModelAcquisitionPhase.REVISION_RESOLUTION_PROGRESS:
            release.set()

    def resolve(requested_repo: str) -> str:
        assert requested_repo == repo
        assert release.wait(timeout=0.5), "revision heartbeat was not emitted"
        return _REVISION

    downloader = _Downloader(tmp_path / "cache")
    manager = _manager(
        tmp_path,
        downloader,
        event_sink=sink,
        revision_resolver=resolve,
        repo=repo,
    )

    assert manager.resolve_revision(repo) == _REVISION

    assert [event.phase for event in events] == [
        ModelAcquisitionPhase.REVISION_RESOLUTION_STARTED,
        ModelAcquisitionPhase.ANONYMOUS_PUBLIC,
        ModelAcquisitionPhase.REVISION_RESOLUTION_PROGRESS,
        ModelAcquisitionPhase.REVISION_RESOLUTION_COMPLETED,
    ]
    assert len({event.operation_id for event in events}) == 1
    assert all(re.fullmatch(r"[0-9a-f]{16}", event.repository_key) for event in events)
    assert all(event.elapsed_seconds >= 0 for event in events)
    assert events[-1].revision == _REVISION
    assert _SECRET not in repr(events)
    assert repo not in repr(events)
    assert not any(
        thread.name.startswith("gludd-model-acquire-")
        for thread in threading.enumerate()
    )


def test_download_emits_progress_completion_then_cache_hit_without_redownload(
    tmp_path: Path,
) -> None:
    release = threading.Event()
    events: list[ModelAcquisitionEvent] = []

    def sink(event: ModelAcquisitionEvent) -> None:
        events.append(event)
        if event.phase is ModelAcquisitionPhase.DOWNLOAD_PROGRESS:
            release.set()

    downloader = _Downloader(tmp_path / "cache", release=release)
    manager = _manager(tmp_path, downloader, event_sink=sink)

    with manager.acquire("implement a tested repair") as acquired:
        assert acquired.path.is_file()

    first_phases = [event.phase for event in events]
    assert ModelAcquisitionPhase.DOWNLOAD_STARTED in first_phases
    assert ModelAcquisitionPhase.DOWNLOAD_PROGRESS in first_phases
    assert ModelAcquisitionPhase.DOWNLOAD_COMPLETED in first_phases

    events.clear()
    with manager.acquire("implement a tested repair") as cached:
        assert cached.path == acquired.path

    assert downloader.calls == 1
    assert ModelAcquisitionPhase.CACHE_HIT in {
        event.phase for event in events
    }
    assert ModelAcquisitionPhase.DOWNLOAD_STARTED not in {
        event.phase for event in events
    }


def test_download_failure_is_typed_secret_safe_and_releases_acquisition_lock(
    tmp_path: Path,
) -> None:
    events: list[ModelAcquisitionEvent] = []
    downloader = _Downloader(
        tmp_path / "cache",
        failure=RuntimeError(f"remote said {_SECRET}"),
    )
    manager = _manager(tmp_path, downloader, event_sink=events.append)

    with (
        pytest.raises(RuntimeError, match="remote said"),
        manager.acquire("implement code"),
    ):
        pytest.fail("failed download must not yield")

    phases = [event.phase for event in events]
    assert ModelAcquisitionPhase.DOWNLOAD_STARTED in phases
    assert ModelAcquisitionPhase.DOWNLOAD_FAILED in phases
    assert ModelAcquisitionPhase.DOWNLOAD_COMPLETED not in phases
    failure = next(
        event
        for event in events
        if event.phase is ModelAcquisitionPhase.DOWNLOAD_FAILED
    )
    assert failure.failure is ModelAcquisitionFailure.INTERNAL
    assert _SECRET not in repr(events)
    acquiring = manager.cache_root / ".gludd" / "acquiring"
    assert list(acquiring.glob("*.lock")) == []
    assert not any(
        thread.name.startswith("gludd-model-acquire-")
        for thread in threading.enumerate()
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(_SECRET), ModelAcquisitionFailure.TIMEOUT),
        (OSError(_SECRET), ModelAcquisitionFailure.IO),
        (ValueError(_SECRET), ModelAcquisitionFailure.VALIDATION),
        (KeyboardInterrupt(_SECRET), ModelAcquisitionFailure.INTERRUPTED),
    ],
)
def test_revision_failure_uses_stable_category_without_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected: ModelAcquisitionFailure,
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    events: list[ModelAcquisitionEvent] = []

    def fail_resolution(_repo: str) -> str:
        raise error

    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        event_sink=events.append,
        revision_resolver=fail_resolution,
    )

    with pytest.raises(type(error)):
        manager.resolve_revision(f"example/{_SECRET}")

    assert [event.phase for event in events] == [
        ModelAcquisitionPhase.REVISION_RESOLUTION_STARTED,
        ModelAcquisitionPhase.ANONYMOUS_PUBLIC,
        ModelAcquisitionPhase.REVISION_RESOLUTION_FAILED,
    ]
    assert events[-1].failure is expected
    assert _SECRET not in repr(events)
    assert not any(
        thread.name.startswith("gludd-model-acquire-")
        for thread in threading.enumerate()
    )


def test_event_sink_failure_cannot_break_resource_acquisition_or_leak_secret(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def rejected_sink(_event: ModelAcquisitionEvent) -> None:
        raise RuntimeError(_SECRET)

    downloader = _Downloader(tmp_path / "cache")
    manager = _manager(tmp_path, downloader, event_sink=rejected_sink)

    with manager.acquire("implement code") as acquired:
        assert acquired.path.is_file()

    assert downloader.calls == 1
    assert _SECRET not in caplog.text


@pytest.mark.parametrize("interval", [0, -1, True, float("nan"), float("inf")])
def test_heartbeat_interval_must_be_positive_and_finite(
    tmp_path: Path,
    interval: float,
) -> None:
    with pytest.raises(ValueError, match="heartbeat interval"):
        _manager(
            tmp_path,
            _Downloader(tmp_path / "cache"),
            event_sink=lambda _event: None,
            heartbeat_interval_seconds=interval,
        )
