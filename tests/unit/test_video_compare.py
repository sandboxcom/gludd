"""Unit tests for video_compare module — cloud/video_compare.py."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import general_ludd.cloud.video_compare as video_compare
from general_ludd.cloud.video_compare import (
    _HAS_CV2,
    REFERENCE_VIDEO_SPECS,
    REFERENCE_VIDEOS,
    compare_gameplay,
    compare_gameplay_to_reference,
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

    def test_fps_reference_manifest_is_bounded_and_non_redistributable(self) -> None:
        for game_name in ("doom_e1m1_hallway", "quake_dm6_arena"):
            source = REFERENCE_VIDEO_SPECS[game_name]
            assert REFERENCE_VIDEOS[game_name] == source.source_url
            assert source.source_url.endswith(source.video_id)
            assert 0.0 < source.clip_duration_seconds <= 15.0
            assert 0 < source.sample_frame_count <= 30
            assert source.redistribution_allowed is False
            assert source.video_id in source.cache_filename

    def test_offline_cache_miss_never_downloads(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def fail_download(*args: object, **kwargs: object) -> str:
            raise AssertionError("offline comparison attempted a network download")

        monkeypatch.setattr(video_compare, "download_youtube_video", fail_download)
        generated = [_make_frame(64, 64, i * 10) for i in range(6)]

        result = compare_gameplay_to_reference(
            "doom_e1m1_hallway",
            generated,
            cache_dir=tmp_path,
        )

        assert result.game_name == "doom_e1m1_hallway"
        assert result.source_video_id == "YUU7d93IUBE"
        assert result.source_url == REFERENCE_VIDEOS[result.game_name]
        assert result.cache_status == "missing"
        assert result.network_used is False
        assert result.generated_frame_count == 6
        assert result.reference_frame_count == 0
        assert result.compared_frame_count == 0
        assert result.threshold == 0.35
        assert result.passed is False

    def test_cached_clip_produces_auditable_comparison(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        source = REFERENCE_VIDEO_SPECS["doom_e1m1_hallway"]
        cache_path = tmp_path / source.cache_filename
        cache_path.write_bytes(b"locally cached test clip")
        generated = [_make_frame(64, 64, i * 10) for i in range(6)]
        sampled: dict[str, object] = {}

        def fake_extract(video_path: str | Path, num_frames: int, interval: float) -> list[np.ndarray]:
            sampled.update(path=Path(video_path), count=num_frames, interval=interval)
            return [frame.copy() for frame in generated]

        monkeypatch.setattr(video_compare, "extract_frames", fake_extract)
        monkeypatch.setattr(video_compare, "resize_frames", lambda frames, target_size=(800, 600): frames)

        result = compare_gameplay_to_reference(
            "doom_e1m1_hallway",
            generated,
            cache_dir=tmp_path,
        )

        assert sampled == {
            "path": cache_path,
            "count": source.sample_frame_count,
            "interval": source.sample_interval_seconds,
        }
        assert result.cache_status == "cached"
        assert result.network_used is False
        assert result.reference_frame_count == 6
        assert result.generated_frame_count == 6
        assert result.compared_frame_count == 6
        assert result.mean_ssim == 1.0
        assert result.threshold == source.comparison_threshold
        assert result.passed is True

    def test_network_opt_in_requests_only_manifest_clip(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        source = REFERENCE_VIDEO_SPECS["quake_dm6_arena"]
        requested: dict[str, object] = {}

        def fake_download(
            url: str,
            output_path: str | Path,
            *,
            clip_start_seconds: float,
            clip_duration_seconds: float,
        ) -> str:
            requested.update(
                url=url,
                path=Path(output_path),
                start=clip_start_seconds,
                duration=clip_duration_seconds,
            )
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"bounded test clip")
            return str(output_path)

        generated = [_make_frame(64, 64, i * 10) for i in range(6)]
        monkeypatch.setattr(video_compare, "download_youtube_video", fake_download)
        monkeypatch.setattr(video_compare, "extract_frames", lambda *args, **kwargs: generated)
        monkeypatch.setattr(video_compare, "resize_frames", lambda frames, target_size=(800, 600): frames)

        result = compare_gameplay_to_reference(
            "quake_dm6_arena",
            generated,
            cache_dir=tmp_path,
            allow_network=True,
        )

        assert requested == {
            "url": source.source_url,
            "path": tmp_path / source.cache_filename,
            "start": source.clip_start_seconds,
            "duration": source.clip_duration_seconds,
        }
        assert result.cache_status == "downloaded"
        assert result.network_used is True
        assert result.source_video_id == source.video_id
        assert result.passed is True
