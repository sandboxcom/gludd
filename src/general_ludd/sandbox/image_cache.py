"""Docker image cache management for sandbox environments."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CachedImage:
    name: str
    tag: str
    image_id: str = ""
    pulled_at: float = 0.0
    size_bytes: int = 0

    @property
    def full_name(self) -> str:
        return f"{self.name}:{self.tag}"

    @property
    def age_seconds(self) -> float:
        return time.time() - self.pulled_at

    def is_stale(self, max_age_seconds: int) -> bool:
        return self.age_seconds > max_age_seconds

class ImageCache:
    def __init__(self, cache_dir: str = "/tmp/gludd-image-cache") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._images: dict[str, CachedImage] = {}

    def get(self, name: str, tag: str = "latest") -> CachedImage | None:
        key = f"{name}:{tag}"
        return self._images.get(key)

    def put(
        self, image_id: str, name: str, tag: str = "latest",
        size_bytes: int = 0, pulled_at: float | None = None,
    ) -> CachedImage:
        key = f"{name}:{tag}"
        entry = CachedImage(
            name=name, tag=tag, image_id=image_id,
            pulled_at=pulled_at if pulled_at is not None else time.time(),
            size_bytes=size_bytes,
        )
        self._images[key] = entry
        return entry

    def remove(self, name: str, tag: str = "latest") -> bool:
        key = f"{name}:{tag}"
        if key in self._images:
            del self._images[key]
            return True
        return False

    def ensure_present(self, name: str, tag: str = "latest") -> bool:
        existing = self.get(name, tag)
        if existing is not None:
            return True
        result = subprocess.run(["docker", "pull", f"{name}:{tag}"], capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            self.put(image_id=result.stdout.strip().split("\n")[-1], name=name, tag=tag)
            return True
        return False

    def cleanup_stale(self, max_age_seconds: int = 3600) -> int:
        stale_keys = [key for key, img in self._images.items() if img.is_stale(max_age_seconds)]
        for key in stale_keys:
            del self._images[key]
        return len(stale_keys)

    def prune_all(self) -> int:
        count = len(self._images)
        subprocess.run(["docker", "image", "prune", "-f"], capture_output=True, text=True, timeout=30)
        self._images.clear()
        return count

    def image_count(self) -> int:
        return len(self._images)

    def total_size_bytes(self) -> int:
        return sum(img.size_bytes for img in self._images.values())

    def list_images(self) -> list[CachedImage]:
        return sorted(self._images.values(), key=lambda img: img.pulled_at, reverse=True)
