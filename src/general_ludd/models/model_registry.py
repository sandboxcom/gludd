"""Discover, inspect, download, and inventory public model artifacts."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from general_ludd.models.model_deployment_metadata import (
    ModelDeploymentMetadata as ModelDeploymentMetadata,
)
from general_ludd.models.model_deployment_metadata import (
    model_context_tokens,
    model_license_id,
    safetensors_shape,
)

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = os.path.expanduser("~/.cache/general-ludd/models")


@dataclass
class ModelSearchResult:
    """Represent bounded model-catalog search metadata."""

    model_id: str
    author: str = ""
    downloads: int = 0
    tags: list[str] = field(default_factory=list)
    pipeline_tag: str = ""
    library_name: str = ""
    description: str = ""


@dataclass
class DownloadedModel:
    """Record one materialized model artifact and its immutable revision."""

    model_id: str
    local_path: str
    filename: str | None = None
    engine: str = "vllm"
    size_bytes: int = 0
    downloaded_at: float = 0.0
    revision: str | None = None


class ModelRegistry:
    """Access public model metadata and manage the local artifact index."""

    def __init__(self, cache_dir: str | None = None, hf_token: str | None = None) -> None:
        """Initialize the registry under an explicit or default cache directory."""
        self._cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._hf_token = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        self._downloaded: dict[str, DownloadedModel] = {}
        self._load_index()

    def _get_api(self) -> Any:
        from huggingface_hub import HfApi

        return HfApi(token=self._hf_token)

    def search(
        self,
        query: str = "",
        tags: list[str] | None = None,
        sort: str = "downloads",
        limit: int = 20,
        author: str | None = None,
    ) -> list[ModelSearchResult]:
        """Search the configured public model catalog with bounded filters."""
        api = self._get_api()
        kwargs: dict[str, object] = {"sort": sort, "limit": limit, "direction": -1}
        if query:
            kwargs["search"] = query
        if tags:
            kwargs["filter"] = tags
        if author:
            kwargs["author"] = author
        results = []
        for m in api.list_models(**kwargs):
            results.append(
                ModelSearchResult(
                    model_id=m.id,
                    author=m.author or "",
                    downloads=m.downloads or 0,
                    tags=list(m.tags) if m.tags else [],
                    pipeline_tag=m.pipeline_tag or "",
                    library_name=m.library_name or "",
                )
            )
        return results

    def get_model_info(self, model_id: str) -> dict[str, object]:
        """Return descriptive public metadata for one model identifier."""
        api = self._get_api()
        info = api.model_info(repo_id=model_id)
        return {
            "model_id": info.id,
            "author": info.author,
            "downloads": info.downloads,
            "tags": list(info.tags) if info.tags else [],
            "pipeline_tag": info.pipeline_tag,
            "library_name": info.library_name,
            "last_modified": str(info.last_modified) if info.last_modified else None,
        }

    def get_deployment_metadata(self, model_id: str) -> ModelDeploymentMetadata:
        """Resolve one public model to immutable deployment-planning facts."""
        info = self._get_api().model_info(
            repo_id=model_id,
            expand=[
                "cardData",
                "config",
                "downloads",
                "gated",
                "library_name",
                "pipeline_tag",
                "private",
                "safetensors",
                "sha",
                "tags",
                "usedStorage",
            ],
        )
        if getattr(info, "private", None) is not False or getattr(
            info, "gated", None
        ) is not False:
            raise ValueError("model deployment requires a public ungated repository")
        parameter_count, weight_bits = safetensors_shape(info)
        tags = tuple(sorted(set(getattr(info, "tags", None) or ())))
        storage = getattr(info, "used_storage", None)
        if isinstance(storage, bool) or not isinstance(storage, int) or storage <= 0:
            storage = (parameter_count * weight_bits + 7) // 8
        return ModelDeploymentMetadata(
            model_id=str(getattr(info, "id", "")),
            revision=str(getattr(info, "sha", "")).casefold(),
            parameter_count=parameter_count,
            context_tokens=model_context_tokens(info),
            storage_bytes=storage,
            weight_bits=weight_bits,
            license_id=model_license_id(info, tags),
            tags=tags,
            pipeline_tag=str(getattr(info, "pipeline_tag", "") or ""),
            library_name=str(getattr(info, "library_name", "") or ""),
            downloads=int(getattr(info, "downloads", 0) or 0),
        )

    def list_files(self, model_id: str) -> list[str]:
        """List repository files for one public model identifier."""
        api = self._get_api()
        return list(api.list_repo_files(repo_id=model_id))

    def download(
        self,
        model_id: str,
        filename: str | None = None,
        engine: str = "vllm",
        revision: str | None = None,
    ) -> DownloadedModel:
        """Materialize one model artifact and persist its local index entry."""
        import time

        from huggingface_hub import hf_hub_download, snapshot_download

        if revision is None:
            logger.warning(
                "Downloading model %s without a pinned revision; tracking mutable main HEAD "
                "(supply-chain risk). Pass revision=<sha|tag> to pin.",
                model_id,
            )

        if filename:
            local_path = hf_hub_download(  # nosec B615 - revision forwarded; None warned above
                repo_id=model_id,
                filename=filename,
                cache_dir=str(self._cache_dir),
                token=self._hf_token,
                revision=revision,
            )
            downloaded = DownloadedModel(
                model_id=model_id,
                local_path=local_path,
                filename=filename,
                engine=engine,
                downloaded_at=time.time(),
                revision=revision,
            )
        else:
            local_path = snapshot_download(  # nosec B615 - revision forwarded; None warned above
                repo_id=model_id,
                cache_dir=str(self._cache_dir),
                token=self._hf_token,
                revision=revision,
            )
            downloaded = DownloadedModel(
                model_id=model_id,
                local_path=local_path,
                engine=engine,
                downloaded_at=time.time(),
                revision=revision,
            )
        p = Path(local_path)
        if p.exists():
            downloaded.size_bytes = (
                sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
            )
        self._downloaded[model_id] = downloaded
        self._save_index()
        logger.info("Downloaded model %s to %s", model_id, local_path)
        return downloaded

    def list_downloaded(self) -> list[DownloadedModel]:
        """Return all locally indexed model artifacts."""
        return list(self._downloaded.values())

    def get_downloaded(self, model_id: str) -> DownloadedModel | None:
        """Return one locally indexed model artifact when present."""
        return self._downloaded.get(model_id)

    def remove_downloaded(self, model_id: str) -> None:
        """Remove one model from the local registry index."""
        model = self._downloaded.pop(model_id, None)
        if model:
            self._save_index()
            logger.info("Removed model %s from registry", model_id)

    def refresh(self) -> None:
        """Reload the local model registry from its persisted index."""
        self._downloaded.clear()
        self._load_index()

    def _index_path(self) -> Path:
        return self._cache_dir / "model_index.json"

    def _load_index(self) -> None:
        path = self._index_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                known = {f.name for f in dataclass_fields(DownloadedModel)}
                for item in data:
                    # Tolerate legacy indexes missing `revision` (default applies) and
                    # ignore any unexpected keys from future schema versions.
                    dm = DownloadedModel(**{k: v for k, v in item.items() if k in known})
                    self._downloaded[dm.model_id] = dm
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.warning("Failed to load model index")

    def _save_index(self) -> None:
        path = self._index_path()
        data = []
        for dm in self._downloaded.values():
            data.append(
                {
                    "model_id": dm.model_id,
                    "local_path": dm.local_path,
                    "filename": dm.filename,
                    "engine": dm.engine,
                    "size_bytes": dm.size_bytes,
                    "downloaded_at": dm.downloaded_at,
                    "revision": dm.revision,
                }
            )
        path.write_text(json.dumps(data, indent=2))
