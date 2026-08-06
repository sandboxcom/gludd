from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.small_models.model_hash_db import ModelHashDB

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = os.environ.get(
    "GLUDD_MODEL_DIR",
    os.path.expanduser("~/.cache/general-ludd/models"),
)
DEFAULT_DOWNLOAD_TIMEOUT = float(os.environ.get("GLUDD_HF_DOWNLOAD_TIMEOUT", "30"))
_LARGE_DOWNLOAD_GB = 1.0


class DownloadSource(StrEnum):
    HUGGINGFACE = "huggingface"
    GGUF = "gguf"
    OLLAMA = "ollama"
    CACHE = "cache"


@dataclass
class DownloadedModel:
    model_id: str
    local_path: str
    source: DownloadSource = DownloadSource.HUGGINGFACE
    filename: str | None = None
    size_bytes: int = 0
    revision: str | None = None
    downloaded_at: float = field(default_factory=time.time)


@dataclass
class DownloadProgress:
    filename: str = ""
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed_bytes_per_sec: float = 0.0
    status: str = "idle"

    @property
    def percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(100.0, (self.downloaded_bytes / self.total_bytes) * 100.0)


class ModelDownloader:
    def __init__(
        self,
        cache_dir: str | None = None,
        hf_token: str | None = None,
        timeout: float | None = None,
        hash_db: ModelHashDB | None = None,
        oidc_auth: object | None = None,
    ) -> None:
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
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
        self._on_progress = callback

    def get_progress(self) -> DownloadProgress:
        return self._progress

    def _resolve_token(self) -> str | None:
        if self._oidc_auth is not None:
            from general_ludd.small_models.hf_auth import HfOidcAuth

            if isinstance(self._oidc_auth, HfOidcAuth):
                oidc_token = self._oidc_auth.get_token()
                if oidc_token:
                    return oidc_token
        return self.hf_token

    def download_huggingface(
        self,
        model_id: str,
        filename: str | None = None,
        revision: str | None = None,
    ) -> DownloadedModel:
        from huggingface_hub import hf_hub_download, snapshot_download

        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(self.timeout))
        os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", str(self.timeout))

        if revision is None:
            logger.warning(
                "Downloading model %s without a pinned revision; tracking mutable main HEAD.",
                model_id,
            )

        token = self._resolve_token()

        if filename:
            local_path = hf_hub_download(
                repo_id=model_id,
                filename=filename,
                token=token,
                revision=revision,
                callback=self._make_progress_callback(filename, -1),
            )
        else:
            local_path = snapshot_download(
                repo_id=model_id,
                token=token,
                revision=revision,
            )

        downloaded = DownloadedModel(
            model_id=model_id,
            local_path=local_path,
            source=DownloadSource.HUGGINGFACE,
            filename=filename,
            revision=revision,
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
    ) -> DownloadedModel:
        import time as _time

        from huggingface_hub import hf_hub_download

        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(self.timeout))

        token = self._resolve_token()

        local_path = hf_hub_download(
            repo_id=model_id,
            filename=filename,
            token=token,
            revision=revision,
            callback=self._make_progress_callback(filename, -1),
        )

        downloaded = DownloadedModel(
            model_id=model_id,
            local_path=local_path,
            source=DownloadSource.GGUF,
            filename=filename,
            revision=revision,
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
        import time as _time

        from huggingface_hub import snapshot_download

        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(self.timeout))

        stripped_id = model_id.split(":")[0] if ":" in model_id else model_id

        if revision is None:
            logger.warning(
                "Pulling ollama model %s without a pinned revision.",
                model_id,
            )

        token = self._resolve_token()

        local_path = snapshot_download(
            repo_id=stripped_id,
            token=token,
            revision=revision,
        )

        downloaded = DownloadedModel(
            model_id=model_id,
            local_path=local_path,
            source=DownloadSource.OLLAMA,
            revision=revision,
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

    def download(
        self,
        model_id: str,
        filename: str | None = None,
        revision: str | None = None,
        force: bool = False,
        verify_hash: bool = True,
    ) -> DownloadedModel:
        from general_ludd.small_models.cost import should_defer_download

        defer_info = should_defer_download(_LARGE_DOWNLOAD_GB + 0.1)
        if defer_info.get("defer") and not force:
            logger.warning(
                "Download of %s deferred: peak pricing active, large download (%s). "
                "Use force=True to override or wait for off-peak window.",
                model_id,
                defer_info.get("next_off_peak", {}),
            )

        if filename and filename.lower().endswith(".gguf"):
            result = self.download_gguf(model_id=model_id, filename=filename, revision=revision)
        else:
            result = self.download_huggingface(model_id=model_id, filename=filename, revision=revision)

        if verify_hash and self._hash_db is not None:
            from general_ludd.small_models.model_hash_db import ModelIntegrityError

            try:
                self._hash_db.verify_download(model_id, result.local_path)
            except ModelIntegrityError:
                self._downloaded.pop(model_id, None)
                raise

        return result

    def check_download_scheduling(self, size_gb: float) -> dict[str, object]:
        from general_ludd.small_models.cost import is_off_peak, next_off_peak_window, should_defer_download

        defer_info = should_defer_download(size_gb)
        return {
            "size_gb": round(size_gb, 2),
            "is_off_peak_now": is_off_peak(),
            "should_defer": defer_info.get("defer", False),
            "reason": defer_info.get("reason", "unknown"),
            "next_off_peak": next_off_peak_window(),
        }

    def estimate_download_cost(self, size_gb: float) -> dict[str, object]:
        from general_ludd.small_models.cost import estimate_download_cost as _estimate

        return _estimate("", size_gb=size_gb)

    def list_downloaded(self) -> list[DownloadedModel]:
        return list(self._downloaded.values())

    def get_downloaded(self, model_id: str) -> DownloadedModel | None:
        return self._downloaded.get(model_id)

    def remove_downloaded(self, model_id: str) -> None:
        self._downloaded.pop(model_id, None)

    def _compute_size(self, model: DownloadedModel) -> None:
        p = Path(model.local_path)
        if p.is_dir():
            model.size_bytes = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        elif p.is_file():
            model.size_bytes = p.stat().st_size

    def _make_progress_callback(self, filename: str, total_bytes: int) -> Callable[[int, int], None]:
        self._progress = DownloadProgress(
            filename=filename,
            total_bytes=total_bytes,
            downloaded_bytes=0,
            status="downloading",
        )
        self._last_bytes = 0
        self._last_time = time.time()

        def _cb(bytes_downloaded: int, total_size: int) -> None:
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
