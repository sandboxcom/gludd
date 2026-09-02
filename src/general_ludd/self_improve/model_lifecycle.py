"""Owned acquisition, leasing, and bounded eviction for self-improvement GGUFs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from general_ludd.local_model._local_model_configs import _LOCAL_MODELS, LocalModelConfig
from general_ludd.small_models.download import DownloadedModel, ModelDownloader

_MANIFEST_SCHEMA = 1
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_QUOTA_BYTES = 8 * 1024 * 1024 * 1024
_DEFAULT_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
_JSON_LIMIT = 64 * 1024


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

    priority = (
        "qwen2.5-coder-1.5b",
        "deepseek-coder-1.3b",
        "qwen2.5-coder-0.5b",
    )
    by_name = {model.name: model for model in coding}
    for name in priority:
        if name in by_name:
            return by_name[name]
    if not coding:
        raise RuntimeError("no coding model is configured")
    return min(coding, key=lambda model: (model.size_mb, model.name))


def _default_revision_resolver(repo_id: str) -> str:
    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
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


def _delete_hf_revision(cache_root: Path, revision: str) -> None:
    from huggingface_hub import scan_cache_dir

    strategy = scan_cache_dir(cache_dir=cache_root).delete_revisions(revision)
    strategy.execute()


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

        self._models_dir = self.cache_root / ".gludd" / "models"
        self._leases_dir = self.cache_root / ".gludd" / "leases"
        self._acquire_dir = self.cache_root / ".gludd" / "acquiring"
        for directory in (self._models_dir, self._leases_dir, self._acquire_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._selector = model_selector or _default_selector
        self._resolve_revision = revision_resolver or _default_revision_resolver
        self._downloader_factory = downloader_factory or _default_downloader
        self._disk_free = disk_free or _default_disk_free
        self._delete_revision = revision_deleter or _delete_hf_revision
        self._process_started = process_started or _default_process_started
        current_started = self._process_started(os.getpid())
        if current_started is None:
            raise RuntimeError("cannot identify current model lease owner")
        self._current_started = current_started

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

    def reclaim(self, *, required_bytes: int) -> tuple[Path, ...]:
        """Evict oldest owned, unleased revisions until quota/headroom is safe."""
        if isinstance(required_bytes, bool) or required_bytes < 0:
            raise ValueError("required_bytes must be a non-negative integer")
        manifests = self._load_manifests()
        active = self._active_digests()
        total = sum(item.size_bytes for item in manifests)
        removed: list[Path] = []

        def under_pressure() -> bool:
            try:
                free = self._disk_free(self.cache_root)
            except OSError as exc:
                raise RuntimeError("cannot inspect model cache disk headroom") from exc
            return total + required_bytes > self.quota_bytes or free < self.reserve_bytes + required_bytes

        if not under_pressure():
            return ()

        candidates = sorted(
            (item for item in manifests if item.artifact_sha256 not in active),
            key=lambda item: (item.last_used_ns, item.artifact_sha256),
        )
        for item in candidates:
            self._delete_revision(self.cache_root, item.revision)
            if item.path.exists():
                raise RuntimeError(
                    f"model revision eviction did not remove owned artifact: {item.path}"
                )
            try:
                item.manifest_path.unlink()
            except OSError as exc:
                raise RuntimeError(
                    f"model ownership manifest cleanup failed: {item.manifest_path}"
                ) from exc
            total -= item.size_bytes
            removed.append(item.path)
            if not under_pressure():
                return tuple(removed)

        raise RuntimeError(
            "insufficient model cache headroom: all remaining artifacts are leased "
            "or the configured quota/reserve cannot admit the request"
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
            return newest.revision
        resolved = self._resolve_revision(normalized_repo)
        revision = resolved.lower() if isinstance(resolved, str) else ""
        if _SHA_RE.fullmatch(revision) is None:
            raise RuntimeError("model revision resolver did not return a 40-character commit")
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
        if cached is None:
            estimated = config.size_mb * 1024 * 1024
            self.reclaim(required_bytes=estimated)
            lock_path = self._acquisition_lock_path(config.repo, revision)
            descriptor = self._claim_acquisition(lock_path)
            try:
                cached = self._find_owned(config, revision)
                if cached is None:
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


__all__ = ["AcquiredModel", "ModelLeaseManager"]
