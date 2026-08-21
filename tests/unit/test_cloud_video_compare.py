"""Unit tests for video_compare — frame comparison, reference specs, hashing."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from general_ludd.cloud.video_compare import (
    REFERENCE_VIDEO_SPECS,
    REFERENCE_VIDEOS,
    ReferenceCacheValidation,
    ReferenceComparisonResult,
    ReferenceVideoSpec,
    _average_pool_for_ssim,
    _ensure_cv2,
    _ensure_yt_dlp,
    _global_ssim,
    _read_provenance,
    _reference_spec,
    _sha256_file,
    _sha256_frames,
    _yt_dlp_version,
    compare_gameplay,
    compute_motion_signature,
    compute_ssim,
    motion_correlation,
)


def _make_frame(shape=(80, 60, 3), value=100):
    return np.full(shape, value, dtype=np.uint8)


class TestReferenceVideoSpec:
    def test_defaults(self):
        spec = ReferenceVideoSpec(
            game_name="test_game",
            source_url="https://youtube.com?v=abc",
            video_id="abc",
        )
        assert spec.game_name == "test_game"
        assert spec.clip_start_seconds == 0.0
        assert spec.clip_duration_seconds == 12.0
        assert spec.sample_frame_count == 30
        assert spec.sample_interval_seconds == 0.4
        assert spec.comparison_threshold == 0.35
        assert spec.redistribution_allowed is False
        assert spec.approval_version == 1

    def test_cache_filename(self):
        spec = ReferenceVideoSpec(game_name="doom", source_url="https://youtube.com?v=xyz", video_id="xyz")
        assert spec.cache_filename == "doom-xyz.mp4"

    def test_provenance_filename(self):
        spec = ReferenceVideoSpec(game_name="quake", source_url="https://youtube.com?v=123", video_id="123")
        assert spec.provenance_filename == "quake-123.mp4.provenance.json"

    def test_custom_values(self):
        spec = ReferenceVideoSpec(
            game_name="custom",
            source_url="https://youtube.com?v=c",
            video_id="c",
            clip_start_seconds=2.0,
            clip_duration_seconds=5.0,
            sample_frame_count=10,
            sample_interval_seconds=1.0,
            comparison_threshold=0.5,
            redistribution_allowed=True,
            approval_version=3,
        )
        assert spec.clip_start_seconds == 2.0
        assert spec.sample_frame_count == 10
        assert spec.comparison_threshold == 0.5
        assert spec.redistribution_allowed is True

    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        spec = ReferenceVideoSpec(game_name="frozen", source_url="https://x.com", video_id="x")
        try:
            spec.game_name = "different"
        except FrozenInstanceError:
            return
        raise AssertionError("Expected FrozenInstanceError on frozen dataclass")


class TestReferenceComparisonResult:
    def test_default_failure_result(self):
        result = ReferenceComparisonResult(
            game_name="g",
            source_url="u",
            source_video_id="v",
            cache_path="/tmp/x",
            cache_status="missing",
            network_used=False,
            generated_frame_count=10,
            reference_frame_count=0,
            compared_frame_count=0,
            threshold=0.35,
            mean_ssim=0.0,
            motion_correlation=0.0,
            per_frame_ssim=(),
            passed=False,
        )
        assert result.passed is False
        assert result.mean_ssim == 0.0
        assert result.cache_status == "missing"

    def test_pass_result(self):
        result = ReferenceComparisonResult(
            game_name="g",
            source_url="u",
            source_video_id="v",
            cache_path="/tmp/x",
            cache_status="cached",
            network_used=False,
            generated_frame_count=30,
            reference_frame_count=30,
            compared_frame_count=30,
            threshold=0.35,
            mean_ssim=0.85,
            motion_correlation=0.72,
            per_frame_ssim=(0.8, 0.9),
            passed=True,
        )
        assert result.passed is True
        assert result.mean_ssim == 0.85
        assert len(result.per_frame_ssim) == 2


class TestReferenceSpecs:
    def test_reference_video_specs_known_games(self):
        assert "doom_e1m1_hallway" in REFERENCE_VIDEO_SPECS
        assert "quake_dm6_arena" in REFERENCE_VIDEO_SPECS
        for _name, spec in REFERENCE_VIDEO_SPECS.items():
            assert spec.video_id
            assert spec.source_url.startswith("https://www.youtube.com/watch?v=")

    def test_reference_spec_helper(self):
        spec = _reference_spec("test_game", "abc123")
        assert spec.game_name == "test_game"
        assert spec.video_id == "abc123"
        assert spec.source_url == "https://www.youtube.com/watch?v=abc123"

    def test_reference_videos_url_map(self):
        assert REFERENCE_VIDEOS["doom_e1m1_hallway"] == REFERENCE_VIDEO_SPECS["doom_e1m1_hallway"].source_url


class TestHashing:
    def test_sha256_file(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"hello world")
        digest = _sha256_file(f)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_sha256_file_empty(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        digest = _sha256_file(f)
        assert len(digest) == 64

    def test_sha256_frames_deterministic(self):
        frame = _make_frame((32, 32, 3))
        digest1 = _sha256_frames([frame])
        digest2 = _sha256_frames([frame])
        assert digest1 == digest2

    def test_sha256_frames_different_content(self):
        a = _make_frame((32, 32, 3), value=50)
        b = _make_frame((32, 32, 3), value=200)
        assert _sha256_frames([a]) != _sha256_frames([b])

    def test_sha256_frames_multi_frame(self):
        frames = [_make_frame((16, 16, 3), value=i) for i in range(3)]
        digest = _sha256_frames(frames)
        assert len(digest) == 64


class TestSSIM:
    def test_identical_frames(self):
        frame = _make_frame((80, 60, 3), value=128)
        assert compute_ssim(frame, frame) == 1.0

    def test_shape_mismatch(self):
        a = _make_frame((80, 60, 3))
        b = _make_frame((60, 80, 3))
        assert compute_ssim(a, b) == 0.0

    def test_dissimilar_frames(self):
        a = _make_frame((80, 60, 3), value=0)
        b = _make_frame((80, 60, 3), value=255)
        s = compute_ssim(a, b)
        assert 0.0 <= s <= 1.0
        assert s < 0.1

    def test_similar_frames(self):
        a = _make_frame((80, 60, 3), value=128)
        b = _make_frame((80, 60, 3), value=130)
        s = compute_ssim(a, b)
        assert s > 0.8

    def test_ssim_returns_float(self):
        a = _make_frame((32, 32, 3))
        b = _make_frame((32, 32, 3), value=200)
        s = compute_ssim(a, b)
        assert isinstance(s, float)
        assert 0.0 <= s <= 1.0

    def test_grayscale_frames(self):
        a = np.full((40, 30), 100, dtype=np.uint8)
        b = np.full((40, 30), 100, dtype=np.uint8)
        s = compute_ssim(a, b)
        assert s == 1.0


class TestAveragePoolForSSIM:
    def test_factor_1_identity(self):
        frame = np.random.rand(40, 60, 3).astype(np.float64)
        result = _average_pool_for_ssim(frame, 1)
        assert np.allclose(result, frame)

    def test_factor_2_reduces_spatial(self):
        frame = np.random.rand(40, 60, 3).astype(np.float64)
        result = _average_pool_for_ssim(frame, 2)
        assert result.shape == (20, 30, 3)

    def test_factor_3(self):
        frame = np.random.rand(30, 45, 3).astype(np.float64)
        result = _average_pool_for_ssim(frame, 3)
        assert result.shape == (10, 15, 3)

    def test_factor_0_identity(self):
        frame = np.random.rand(24, 24, 3).astype(np.float64)
        result = _average_pool_for_ssim(frame, 0)
        assert np.allclose(result, frame)


class TestGlobalSSIM:
    def test_identical_frames_close_to_one(self):
        frame = np.random.rand(64, 48, 3).astype(np.float64) * 255
        s = _global_ssim(frame, frame)
        assert 0.99 <= s <= 1.0

    def test_opposite_frames(self):
        a = np.zeros((40, 30, 3), dtype=np.float64)
        b = np.full((40, 30, 3), 255.0, dtype=np.float64)
        s = _global_ssim(a, b)
        assert isinstance(s, float)
        assert 0.0 <= s <= 1.0

    def test_returns_float(self):
        a = np.random.rand(32, 32, 3).astype(np.float64) * 255
        b = np.random.rand(32, 32, 3).astype(np.float64) * 255
        s = _global_ssim(a, b)
        assert isinstance(s, float)
        assert 0.0 <= s <= 1.0


class TestMotionSignature:
    def test_empty_with_few_frames(self):
        frames = [_make_frame((32, 32, 3)) for _ in range(3)]
        sig = compute_motion_signature(frames, window=5)
        assert sig == []

    def test_returns_list_of_floats(self):
        frames = [_make_frame((32, 32, 3), value=i * 10) for i in range(10)]
        sig = compute_motion_signature(frames, window=5)
        assert len(sig) == 5
        assert all(isinstance(v, float) for v in sig)

    def test_constant_frames_zero_motion(self):
        frame = _make_frame((32, 32, 3), value=100)
        frames = [frame.copy() for _ in range(10)]
        sig = compute_motion_signature(frames, window=5)
        assert all(v == 0.0 for v in sig)


class TestMotionCorrelation:
    def test_identical_signals(self):
        sig = [1.0, 2.0, 3.0, 4.0, 5.0]
        corr = motion_correlation(sig, sig)
        assert 0.99 <= corr <= 1.0

    def test_short_signals_return_zero(self):
        assert motion_correlation([1.0], [1.0]) == 0.0

    def test_anti_correlated(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [5.0, 4.0, 3.0, 2.0, 1.0]
        corr = motion_correlation(a, b)
        assert -1.0 <= corr <= -0.99

    def test_constant_signal(self):
        sig_a = [3.0, 3.0, 3.0, 3.0]
        sig_b = [1.0, 2.0, 3.0, 4.0]
        assert motion_correlation(sig_a, sig_b) == 0.0

    def test_different_lengths(self):
        a = [0.1, 0.2, 0.3, 0.4, 0.5]
        b = [0.1, 0.2, 0.3]
        corr = motion_correlation(a, b)
        assert isinstance(corr, float)


class TestCompareGameplay:
    def test_empty_frames(self):
        result = compare_gameplay([], [])
        assert result["mean_ssim"] == 0.0
        assert result["per_frame_ssim"] == []
        assert result["pass"] is False
        assert result["pass_threshold"] is False

    def test_single_empty_list(self):
        with patch("general_ludd.cloud.video_compare.cv2", create=True):
            frame = _make_frame((40, 30, 3))
            result = compare_gameplay([frame], [])
            assert result["pass"] is False

    def test_identical_frames_pass(self):
        mock_cv2 = MagicMock()
        mock_cv2.resize = lambda frame, size: frame
        with patch("general_ludd.cloud.video_compare.cv2", mock_cv2):
            frame = _make_frame((64, 48, 3), value=128)
            gen = [frame.copy() for _ in range(10)]
            ref = [frame.copy() for _ in range(10)]
            result = compare_gameplay(gen, ref, threshold=0.80)
            assert result["pass"] is True
            assert result["mean_ssim"] >= 0.99
            assert result["frame_count"] == 10
            assert len(result["per_frame_ssim"]) == 10

    def test_dissimilar_frames_fail(self):
        mock_cv2 = MagicMock()
        mock_cv2.resize = lambda frame, size: frame
        with patch("general_ludd.cloud.video_compare.cv2", mock_cv2):
            gen = [_make_frame((64, 48, 3), value=0) for _ in range(5)]
            ref = [_make_frame((64, 48, 3), value=255) for _ in range(5)]
            result = compare_gameplay(gen, ref, threshold=0.35)
            assert result["pass"] is False
            assert "motion_correlation" in result

    def test_mismatched_frame_counts(self):
        mock_cv2 = MagicMock()
        mock_cv2.resize = lambda frame, size: frame
        with patch("general_ludd.cloud.video_compare.cv2", mock_cv2):
            gen = [_make_frame((48, 36, 3)) for _ in range(5)]
            ref = [_make_frame((48, 36, 3)) for _ in range(12)]
            result = compare_gameplay(gen, ref, threshold=0.80)
            assert result["frame_count"] == 5


class TestProvenance:
    def test_read_valid_provenance(self, tmp_path):
        payload = {
            "schema_version": 1,
            "game_name": "test",
            "key": "value",
        }
        p = tmp_path / "prov.json"
        p.write_text(json.dumps(payload))
        result = _read_provenance(p)
        assert result == payload

    def test_read_not_dict_raises(self, tmp_path):
        p = tmp_path / "prov.json"
        p.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(RuntimeError, match="JSON object"):
            _read_provenance(p)

    def test_read_invalid_json_raises(self, tmp_path):
        p = tmp_path / "prov.json"
        p.write_text("not json")
        with pytest.raises(RuntimeError, match="invalid provenance"):
            _read_provenance(p)

    def test_read_missing_file_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="invalid provenance"):
            _read_provenance(tmp_path / "missing.json")


class TestEnsureModules:
    def test_ensure_yt_dlp_missing(self):
        with patch("general_ludd.cloud.video_compare.yt_dlp", None), pytest.raises(ImportError, match="yt-dlp"):
            _ensure_yt_dlp()

    def test_ensure_cv2_missing(self):
        with patch("general_ludd.cloud.video_compare.cv2", None), pytest.raises(ImportError, match="opencv"):
            _ensure_cv2()


class TestYtDlpVersion:
    def test_version_unknown(self):
        with patch("general_ludd.cloud.video_compare.yt_dlp", MagicMock()):
            result = _yt_dlp_version()
            assert result == "unknown"


class TestReferenceCacheValidation:
    def test_validation_attrs(self):
        v = ReferenceCacheValidation(
            game_name="g",
            cache_path=Path("/tmp/x.mp4"),
            provenance_path=Path("/tmp/x.mp4.provenance.json"),
            cache_status="verified",
            object_sha256="abc123",
            decoded_frames_sha256="def456",
            reference_frame_count=30,
        )
        assert v.cache_status == "verified"
        assert v.reference_frame_count == 30
        assert v.object_sha256 == "abc123"


class TestCompareGameplayToReference:
    def test_unknown_game_raises(self):
        with pytest.raises(ValueError, match="No approved reference video"):
            from general_ludd.cloud.video_compare import compare_gameplay_to_reference

            compare_gameplay_to_reference("nonexistent_game", [], "/tmp")

    def test_missing_cache_no_network(self):
        with patch("general_ludd.cloud.video_compare.Path.is_file", return_value=False):
            from general_ludd.cloud.video_compare import compare_gameplay_to_reference

            result = compare_gameplay_to_reference(
                "doom_e1m1_hallway",
                [_make_frame((64, 48, 3))],
                "/tmp/nonexistent",
                allow_network=False,
            )
            assert result.cache_status == "missing"
            assert result.passed is False
            assert result.reference_frame_count == 0


# ── Deep numerical edge cases ──────────────────────────────────────────────


class TestSSIMDeepEdgeCases:
    def test_identical_random_frames(self):
        rng = np.random.RandomState(42)
        a = rng.randint(0, 256, (64, 48, 3), dtype=np.uint8)
        b = a.copy()
        assert compute_ssim(a, b) == 1.0

    def test_single_pixel_frame(self):
        a = np.array([[[128, 128, 128]]], dtype=np.uint8)
        b = np.array([[[128, 128, 128]]], dtype=np.uint8)
        s = compute_ssim(a, b)
        assert s == 1.0

    def test_single_pixel_different(self):
        a = np.array([[[0, 0, 0]]], dtype=np.uint8)
        b = np.array([[[255, 255, 255]]], dtype=np.uint8)
        s = compute_ssim(a, b)
        assert 0.0 <= s < 0.1

    def test_4d_tensor_identical(self):
        a = np.full((2, 2, 2, 3), 128, dtype=np.uint8)
        b = np.full((2, 2, 2, 3), 128, dtype=np.uint8)
        s = compute_ssim(a, b)
        assert 0.0 <= s <= 1.0

    def test_non_contiguous_array(self):
        a = np.full((80, 60, 3), 128, dtype=np.uint8)[::2, ::2, :]
        b = np.full((40, 30, 3), 128, dtype=np.uint8)
        s = compute_ssim(a, b)
        assert 0.0 <= s <= 1.0

    def test_large_frame_dissimilar(self):
        a = np.full((256, 256, 3), 0, dtype=np.uint8)
        b = np.full((256, 256, 3), 255, dtype=np.uint8)
        s = compute_ssim(a, b)
        assert s < 0.05

    def test_zero_value_frames(self):
        a = np.zeros((32, 32, 3), dtype=np.uint8)
        b = np.zeros((32, 32, 3), dtype=np.uint8)
        assert compute_ssim(a, b) == 1.0

    def test_max_value_frames(self):
        a = np.full((32, 32, 3), 255, dtype=np.uint8)
        b = np.full((32, 32, 3), 255, dtype=np.uint8)
        assert compute_ssim(a, b) == 1.0

    def test_different_dtype(self):
        a = np.full((40, 30, 3), 128, dtype=np.uint8)
        b = np.full((40, 30, 3), 128, dtype=np.int32)
        s = compute_ssim(a, b)
        assert 0.0 <= s <= 1.0

    def test_float_input(self):
        a = np.full((32, 24, 3), 0.5, dtype=np.float32)
        b = np.full((32, 24, 3), 0.5, dtype=np.float32)
        s = compute_ssim(a, b)
        assert 0.0 <= s <= 1.0

    def test_monotonic_similarity(self):
        base = np.full((40, 30, 3), 128, dtype=np.uint8)
        s1 = compute_ssim(base, np.full((40, 30, 3), 128, dtype=np.uint8))
        s2 = compute_ssim(base, np.full((40, 30, 3), 130, dtype=np.uint8))
        s3 = compute_ssim(base, np.full((40, 30, 3), 140, dtype=np.uint8))
        s4 = compute_ssim(base, np.full((40, 30, 3), 200, dtype=np.uint8))
        assert s1 >= s2 >= s3 >= s4


class TestMotionCorrelationDeep:
    def test_exact_linear_increasing(self):
        a = [float(i) for i in range(20)]
        b = [float(i) for i in range(20)]
        corr = motion_correlation(a, b)
        assert 0.99 <= corr <= 1.0

    def test_exact_linear_decreasing_vs_increasing(self):
        a = [float(i) for i in range(20)]
        b = [float(20 - i) for i in range(20)]
        corr = motion_correlation(a, b)
        assert -1.0 <= corr <= -0.99

    def test_large_value_range(self):
        a = [0.0, 1000.0, 2000.0, 3000.0, 4000.0]
        b = [0.0, 1000.0, 2000.0, 3000.0, 4000.0]
        corr = motion_correlation(a, b)
        assert corr >= 0.999

    def test_small_floating_point_variation(self):
        a = [0.0001, 0.0002, 0.0003, 0.0004, 0.0005]
        b = [0.0001, 0.0002, 0.0003, 0.0004, 0.0005]
        corr = motion_correlation(a, b)
        assert 0.99 <= corr <= 1.0

    def test_negative_values(self):
        a = [-5.0, -3.0, -1.0, 1.0, 3.0, 5.0]
        b = [-5.0, -3.0, -1.0, 1.0, 3.0, 5.0]
        corr = motion_correlation(a, b)
        assert 0.99 <= corr <= 1.0

    def test_identical_value_then_spike(self):
        a = [1.0, 1.0, 1.0, 1.0, 100.0]
        b = [1.0, 1.0, 1.0, 1.0, 100.0]
        corr = motion_correlation(a, b)
        assert 0.99 <= corr <= 1.0

    def test_near_constant_with_noise(self):
        rng = np.random.RandomState(99)
        a = [3.0 + rng.uniform(-0.001, 0.001) for _ in range(10)]
        b = [3.0 + rng.uniform(-0.001, 0.001) for _ in range(10)]
        corr = motion_correlation(a, b)
        assert isinstance(corr, float)
        assert -1.0 <= corr <= 1.0

    def test_different_lengths_long_first(self):
        a = [float(i) for i in range(100)]
        b = [float(i) for i in range(5)]
        corr = motion_correlation(a, b)
        assert isinstance(corr, float)

    def test_single_element_list(self):
        assert motion_correlation([0.5], [0.5]) == 0.0

    def test_nan_in_signal(self):
        a = [1.0, 2.0, float("nan"), 4.0, 5.0]
        b = [1.0, 2.0, 3.0, 4.0, 5.0]
        corr = motion_correlation(a, b)
        assert corr == 0.0

    def test_infinite_value(self):
        a = [1.0, 2.0, float("inf")]
        b = [1.0, 2.0, 3.0]
        corr = motion_correlation(a, b)
        assert corr == 0.0


class TestReferenceResourceBoundaries:
    def test_acquisition_and_validation_close_the_atomic_cache_contract(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import general_ludd.cloud.video_compare as video_compare

        spec = ReferenceVideoSpec(
            game_name="fixture",
            source_url="https://example.invalid/video",
            video_id="fixture-id",
            sample_frame_count=2,
        )
        frames = [_make_frame(value=10), _make_frame(value=20)]
        cache_path = tmp_path / spec.cache_filename
        provenance_path = tmp_path / spec.provenance_filename
        events: list[tuple[str, dict[str, object]]] = []

        def download(
            _url: str,
            output_path: str | Path,
            **kwargs: object,
        ) -> str:
            path = Path(output_path)
            path.write_bytes(b"bounded fixture")
            metadata_sink = kwargs["metadata_sink"]
            progress_sink = kwargs["progress_sink"]
            assert callable(metadata_sink)
            assert callable(progress_sink)
            metadata_sink({"format_id": "fixture"})
            progress_sink({"status": "downloading"})
            return str(path)

        monkeypatch.setattr(video_compare, "download_youtube_video", download)
        monkeypatch.setattr(video_compare, "extract_frames", lambda *args, **kwargs: frames)
        monkeypatch.setattr(video_compare, "_ffmpeg_version", lambda: "ffmpeg fixture")
        monkeypatch.setattr(video_compare, "_video_duration_seconds", lambda _path: 12.0)
        monkeypatch.setattr(video_compare, "_yt_dlp_version", lambda: "yt-dlp fixture")

        acquired = video_compare._acquire_reference(
            spec,
            cache_path,
            provenance_path,
            lambda name, payload: events.append((name, dict(payload))),
        )
        verified = video_compare._validate_cached_reference(
            spec,
            cache_path,
            provenance_path,
        )

        assert acquired.cache_status == "downloaded"
        assert verified.cache_status == "verified"
        assert verified.object_sha256 == acquired.object_sha256
        assert events == [
            (
                "reference_acquisition_progress",
                {"game_name": "fixture", "status": "downloading"},
            )
        ]
        assert not list(tmp_path.glob("*.tmp"))

        payload = json.loads(provenance_path.read_text(encoding="utf-8"))

        def assert_invalid(overrides: dict[str, object], message: str) -> None:
            provenance_path.write_text(
                json.dumps({**payload, **overrides}),
                encoding="utf-8",
            )
            with pytest.raises(RuntimeError, match=message):
                video_compare._validate_cached_reference(
                    spec,
                    cache_path,
                    provenance_path,
                )

        assert_invalid({"clip_actual_duration_seconds": True}, "duration")
        assert_invalid({"object_size_bytes": -1}, "object size")
        assert_invalid({"object_sha256": "wrong"}, "object digest")
        assert_invalid({"decoded_frames_sha256": "wrong"}, "frame digest")
        assert_invalid({"decoded_frame_count": -1}, "frame count")

        provenance_path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setitem(video_compare.REFERENCE_VIDEO_SPECS, spec.game_name, spec)
        ready_events: list[str] = []
        cached = video_compare.preflight_reference_videos(
            (spec.game_name,),
            tmp_path,
            event_reporter=lambda name, _payload: ready_events.append(name),
        )
        assert cached[spec.game_name].cache_status == "verified"
        assert ready_events == ["reference_check_started", "reference_ready"]

        with pytest.raises(RuntimeError, match="no approved reference manifest"):
            video_compare.preflight_reference_videos(
                ("unknown",),
                tmp_path,
                event_reporter=lambda name, _payload: ready_events.append(name),
            )

    def test_media_version_probes_are_bounded_and_parsed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import general_ludd.cloud.video_compare as video_compare

        completed = MagicMock(returncode=0, stdout="tool 1.0\nmore\n")
        monkeypatch.setattr(video_compare.shutil, "which", lambda _name: "/usr/bin/tool")
        monkeypatch.setattr(video_compare.subprocess, "run", lambda *args, **kwargs: completed)

        assert video_compare._ffmpeg_version() == "tool 1.0"
        completed.stdout = "12.5\n"
        assert video_compare._video_duration_seconds(tmp_path / "clip.mp4") == 12.5

    def test_media_probe_and_decode_failures_are_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import general_ludd.cloud.video_compare as video_compare

        monkeypatch.setattr(video_compare.shutil, "which", lambda _name: None)
        with pytest.raises(RuntimeError, match="ffmpeg is required"):
            video_compare._ffmpeg_version()
        with pytest.raises(RuntimeError, match="ffprobe is required"):
            video_compare._video_duration_seconds(tmp_path / "clip.mp4")

        completed = MagicMock(returncode=1, stdout="")
        monkeypatch.setattr(video_compare.shutil, "which", lambda _name: "/usr/bin/tool")
        monkeypatch.setattr(video_compare.subprocess, "run", lambda *args, **kwargs: completed)
        with pytest.raises(RuntimeError, match="ffmpeg version probe"):
            video_compare._ffmpeg_version()
        completed.returncode = 0
        completed.stdout = "not-a-duration"
        with pytest.raises(RuntimeError, match="invalid reference duration"):
            video_compare._video_duration_seconds(tmp_path / "clip.mp4")

        spec = ReferenceVideoSpec("fixture", "https://example.invalid", "fixture")
        monkeypatch.setattr(video_compare, "extract_frames", lambda *args, **kwargs: [])
        with pytest.raises(RuntimeError, match="zero frames"):
            video_compare._decode_reference_frames(spec, tmp_path / "clip.mp4")
        frames = [_make_frame(shape=(8, 8, 3)), _make_frame(shape=(9, 8, 3))]
        monkeypatch.setattr(video_compare, "extract_frames", lambda *args, **kwargs: frames)
        with pytest.raises(RuntimeError, match="dimensions changed"):
            video_compare._decode_reference_frames(spec, tmp_path / "clip.mp4")

    def test_download_adapter_handles_missing_ranges_and_selected_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import general_ludd.cloud.video_compare as video_compare

        monkeypatch.setattr(
            video_compare,
            "yt_dlp",
            SimpleNamespace(utils=object(), YoutubeDL=MagicMock()),
        )
        with pytest.raises(RuntimeError, match="download_range_func"):
            video_compare.download_youtube_video(
                "https://example.invalid",
                tmp_path,
                clip_duration_seconds=1.0,
            )

        candidate = tmp_path / "fallback.webm"
        candidate.write_bytes(b"fixture")

        class FakeYoutubeDL:
            def __init__(self, _options: dict[str, object]) -> None:
                pass

            def __enter__(self) -> FakeYoutubeDL:
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def extract_info(self, _url: str, *, download: bool) -> dict[str, object]:
                assert download is True
                return {"requested_downloads": [object()]}

        monkeypatch.setattr(
            video_compare,
            "yt_dlp",
            SimpleNamespace(YoutubeDL=FakeYoutubeDL),
        )
        assert video_compare.download_youtube_video(
            "https://example.invalid",
            tmp_path,
        ) == str(candidate)

    def test_extract_frames_owns_capture_on_zero_fps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import general_ludd.cloud.video_compare as video_compare

        frame = _make_frame(shape=(8, 8, 3))
        capture = MagicMock()
        capture.isOpened.return_value = True
        capture.get.side_effect = [0.0, 1.0]
        capture.read.return_value = (True, frame)
        module = MagicMock(
            CAP_PROP_FPS=1,
            CAP_PROP_FRAME_COUNT=2,
            CAP_PROP_POS_FRAMES=3,
            COLOR_BGR2RGB=4,
        )
        module.VideoCapture.return_value = capture
        module.cvtColor.return_value = frame
        monkeypatch.setattr(video_compare, "cv2", module)

        assert video_compare.extract_frames(tmp_path / "clip.mp4", num_frames=1) == [frame]
        capture.release.assert_called_once_with()

    def test_ssim_non_finite_backend_result_falls_back_without_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import general_ludd.cloud.video_compare as video_compare

        monkeypatch.setattr(video_compare, "structural_similarity", lambda *args, **kwargs: float("nan"))
        frame_a = _make_frame(shape=(16, 16, 3), value=20)
        frame_b = _make_frame(shape=(16, 16, 3), value=40)

        result = video_compare.compute_ssim(frame_a, frame_b)

        assert np.isfinite(result)
        assert 0.0 <= result <= 1.0

        monkeypatch.setattr(video_compare, "structural_similarity", lambda *args, **kwargs: 0.9)
        high_error = video_compare.compute_ssim(
            _make_frame(shape=(16, 16, 3), value=0),
            _make_frame(shape=(16, 16, 3), value=255),
        )
        assert 0.0 <= high_error < 0.9


class TestGlobalSSIMDeep:
    def test_channel_wise_identical(self):
        a = np.full((32, 32, 3), 100.0, dtype=np.float64)
        b = np.full((32, 32, 3), 100.0, dtype=np.float64)
        s = _global_ssim(a, b)
        assert 0.99 <= s <= 1.0

    def test_one_channel_off(self):
        a = np.full((32, 32, 3), 100.0, dtype=np.float64)
        b = np.full((32, 32, 3), 100.0, dtype=np.float64)
        b[:, :, 0] = 200.0
        s = _global_ssim(a, b)
        assert 0.0 <= s < 1.0

    def test_all_channels_off(self):
        a = np.full((32, 32, 3), 100.0, dtype=np.float64)
        b = np.full((32, 32, 3), 200.0, dtype=np.float64)
        s = _global_ssim(a, b)
        assert 0.0 <= s < 1.0

    def test_grayscale_frame(self):
        a = np.full((40, 30), 128.0, dtype=np.float64)
        b = np.full((40, 30), 128.0, dtype=np.float64)
        s = _global_ssim(a, b)
        assert 0.99 <= s <= 1.0

    def test_boundary_values(self):
        a = np.zeros((16, 16, 3), dtype=np.float64)
        b = np.full((16, 16, 3), 255.0, dtype=np.float64)
        s = _global_ssim(a, b)
        assert 0.0 <= s <= 1.0

    def test_single_pixel(self):
        a = np.array([[[128.0, 128.0, 128.0]]], dtype=np.float64)
        b = np.array([[[128.0, 128.0, 128.0]]], dtype=np.float64)
        s = _global_ssim(a, b)
        assert 0.99 <= s <= 1.0


class TestAveragePoolDeep:
    def test_factor_4(self):
        frame = np.random.rand(64, 80, 3).astype(np.float64)
        result = _average_pool_for_ssim(frame, 4)
        assert result.shape == (16, 20, 3)

    def test_factor_larger_than_dims(self):
        frame = np.random.rand(20, 30, 3).astype(np.float64)
        result = _average_pool_for_ssim(frame, 50)
        assert result.shape[0] <= 20
        assert result.shape[1] <= 30

    def test_non_divisible_dimensions(self):
        frame = np.random.rand(31, 47, 3).astype(np.float64)
        result = _average_pool_for_ssim(frame, 3)
        assert result.shape[0] == 10
        assert result.shape[1] == 15

    def test_pooling_preserves_range(self):
        frame = np.full((32, 48, 3), 0.5, dtype=np.float64)
        result = _average_pool_for_ssim(frame, 2)
        assert np.allclose(result, 0.5)

    def test_negative_pool_factor(self):
        frame = np.random.rand(24, 36, 3).astype(np.float64)
        result = _average_pool_for_ssim(frame, -1)
        assert np.allclose(result, frame)


class TestDownloadYoutubeVideoDeep:
    def test_clip_start_seconds_negative_raises(self):
        from general_ludd.cloud.video_compare import download_youtube_video

        with patch("general_ludd.cloud.video_compare.yt_dlp"), pytest.raises(ValueError, match="non-negative"):
            download_youtube_video("https://youtube.com?v=x", "/tmp", clip_start_seconds=-1.0)

    def test_clip_duration_seconds_zero_raises(self):
        from general_ludd.cloud.video_compare import download_youtube_video

        with patch("general_ludd.cloud.video_compare.yt_dlp"), pytest.raises(ValueError, match="positive"):
            download_youtube_video(
                "https://youtube.com?v=x",
                "/tmp",
                clip_start_seconds=0.0,
                clip_duration_seconds=0.0,
            )

    def test_clip_duration_seconds_negative_raises(self):
        from general_ludd.cloud.video_compare import download_youtube_video

        with patch("general_ludd.cloud.video_compare.yt_dlp"), pytest.raises(ValueError, match="positive"):
            download_youtube_video(
                "https://youtube.com?v=x",
                "/tmp",
                clip_start_seconds=0.0,
                clip_duration_seconds=-5.0,
            )

    def test_valid_clip_params_ok(self):
        from general_ludd.cloud.video_compare import download_youtube_video

        mock_ytdlp = MagicMock()
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "requested_downloads": [{"format_id": "mp4", "ext": "mp4"}],
            "format_id": "mp4",
            "ext": "mp4",
            "vcodec": "h264",
            "duration": 60.0,
        }
        mock_ytdlp.YoutubeDL.return_value.__enter__.return_value = mock_ydl
        mock_ytdlp.utils.download_range_func = lambda a, b: "range_func_result"

        with (
            patch("general_ludd.cloud.video_compare.yt_dlp", mock_ytdlp),
            patch("pathlib.Path.mkdir"),
            patch("os.path.exists", return_value=True),
        ):
            result = download_youtube_video(
                "https://youtube.com?v=x",
                "/tmp/test.mp4",
                clip_start_seconds=0.0,
                clip_duration_seconds=10.0,
            )
            assert result.endswith(".mp4")

    def test_metadata_sink_receives_fields(self):
        from general_ludd.cloud.video_compare import download_youtube_video

        mock_ytdlp = MagicMock()
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "requested_downloads": [{"format_id": "mp4", "ext": "mp4", "vcodec": "h264", "fps": 30.0}],
            "duration": 120.0,
            "uploader": "test-channel",
            "channel": "test-channel",
        }
        mock_ytdlp.YoutubeDL.return_value.__enter__.return_value = mock_ydl
        metadata = {}

        with (
            patch("general_ludd.cloud.video_compare.yt_dlp", mock_ytdlp),
            patch("pathlib.Path.mkdir"),
            patch("os.path.exists", return_value=True),
        ):
            download_youtube_video(
                "https://youtube.com?v=x",
                "/tmp/test.mp4",
                metadata_sink=metadata.update,
            )
            assert "format_id" in metadata
            assert "container" in metadata

    def test_no_requested_downloads_falls_back_to_info(self):
        from general_ludd.cloud.video_compare import download_youtube_video

        mock_ytdlp = MagicMock()
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "format_id": "mp4",
            "ext": "mp4",
            "vcodec": "h264",
            "pix_fmt": "yuv420p",
            "duration": 30.0,
        }
        mock_ytdlp.YoutubeDL.return_value.__enter__.return_value = mock_ydl
        metadata = {}

        with (
            patch("general_ludd.cloud.video_compare.yt_dlp", mock_ytdlp),
            patch("pathlib.Path.mkdir"),
            patch("os.path.exists", return_value=True),
        ):
            download_youtube_video(
                "https://youtube.com?v=x",
                "/tmp/test.mp4",
                metadata_sink=metadata.update,
            )
            assert metadata["format_id"] == "mp4"

    def test_download_failure_raises_runtime_error(self):
        from general_ludd.cloud.video_compare import download_youtube_video

        mock_ytdlp = MagicMock()
        mock_ytdlp.YoutubeDL.side_effect = RuntimeError("network down")

        with (
            patch("general_ludd.cloud.video_compare.yt_dlp", mock_ytdlp),
            patch("pathlib.Path.mkdir"),
            pytest.raises(RuntimeError, match="Failed to download"),
        ):
            download_youtube_video("https://youtube.com?v=x", "/tmp/test.mp4")

    def test_progress_sink_receives_fields(self):
        from general_ludd.cloud.video_compare import download_youtube_video

        mock_ytdlp = MagicMock()
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "requested_downloads": [{"format_id": "mp4", "ext": "mp4"}],
        }

        progress_records = []
        mock_ytdlp.YoutubeDL.return_value.__enter__.return_value = mock_ydl

        with (
            patch("general_ludd.cloud.video_compare.yt_dlp", mock_ytdlp),
            patch("pathlib.Path.mkdir"),
            patch("os.path.exists", return_value=True),
        ):
            download_youtube_video(
                "https://youtube.com?v=x",
                "/tmp/test.mp4",
                progress_sink=progress_records.append,
            )
            extracted_opts = mock_ytdlp.YoutubeDL.call_args[0][0]
            assert "progress_hooks" in extracted_opts

    def test_output_path_directory_format(self):
        from general_ludd.cloud.video_compare import download_youtube_video

        mock_ytdlp = MagicMock()
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "requested_downloads": [{"format_id": "mp4", "ext": "mp4"}],
        }
        mock_ytdlp.YoutubeDL.return_value.__enter__.return_value = mock_ydl

        with (
            patch("general_ludd.cloud.video_compare.yt_dlp", mock_ytdlp),
            patch("pathlib.Path.mkdir"),
            patch("os.path.exists", return_value=True),
        ):
            result = download_youtube_video("https://youtube.com?v=x", "/tmp/dir/")
            assert result.endswith(".mp4")


class TestReferenceVideoSpecDeep:
    def test_all_registered_specs_have_positive_duration(self):
        for name, spec in REFERENCE_VIDEO_SPECS.items():
            assert spec.clip_duration_seconds > 0, f"{name} has non-positive duration"

    def test_all_registered_specs_have_positive_frame_count(self):
        for name, spec in REFERENCE_VIDEO_SPECS.items():
            assert spec.sample_frame_count > 0, f"{name} has non-positive frame count"

    def test_all_registered_specs_have_positive_interval(self):
        for name, spec in REFERENCE_VIDEO_SPECS.items():
            assert spec.sample_interval_seconds > 0, f"{name} has non-positive interval"

    def test_all_registered_specs_have_threshold_in_range(self):
        for name, spec in REFERENCE_VIDEO_SPECS.items():
            assert 0.0 < spec.comparison_threshold < 1.0, f"{name} threshold out of range"

    def test_all_registered_specs_have_non_empty_video_id(self):
        for name, spec in REFERENCE_VIDEO_SPECS.items():
            assert len(spec.video_id) > 0, f"{name} has empty video_id"


class TestEnsureModulesDeep:
    def test_ensure_yt_dlp_missing_with_module_present(self):
        from general_ludd.cloud.video_compare import _ensure_yt_dlp

        mock_mod = MagicMock()
        with patch("general_ludd.cloud.video_compare.yt_dlp", mock_mod):
            result = _ensure_yt_dlp()
            assert result is mock_mod

    def test_ensure_cv2_missing_with_module_present(self):
        from general_ludd.cloud.video_compare import _ensure_cv2

        mock_mod = MagicMock()
        with patch("general_ludd.cloud.video_compare.cv2", mock_mod):
            result = _ensure_cv2()
            assert result is mock_mod
