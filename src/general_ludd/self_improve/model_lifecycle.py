"""Owned acquisition, leasing, and bounded eviction for self-improvement GGUFs."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import multiprocessing
import os
import re
import shutil
import stat
import sys
import threading
import time
import uuid
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Protocol, cast

from general_ludd.local_model._local_model_configs import _LOCAL_MODELS, LocalModelConfig
from general_ludd.self_improve.hf_cache_delete import (
    CacheArtifactIdentity,
    HuggingFaceCacheDeletion,
)
from general_ludd.small_models.download import DownloadedModel, ModelDownloader

_MANIFEST_SCHEMA = 1
_RESERVATION_SCHEMA = 1
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_QUOTA_BYTES = 8 * 1024 * 1024 * 1024
_DEFAULT_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
_JSON_LIMIT = 64 * 1024
_DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15.0
_DEFAULT_ACQUISITION_TIMEOUT_SECONDS = 600.0
_PROCESS_SHUTDOWN_GRACE_SECONDS = 5.0
DEFAULT_SELF_IMPROVE_MODEL_PRIORITY = (
    "qwen2.5-coder-1.5b",
    "qwen2.5-coder-3b",
    "codellama-7b",
)

logger = logging.getLogger(__name__)


class ModelAcquisitionAuthMode(StrEnum):
    """Secret-free authentication choice for one Hugging Face operation."""

    ANONYMOUS_PUBLIC = "anonymous_public"
    EXPLICIT_ENV_TOKEN = "explicit_env_token"


class ModelAcquisitionPhase(StrEnum):
    """Secret-safe phases emitted during managed model acquisition."""

    CACHE_HIT = "cache_hit"
    ANONYMOUS_PUBLIC = "anonymous_public"
    EXPLICIT_ENV_TOKEN = "explicit_env_token"
    REVISION_RESOLUTION_STARTED = "revision_resolution_started"
    REVISION_RESOLUTION_PROGRESS = "revision_resolution_progress"
    REVISION_RESOLUTION_COMPLETED = "revision_resolution_completed"
    REVISION_RESOLUTION_FAILED = "revision_resolution_failed"
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_PROGRESS = "download_progress"
    DOWNLOAD_COMPLETED = "download_completed"
    DOWNLOAD_FAILED = "download_failed"
    EVICTION_PLANNED = "eviction_planned"
    EVICTION_COMPLETED = "eviction_completed"
    EVICTION_REFUSED = "eviction_refused"


class ModelAcquisitionFailure(StrEnum):
    """Bounded failure categories that never include exception messages."""

    TIMEOUT = "timeout"
    IO = "io"
    VALIDATION = "validation"
    INTERRUPTED = "interrupted"
    INTERNAL = "internal"
    CACHE_RECLAIM = "cache_reclaim"
    CACHE_HEADROOM = "cache_headroom"


class ModelAcquisitionError(RuntimeError):
    """Typed, secret-safe failure raised before a managed model is usable."""

    def __init__(self, failure: ModelAcquisitionFailure) -> None:
        """Retain only a stable category and a bounded operator-safe message."""
        if failure is ModelAcquisitionFailure.CACHE_RECLAIM:
            message = "managed model acquisition failed: cache_reclaim"
        elif failure is ModelAcquisitionFailure.CACHE_HEADROOM:
            message = (
                "insufficient model cache headroom: all remaining artifacts are "
                "leased, reserved, or the configured quota/reserve cannot admit "
                "the request"
            )
        else:
            raise ValueError("unsupported typed model acquisition failure")
        super().__init__(message)
        self.failure = failure


@dataclass(frozen=True, slots=True)
class ModelAcquisitionEvent:
    """One bounded event for an observable managed-model operation."""

    phase: ModelAcquisitionPhase
    operation_id: str
    repository_key: str
    model_key: str | None
    revision: str | None
    elapsed_seconds: float
    failure: ModelAcquisitionFailure | None = None
    auth_mode: ModelAcquisitionAuthMode | None = None


@dataclass(frozen=True, slots=True)
class ModelCacheDiagnostic:
    """Secret-safe cache pressure and reclamation feasibility evidence."""

    cache_key: str
    payload_bytes: int
    required_bytes: int
    quota_bytes: int
    reserve_bytes: int
    disk_free_bytes: int
    owned_count: int
    leased_count: int
    eviction_candidate_count: int
    under_pressure: bool
    can_reclaim: bool


@dataclass(frozen=True, slots=True)
class ModelArtifactIdentity:
    """Exact immutable identity used to protect one planned GGUF artifact."""

    model_id: str
    repo_id: str
    filename: str
    revision: str

    def __post_init__(self) -> None:
        """Reject ambiguous, mutable, or path-escaping artifact identities."""
        strings = (self.model_id, self.repo_id, self.filename, self.revision)
        if not all(isinstance(item, str) and item.strip() == item and item for item in strings):
            raise ValueError("model artifact identity fields must be non-empty strings")
        revision = self.revision.lower()
        if _SHA_RE.fullmatch(revision) is None:
            raise ValueError("model artifact revision must be an immutable commit")
        filename = Path(self.filename)
        if filename.is_absolute() or ".." in filename.parts:
            raise ValueError("model artifact filename must stay inside its repository")
        object.__setattr__(self, "revision", revision)


class _ReservationState(StrEnum):
    PLANNED = "planned"
    ELIGIBLE = "eligible"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class _ActivePlanReservations:
    protected: frozenset[ModelArtifactIdentity]
    failure_hints: frozenset[ModelArtifactIdentity]


class ModelPlanReservation:
    """Mutable atomic state for one bounded candidate retry plan."""

    def __init__(
        self,
        manager: ModelLeaseManager,
        path: Path,
        candidates: frozenset[ModelArtifactIdentity],
        failure_hints: frozenset[ModelArtifactIdentity],
    ) -> None:
        """Create an in-memory plan whose persistence is owned by the manager."""
        self._manager = manager
        self.path = path
        self._created_ns = time.time_ns()
        self._states = {
            identity: _ReservationState.PLANNED for identity in candidates
        }
        self._failure_hints = failure_hints

    def mark_eligible(self, identity: ModelArtifactIdentity) -> None:
        """Allow normal LRU eviction after this candidate finishes generation."""
        self._transition(identity, _ReservationState.ELIGIBLE)

    def mark_failed(self, identity: ModelArtifactIdentity) -> None:
        """Prioritize an exact failed candidate ahead of normal LRU entries."""
        self._transition(identity, _ReservationState.FAILED)

    def _transition(
        self,
        identity: ModelArtifactIdentity,
        state: _ReservationState,
    ) -> None:
        if not isinstance(identity, ModelArtifactIdentity) or identity not in self._states:
            raise ValueError("reservation transition requires a planned identity")
        previous = self._states[identity]
        if previous is _ReservationState.FAILED and state is _ReservationState.ELIGIBLE:
            raise RuntimeError("failed model reservation state cannot become eligible")
        if previous is state:
            return
        self._states[identity] = state
        try:
            self._manager._write_plan_reservation(self, require_existing=True)
        except BaseException:
            self._states[identity] = previous
            raise


class _Downloader(Protocol):
    def download_gguf(
        self,
        model_id: str,
        filename: str,
        revision: str | None = None,
        *,
        local_files_only: bool = False,
    ) -> DownloadedModel: ...


@dataclass(frozen=True)
class AcquiredModel:
    """Immutable identity and ownership evidence for one active model lease."""

    path: Path
    model_id: str
    repo_id: str | None
    filename: str
    resolved_revision: str | None
    artifact_sha256: str
    source: str
    manifest_path: Path
    lease_path: Path


@dataclass(frozen=True)
class _OwnedArtifact:
    model_id: str
    repo_id: str
    filename: str
    revision: str
    artifact_sha256: str
    path: Path
    size_bytes: int
    last_used_ns: int
    manifest_path: Path


def _default_cache_root() -> Path:
    configured = os.environ.get("GLUDD_SELF_IMPROVE_MODEL_CACHE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "general-ludd" / "models" / "self-improve"


def _default_selector(_task: str) -> LocalModelConfig:
    requested = os.environ.get("GLUDD_SELF_IMPROVE_MODEL", "").strip()
    coding = [model for model in _LOCAL_MODELS if model.category == "coding"]
    if requested:
        matches = [
            model
            for model in coding
            if requested in (model.name, model.repo, *model.aliases)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "GLUDD_SELF_IMPROVE_MODEL must identify exactly one coding model"
            )
        return matches[0]

    by_name = {model.name: model for model in coding}
    for name in DEFAULT_SELF_IMPROVE_MODEL_PRIORITY:
        if name in by_name:
            return by_name[name]
    if not coding:
        raise RuntimeError("no coding model is configured")
    return min(coding, key=lambda model: (model.size_mb, model.name))


def _hf_token_from_environment() -> str | None:
    """Return an explicitly configured token without retaining or logging it."""
    return (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or None
    )


def _hf_token_required_from_environment() -> bool:
    """Parse the fail-closed optional Hugging Face authentication policy."""
    raw = os.environ.get("GLUDD_SELF_IMPROVE_HF_TOKEN_REQUIRED", "false")
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(
        "HF token required policy must be a boolean value"
    )


def _default_revision_resolver(repo_id: str) -> str:
    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    token: str | bool = _hf_token_from_environment() or False
    try:
        info = HfApi(token=token).model_info(repo_id=repo_id, revision="main")
    except (HfHubHTTPError, OSError) as exc:
        raise RuntimeError(f"Hub revision lookup failed for {repo_id}: {exc}") from exc
    revision = str(info.sha or "").lower()
    if _SHA_RE.fullmatch(revision) is None:
        raise RuntimeError(f"Hub did not return an immutable revision for {repo_id}")
    return revision


def _default_downloader(cache_root: Path) -> _Downloader:
    return ModelDownloader(cache_dir=str(cache_root))


def _download_default_gguf(
    cache_root: str,
    model_id: str,
    filename: str,
    revision: str,
) -> DownloadedModel:
    """Download one immutable GGUF inside the isolated acquisition worker."""
    return _default_downloader(Path(cache_root)).download_gguf(
        model_id,
        filename,
        revision=revision,
    )


def _default_disk_free(cache_root: Path) -> int:
    return shutil.disk_usage(cache_root).free


def _default_process_started(pid: int) -> float | None:
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return None
    except (psutil.AccessDenied, OSError) as exc:
        raise RuntimeError(f"cannot verify model lease owner pid={pid}") from exc



def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _JSON_LIMIT:
            raise ValueError("not a bounded regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"{label} is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} is invalid: {path}")
    return cast(dict[str, object], value)


def _bounded_process_entry(
    sender: Connection,
    operation: Callable[..., object],
    args: tuple[object, ...],
) -> None:
    """Execute one blocking external operation and return one bounded payload."""
    try:
        try:
            sender.send(("result", operation(*args)))
        except BaseException as error:
            try:
                sender.send(("error", error))
            except Exception:
                sender.send(("error_type", type(error).__name__))
    finally:
        sender.close()


def _run_bounded_process(
    operation: Callable[..., object],
    args: tuple[object, ...],
    *,
    timeout_seconds: float,
    process_name: str,
) -> object:
    """Run a blocking external operation in a terminable, always-joined process."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_bounded_process_entry,
        args=(sender, operation, args),
        name=process_name,
        daemon=False,
    )
    started = False
    try:
        process.start()
        started = True
        sender.close()
        process.join(timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(_PROCESS_SHUTDOWN_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(_PROCESS_SHUTDOWN_GRACE_SECONDS)
            if process.is_alive():
                raise RuntimeError("model acquisition worker could not be stopped")
            raise TimeoutError("model acquisition deadline exceeded")
        if not receiver.poll(_PROCESS_SHUTDOWN_GRACE_SECONDS):
            raise RuntimeError("model acquisition worker returned no result")
        try:
            payload = receiver.recv()
        except EOFError as exc:
            raise RuntimeError(
                "model acquisition worker returned no result"
            ) from exc
    finally:
        if started and process.is_alive():
            process.terminate()
            process.join(_PROCESS_SHUTDOWN_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(_PROCESS_SHUTDOWN_GRACE_SECONDS)
        receiver.close()
        with suppress(OSError, ValueError):
            sender.close()
        if started and not process.is_alive():
            process.close()

    if (
        not isinstance(payload, tuple)
        or len(payload) != 2
        or not isinstance(payload[0], str)
    ):
        raise RuntimeError("model acquisition worker returned an invalid result")
    status, value = payload
    if status == "result":
        return value
    if status == "error" and isinstance(value, BaseException):
        raise value
    if status == "error_type" and isinstance(value, str):
        raise RuntimeError(f"model acquisition worker failed with {value}")
    raise RuntimeError("model acquisition worker returned an invalid result")


class ModelLeaseManager:
    """Own self-improvement model acquisition and reclaim only safe cache entries."""

    def __init__(
        self,
        *,
        cache_root: Path | None = None,
        quota_bytes: int | None = None,
        reserve_bytes: int | None = None,
        model_selector: Callable[[str], LocalModelConfig] | None = None,
        revision_resolver: Callable[[str], str] | None = None,
        downloader_factory: Callable[[Path], _Downloader] | None = None,
        disk_free: Callable[[Path], int] | None = None,
        revision_deleter: Callable[[Path, str], None] | None = None,
        process_started: Callable[[int], float | None] | None = None,
        event_sink: Callable[[ModelAcquisitionEvent], None] | None = None,
        heartbeat_interval_seconds: float = _DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        acquisition_timeout_seconds: float | None = None,
        hf_token_required: bool | None = None,
    ) -> None:
        """Initialize a dedicated model cache with explicit quota and reserve."""
        selected_root = cache_root or _default_cache_root()
        selected_root.mkdir(parents=True, exist_ok=True)
        if selected_root.is_symlink():
            raise RuntimeError("self-improvement model cache root must not be a symlink")
        self.cache_root = selected_root.resolve(strict=True)
        self.quota_bytes = (
            quota_bytes
            if quota_bytes is not None
            else int(os.environ.get("GLUDD_SELF_IMPROVE_MODEL_QUOTA_BYTES", _DEFAULT_QUOTA_BYTES))
        )
        self.reserve_bytes = (
            reserve_bytes
            if reserve_bytes is not None
            else int(os.environ.get("GLUDD_SELF_IMPROVE_MODEL_RESERVE_BYTES", _DEFAULT_RESERVE_BYTES))
        )
        if (
            isinstance(self.quota_bytes, bool)
            or self.quota_bytes <= 0
            or isinstance(self.reserve_bytes, bool)
            or self.reserve_bytes < 0
        ):
            raise ValueError("model cache quota must be positive and reserve non-negative")
        if event_sink is not None and not callable(event_sink):
            raise TypeError("model acquisition event sink must be callable")
        if hf_token_required is not None and not isinstance(hf_token_required, bool):
            raise TypeError("HF token required policy must be a boolean or None")
        self._hf_token_required = (
            _hf_token_required_from_environment()
            if hf_token_required is None
            else hf_token_required
        )
        if (
            isinstance(heartbeat_interval_seconds, bool)
            or not isinstance(heartbeat_interval_seconds, (int, float))
            or not math.isfinite(float(heartbeat_interval_seconds))
            or heartbeat_interval_seconds <= 0
        ):
            raise ValueError("model acquisition heartbeat interval must be positive and finite")
        timeout_value: object = (
            acquisition_timeout_seconds
            if acquisition_timeout_seconds is not None
            else os.environ.get(
                "GLUDD_SELF_IMPROVE_MODEL_ACQUISITION_TIMEOUT_SECONDS",
                _DEFAULT_ACQUISITION_TIMEOUT_SECONDS,
            )
        )
        if isinstance(timeout_value, bool):
            raise ValueError("model acquisition deadline must be positive and finite")
        try:
            normalized_timeout = float(cast(str | float, timeout_value))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "model acquisition deadline must be positive and finite"
            ) from exc
        if not math.isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError("model acquisition deadline must be positive and finite")
        self._event_sink = event_sink
        self._heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self._acquisition_timeout_seconds = normalized_timeout
        self._event_sink_lock = threading.Lock()

        self._models_dir = self.cache_root / ".gludd" / "models"
        self._leases_dir = self.cache_root / ".gludd" / "leases"
        self._reservations_dir = self.cache_root / ".gludd" / "reservations"
        self._acquire_dir = self.cache_root / ".gludd" / "acquiring"
        for directory in (
            self._models_dir,
            self._leases_dir,
            self._reservations_dir,
            self._acquire_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._selector = model_selector or _default_selector
        self._isolate_revision_resolution = revision_resolver is None
        self._resolve_revision = revision_resolver or _default_revision_resolver
        self._isolate_download = downloader_factory is None
        self._downloader_factory = downloader_factory or _default_downloader
        self._disk_free = disk_free or _default_disk_free
        self._delete_revision = revision_deleter
        self._process_started = process_started or _default_process_started
        current_started = self._process_started(os.getpid())
        if (
            current_started is None
            or isinstance(current_started, bool)
            or not isinstance(current_started, (int, float))
            or not math.isfinite(float(current_started))
            or float(current_started) <= 0
        ):
            raise RuntimeError("cannot identify current model lease owner")
        self._current_started = float(current_started)

    @staticmethod
    def _identity_key(value: str) -> str:
        """Return a bounded correlation key without exposing an identifier."""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _failure_category(error: BaseException) -> ModelAcquisitionFailure:
        """Map an exception to a stable category without retaining its message."""
        if isinstance(error, ModelAcquisitionError):
            return error.failure
        if isinstance(error, TimeoutError):
            return ModelAcquisitionFailure.TIMEOUT
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            return ModelAcquisitionFailure.INTERRUPTED
        if isinstance(error, OSError):
            return ModelAcquisitionFailure.IO
        if isinstance(error, ValueError):
            return ModelAcquisitionFailure.VALIDATION
        return ModelAcquisitionFailure.INTERNAL

    def _emit_event(self, event: ModelAcquisitionEvent) -> None:
        """Invoke the non-blocking observer without risking model ownership."""
        with self._event_sink_lock:
            sink = self._event_sink
        if sink is None:
            return
        try:
            sink(event)
        except Exception:
            with self._event_sink_lock:
                if self._event_sink is sink:
                    self._event_sink = None
            logger.warning(
                "model acquisition event sink failed for phase=%s",
                event.phase.value,
            )

    def _emit_cache_hit(self, artifact: _OwnedArtifact) -> None:
        """Emit verified cache reuse without exposing paths or repository names."""
        self._emit_event(
            ModelAcquisitionEvent(
                phase=ModelAcquisitionPhase.CACHE_HIT,
                operation_id=uuid.uuid4().hex,
                repository_key=self._identity_key(artifact.repo_id),
                model_key=self._identity_key(artifact.model_id),
                revision=artifact.revision,
                elapsed_seconds=0.0,
            )
        )

    def _emit_eviction_event(
        self,
        phase: ModelAcquisitionPhase,
        *,
        operation_id: str,
        started_at: float,
        artifact: _OwnedArtifact | None = None,
        failure: ModelAcquisitionFailure | None = None,
    ) -> None:
        """Emit one secret-safe cache-pressure decision event."""
        self._emit_event(
            ModelAcquisitionEvent(
                phase=phase,
                operation_id=operation_id,
                repository_key=self._identity_key(
                    artifact.repo_id if artifact is not None else "cache-pressure"
                ),
                model_key=(
                    self._identity_key(artifact.model_id)
                    if artifact is not None
                    else None
                ),
                revision=artifact.revision if artifact is not None else None,
                elapsed_seconds=round(
                    max(0.0, time.monotonic() - started_at),
                    3,
                ),
                failure=failure,
            )
        )

    def _cache_payload_bytes(self) -> int:
        """Measure physical cache payload, including unmanifested partial files."""
        total = 0
        seen_files: set[tuple[int, int]] = set()
        walk_errors: list[OSError] = []

        def remember_error(error: OSError) -> None:
            walk_errors.append(error)

        for directory, child_dirs, filenames in os.walk(
            self.cache_root,
            followlinks=False,
            onerror=remember_error,
        ):
            current = Path(directory)
            if current == self.cache_root:
                child_dirs[:] = [name for name in child_dirs if name != ".gludd"]
            for filename in filenames:
                path = current / filename
                try:
                    metadata = path.lstat()
                except OSError as exc:
                    raise RuntimeError(
                        "cannot inspect model cache payload"
                    ) from exc
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                identity = (metadata.st_dev, metadata.st_ino)
                if identity in seen_files:
                    continue
                seen_files.add(identity)
                total += metadata.st_size
        if walk_errors:
            raise RuntimeError("cannot inspect model cache payload") from walk_errors[0]
        return total

    def _partial_transfer_paths(self, repository_id: str | None) -> set[Path]:
        """Snapshot regular partials under the owned cache without following links."""
        expected_root = (
            f"models--{repository_id.replace('/', '--')}"
            if repository_id is not None
            else None
        )
        partials: set[Path] = set()
        try:
            candidates = tuple(self.cache_root.rglob("*.incomplete"))
        except OSError as exc:
            raise RuntimeError("cannot inspect partial model transfers") from exc
        for candidate in candidates:
            try:
                metadata = candidate.lstat()
                relative = candidate.relative_to(self.cache_root)
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(self.cache_root)
            except (OSError, ValueError):
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or (expected_root is not None and relative.parts[0] != expected_root)
            ):
                continue
            partials.add(resolved)
        return partials

    def _cleanup_new_partial_transfers(
        self,
        before: set[Path],
        *,
        repository_id: str | None,
    ) -> None:
        """Delete only partial files created by this bounded operation."""
        after = self._partial_transfer_paths(repository_id)
        for partial in sorted(after - before):
            try:
                partial.unlink()
            except OSError as exc:
                raise RuntimeError(
                    "timed-out model partial cleanup failed"
                ) from exc

    def _run_isolated_operation(
        self,
        operation: Callable[..., object],
        args: tuple[object, ...],
        *,
        operation_name: str,
        clean_partial_transfers: bool,
        repository_id: str | None = None,
    ) -> object:
        """Run one default external call under a finite, terminable deadline."""
        if operation_name not in {"download", "revision"}:
            raise ValueError("unsupported model acquisition operation")
        before = (
            self._partial_transfer_paths(repository_id)
            if clean_partial_transfers
            else set()
        )
        cache_key = self._identity_key(str(self.cache_root))
        try:
            return _run_bounded_process(
                operation,
                args,
                timeout_seconds=self._acquisition_timeout_seconds,
                process_name=f"gludd-model-{operation_name}-{cache_key}",
            )
        except TimeoutError:
            if clean_partial_transfers:
                self._cleanup_new_partial_transfers(
                    before,
                    repository_id=repository_id,
                )
            raise

    @staticmethod
    def _hf_auth_mode() -> ModelAcquisitionAuthMode:
        """Classify ambient auth without retaining the credential value."""
        if _hf_token_from_environment() is not None:
            return ModelAcquisitionAuthMode.EXPLICIT_ENV_TOKEN
        return ModelAcquisitionAuthMode.ANONYMOUS_PUBLIC

    @staticmethod
    def _missing_required_hf_token() -> RuntimeError:
        """Build a secret-free strict-policy refusal."""
        return RuntimeError(
            "managed model acquisition requires an explicit Hugging Face token "
            "in HF_TOKEN or HUGGING_FACE_HUB_TOKEN"
        )

    @contextmanager
    def _observe_operation(
        self,
        *,
        repository_id: str,
        model_id: str | None,
        revision: str | None,
        started_phase: ModelAcquisitionPhase,
        progress_phase: ModelAcquisitionPhase,
        completed_phase: ModelAcquisitionPhase,
        failed_phase: ModelAcquisitionPhase,
    ) -> Iterator[Callable[[str | None], None]]:
        """Emit ordered lifecycle and auth events with bounded heartbeats."""
        auth_mode = self._hf_auth_mode()
        if self._event_sink is None:
            if (
                self._hf_token_required
                and auth_mode is ModelAcquisitionAuthMode.ANONYMOUS_PUBLIC
            ):
                raise self._missing_required_hf_token()
            yield lambda _revision: None
            return

        operation_id = uuid.uuid4().hex
        repository_key = self._identity_key(repository_id)
        model_key = self._identity_key(model_id) if model_id is not None else None
        started_at = time.monotonic()
        completed_revision = [revision]

        def event(
            phase: ModelAcquisitionPhase,
            *,
            failure: ModelAcquisitionFailure | None = None,
            selected_auth_mode: ModelAcquisitionAuthMode | None = None,
        ) -> ModelAcquisitionEvent:
            return ModelAcquisitionEvent(
                phase=phase,
                operation_id=operation_id,
                repository_key=repository_key,
                model_key=model_key,
                revision=completed_revision[0],
                elapsed_seconds=round(max(0.0, time.monotonic() - started_at), 3),
                failure=failure,
                auth_mode=selected_auth_mode,
            )

        def remember_revision(resolved: str | None) -> None:
            completed_revision[0] = (
                resolved if isinstance(resolved, str) and _SHA_RE.fullmatch(resolved) else None
            )

        self._emit_event(event(started_phase))
        self._emit_event(
            event(
                ModelAcquisitionPhase(auth_mode.value),
                selected_auth_mode=auth_mode,
            )
        )
        if (
            self._hf_token_required
            and auth_mode is ModelAcquisitionAuthMode.ANONYMOUS_PUBLIC
        ):
            error = self._missing_required_hf_token()
            self._emit_event(
                event(failed_phase, failure=ModelAcquisitionFailure.VALIDATION)
            )
            raise error

        stopped = threading.Event()

        def heartbeat() -> None:
            while not stopped.wait(self._heartbeat_interval_seconds):
                self._emit_event(event(progress_phase))

        observer: threading.Thread | None = None
        if self._event_sink is not None:
            observer = threading.Thread(
                target=heartbeat,
                name=f"gludd-model-acquire-{operation_id[:8]}",
                daemon=False,
            )
            observer.start()

        def stop_observer() -> None:
            stopped.set()
            if observer is not None:
                observer.join()

        try:
            yield remember_revision
        except BaseException as error:
            stop_observer()
            self._emit_event(
                event(failed_phase, failure=self._failure_category(error))
            )
            raise
        else:
            stop_observer()
            self._emit_event(event(completed_phase))

    @contextmanager
    def acquire(
        self,
        task_description: str,
        *,
        explicit_path: Path | None = None,
        model_config: LocalModelConfig | None = None,
        resolved_revision: str | None = None,
    ) -> Iterator[AcquiredModel]:
        """Acquire one explicit or planned model and always release its lease."""
        if not task_description.strip():
            raise ValueError("task description must not be empty")
        if (model_config is None) != (resolved_revision is None):
            raise ValueError("planned model config and resolved revision must be paired")
        if explicit_path is not None and model_config is not None:
            raise ValueError("explicit model path cannot combine with a planned candidate")
        model = (
            self._acquire_explicit(explicit_path)
            if explicit_path is not None
            else self._acquire_managed(
                task_description,
                model_config=model_config,
                resolved_revision=resolved_revision,
            )
        )
        primary_error: BaseException | None = None
        try:
            yield model
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                self._release_lease(model.lease_path)
            except RuntimeError as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(
                    f"model lease cleanup also failed: {str(cleanup_error)[:1000]}"
                )
            if primary_error is None:
                self.reclaim(required_bytes=0)

    @staticmethod
    def _identity_sort_key(
        identity: ModelArtifactIdentity,
    ) -> tuple[str, str, str, str]:
        """Return a stable order for serialized immutable identities."""
        return (
            identity.model_id,
            identity.repo_id,
            identity.filename,
            identity.revision,
        )

    @staticmethod
    def _identity_payload(identity: ModelArtifactIdentity) -> dict[str, str]:
        """Serialize one validated identity without paths or credentials."""
        return {
            "filename": identity.filename,
            "model_id": identity.model_id,
            "repo_id": identity.repo_id,
            "revision": identity.revision,
        }

    @staticmethod
    def _identity_from_payload(
        value: object,
        *,
        reservation_path: Path,
    ) -> ModelArtifactIdentity:
        """Parse one exact identity from bounded reservation metadata."""
        if not isinstance(value, dict) or set(value) != {
            "filename",
            "model_id",
            "repo_id",
            "revision",
        }:
            raise RuntimeError(
                f"model plan reservation is invalid: {reservation_path}"
            )
        try:
            return ModelArtifactIdentity(
                model_id=cast(str, value["model_id"]),
                repo_id=cast(str, value["repo_id"]),
                filename=cast(str, value["filename"]),
                revision=cast(str, value["revision"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"model plan reservation is invalid: {reservation_path}"
            ) from exc

    @contextmanager
    def reserve_plan(
        self,
        candidates: Sequence[ModelArtifactIdentity],
        *,
        failure_hints: Sequence[ModelArtifactIdentity] = (),
    ) -> Iterator[ModelPlanReservation]:
        """Atomically protect all immutable candidates for one live retry plan."""
        if isinstance(candidates, (str, bytes)) or not candidates:
            raise ValueError("model plan must contain at least one artifact identity")
        if any(
            not isinstance(item, ModelArtifactIdentity) for item in candidates
        ) or any(
            not isinstance(item, ModelArtifactIdentity) for item in failure_hints
        ):
            raise ValueError("model plan identities must be unique validated artifacts")
        protected = frozenset(candidates)
        failures = frozenset(failure_hints)
        if len(protected) != len(candidates) or len(failures) != len(failure_hints):
            raise ValueError("model plan identities must be unique validated artifacts")
        reservation_path = self._reservations_dir / (
            f"{os.getpid()}-{uuid.uuid4().hex}.json"
        )
        reservation = ModelPlanReservation(
            self,
            reservation_path,
            protected,
            failures,
        )
        self._write_plan_reservation(reservation, require_existing=False)
        primary_error: BaseException | None = None
        try:
            yield reservation
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                reservation_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_error = RuntimeError(
                    f"model plan reservation cleanup failed: {reservation_path}"
                )
                if primary_error is None:
                    raise cleanup_error from exc
                primary_error.add_note(str(cleanup_error)[:1000])

    def _write_plan_reservation(
        self,
        reservation: ModelPlanReservation,
        *,
        require_existing: bool,
    ) -> None:
        """Persist one complete plan state with an atomic replacement."""
        if require_existing and (
            reservation.path.is_symlink() or not reservation.path.is_file()
        ):
            raise RuntimeError(
                f"model plan reservation disappeared before update: {reservation.path}"
            )
        candidates = [
            {
                "identity": self._identity_payload(identity),
                "state": state.value,
            }
            for identity, state in sorted(
                reservation._states.items(),
                key=lambda item: self._identity_sort_key(item[0]),
            )
        ]
        _atomic_json(
            reservation.path,
            {
                "schema_version": _RESERVATION_SCHEMA,
                "pid": os.getpid(),
                "process_started": self._current_started,
                "created_ns": reservation._created_ns,
                "candidates": candidates,
                "failure_hints": [
                    self._identity_payload(item)
                    for item in sorted(
                        reservation._failure_hints,
                        key=self._identity_sort_key,
                    )
                ],
            },
        )

    def owned_identities_for_model_ids(
        self,
        model_ids: Collection[str],
    ) -> tuple[ModelArtifactIdentity, ...]:
        """Expand historical model IDs only to exact currently owned artifacts."""
        if isinstance(model_ids, (str, bytes)) or any(
            not isinstance(item, str) or not item.strip() for item in model_ids
        ):
            raise ValueError("failed model identifiers must be non-empty strings")
        requested = set(model_ids)
        identities = {
            self._owned_identity(item)
            for item in self._load_manifests()
            if item.model_id in requested
        }
        return tuple(sorted(identities, key=self._identity_sort_key))

    @staticmethod
    def _owned_identity(artifact: _OwnedArtifact) -> ModelArtifactIdentity:
        """Project one verified ownership manifest into an immutable identity."""
        return ModelArtifactIdentity(
            model_id=artifact.model_id,
            repo_id=artifact.repo_id,
            filename=artifact.filename,
            revision=artifact.revision,
        )

    def _delete_owned_artifact(self, artifact: _OwnedArtifact) -> None:
        """Delete one proven owned artifact through the exact cache adapter."""
        if self._delete_revision is not None:
            self._delete_revision(self.cache_root, artifact.revision)
            return
        plan = HuggingFaceCacheDeletion(self.cache_root).plan(
            CacheArtifactIdentity(
                repo_id=artifact.repo_id,
                revision=artifact.revision,
                filename=artifact.filename,
                path=artifact.path,
            )
        )
        plan.dry_run()
        plan.execute_and_verify()

    def diagnose_reclaim(self, *, required_bytes: int) -> ModelCacheDiagnostic:
        """Report whether owned unleased artifacts can satisfy cache pressure."""
        if (
            isinstance(required_bytes, bool)
            or not isinstance(required_bytes, int)
            or required_bytes < 0
        ):
            raise ValueError("required_bytes must be a non-negative integer")
        manifests = self._load_manifests()
        active = self._active_digests()
        reservation = self._active_plan_reservation()
        try:
            free = self._disk_free(self.cache_root)
        except OSError as exc:
            raise RuntimeError("cannot inspect model cache disk headroom") from exc
        payload = self._cache_payload_bytes()
        candidates = [
            item
            for item in manifests
            if item.artifact_sha256 not in active
            and self._owned_identity(item) not in reservation.protected
        ]
        reclaimable_bytes = sum(item.size_bytes for item in candidates)
        under_pressure = (
            payload + required_bytes > self.quota_bytes
            or free < self.reserve_bytes + required_bytes
        )
        can_reclaim = (
            not under_pressure
            or (
                max(0, payload - reclaimable_bytes) + required_bytes
                <= self.quota_bytes
                and free + reclaimable_bytes
                >= self.reserve_bytes + required_bytes
            )
        )
        return ModelCacheDiagnostic(
            cache_key=self._identity_key(str(self.cache_root)),
            payload_bytes=payload,
            required_bytes=required_bytes,
            quota_bytes=self.quota_bytes,
            reserve_bytes=self.reserve_bytes,
            disk_free_bytes=free,
            owned_count=len(manifests),
            leased_count=sum(
                item.artifact_sha256 in active for item in manifests
            ),
            eviction_candidate_count=len(candidates),
            under_pressure=under_pressure,
            can_reclaim=can_reclaim,
        )

    def reclaim(self, *, required_bytes: int) -> tuple[Path, ...]:
        """Evict oldest owned, unleased revisions until quota/headroom is safe."""
        if (
            isinstance(required_bytes, bool)
            or not isinstance(required_bytes, int)
            or required_bytes < 0
        ):
            raise ValueError("required_bytes must be a non-negative integer")
        manifests = self._load_manifests()
        active = self._active_digests()
        reservation = self._active_plan_reservation()
        removed: list[Path] = []
        operation_id = uuid.uuid4().hex
        started_at = time.monotonic()

        def under_pressure() -> bool:
            try:
                free = self._disk_free(self.cache_root)
            except OSError as exc:
                raise RuntimeError("cannot inspect model cache disk headroom") from exc
            return (
                self._cache_payload_bytes() + required_bytes > self.quota_bytes
                or free < self.reserve_bytes + required_bytes
            )

        if not under_pressure():
            return ()

        candidates = sorted(
            (
                item
                for item in manifests
                if item.artifact_sha256 not in active
                and self._owned_identity(item) not in reservation.protected
            ),
            key=lambda item: (
                self._owned_identity(item) not in reservation.failure_hints,
                item.last_used_ns,
                item.artifact_sha256,
            ),
        )
        for item in candidates:
            self._emit_eviction_event(
                ModelAcquisitionPhase.EVICTION_PLANNED,
                operation_id=operation_id,
                started_at=started_at,
                artifact=item,
            )
            try:
                self._delete_owned_artifact(item)
            except (KeyboardInterrupt, SystemExit):
                self._emit_eviction_event(
                    ModelAcquisitionPhase.EVICTION_REFUSED,
                    operation_id=operation_id,
                    started_at=started_at,
                    artifact=item,
                    failure=ModelAcquisitionFailure.INTERRUPTED,
                )
                raise
            except Exception:
                self._emit_eviction_event(
                    ModelAcquisitionPhase.EVICTION_REFUSED,
                    operation_id=operation_id,
                    started_at=started_at,
                    artifact=item,
                    failure=ModelAcquisitionFailure.CACHE_RECLAIM,
                )
                raise
            if item.path.exists():
                self._emit_eviction_event(
                    ModelAcquisitionPhase.EVICTION_REFUSED,
                    operation_id=operation_id,
                    started_at=started_at,
                    artifact=item,
                    failure=ModelAcquisitionFailure.CACHE_RECLAIM,
                )
                raise RuntimeError(
                    f"model revision eviction did not remove owned artifact: {item.path}"
                )
            try:
                item.manifest_path.unlink()
            except OSError as exc:
                self._emit_eviction_event(
                    ModelAcquisitionPhase.EVICTION_REFUSED,
                    operation_id=operation_id,
                    started_at=started_at,
                    artifact=item,
                    failure=ModelAcquisitionFailure.CACHE_RECLAIM,
                )
                raise RuntimeError(
                    f"model ownership manifest cleanup failed: {item.manifest_path}"
                ) from exc
            removed.append(item.path)
            self._emit_eviction_event(
                ModelAcquisitionPhase.EVICTION_COMPLETED,
                operation_id=operation_id,
                started_at=started_at,
                artifact=item,
            )
            if not under_pressure():
                return tuple(removed)

        self._emit_eviction_event(
            ModelAcquisitionPhase.EVICTION_REFUSED,
            operation_id=operation_id,
            started_at=started_at,
            failure=ModelAcquisitionFailure.CACHE_HEADROOM,
        )
        raise ModelAcquisitionError(
            ModelAcquisitionFailure.CACHE_HEADROOM
        )

    def resolve_revision(self, repo_id: str) -> str:
        """Resolve one repository to a normalized immutable model commit."""
        if not isinstance(repo_id, str) or not repo_id.strip():
            raise ValueError("model repository identifier must be non-empty")
        normalized_repo = repo_id.strip()
        owned = sorted(
            (
                item
                for item in self._load_manifests()
                if item.repo_id == normalized_repo
            ),
            key=lambda item: (item.last_used_ns, item.revision),
            reverse=True,
        )
        if owned:
            newest = owned[0]
            if (
                not newest.path.is_file()
                or newest.path.stat().st_size != newest.size_bytes
                or _sha256(newest.path) != newest.artifact_sha256
            ):
                raise RuntimeError(
                    f"owned model artifact is missing or changed: {newest.path}"
                )
            self._emit_cache_hit(newest)
            return newest.revision

        with self._observe_operation(
            repository_id=normalized_repo,
            model_id=None,
            revision=None,
            started_phase=ModelAcquisitionPhase.REVISION_RESOLUTION_STARTED,
            progress_phase=ModelAcquisitionPhase.REVISION_RESOLUTION_PROGRESS,
            completed_phase=ModelAcquisitionPhase.REVISION_RESOLUTION_COMPLETED,
            failed_phase=ModelAcquisitionPhase.REVISION_RESOLUTION_FAILED,
        ) as remember_revision:
            resolved: object = (
                self._run_isolated_operation(
                    self._resolve_revision,
                    (normalized_repo,),
                    operation_name="revision",
                    clean_partial_transfers=False,
                )
                if self._isolate_revision_resolution
                else self._resolve_revision(normalized_repo)
            )
            revision = resolved.lower() if isinstance(resolved, str) else ""
            if _SHA_RE.fullmatch(revision) is None:
                raise RuntimeError(
                    "model revision resolver did not return a 40-character commit"
                )
            remember_revision(revision)
        return revision

    def _acquire_managed(
        self,
        task_description: str,
        *,
        model_config: LocalModelConfig | None = None,
        resolved_revision: str | None = None,
    ) -> AcquiredModel:
        config = model_config if model_config is not None else self._selector(task_description)
        if not isinstance(config, LocalModelConfig) or config.category != "coding":
            raise RuntimeError("self-improvement selector must return a coding model")
        revision = (
            resolved_revision.lower()
            if isinstance(resolved_revision, str)
            else self.resolve_revision(config.repo)
        )
        if _SHA_RE.fullmatch(revision) is None:
            raise RuntimeError("planned model revision must be a 40-character commit")

        cached = self._find_owned(config, revision)
        if cached is not None:
            self._emit_cache_hit(cached)
        else:
            estimated = config.size_mb * 1024 * 1024
            try:
                self.reclaim(required_bytes=estimated)
            except ModelAcquisitionError:
                raise
            except (OSError, RuntimeError) as error:
                raise ModelAcquisitionError(
                    ModelAcquisitionFailure.CACHE_RECLAIM
                ) from error
            lock_path = self._acquisition_lock_path(config.repo, revision)
            descriptor = self._claim_acquisition(lock_path)
            try:
                cached = self._find_owned(config, revision)
                if cached is not None:
                    self._emit_cache_hit(cached)
                else:
                    with self._observe_operation(
                        repository_id=config.repo,
                        model_id=config.name,
                        revision=revision,
                        started_phase=ModelAcquisitionPhase.DOWNLOAD_STARTED,
                        progress_phase=ModelAcquisitionPhase.DOWNLOAD_PROGRESS,
                        completed_phase=ModelAcquisitionPhase.DOWNLOAD_COMPLETED,
                        failed_phase=ModelAcquisitionPhase.DOWNLOAD_FAILED,
                    ):
                        if self._isolate_download:
                            result = self._run_isolated_operation(
                                _download_default_gguf,
                                (
                                    str(self.cache_root),
                                    config.repo,
                                    config.filename,
                                    revision,
                                ),
                                operation_name="download",
                                clean_partial_transfers=True,
                                repository_id=config.repo,
                            )
                            if not isinstance(result, DownloadedModel):
                                raise RuntimeError(
                                    "model acquisition worker returned an invalid artifact"
                                )
                            downloaded = result
                        else:
                            downloader = self._downloader_factory(self.cache_root)
                            downloaded = downloader.download_gguf(
                                config.repo,
                                config.filename,
                                revision=revision,
                            )
                        cached = self._record_download(config, revision, downloaded)
            finally:
                os.close(descriptor)
                with suppress(FileNotFoundError):
                    lock_path.unlink()

        return self._lease_artifact(cached)

    def _acquire_explicit(self, explicit_path: Path) -> AcquiredModel:
        try:
            path = explicit_path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise FileNotFoundError(f"explicit GGUF is not readable: {explicit_path}") from exc
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"explicit GGUF must be a non-empty regular file: {path}")
        digest = _sha256(path)
        lease_path = self._write_lease(digest)
        return AcquiredModel(
            path=path,
            model_id="explicit",
            repo_id=None,
            filename=path.name,
            resolved_revision=None,
            artifact_sha256=digest,
            source="explicit",
            manifest_path=self._models_dir / f"{digest}.json",
            lease_path=lease_path,
        )

    def _record_download(
        self,
        config: LocalModelConfig,
        revision: str,
        downloaded: DownloadedModel,
    ) -> _OwnedArtifact:
        path = Path(downloaded.local_path)
        if (
            not path.is_file()
            or path.stat().st_size <= 0
            or downloaded.revision != revision
        ):
            raise RuntimeError("downloaded model did not satisfy immutable artifact identity")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(self.cache_root)
        except (OSError, ValueError) as exc:
            raise RuntimeError("downloaded model escaped the Gludd-owned cache") from exc
        digest = _sha256(path)
        now = time.time_ns()
        manifest_path = self._models_dir / f"{digest}.json"
        artifact = _OwnedArtifact(
            model_id=config.name,
            repo_id=config.repo,
            filename=config.filename,
            revision=revision,
            artifact_sha256=digest,
            path=path,
            size_bytes=path.stat().st_size,
            last_used_ns=now,
            manifest_path=manifest_path,
        )
        self._write_manifest(artifact)
        return artifact

    def _find_owned(
        self,
        config: LocalModelConfig,
        revision: str,
    ) -> _OwnedArtifact | None:
        for item in self._load_manifests():
            if (
                item.model_id != config.name
                or item.repo_id != config.repo
                or item.filename != config.filename
                or item.revision != revision
            ):
                continue
            if not item.path.is_file() or item.path.stat().st_size != item.size_bytes:
                raise RuntimeError(
                    f"owned model artifact is missing or changed: {item.path}"
                )
            if _sha256(item.path) != item.artifact_sha256:
                raise RuntimeError(
                    f"owned model artifact digest changed: {item.path}"
                )
            refreshed = _OwnedArtifact(
                **{
                    **item.__dict__,
                    "last_used_ns": time.time_ns(),
                }
            )
            self._write_manifest(refreshed)
            return refreshed
        return None

    def _lease_artifact(self, artifact: _OwnedArtifact) -> AcquiredModel:
        lease_path = self._write_lease(artifact.artifact_sha256)
        return AcquiredModel(
            path=artifact.path,
            model_id=artifact.model_id,
            repo_id=artifact.repo_id,
            filename=artifact.filename,
            resolved_revision=artifact.revision,
            artifact_sha256=artifact.artifact_sha256,
            source="managed",
            manifest_path=artifact.manifest_path,
            lease_path=lease_path,
        )

    def _write_lease(self, digest: str) -> Path:
        lease_path = self._leases_dir / f"{os.getpid()}-{uuid.uuid4().hex}.json"
        _atomic_json(
            lease_path,
            {
                "schema_version": _MANIFEST_SCHEMA,
                "artifact_sha256": digest,
                "pid": os.getpid(),
                "process_started": self._current_started,
                "created_ns": time.time_ns(),
            },
        )
        return lease_path

    @staticmethod
    def _release_lease(lease_path: Path) -> None:
        try:
            lease_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeError(f"model lease cleanup failed: {lease_path}") from exc

    def _load_manifests(self) -> list[_OwnedArtifact]:
        manifests: list[_OwnedArtifact] = []
        for path in sorted(self._models_dir.glob("*.json")):
            value = _read_json(path, "model ownership manifest")
            expected = {
                "schema_version",
                "model_id",
                "repo_id",
                "filename",
                "revision",
                "artifact_sha256",
                "path",
                "size_bytes",
                "last_used_ns",
            }
            if set(value) != expected or value.get("schema_version") != _MANIFEST_SCHEMA:
                raise RuntimeError(f"model ownership manifest is invalid: {path}")
            strings = (
                value.get("model_id"),
                value.get("repo_id"),
                value.get("filename"),
                value.get("revision"),
                value.get("artifact_sha256"),
                value.get("path"),
            )
            if not all(isinstance(item, str) and item for item in strings):
                raise RuntimeError(f"model ownership manifest is invalid: {path}")
            revision = cast(str, value["revision"])
            digest = cast(str, value["artifact_sha256"])
            size = value.get("size_bytes")
            last_used = value.get("last_used_ns")
            if (
                _SHA_RE.fullmatch(revision) is None
                or _DIGEST_RE.fullmatch(digest) is None
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size <= 0
                or isinstance(last_used, bool)
                or not isinstance(last_used, int)
                or last_used <= 0
            ):
                raise RuntimeError(f"model ownership manifest is invalid: {path}")
            artifact_path = Path(cast(str, value["path"]))
            try:
                artifact_path.resolve(strict=False).relative_to(self.cache_root)
            except ValueError as exc:
                raise RuntimeError(
                    f"model ownership manifest escapes cache: {path}"
                ) from exc
            manifests.append(
                _OwnedArtifact(
                    model_id=cast(str, value["model_id"]),
                    repo_id=cast(str, value["repo_id"]),
                    filename=cast(str, value["filename"]),
                    revision=revision,
                    artifact_sha256=digest,
                    path=artifact_path,
                    size_bytes=size,
                    last_used_ns=last_used,
                    manifest_path=path,
                )
            )
        return manifests

    def _write_manifest(self, artifact: _OwnedArtifact) -> None:
        _atomic_json(
            artifact.manifest_path,
            {
                "schema_version": _MANIFEST_SCHEMA,
                "model_id": artifact.model_id,
                "repo_id": artifact.repo_id,
                "filename": artifact.filename,
                "revision": artifact.revision,
                "artifact_sha256": artifact.artifact_sha256,
                "path": str(artifact.path),
                "size_bytes": artifact.size_bytes,
                "last_used_ns": artifact.last_used_ns,
            },
        )

    def _active_plan_reservation(self) -> _ActivePlanReservations:
        """Load live reservations and reap only PID/birth-verified stale files."""
        protected: set[ModelArtifactIdentity] = set()
        failure_hints: set[ModelArtifactIdentity] = set()
        for path in sorted(self._reservations_dir.glob("*.json")):
            value = _read_json(path, "model plan reservation")
            expected = {
                "schema_version",
                "pid",
                "process_started",
                "created_ns",
                "candidates",
                "failure_hints",
            }
            pid = value.get("pid")
            started = value.get("process_started")
            created = value.get("created_ns")
            candidate_values = value.get("candidates")
            failure_values = value.get("failure_hints")
            if (
                set(value) != expected
                or value.get("schema_version") != _RESERVATION_SCHEMA
                or isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 0
                or isinstance(started, bool)
                or not isinstance(started, (int, float))
                or not math.isfinite(float(started))
                or float(started) <= 0
                or isinstance(created, bool)
                or not isinstance(created, int)
                or created <= 0
                or not isinstance(candidate_values, list)
                or not candidate_values
                or not isinstance(failure_values, list)
            ):
                raise RuntimeError(f"model plan reservation is invalid: {path}")
            parsed_candidates: list[
                tuple[ModelArtifactIdentity, _ReservationState]
            ] = []
            for item in candidate_values:
                if not isinstance(item, dict) or set(item) != {"identity", "state"}:
                    raise RuntimeError(
                        f"model plan reservation is invalid: {path}"
                    )
                identity = self._identity_from_payload(
                    item.get("identity"),
                    reservation_path=path,
                )
                state_value = item.get("state")
                if not isinstance(state_value, str):
                    raise RuntimeError(
                        f"model plan reservation is invalid: {path}"
                    )
                try:
                    state = _ReservationState(state_value)
                except ValueError as exc:
                    raise RuntimeError(
                        f"model plan reservation is invalid: {path}"
                    ) from exc
                parsed_candidates.append((identity, state))
            parsed_failures = tuple(
                self._identity_from_payload(item, reservation_path=path)
                for item in failure_values
            )
            candidate_identities = [item[0] for item in parsed_candidates]
            if (
                len(set(candidate_identities)) != len(candidate_identities)
                or len(set(parsed_failures)) != len(parsed_failures)
            ):
                raise RuntimeError(f"model plan reservation is invalid: {path}")
            observed = self._process_started(pid)
            if observed is not None and (
                isinstance(observed, bool)
                or not isinstance(observed, (int, float))
                or not math.isfinite(float(observed))
                or float(observed) <= 0
            ):
                raise RuntimeError(
                    f"model plan reservation process birth is invalid: {path}"
                )
            if observed is None or abs(float(started) - float(observed)) > 0.001:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise RuntimeError(
                        f"stale model plan reservation cleanup failed: {path}"
                    ) from exc
                continue
            protected.update(
                identity
                for identity, state in parsed_candidates
                if state is _ReservationState.PLANNED
            )
            failure_hints.update(parsed_failures)
            failure_hints.update(
                identity
                for identity, state in parsed_candidates
                if state is _ReservationState.FAILED
            )
        return _ActivePlanReservations(
            protected=frozenset(protected),
            failure_hints=frozenset(failure_hints),
        )

    def _active_digests(self) -> set[str]:
        active: set[str] = set()
        for path in sorted(self._leases_dir.glob("*.json")):
            value = _read_json(path, "model lease")
            expected = {
                "schema_version",
                "artifact_sha256",
                "pid",
                "process_started",
                "created_ns",
            }
            digest = value.get("artifact_sha256")
            pid = value.get("pid")
            started = value.get("process_started")
            created = value.get("created_ns")
            if (
                set(value) != expected
                or value.get("schema_version") != _MANIFEST_SCHEMA
                or not isinstance(digest, str)
                or _DIGEST_RE.fullmatch(digest) is None
                or isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 0
                or not isinstance(started, (int, float))
                or isinstance(started, bool)
                or isinstance(created, bool)
                or not isinstance(created, int)
                or created <= 0
            ):
                raise RuntimeError(f"model lease is invalid: {path}")
            observed = self._process_started(pid)
            if observed is None:
                path.unlink()
                continue
            if abs(float(started) - observed) > 0.001:
                path.unlink()
                continue
            active.add(digest)
        return active

    def _acquisition_lock_path(self, repo_id: str, revision: str) -> Path:
        key = hashlib.sha256(f"{repo_id}@{revision}".encode()).hexdigest()
        return self._acquire_dir / f"{key}.lock"

    @staticmethod
    def _claim_acquisition(lock_path: Path) -> int:
        try:
            return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(
                "model acquisition is already owned by another Gludd process"
            ) from exc


def _print_operator_event(event: ModelAcquisitionEvent) -> None:
    """Print one secret-safe lifecycle event for an operator invocation."""
    print(
        json.dumps(
            {
                "failure": event.failure.value if event.failure is not None else None,
                "kind": "model_cache_event",
                "operation_id": event.operation_id,
                "phase": event.phase.value,
                "repository_key": event.repository_key,
                "revision": event.revision,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fail-closed operator cache diagnostic or reclamation fallback."""
    parser = argparse.ArgumentParser(
        description="Diagnose or reclaim Gludd-owned self-improvement models."
    )
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--required-bytes", required=True, type=int)
    parser.add_argument("--validate-only", required=True, choices=("0", "1"))
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    try:
        configured_root = str(arguments.cache_root).strip()
        manager = ModelLeaseManager(
            cache_root=(
                Path(configured_root).expanduser()
                if configured_root
                else None
            ),
            event_sink=_print_operator_event,
        )
        diagnostic = manager.diagnose_reclaim(
            required_bytes=arguments.required_bytes
        )
        removed: tuple[Path, ...] = ()
        if arguments.validate_only == "1":
            status = "validated" if diagnostic.can_reclaim else "refused"
        else:
            removed = manager.reclaim(required_bytes=arguments.required_bytes)
            status = "applied"
        payload = {
            "cache_key": diagnostic.cache_key,
            "can_reclaim": diagnostic.can_reclaim,
            "disk_free_bytes": diagnostic.disk_free_bytes,
            "eviction_candidate_count": diagnostic.eviction_candidate_count,
            "leased_count": diagnostic.leased_count,
            "owned_count": diagnostic.owned_count,
            "payload_bytes": diagnostic.payload_bytes,
            "quota_bytes": diagnostic.quota_bytes,
            "removed_count": len(removed),
            "required_bytes": diagnostic.required_bytes,
            "reserve_bytes": diagnostic.reserve_bytes,
            "status": status,
            "under_pressure": diagnostic.under_pressure,
        }
        print(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
        return 0 if status != "refused" else 2
    except (OSError, RuntimeError, ValueError) as error:
        print(
            json.dumps(
                {
                    "error_type": type(error).__name__,
                    "status": "refused",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2


__all__ = [
    "AcquiredModel",
    "ModelAcquisitionAuthMode",
    "ModelAcquisitionError",
    "ModelAcquisitionEvent",
    "ModelAcquisitionFailure",
    "ModelAcquisitionPhase",
    "ModelCacheDiagnostic",
    "ModelLeaseManager",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
