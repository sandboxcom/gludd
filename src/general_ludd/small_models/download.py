"""Multi-source model download with progress tracking and scheduling."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from general_ludd.security.state import project_state

if TYPE_CHECKING:
    from general_ludd.cloud.model_sources import ModelSource
    from general_ludd.small_models.model_hash_db import ModelHashDB

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = os.environ.get(
    "GLUDD_MODEL_DIR",
    os.path.expanduser("~/.cache/general-ludd/models"),
)
DEFAULT_DOWNLOAD_TIMEOUT = float(os.environ.get("GLUDD_HF_DOWNLOAD_TIMEOUT", "30"))
_LARGE_DOWNLOAD_GB = 1.0
_HF_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_HF_BLOB_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.IGNORECASE)


class DownloadSource(StrEnum):
    """Where a model file was fetched from."""

    HUGGINGFACE = "huggingface"
    GGUF = "gguf"
    OLLAMA = "ollama"
    CACHE = "cache"


class ModelCacheIntegrityError(RuntimeError):
    """Raised when an exact cached artifact cannot be trusted."""


@dataclass
class DownloadedModel:
    """Record of a completed model download."""

    model_id: str
    local_path: str
    source: DownloadSource = DownloadSource.HUGGINGFACE
    filename: str | None = None
    size_bytes: int = 0
    revision: str | None = None
    downloaded_at: float = field(default_factory=time.time)


@dataclass
class DownloadProgress:
    """Mutable progress state for an in-flight download."""

    filename: str = ""
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed_bytes_per_sec: float = 0.0
    status: str = "idle"

    @property
    def percent(self) -> float:
        """Percent complete in [0, 100], or 0 when total bytes are unknown."""
        if self.total_bytes <= 0:
            return 0.0
        return min(100.0, (self.downloaded_bytes / self.total_bytes) * 100.0)


def _map_model_source_to_download_source(source: ModelSource) -> DownloadSource:
    from general_ludd.cloud.model_sources import ModelSource

    _MAP = {
        ModelSource.HUGGINGFACE: DownloadSource.HUGGINGFACE,
        ModelSource.OLLAMA: DownloadSource.OLLAMA,
        ModelSource.DIRECT_URL: DownloadSource.HUGGINGFACE,
        ModelSource.LOCAL_PATH: DownloadSource.CACHE,
        ModelSource.S3_MIRROR: DownloadSource.HUGGINGFACE,
    }
    return _MAP[source]


class ModelDownloader:
    """Downloads models from Hugging Face, GGUF, and Ollama sources."""

    def __init__(
        self,
        cache_dir: str | None = None,
        hf_token: str | None = None,
        timeout: float | None = None,
        hash_db: ModelHashDB | None = None,
        oidc_auth: object | None = None,
    ) -> None:
        """Initialize the downloader with an optional cache dir and auth."""
        explicit_cache = cache_dir or os.environ.get("GLUDD_MODEL_DIR")
        self.cache_dir = explicit_cache or DEFAULT_CACHE_DIR
        try:
            Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        except OSError:
            if explicit_cache is not None:
                raise
            # Service accounts and isolated tests may intentionally have no
            # writable home directory. Keep startup available without sharing a
            # global temp path by falling back to the secure project namespace.
            self.cache_dir = str(project_state().directory("models"))
        self.hf_token = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        self._oidc_auth = oidc_auth
        self.timeout = timeout if timeout is not None else DEFAULT_DOWNLOAD_TIMEOUT
        self._hash_db = hash_db
        self._downloaded: dict[str, DownloadedModel] = {}
        self._progress: DownloadProgress = DownloadProgress()
        self._last_bytes: int = 0
        self._last_time: float = 0.0
        self._on_progress: Callable[[DownloadProgress], None] | None = None

    def on_progress(self, callback: Callable[[DownloadProgress], None]) -> None:
        """Register a callback invoked as download progress changes."""
        self._on_progress = callback

    def get_progress(self) -> DownloadProgress:
        """Return the current download progress state."""
        return self._progress

    def _resolve_token(self) -> str | None:
        if self._oidc_auth is not None:
            from general_ludd.small_models.hf_auth import HfOidcAuth

            if isinstance(self._oidc_auth, HfOidcAuth):
                oidc_token = self._oidc_auth.get_token()
                if oidc_token:
                    return oidc_token
        return self.hf_token

    @staticmethod
    def _resolved_revision(local_path: str, requested_revision: str | None) -> str | None:
        """Return the immutable commit encoded by a Hub snapshot path."""
        if requested_revision is not None and _HF_COMMIT_RE.fullmatch(requested_revision):
            return requested_revision

        parts = Path(local_path).parts
        for index, part in enumerate(parts[:-1]):
            if part == "snapshots" and _HF_COMMIT_RE.fullmatch(parts[index + 1]):
                return parts[index + 1]
        return requested_revision

    @staticmethod
    def _blob_digest(path: Path) -> str | None:
        """Compute the digest encoded by a Hugging Face blob filename."""
        blob_name = path.name
        if not _HF_BLOB_RE.fullmatch(blob_name):
            return None

        if len(blob_name) == 64:
            digest = hashlib.sha256()
        else:
            digest = hashlib.sha1(usedforsecurity=False)
            digest.update(f"blob {path.stat().st_size}\0".encode())

        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _validate_cached_artifact(self, cached_path: str, filename: str) -> None:
        """Reject incomplete, escaped, empty, or digest-mismatched cache entries."""
        candidate = Path(cached_path)
        cache_root = Path(self.cache_dir).resolve(strict=True)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(cache_root)
        except (OSError, ValueError) as exc:
            raise ModelCacheIntegrityError(
                f"cached model artifact failed integrity validation: {candidate}"
            ) from exc

        if (
            not resolved.is_file()
            or resolved.stat().st_size <= 0
            or any(part.endswith(".incomplete") for part in (*candidate.parts, *resolved.parts))
        ):
            raise ModelCacheIntegrityError(
                f"cached model artifact failed integrity validation: {candidate}"
            )

        expected_digest = resolved.name.lower()
        actual_digest = self._blob_digest(resolved)
        if actual_digest is not None and actual_digest != expected_digest:
            raise ModelCacheIntegrityError(
                f"cached model artifact failed integrity validation: {candidate}"
            )

        if filename.lower().endswith(".gguf"):
            with resolved.open("rb") as artifact:
                if artifact.read(4) != b"GGUF":
                    raise ModelCacheIntegrityError(
                        f"cached model artifact failed integrity validation: {candidate}"
                    )

    def _find_cached_artifact(
        self,
        model_id: str,
        filename: str,
        revision: str | None,
    ) -> tuple[str, str | None] | None:
        """Look up and validate one exact artifact without contacting the Hub."""
        from huggingface_hub import try_to_load_from_cache

        cached_path = try_to_load_from_cache(
            repo_id=model_id,
            filename=filename,
            cache_dir=self.cache_dir,
            revision=revision,
        )
        if not isinstance(cached_path, str):
            return None

        self._validate_cached_artifact(cached_path, filename)
        return cached_path, self._resolved_revision(cached_path, revision)

    def download_huggingface(
        self,
        model_id: str,
        filename: str | None = None,
        revision: str | None = None,
    ) -> DownloadedModel:
        """Download a model repo or single file from Hugging Face."""
        from huggingface_hub import hf_hub_download, snapshot_download

        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(self.timeout))
        os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", str(self.timeout))

        if revision is None:
            logger.warning(
                "Downloading model %s without a pinned revision; tracking mutable main HEAD.",
                model_id,
            )

        cached = self._find_cached_artifact(model_id, filename, revision) if filename else None
        if cached is not None:
            local_path, resolved_revision = cached
            source = DownloadSource.CACHE
        else:
            token: str | bool = self._resolve_token() or False
            if filename:
                local_path = hf_hub_download(
                    repo_id=model_id,
                    filename=filename,
                    token=token,
                    revision=revision,
                    cache_dir=self.cache_dir,
                )
            else:
                local_path = snapshot_download(
                    repo_id=model_id,
                    token=token,
                    revision=revision,
                    cache_dir=self.cache_dir,
                )
            resolved_revision = self._resolved_revision(local_path, revision)
            source = DownloadSource.HUGGINGFACE

        downloaded = DownloadedModel(
            model_id=model_id,
            local_path=local_path,
            source=source,
            filename=filename,
            revision=resolved_revision,
            downloaded_at=time.time(),
        )

        self._compute_size(downloaded)
        self._downloaded[model_id] = downloaded
        logger.info("Downloaded HF model %s to %s (%.1f MB)", model_id, local_path, downloaded.size_bytes / 1e6)
        return downloaded

    def download_gguf(
        self,
        model_id: str,
        filename: str,
        revision: str | None = None,
        *,
        local_files_only: bool = False,
    ) -> DownloadedModel:
        """Download a single GGUF file from Hugging Face."""
        import time as _time

        from huggingface_hub import hf_hub_download

        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(self.timeout))

        cached = self._find_cached_artifact(model_id, filename, revision)
        if cached is not None:
            local_path, resolved_revision = cached
            source = DownloadSource.CACHE
        else:
            token: str | bool = self._resolve_token() or False
            local_path = hf_hub_download(
                repo_id=model_id,
                filename=filename,
                token=token,
                revision=revision,
                cache_dir=self.cache_dir,
                local_files_only=local_files_only,
            )
            resolved_revision = self._resolved_revision(local_path, revision)
            source = DownloadSource.GGUF

        downloaded = DownloadedModel(
            model_id=model_id,
            local_path=local_path,
            source=source,
            filename=filename,
            revision=resolved_revision,
            downloaded_at=_time.time(),
        )

        self._compute_size(downloaded)
        self._downloaded[model_id] = downloaded
        logger.info(
            "Downloaded GGUF model %s (%s) to %s (%.1f MB)", model_id, filename, local_path, downloaded.size_bytes / 1e6
        )
        return downloaded

    def pull_ollama(
        self,
        model_id: str,
        revision: str | None = None,
    ) -> DownloadedModel:
        """Pull an ollama-tagged model repo snapshot from Hugging Face."""
        import time as _time

        from huggingface_hub import snapshot_download

        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(self.timeout))

        stripped_id = model_id.split(":")[0] if ":" in model_id else model_id

        if revision is None:
            logger.warning(
                "Pulling ollama model %s without a pinned revision.",
                model_id,
            )

        token: str | bool = self._resolve_token() or False

        local_path = snapshot_download(
            repo_id=stripped_id,
            token=token,
            revision=revision,
            cache_dir=self.cache_dir,
        )

        downloaded = DownloadedModel(
            model_id=model_id,
            local_path=local_path,
            source=DownloadSource.OLLAMA,
            revision=self._resolved_revision(local_path, revision),
            downloaded_at=_time.time(),
        )

        self._compute_size(downloaded)
        self._downloaded[model_id] = downloaded
        logger.info(
            "Pulled ollama model %s (resolved to %s) to %s (%.1f MB)",
            model_id,
            stripped_id,
            local_path,
            downloaded.size_bytes / 1e6,
        )
        return downloaded

    def download_multi_source(
        self,
        model_id: str,
        filename: str | None = None,
        order: list[str] | None = None,
        retries: int = 1,
    ) -> DownloadedModel | None:
        """Download via the configured source order; None when unknown."""
        from general_ludd.cloud.model_sources import (
            ModelSource,
            download_with_fallback,
        )
        from general_ludd.local_model._local_model_configs import _LOCAL_MODELS

        config = next((c for c in _LOCAL_MODELS if c.name == model_id), None)
        if config is None:
            return None

        source_order: list[ModelSource] | None = None
        if order:
            source_map = {s.value: s for s in ModelSource}
            source_order = []
            for s in order:
                mapped = source_map.get(s)
                if mapped is not None:
                    source_order.append(mapped)

        try:
            result = download_with_fallback(
                config=config,
                order=source_order,
                cache_dir=self.cache_dir,
                retries=retries,
                timeout=self.timeout,
            )
            downloaded_source = _map_model_source_to_download_source(result.source)
            downloaded = DownloadedModel(
                model_id=model_id,
                local_path=result.local_path,
                source=downloaded_source,
                filename=filename or config.filename,
                size_bytes=result.size_bytes,
                downloaded_at=result.downloaded_at,
            )
            self._downloaded[model_id] = downloaded
            return downloaded
        except Exception:
            return None

    def download(
        self,
        model_id: str,
        filename: str | None = None,
        revision: str | None = None,
        force: bool = False,
        verify_hash: bool = True,
        order: list[str] | None = None,
        estimated_size_gb: float | None = None,
    ) -> DownloadedModel:
        """Download a model, preferring multi-source dispatch with fallback.

        Peak-window advice is evaluated only when the model size is known.  An
        explicit estimate takes precedence over catalog metadata, while
        ``force=True`` bypasses scheduling advice entirely.
        """
        download_size_gb = estimated_size_gb
        if download_size_gb is None:
            from general_ludd.local_model._local_model_configs import _LOCAL_MODELS

            config = next(
                (
                    candidate
                    for candidate in _LOCAL_MODELS
                    if model_id in (candidate.name, candidate.repo, *candidate.aliases)
                ),
                None,
            )
            if config is not None and config.size_mb > 0:
                download_size_gb = config.size_mb / 1024

        if not force and download_size_gb is not None:
            from general_ludd.small_models.cost import should_defer_download

            defer_info = should_defer_download(download_size_gb, threshold_gb=_LARGE_DOWNLOAD_GB)
            if defer_info.get("defer"):
                logger.warning(
                    "Large download of %s is starting during peak pricing (%s). "
                    "Use force=True to acknowledge peak transfer cost or wait for the off-peak window.",
                    model_id,
                    defer_info.get("next_off_peak", {}),
                )

        multi_result = self.download_multi_source(
            model_id=model_id,
            filename=filename,
            order=order,
        )
        if multi_result is not None:
            result = multi_result
        elif filename and filename.lower().endswith(".gguf"):
            result = self.download_gguf(model_id=model_id, filename=filename, revision=revision)
        else:
            result = self.download_huggingface(model_id=model_id, filename=filename, revision=revision)

        # The public orchestration method owns its state invariant even when a
        # backend implementation is replaced or wrapped by an integration.
        self._downloaded[model_id] = result

        if verify_hash and self._hash_db is not None:
            from general_ludd.small_models.model_hash_db import ModelIntegrityError

            try:
                self._hash_db.verify_download(model_id, result.local_path)
            except ModelIntegrityError:
                self._downloaded.pop(model_id, None)
                raise

        return result

    def check_download_scheduling(self, size_gb: float) -> dict[str, object]:
        """Report whether a download of the given size should be deferred."""
        from general_ludd.small_models.cost import is_off_peak, next_off_peak_window, should_defer_download

        defer_info = should_defer_download(size_gb, threshold_gb=_LARGE_DOWNLOAD_GB)
        return {
            "size_gb": round(size_gb, 2),
            "is_off_peak_now": is_off_peak(),
            "should_defer": defer_info.get("defer", False),
            "reason": defer_info.get("reason", "unknown"),
            "next_off_peak": next_off_peak_window(),
        }

    def estimate_download_cost(self, size_gb: float) -> dict[str, object]:
        """Estimate egress, storage, and off-peak preference for a download."""
        from general_ludd.small_models.cost import estimate_download_cost as _estimate

        return _estimate("", size_gb=size_gb)

    def list_downloaded(self) -> list[DownloadedModel]:
        """List every tracked downloaded model."""
        return list(self._downloaded.values())

    def get_downloaded(self, model_id: str) -> DownloadedModel | None:
        """Return the tracked download for a model id, if any."""
        return self._downloaded.get(model_id)

    def remove_downloaded(self, model_id: str) -> None:
        """Forget the tracked download record for a model id."""
        self._downloaded.pop(model_id, None)

    def _compute_size(self, model: DownloadedModel) -> None:
        p = Path(model.local_path)
        if p.is_dir():
            model.size_bytes = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        elif p.is_file():
            model.size_bytes = p.stat().st_size

    def _make_progress_callback(self, filename: str, total_bytes: int) -> Callable[[int, int, int], None]:
        self._progress = DownloadProgress(
            filename=filename,
            total_bytes=total_bytes,
            downloaded_bytes=0,
            status="downloading",
        )
        self._last_bytes = 0
        self._last_time = time.time()

        def _cb(_chunk_bytes: int, bytes_downloaded: int, total_size: int) -> None:
            now = time.time()
            elapsed = now - self._last_time
            if elapsed > 0:
                self._progress.speed_bytes_per_sec = (bytes_downloaded - self._last_bytes) / elapsed
            self._last_bytes = bytes_downloaded
            self._last_time = now
            self._progress.downloaded_bytes = bytes_downloaded
            self._progress.total_bytes = total_size

            if self._progress.percent >= 100.0:
                self._progress.status = "done"

            if self._on_progress:
                self._on_progress(self._progress)

        return _cb
