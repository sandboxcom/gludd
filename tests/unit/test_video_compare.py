"""Unit tests for video_compare module — cloud/video_compare.py."""

from __future__ import annotations

import numpy as np
import pytest

from general_ludd.cloud.video_compare import (
    _HAS_CV2,
    REFERENCE_VIDEOS,
    compare_gameplay,
    compute_motion_signature,
    compute_ssim,
    motion_correlation,
    resize_frames,
)


def _make_frame(h: int, w: int, color: int = 128) -> np.ndarray:
    return np.full((h, w, 3), min(max(color, 0), 255), dtype=np.uint8)


class TestSSIM:
    def test_ssim_identical_frames(self) -> None:
        frame = _make_frame(64, 64, 128)
        result = compute_ssim(frame, frame)
        assert 0.95 <= result <= 1.0, f"SSIM={result} for identical frames"

    def test_ssim_different_frames(self) -> None:
        frame_a = _make_frame(64, 64, 0)
        frame_b = _make_frame(64, 64, 255)
        result = compute_ssim(frame_a, frame_b)
        assert result < 0.5, f"SSIM={result} for very different frames"

    def test_ssim_shape_mismatch(self) -> None:
        frame_a = _make_frame(64, 64, 0)
        frame_b = _make_frame(32, 32, 0)
        result = compute_ssim(frame_a, frame_b)
        assert result == 0.0

    def test_ssim_near_identical(self) -> None:
        frame_a = _make_frame(64, 64, 128)
        frame_b = frame_a.copy()
        frame_b[10:15, 10:15] = 125
        result = compute_ssim(frame_a, frame_b)
        assert result > 0.9, f"SSIM={result} for near-identical frames"


class TestResizeFrames:
    @pytest.mark.skipif(not _HAS_CV2, reason="opencv-python not installed")
    def test_resize_frames(self) -> None:
        frames = [_make_frame(100, 200, i * 10) for i in range(5)]
        resized = resize_frames(frames, target_size=(80, 60))
        assert len(resized) == 5
        for f in resized:
            assert f.shape == (60, 80, 3), f"Expected (60, 80, 3), got {f.shape}"

    @pytest.mark.skipif(not _HAS_CV2, reason="opencv-python not installed")
    def test_resize_empty_frames(self) -> None:
        result = resize_frames([], target_size=(80, 60))
        assert result == []


class TestMotionSignature:
    def test_compute_motion_signature(self) -> None:
        frames = [_make_frame(64, 64, i * 10) for i in range(30)]
        sig = compute_motion_signature(frames, window=5)
        assert len(sig) == 25
        for val in sig:
            assert val >= 0.0

    def test_motion_signature_insufficient_frames(self) -> None:
        frames = [_make_frame(64, 64, 0) for _ in range(3)]
        sig = compute_motion_signature(frames, window=5)
        assert sig == []

    def test_motion_signature_static_frames(self) -> None:
        frames = [_make_frame(64, 64, 128) for _ in range(10)]
        sig = compute_motion_signature(frames, window=5)
        assert len(sig) == 5
        for val in sig:
            assert val < 1.0

    def test_motion_correlation_identical(self) -> None:
        sig = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        corr = motion_correlation(sig, sig)
        assert 0.99 <= corr <= 1.01, f"corr={corr}"

    def test_motion_correlation_empty(self) -> None:
        assert motion_correlation([], [1.0, 2.0, 3.0]) == 0.0
        assert motion_correlation([1.0], [1.0, 2.0]) == 0.0


class TestCompareGameplay:
    @pytest.mark.skipif(not _HAS_CV2, reason="opencv-python not installed")
    def test_compare_gameplay_passes(self) -> None:
        gen = [_make_frame(64, 64, 128) for _ in range(10)]
        ref = [_make_frame(64, 64, 128) for _ in range(10)]
        result = compare_gameplay(gen, ref, threshold=0.5)
        assert result["pass_threshold"] is True
        assert result["pass"] is True
        assert result["frame_count"] == 10
        assert isinstance(result["per_frame_ssim"], list)

    @pytest.mark.skipif(not _HAS_CV2, reason="opencv-python not installed")
    def test_compare_gameplay_fails_below_threshold(self) -> None:
        gen = [_make_frame(64, 64, 0) for _ in range(10)]
        ref = [_make_frame(64, 64, 255) for _ in range(10)]
        result = compare_gameplay(gen, ref, threshold=0.5)
        assert result["pass_threshold"] is False
        assert result["pass"] is False

    def test_compare_gameplay_empty(self) -> None:
        result = compare_gameplay([], [], threshold=0.5)
        assert result["frame_count"] == 0
        assert result["mean_ssim"] == 0.0
        assert result["pass_threshold"] is False
        assert result["pass"] is False

    @pytest.mark.skipif(not _HAS_CV2, reason="opencv-python not installed")
    def test_compare_gameplay_result_structure(self) -> None:
        gen = [_make_frame(64, 64, 100) for _ in range(5)]
        ref = [_make_frame(64, 64, 105) for _ in range(5)]
        result = compare_gameplay(gen, ref, threshold=0.9)
        assert "mean_ssim" in result
        assert "per_frame_ssim" in result
        assert "motion_correlation" in result
        assert "pass_threshold" in result
        assert "frame_count" in result
        assert "pass" in result
        assert isinstance(result["mean_ssim"], float)
        assert isinstance(result["motion_correlation"], float)


class TestReferenceVideos:
    def test_reference_videos_dict_complete(self) -> None:
        assert len(REFERENCE_VIDEOS) >= 5
        for name in ["doom_e1m1_hallway", "quake_dm6_arena", "wipeout_racing", "descent_tunnel", "rogue_dungeon"]:
            assert name in REFERENCE_VIDEOS, f"Missing reference video: {name}"

    def test_download_url_format(self) -> None:
        for name, url in REFERENCE_VIDEOS.items():
            assert url.startswith("https://www.youtube.com/watch?v="), f"Invalid URL format for {name}: {url}"
            assert len(url) > 30, f"URL too short for {name}: {url}"
