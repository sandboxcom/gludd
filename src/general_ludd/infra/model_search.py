"""SearX-based model discovery and indexing system.

Queries a SearX/SearXNG instance for model metadata across HuggingFace,
GitHub, and generic web sources. Caches results locally for fast lookup.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import httpx

SEARX_DEFAULT_URL = os.environ.get("GLUDD_SEARX_URL") or "http://localhost:8888"
SEARX_DOCKER_URL = os.environ.get("GLUDD_SEARX_DOCKER_URL", "http://localhost:8080")
INDEX_CACHE_DIR = os.environ.get(
    "GLUDD_MODEL_INDEX_DIR",
    str(Path.home() / ".gludd" / "model_index"),
)

_SOURCE_ENGINES: dict[str, str] = {
    "huggingface": "huggingface",
    "github": "github",
    "web": "google",
}

_QUANT_PATTERNS: dict[str, str] = {
    "q8_0": r"q8[_\-\.]?0",
    "q6_k": r"q6[_\-\.]?k",
    "q5_k_m": r"q5[_\-\.]?k[_\-\.]?m",
    "q4_k_m": r"q4[_\-\.]?k[_\-\.]?m",
    "awq": r"awq",
    "gptq": r"gptq",
    "fp8": r"fp8",
    "fp16": r"fp16",
    "bf16": r"bf16",
    "gguf": r"gguf",
}


@dataclass
class ModelSearchResult:
    """One discovered model with its metadata and download sources."""

    name: str
    source_url: str = ""
    download_urls: list[str] = field(default_factory=list)
    params_count: float = 0.0
    quantizations_available: list[str] = field(default_factory=list)
    license: str = ""
    description: str = ""


class SearXModelSearch:
    """Query a SearX/SearXNG instance for model metadata across the web."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        """Initialize with the SearX base URL and request timeout."""
        self._base_url = (base_url or SEARX_DEFAULT_URL).rstrip("/")
        self._timeout = timeout

    def _search_url(self) -> str:
        return f"{self._base_url}/search"

    def _do_search(self, query: str, engines: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"q": query, "format": "json", "language": "en"}
        if engines:
            params["engines"] = engines
        try:
            resp = httpx.get(self._search_url(), params=params, timeout=self._timeout)
            resp.raise_for_status()
            data: dict[str, object] = resp.json()
            raw = data.get("results", [])
            if not isinstance(raw, list):
                return []
            return cast("list[dict[str, Any]]", raw)
        except Exception:
            return []

    def search_models(
        self,
        query: str,
        source: str = "huggingface",
    ) -> list[ModelSearchResult]:
        """Search for models matching the query from the given source engine."""
        engine = _SOURCE_ENGINES.get(source, "google")
        raw = self._do_search(f"site:huggingface.co {query} LLM model", engines=engine)
        results: list[ModelSearchResult] = []
        seen: set[str] = set()
        for r in raw:
            url = str(r.get("url", ""))
            if not url or "huggingface.co" not in url:
                continue
            if url in seen:
                continue
            seen.add(url)
            name = self._extract_model_name(url, str(r.get("title", "")))
            result = ModelSearchResult(
                name=name,
                source_url=url,
                description=str(r.get("content", ""))[:500],
            )
            results.append(result)
        return results

    def find_model(self, model_name: str) -> ModelSearchResult | None:
        """Search for one named model and return a rich result, or None."""
        raw = self._do_search(
            f"{model_name} huggingface GGUF download quantization parameters",
        )
        if not raw:
            return None

        best_url = ""
        best_title = ""
        best_content = ""
        for r in raw:
            url = str(r.get("url", ""))
            if "huggingface.co" in url:
                best_url = url
                best_title = str(r.get("title", ""))
                best_content = str(r.get("content", ""))
                break
        if not best_url:
            for r in raw:
                url = str(r.get("url", ""))
                if url and "github.com" in url:
                    best_url = url
                    best_title = str(r.get("title", ""))
                    best_content = str(r.get("content", ""))
                    break

        if not best_url:
            return None

        name = self._extract_model_name(best_url, best_title)
        download_urls = self._extract_download_urls(best_content)
        quants = self._detect_quants(best_title + " " + best_content)
        params = self._extract_param_count(best_title + " " + best_content)
        license_str = self._extract_license(best_content)

        return ModelSearchResult(
            name=name,
            source_url=best_url,
            download_urls=download_urls,
            params_count=params,
            quantizations_available=quants,
            license=license_str,
            description=best_content[:500],
        )

    @staticmethod
    def _extract_model_name(url: str, title: str) -> str:
        if "huggingface.co/" in url:
            parts = url.split("huggingface.co/")
            if len(parts) > 1:
                rest = parts[1].split("?")[0].split("#")[0].rstrip("/")
                return rest.replace("/", "__")
        for word in title.replace("\u00b7", " ").replace("\u2013", " ").split():
            if "/" in word and len(word) > 3:
                return word.replace("/", "__").rstrip(".")
        return title.strip()

    @staticmethod
    def _extract_download_urls(text: str) -> list[str]:
        urls: list[str] = []
        for word in text.split():
            if word.startswith(("https://huggingface.co/", "https://github.com/")) and (
                ".gguf" in word.lower() or "resolve/main" in word or "/blob/" in word
            ):
                urls.append(word.rstrip(".,;:)"))
        return urls[:5]

    @staticmethod
    def _detect_quants(text: str) -> list[str]:
        import re

        found: list[str] = []
        text_lower = text.lower()
        for quant, pattern in _QUANT_PATTERNS.items():
            if re.search(pattern, text_lower):
                found.append(quant)
        return found

    @staticmethod
    def _extract_param_count(text: str) -> float:
        import re

        m = re.search(r"(\d+\.?\d*)\s*[bB]\s*(?:param|model|LLM)", text)
        if m:
            return float(m.group(1))
        m = re.search(r"(\d+)\s*[bB]\b", text)
        if m:
            return float(m.group(1))
        m = re.search(r"(\d+\.?\d*)\s*(?:billion|Bn|Billion)", text)
        if m:
            return float(m.group(1))
        return 0.0

    @staticmethod
    def _extract_license(text: str) -> str:
        import re

        known = [
            "apache-2.0",
            "apache 2.0",
            "MIT",
            "llama3",
            "llama 3",
            "cc-by-nc-4.0",
            "cc-by-4.0",
            "gpl-3.0",
            "gpl 3.0",
            "bsd-3-clause",
            "bsd 2-clause",
            "gemma",
            "openrail",
        ]
        text_lower = text.lower()
        for lic in known:
            if lic in text_lower:
                return lic
        m = re.search(r"(?:license|licensed under)\s+([A-Za-z0-9\-\.]+(?:\s+v?\d+\.?\d*)?)", text, re.IGNORECASE)
        if m:
            captured = m.group(1).strip().lower()
            if any(c.isdigit() or c == "-" for c in captured):
                return captured
        return ""


class ModelIndex:
    """Local JSON cache of discovered models keyed by model name."""

    def __init__(self, cache_dir: str | None = None) -> None:
        """Load (or create) the index cache at the given directory."""
        self._cache_dir = Path(cache_dir or INDEX_CACHE_DIR)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._cache_dir / "index.json"
        self._entries: dict[str, ModelSearchResult] = {}
        self._load()

    def _load(self) -> None:
        if not self._index_path.exists():
            return
        try:
            with open(self._index_path) as f:
                decoded: object = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(decoded, dict):
            return
        raw = cast("dict[str, Any]", decoded)
        for name, data in raw.items():
            if isinstance(name, str) and isinstance(data, dict):
                self._entries[name] = ModelSearchResult(**data)

    def _save(self) -> None:
        serializable = {
            name: {
                "name": r.name,
                "source_url": r.source_url,
                "download_urls": r.download_urls,
                "params_count": r.params_count,
                "quantizations_available": r.quantizations_available,
                "license": r.license,
                "description": r.description,
            }
            for name, r in self._entries.items()
        }
        with open(self._index_path, "w") as f:
            json.dump(serializable, f, indent=2)

    def get(self, model_name: str) -> ModelSearchResult | None:
        """Return the cached result for one model name, or None."""
        return self._entries.get(model_name)

    def put(self, result: ModelSearchResult) -> None:
        """Store one result in the index and persist it to disk."""
        self._entries[result.name] = result
        self._save()

    def search(self, query: str) -> list[ModelSearchResult]:
        """Return entries whose name or description contains the query."""
        query_lower = query.lower()
        return [
            r for r in self._entries.values() if query_lower in r.name.lower() or query_lower in r.description.lower()
        ]

    def list_all(self) -> list[ModelSearchResult]:
        """Return every cached entry in insertion order."""
        return list(self._entries.values())

    def size(self) -> int:
        """Return the number of cached entries."""
        return len(self._entries)
