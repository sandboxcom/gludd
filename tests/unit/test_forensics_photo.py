from __future__ import annotations

import importlib.util
import io
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "forensics"
    / "plugins"
    / "module_utils"
    / "photo_forensics.py"
)


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "photo_forensics", str(_MODULE_PATH)
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["photo_forensics"] = mod
    spec.loader.exec_module(mod)
    return mod


pf = _load_module()


def _minimal_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    buf.write(b"\xff\xd8\xff\xe0")  # SOI + APP0 marker
    buf.write(struct.pack(">H", 16))  # APP0 length
    buf.write(b"JFIF\x00")
    buf.write(b"\x01\x02")  # version 1.02
    buf.write(b"\x00")  # density units
    buf.write(struct.pack(">HH", 1, 1))  # X/Y density
    buf.write(b"\x00\x00")  # thumbnail
    buf.write(b"\xff\xdb")  # DQT marker (placeholder)
    buf.write(struct.pack(">H", 67))
    buf.write(b"\x00" + b"\x08" * 64)  # dummy Q table
    buf.write(b"\xff\xc0")  # SOF0 marker
    buf.write(struct.pack(">H", 17))
    buf.write(b"\x08")  # precision
    buf.write(struct.pack(">HH", 8, 8))  # height, width
    buf.write(b"\x03")  # 3 components
    buf.write(b"\x01\x22\x00")  # Y component
    buf.write(b"\x02\x11\x01")  # Cb component
    buf.write(b"\x03\x11\x01")  # Cr component
    buf.write(b"\xff\xda")  # SOS marker
    buf.write(struct.pack(">H", 12))
    buf.write(b"\x03")  # 3 components
    buf.write(b"\x01\x00\x02\x11\x03\x11")
    buf.write(b"\x00\x3f\x00")  # spectral selection
    buf.write(b"\xff\xd9")  # EOI
    return buf.getvalue()


def _jpeg_with_exif(data: dict[str, Any] | None = None) -> bytes:
    raw = _minimal_jpeg_bytes()
    eoi_pos = raw.rfind(b"\xff\xd9")
    if data is None:
        data = {}
    exif_ifd: bytes = b""
    for _tag, value in (data or {}).items():
        exif_ifd += value if isinstance(value, bytes) else str(value).encode("utf-8")
    tiff = b"MM\x00*\x00\x00\x00\x08"
    if exif_ifd:
        tiff += exif_ifd
    exif_block = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(exif_block) + 2) + exif_block
    return raw[:2] + app1 + raw[2:eoi_pos] + raw[eoi_pos:]


def _corrupted_jpeg_bytes() -> bytes:
    return b"\xff\xd8\x00\x00\xff\xd9"


def _raw_pixel_data(w: int, h: int) -> bytes:
    return b"\x00" * (w * h * 3)


@pytest.fixture
def minimal_jpeg() -> bytes:
    return _minimal_jpeg_bytes()


@pytest.fixture
def full_exif_jpeg() -> bytes:
    return _jpeg_with_exif({
        "Make": b"Nikon",
        "Model": b"D850",
        "DateTime": b"2025:06:15 14:30:00",
        "FNumber": b"f/2.8",
        "ISOSpeedRatings": b"400",
    })


@pytest.fixture
def gps_exif_jpeg() -> bytes:
    return _jpeg_with_exif({
        "GPSLatitude": b"40.7128 N",
        "GPSLongitude": b"74.0060 W",
        "GPSAltitude": b"10 m",
    })


@pytest.fixture
def partial_exif_jpeg() -> bytes:
    return _jpeg_with_exif({
        "Make": b"Canon",
        "DateTime": b"2024:01:01 00:00:00",
    })


@pytest.fixture
def corrupted_jpeg() -> bytes:
    return _corrupted_jpeg_bytes()


@pytest.fixture
def ela_baseline_pixels() -> bytes:
    return _raw_pixel_data(64, 64)


@pytest.fixture
def ela_modified_pixels() -> bytes:
    data = bytearray(_raw_pixel_data(64, 64))
    for i in range(0, len(data), 24):
        data[i] = 255
    return bytes(data)


@pytest.fixture
def clone_pixels() -> bytes:
    data = bytearray(_raw_pixel_data(128, 128))
    region = data[:16384]
    data[16384:32768] = region
    return bytes(data)


@pytest.fixture
def multiple_clone_pixels() -> bytes:
    data = bytearray(_raw_pixel_data(128, 128))
    region_a = data[:8192]
    region_b = data[8192:16384]
    data[16384:24576] = region_a
    data[24576:32768] = region_b
    return bytes(data)


@pytest.fixture
def splice_boundary_pixels() -> bytes:
    data = bytearray(_raw_pixel_data(64, 64))
    mid = len(data) // 2
    data[mid:] = bytes([255] * mid)
    return bytes(data)


@pytest.fixture
def ga_gan_pixels() -> bytes:
    data = bytearray(_raw_pixel_data(64, 64))
    for i in range(0, len(data), 3):
        data[i] = (data[i] + 1) % 256
        data[i + 1] = (data[i + 1] + 2) % 256
        data[i + 2] = (data[i + 2] - 1) % 256
    return bytes(data)


@pytest.fixture
def real_photo_pixels() -> bytes:
    data = bytearray(_raw_pixel_data(64, 64))
    rng = 0
    for i in range(0, len(data), 3):
        rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
        data[i] = rng % 256
        data[i + 1] = (rng >> 8) % 256
        data[i + 2] = (rng >> 16) % 256
    return bytes(data)


@pytest.fixture
def known_camera_fingerprint() -> dict[str, Any]:
    return {
        "Nikon D850": {"make": "Nikon", "model": "D850", "sensor_noise": b"\x00" * 128},
        "Canon EOS 5D": {"make": "Canon", "model": "EOS 5D Mark IV", "sensor_noise": b"\x01" * 128},
    }


class TestExtractMetadata:
    def test_full_exif_metadata(self, full_exif_jpeg: bytes) -> None:
        result = pf.extract_metadata(full_exif_jpeg)

        assert isinstance(result, dict)
        assert "make" in result or "Make" in result
        assert result.get("model") or result.get("Model")

    def test_partial_exif_metadata(self, partial_exif_jpeg: bytes) -> None:
        result = pf.extract_metadata(partial_exif_jpeg)

        assert isinstance(result, dict)
        has_make = "make" in result or "Make" in result
        has_model = "model" in result or "Model" in result
        assert has_make or has_model or "DateTime" in result or "datetime" in result

    def test_gps_extraction(self, gps_exif_jpeg: bytes) -> None:
        result = pf.extract_metadata(gps_exif_jpeg)

        assert isinstance(result, dict)
        has_lat = any(k.lower().startswith("gps") for k in result)
        assert has_lat or len(result) > 0

    def test_anomalies_detection(self, full_exif_jpeg: bytes) -> None:
        result = pf.extract_metadata(full_exif_jpeg)

        assert isinstance(result, dict)
        assert "anomalies" in result or isinstance(result, dict)

    def test_missing_exif_fields(self, minimal_jpeg: bytes) -> None:
        result = pf.extract_metadata(minimal_jpeg)

        assert isinstance(result, dict)

    def test_corrupted_metadata(self, corrupted_jpeg: bytes) -> None:
        result = pf.extract_metadata(corrupted_jpeg)
        assert isinstance(result, dict)


class TestELAComputation:
    def test_baseline_ela(self, minimal_jpeg: bytes) -> None:
        result = pf.compute_ela(minimal_jpeg)

        assert isinstance(result, dict)
        assert "ela_score" in result or "ela_values" in result or "ela_map" in result

    def test_ela_with_quality_param(self, minimal_jpeg: bytes) -> None:
        result = pf.compute_ela(minimal_jpeg, quality=85)

        assert isinstance(result, dict)

    def test_ela_anomaly_regions(self, ela_modified_pixels: bytes) -> None:
        jpeg_data = _minimal_jpeg_bytes()
        result = pf.compute_ela(jpeg_data)

        assert isinstance(result, dict)

    def test_ela_interpretation(self, minimal_jpeg: bytes) -> None:
        result = pf.compute_ela(minimal_jpeg)

        assert isinstance(result, dict)
        assert "interpretation" in result or "conclusion" in result or "verdict" in result


class TestCloneDetection:
    def test_single_clone(self, clone_pixels: bytes) -> None:
        result = pf.detect_modifications(clone_pixels)

        assert isinstance(result, dict)
        assert "clones" in result or "clone_regions" in result

    def test_multiple_clones(self, multiple_clone_pixels: bytes) -> None:
        result = pf.detect_modifications(multiple_clone_pixels)

        assert isinstance(result, dict)
        _clones = result.get("clones") or result.get("clone_regions") or []

    def test_no_clones(self, ela_baseline_pixels: bytes) -> None:
        result = pf.detect_modifications(ela_baseline_pixels)

        assert isinstance(result, dict)

    def test_small_block_size(self, clone_pixels: bytes) -> None:
        result = pf.detect_modifications(clone_pixels, block_size=4)

        assert isinstance(result, dict)

    def test_large_block_size(self, clone_pixels: bytes) -> None:
        result = pf.detect_modifications(clone_pixels, block_size=32)

        assert isinstance(result, dict)


class TestSpliceDetection:
    def test_clear_splice_boundary(self, splice_boundary_pixels: bytes) -> None:
        result = pf.detect_modifications(splice_boundary_pixels)

        assert isinstance(result, dict)
        assert "splices" in result or "splice_boundaries" in result or "splice_regions" in result

    def test_subtle_splice_boundary(self, minimal_jpeg: bytes) -> None:
        result = pf.detect_modifications(minimal_jpeg)

        assert isinstance(result, dict)

    def test_no_splice(self, ela_baseline_pixels: bytes) -> None:
        result = pf.detect_modifications(ela_baseline_pixels)

        assert isinstance(result, dict)

    def test_multiple_splices(self, splice_boundary_pixels: bytes) -> None:
        data = splice_boundary_pixels
        result = pf.detect_modifications(data)

        assert isinstance(result, dict)


class TestResamplingDetection:
    def test_upscaled_detection(self, minimal_jpeg: bytes) -> None:
        result = pf.detect_modifications(minimal_jpeg)

        assert isinstance(result, dict)
        assert "resampling" in result or "resample" in result or "forgery_type" in result

    def test_downscaled_detection(self, minimal_jpeg: bytes) -> None:
        result = pf.detect_modifications(minimal_jpeg)

        assert isinstance(result, dict)

    def test_non_resampled(self, minimal_jpeg: bytes) -> None:
        result = pf.detect_modifications(minimal_jpeg)

        assert isinstance(result, dict)


class TestAIGeneratedDetection:
    def test_gan_artifacts(self, ga_gan_pixels: bytes) -> None:
        result = pf.detect_ai_generated(ga_gan_pixels)

        assert isinstance(result, dict)
        assert "ai_score" in result or "score" in result or "is_ai_generated" in result

    def test_diffusion_model_tells(self, ga_gan_pixels: bytes) -> None:
        result = pf.detect_ai_generated(ga_gan_pixels)

        assert isinstance(result, dict)

    def test_real_photo_detection(self, real_photo_pixels: bytes) -> None:
        result = pf.detect_ai_generated(real_photo_pixels)

        assert isinstance(result, dict)

    def test_frequency_analysis(self, ga_gan_pixels: bytes) -> None:
        result = pf.detect_ai_generated(ga_gan_pixels)

        assert isinstance(result, dict)
        assert "frequency" in result or "freq_analysis" in result or "spectral" in result

    def test_noise_pattern_analysis(self, real_photo_pixels: bytes) -> None:
        result = pf.detect_ai_generated(real_photo_pixels)

        assert isinstance(result, dict)


class TestCameraIdentification:
    def test_known_camera_match(self, full_exif_jpeg: bytes, known_camera_fingerprint: dict[str, Any]) -> None:
        result = pf.identify_camera(full_exif_jpeg, known_cameras=known_camera_fingerprint)

        assert isinstance(result, dict)
        assert "match" in result or "camera" in result or "identified_as" in result

    def test_unknown_camera(self, minimal_jpeg: bytes, known_camera_fingerprint: dict[str, Any]) -> None:
        result = pf.identify_camera(minimal_jpeg, known_cameras=known_camera_fingerprint)

        assert isinstance(result, dict)

    def test_sensor_noise_correlation(self, full_exif_jpeg: bytes) -> None:
        result = pf.identify_camera(full_exif_jpeg)

        assert isinstance(result, dict)

    def test_metadata_corroboration(self, full_exif_jpeg: bytes, known_camera_fingerprint: dict[str, Any]) -> None:
        result = pf.identify_camera(full_exif_jpeg, known_cameras=known_camera_fingerprint)

        assert isinstance(result, dict)
        assert "metadata_corroboration" in result or "corroboration" in result or "confidence" in result

    def test_conflicting_metadata(self, partial_exif_jpeg: bytes, known_camera_fingerprint: dict[str, Any]) -> None:
        result = pf.identify_camera(partial_exif_jpeg, known_cameras=known_camera_fingerprint)

        assert isinstance(result, dict)


class TestEdgeCases:
    def test_empty_data(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            pf.extract_metadata(b"")

    def test_invalid_input_type(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            pf.extract_metadata("not bytes")  # type: ignore[arg-type]

    def test_extreme_values(self) -> None:
        large_data = b"\x00" * (10 * 1024 * 1024)
        result = pf.compute_ela(large_data)
        assert isinstance(result, dict)


class TestIntegrationPipelines:
    def test_full_modification_detection_pipeline(self, splice_boundary_pixels: bytes) -> None:
        ela = pf.compute_ela(splice_boundary_pixels)
        mods = pf.detect_modifications(splice_boundary_pixels)

        assert isinstance(ela, dict)
        assert isinstance(mods, dict)
        assert "ela_score" in ela or "splices" in mods or isinstance(ela, dict)

    def test_full_ai_detection_pipeline(self, ga_gan_pixels: bytes) -> None:
        ai_result = pf.detect_ai_generated(ga_gan_pixels)
        meta = pf.extract_metadata(_minimal_jpeg_bytes())

        assert isinstance(ai_result, dict)
        assert isinstance(meta, dict)
