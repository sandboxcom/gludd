"""Unit tests for sandbox image cache."""

from __future__ import annotations

import subprocess
import time
from unittest.mock import patch

from general_ludd.sandbox.image_cache import CachedImage, ImageCache


class TestCachedImage:
    def test_full_name(self) -> None:
        img = CachedImage(name="alpine", tag="3.19", image_id="abc123")
        assert img.full_name == "alpine:3.19"

    def test_full_name_latest(self) -> None:
        img = CachedImage(name="python", tag="latest")
        assert img.full_name == "python:latest"

    def test_age_seconds(self) -> None:
        now = time.time()
        img = CachedImage(name="alpine", tag="latest", image_id="abc", pulled_at=now - 60)
        assert 59 <= img.age_seconds <= 61

    def test_is_stale(self) -> None:
        img = CachedImage(name="alpine", tag="latest", image_id="abc", pulled_at=time.time() - 120)
        assert img.is_stale(60) is True
        assert img.is_stale(180) is False

    def test_default_pulled_at(self) -> None:
        img = CachedImage(name="alpine", tag="latest")
        assert img.pulled_at == 0.0

    def test_size_bytes_default(self) -> None:
        img = CachedImage(name="alpine", tag="latest")
        assert img.size_bytes == 0


class TestImageCache:
    def test_put_and_get(self) -> None:
        cache = ImageCache()
        cache.put("abc123", "alpine", "latest", size_bytes=5_000_000)
        img = cache.get("alpine", "latest")
        assert img is not None
        assert img.image_id == "abc123"
        assert img.size_bytes == 5_000_000

    def test_get_missing(self) -> None:
        cache = ImageCache()
        assert cache.get("nonexistent", "latest") is None

    def test_remove(self) -> None:
        cache = ImageCache()
        cache.put("abc", "alpine", "latest")
        assert cache.remove("alpine", "latest") is True
        assert cache.get("alpine", "latest") is None

    def test_remove_missing(self) -> None:
        cache = ImageCache()
        assert cache.remove("missing", "tag") is False

    def test_image_count(self) -> None:
        cache = ImageCache()
        assert cache.image_count() == 0
        cache.put("a", "img1")
        cache.put("b", "img2", "v1")
        assert cache.image_count() == 2

    def test_total_size_bytes(self) -> None:
        cache = ImageCache()
        cache.put("a", "img1", size_bytes=1000)
        cache.put("b", "img2", size_bytes=2000)
        assert cache.total_size_bytes() == 3000

    def test_list_images(self) -> None:
        cache = ImageCache()
        t = time.time()
        cache.put("a", "img1", pulled_at=t)
        cache.put("b", "img2", pulled_at=t - 100)
        images = cache.list_images()
        assert len(images) == 2
        assert images[0].name == "img1"

    def test_cleanup_stale(self) -> None:
        cache = ImageCache()
        cache.put("a", "img1", pulled_at=time.time() - 7200)
        cache.put("b", "img2", pulled_at=time.time())
        count = cache.cleanup_stale(max_age_seconds=3600)
        assert count == 1
        assert cache.get("img1") is None
        assert cache.get("img2") is not None

    def test_cleanup_stale_none_stale(self) -> None:
        cache = ImageCache()
        cache.put("a", "img1", pulled_at=time.time())
        count = cache.cleanup_stale(max_age_seconds=3600)
        assert count == 0

    def test_prune_all(self) -> None:
        cache = ImageCache()
        cache.put("a", "img1")
        cache.put("b", "img2")
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            count = cache.prune_all()
            assert count == 2
            assert cache.image_count() == 0

    def test_ensure_present_already_cached(self) -> None:
        cache = ImageCache()
        cache.put("abc", "alpine", "latest")
        with patch.object(subprocess, "run") as mock_run:
            result = cache.ensure_present("alpine", "latest")
            assert result is True
            mock_run.assert_not_called()

    def test_ensure_present_pulls(self) -> None:
        cache = ImageCache()
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=["docker", "pull", "alpine:latest"], returncode=0, stdout="image_id\n", stderr="")
            result = cache.ensure_present("alpine", "latest")
            assert result is True
            assert cache.get("alpine", "latest") is not None
            mock_run.assert_called_once()

    def test_ensure_present_pull_fails(self) -> None:
        cache = ImageCache()
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=["docker", "pull", "missing:latest"], returncode=1, stdout="", stderr="not found")
            result = cache.ensure_present("missing", "latest")
            assert result is False
            assert cache.get("missing", "latest") is None
