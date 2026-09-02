"""Authentication-mode contracts for self-improvement Hub acquisition."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from huggingface_hub.errors import HfHubHTTPError

from general_ludd.local_model._local_model_configs import LocalModelConfig
from general_ludd.self_improve.model_lifecycle import (
    ModelAcquisitionAuthMode,
    ModelAcquisitionEvent,
    ModelAcquisitionFailure,
    ModelAcquisitionPhase,
    ModelLeaseManager,
    _default_revision_resolver,
)
from general_ludd.small_models.download import (
    DownloadedModel,
    DownloadSource,
    ModelDownloader,
)

_REVISION = "a" * 40
_SECRET = "hf_secret-that-must-never-be-observed"
_GGUF = b"GGUF\x03\x00\x00\x00authenticated-model"


def _config() -> LocalModelConfig:
    return LocalModelConfig(
        name="auth-coder",
        repo="example/auth-coder",
        filename="auth-coder.Q4_K_M.gguf",
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
        artifact.write_bytes(_GGUF)
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
    *,
    downloader: _Downloader,
    events: list[ModelAcquisitionEvent],
    resolver: object,
    hf_token_required: bool | None = None,
) -> ModelLeaseManager:
    assert callable(resolver)
    return ModelLeaseManager(
        cache_root=tmp_path / "cache",
        quota_bytes=64 * 1024 * 1024,
        reserve_bytes=0,
        model_selector=lambda _task: _config(),
        revision_resolver=resolver,
        downloader_factory=lambda _root: downloader,
        disk_free=lambda _root: 1 << 50,
        process_started=lambda _pid: 123.0,
        event_sink=events.append,
        heartbeat_interval_seconds=1.0,
        hf_token_required=hf_token_required,
    )


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("HF_TOKEN", None, False),
        ("HF_TOKEN", _SECRET, _SECRET),
        ("HUGGING_FACE_HUB_TOKEN", _SECRET, _SECRET),
    ],
)
def test_revision_resolver_forwards_only_explicit_environment_auth(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str | None,
    expected: str | bool,
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    if value is not None:
        monkeypatch.setenv(name, value)
    api = SimpleNamespace(
        model_info=lambda **_kwargs: SimpleNamespace(sha=_REVISION)
    )

    with patch("huggingface_hub.HfApi", return_value=api) as api_type:
        assert _default_revision_resolver("example/public") == _REVISION

    assert api_type.call_args.kwargs["token"] == expected


def test_downloader_forwards_explicit_token_and_preserves_upstream_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("HF_TOKEN", _SECRET)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    downloaded = tmp_path / "model.gguf"
    downloaded.write_bytes(_GGUF)
    downloader = ModelDownloader(cache_dir=str(tmp_path / "cache"))
    warning = "X-HF-Warning: authenticated rate-limit guidance"
    upstream_logger = "huggingface_hub.utils._http"

    def hub_download(**kwargs: object) -> str:
        assert kwargs["token"] == _SECRET
        logging.getLogger(upstream_logger).warning(warning)
        return str(downloaded)

    with (
        patch("huggingface_hub.try_to_load_from_cache", return_value=None),
        patch("huggingface_hub.hf_hub_download", side_effect=hub_download),
        caplog.at_level(logging.WARNING, logger=upstream_logger),
    ):
        downloader.download_gguf(
            "example/public",
            "model.gguf",
            revision=_REVISION,
        )

    assert warning in caplog.text


def test_downloader_preserves_vendor_http_failure_type(
    tmp_path: Path,
) -> None:
    downloaded = ModelDownloader(cache_dir=str(tmp_path / "cache"))
    response = httpx.Response(
        429,
        request=httpx.Request("GET", "https://huggingface.co/example/public"),
    )
    vendor_failure = HfHubHTTPError(
        "vendor HTTP failure",
        response=response,
    )

    with (
        patch("huggingface_hub.try_to_load_from_cache", return_value=None),
        patch("huggingface_hub.hf_hub_download", side_effect=vendor_failure),
        pytest.raises(HfHubHTTPError) as observed,
    ):
        downloaded.download_gguf(
            "example/public",
            "model.gguf",
            revision=_REVISION,
        )

    assert observed.value is vendor_failure


@pytest.mark.parametrize(
    ("token", "expected_mode"),
    [
        (None, ModelAcquisitionAuthMode.ANONYMOUS_PUBLIC),
        (_SECRET, ModelAcquisitionAuthMode.EXPLICIT_ENV_TOKEN),
    ],
)
def test_auth_mode_is_typed_secret_safe_and_emitted_before_hub_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token: str | None,
    expected_mode: ModelAcquisitionAuthMode,
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("HF_TOKEN", token)
    events: list[ModelAcquisitionEvent] = []

    def resolver(_repo: str) -> str:
        assert events[-1].phase is ModelAcquisitionPhase(expected_mode.value)
        assert events[-1].auth_mode is expected_mode
        return _REVISION

    manager = _manager(
        tmp_path,
        downloader=_Downloader(tmp_path / "cache"),
        events=events,
        resolver=resolver,
    )

    assert manager.resolve_revision("example/public") == _REVISION
    assert [event.phase for event in events] == [
        ModelAcquisitionPhase.REVISION_RESOLUTION_STARTED,
        ModelAcquisitionPhase(expected_mode.value),
        ModelAcquisitionPhase.REVISION_RESOLUTION_COMPLETED,
    ]
    auth_event = events[1]
    assert auth_event.auth_mode is expected_mode
    assert _SECRET not in repr(events)
    assert "example/public" not in repr(events)


def test_strict_auth_policy_refuses_before_revision_hub_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    calls = 0
    events: list[ModelAcquisitionEvent] = []

    def resolver(_repo: str) -> str:
        nonlocal calls
        calls += 1
        return _REVISION

    manager = _manager(
        tmp_path,
        downloader=_Downloader(tmp_path / "cache"),
        events=events,
        resolver=resolver,
        hf_token_required=True,
    )

    with pytest.raises(RuntimeError, match="requires an explicit Hugging Face token"):
        manager.resolve_revision("example/private")

    assert calls == 0
    assert [event.phase for event in events] == [
        ModelAcquisitionPhase.REVISION_RESOLUTION_STARTED,
        ModelAcquisitionPhase.ANONYMOUS_PUBLIC,
        ModelAcquisitionPhase.REVISION_RESOLUTION_FAILED,
    ]
    assert events[1].auth_mode is ModelAcquisitionAuthMode.ANONYMOUS_PUBLIC
    assert events[-1].failure is ModelAcquisitionFailure.VALIDATION


def test_environment_strict_policy_refuses_before_download_hub_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("GLUDD_SELF_IMPROVE_HF_TOKEN_REQUIRED", "true")
    downloader = _Downloader(tmp_path / "cache")
    events: list[ModelAcquisitionEvent] = []
    manager = _manager(
        tmp_path,
        downloader=downloader,
        events=events,
        resolver=lambda _repo: _REVISION,
        hf_token_required=None,
    )

    with (
        pytest.raises(RuntimeError, match="requires an explicit Hugging Face token"),
        manager.acquire(
            "implement code",
            model_config=_config(),
            resolved_revision=_REVISION,
        ),
    ):
        pytest.fail("strict policy must refuse before downloader invocation")

    assert downloader.calls == 0
    assert ModelAcquisitionPhase.ANONYMOUS_PUBLIC in {
        event.phase for event in events
    }
    assert ModelAcquisitionPhase.DOWNLOAD_FAILED in {
        event.phase for event in events
    }


def test_owned_cache_hit_skips_auth_selection_and_all_hub_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    downloader = _Downloader(tmp_path / "cache")
    initial_events: list[ModelAcquisitionEvent] = []
    initial = _manager(
        tmp_path,
        downloader=downloader,
        events=initial_events,
        resolver=lambda _repo: _REVISION,
    )
    with initial.acquire("implement code") as first:
        first_path = first.path

    calls = 0
    cached_events: list[ModelAcquisitionEvent] = []

    def forbidden_resolver(_repo: str) -> str:
        nonlocal calls
        calls += 1
        raise AssertionError("cache hit must not resolve Hub auth or revision")

    strict = _manager(
        tmp_path,
        downloader=downloader,
        events=cached_events,
        resolver=forbidden_resolver,
        hf_token_required=True,
    )
    with strict.acquire("implement code") as cached:
        assert cached.path == first_path

    assert calls == 0
    assert downloader.calls == 1
    assert ModelAcquisitionPhase.CACHE_HIT in {
        event.phase for event in cached_events
    }
    assert not {
        ModelAcquisitionPhase.ANONYMOUS_PUBLIC,
        ModelAcquisitionPhase.EXPLICIT_ENV_TOKEN,
    }.intersection(event.phase for event in cached_events)


def test_isolated_hub_worker_arguments_never_serialize_environment_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HF_TOKEN", _SECRET)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    events: list[ModelAcquisitionEvent] = []
    manager = ModelLeaseManager(
        cache_root=tmp_path / "cache",
        quota_bytes=64 * 1024 * 1024,
        reserve_bytes=0,
        model_selector=lambda _task: _config(),
        disk_free=lambda _root: 1 << 50,
        process_started=lambda _pid: 123.0,
        event_sink=events.append,
    )
    serialized_arguments: list[tuple[object, ...]] = []

    def isolated(
        _operation: object,
        args: tuple[object, ...],
        *,
        operation_name: str,
        **_kwargs: object,
    ) -> object:
        serialized_arguments.append(args)
        assert _SECRET not in repr(args)
        if operation_name == "revision":
            return _REVISION
        artifact = (
            manager.cache_root
            / "models--example--auth-coder"
            / "snapshots"
            / _REVISION
            / _config().filename
        )
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(_GGUF)
        return DownloadedModel(
            model_id=_config().repo,
            local_path=str(artifact),
            source=DownloadSource.GGUF,
            filename=_config().filename,
            revision=_REVISION,
            size_bytes=artifact.stat().st_size,
        )

    monkeypatch.setattr(manager, "_run_isolated_operation", isolated)

    with manager.acquire("implement code") as acquired:
        assert acquired.path.is_file()

    assert len(serialized_arguments) == 2
    assert [
        event.auth_mode
        for event in events
        if event.phase is ModelAcquisitionPhase.EXPLICIT_ENV_TOKEN
    ] == [
        ModelAcquisitionAuthMode.EXPLICIT_ENV_TOKEN,
        ModelAcquisitionAuthMode.EXPLICIT_ENV_TOKEN,
    ]
    assert _SECRET not in repr(events)


@pytest.mark.parametrize("invalid", ["sometimes", "2", "required"])
def test_invalid_environment_strict_policy_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    monkeypatch.setenv("GLUDD_SELF_IMPROVE_HF_TOKEN_REQUIRED", invalid)

    with pytest.raises(ValueError, match="HF token required policy"):
        _manager(
            tmp_path,
            downloader=_Downloader(tmp_path / "cache"),
            events=[],
            resolver=lambda _repo: _REVISION,
            hf_token_required=None,
        )
