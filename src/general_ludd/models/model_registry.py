from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = os.path.expanduser("~/.cache/general-ludd/models")


@dataclass
class ModelSearchResult:
    model_id: str
    author: str = ""
    downloads: int = 0
    tags: list[str] = field(default_factory=list)
    pipeline_tag: str = ""
    library_name: str = ""
    description: str = ""


@dataclass
class DownloadedModel:
    model_id: str
    local_path: str
    filename: str | None = None
    engine: str = "vllm"
    size_bytes: int = 0
    downloaded_at: float = 0.0


class ModelRegistry:
    def __init__(self, cache_dir: str | None = None, hf_token: str | None = None) -> None:
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
        api = self._get_api()
        kwargs: dict[str, Any] = {"sort": sort, "limit": limit, "direction": -1}
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

    def get_model_info(self, model_id: str) -> dict[str, Any]:
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

    def list_files(self, model_id: str) -> list[str]:
        api = self._get_api()
        return list(api.list_repo_files(repo_id=model_id))

    def download(self, model_id: str, filename: str | None = None, engine: str = "vllm") -> DownloadedModel:
        import time

        from huggingface_hub import hf_hub_download, snapshot_download

        if filename:
            local_path = hf_hub_download(
                repo_id=model_id,
                filename=filename,
                cache_dir=str(self._cache_dir),
                token=self._hf_token,
            )
            downloaded = DownloadedModel(
                model_id=model_id,
                local_path=local_path,
                filename=filename,
                engine=engine,
                downloaded_at=time.time(),
            )
        else:
            local_path = snapshot_download(
                repo_id=model_id,
                cache_dir=str(self._cache_dir),
                token=self._hf_token,
            )
            downloaded = DownloadedModel(
                model_id=model_id,
                local_path=local_path,
                engine=engine,
                downloaded_at=time.time(),
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
        return list(self._downloaded.values())

    def get_downloaded(self, model_id: str) -> DownloadedModel | None:
        return self._downloaded.get(model_id)

    def remove_downloaded(self, model_id: str) -> None:
        model = self._downloaded.pop(model_id, None)
        if model:
            self._save_index()
            logger.info("Removed model %s from registry", model_id)

    def refresh(self) -> None:
        self._downloaded.clear()
        self._load_index()

    def _index_path(self) -> Path:
        return self._cache_dir / "model_index.json"

    def _load_index(self) -> None:
        path = self._index_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                for item in data:
                    dm = DownloadedModel(**item)
                    self._downloaded[dm.model_id] = dm
            except (json.JSONDecodeError, KeyError):
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
                }
            )
        path.write_text(json.dumps(data, indent=2))
