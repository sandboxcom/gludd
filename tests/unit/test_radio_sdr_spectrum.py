"""Tests for sdr_capture and spectrum_scan scripts under radio roles."""

from __future__ import annotations

import importlib
import json
import math
import os
import shutil
import struct
import sys

import pytest

_collection_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "collections")
)
_sdr_dir = os.path.join(
    _collection_root, "ansible_collections", "general_ludd",
    "radio", "roles", "sdr_capture", "files",
)
_scan_dir = os.path.join(
    _collection_root, "ansible_collections", "general_ludd",
    "radio", "roles", "spectrum_scan", "files",
)
if _sdr_dir not in sys.path:
    sys.path.insert(0, _sdr_dir)
if _scan_dir not in sys.path:
    sys.path.insert(0, _scan_dir)

sc = importlib.import_module("sdr_capture")
ss = importlib.import_module("spectrum_scan")



# ============================================================================
# sdr_capture.py — CaptureResult
# ============================================================================


class TestCaptureResult:
    def test_default_field_values(self):
        r = sc.CaptureResult(
            freq_hz=100_000_000,
            sample_rate=2_048_000,
            duration_sec=1.0,
            sample_count=2_048_000,
            format="int16",
            format_bytes=2,
            device_index=0,
            gain="auto",
            output_file="/tmp/iq.bin",
            output_dir="/tmp/gludd-sdr-capture",
            tool="rtl_sdr",
        )
        assert r.rc == -1
        assert r.stderr == ""
        assert r.file_size_bytes == 0
        assert r.i_min == 0.0
        assert r.q_max == 0.0

    def test_to_dict_success_verdict(self):
        r = sc.CaptureResult(
            freq_hz=100_000_000,
            sample_rate=1_000_000,
            duration_sec=2.0,
            sample_count=2_000_000,
            format="int16",
            format_bytes=2,
            device_index=0,
            gain="42",
            output_file="/tmp/o.bin",
            output_dir="/tmp/d",
            tool="rtl_sdr",
        )
        r.rc = 0
        d = r.to_dict()
        assert d["verdict"] == "success"

    def test_to_dict_nonzero_rc_skipped(self):
        r = sc.CaptureResult(
            freq_hz=100_000_000,
            sample_rate=1_000_000,
            duration_sec=1.0,
            sample_count=1_000_000,
            format="int16",
            format_bytes=2,
            device_index=0,
            gain="auto",
            output_file="/tmp/o.bin",
            output_dir="/tmp/d",
            tool="rtl_sdr",
        )
        r.rc = -1
        d = r.to_dict()
        assert d["verdict"] == "skipped"

    def test_to_dict_has_iq_stats_keys(self):
        r = sc.CaptureResult(
            freq_hz=100_000_000,
            sample_rate=1_000_000,
            duration_sec=0.5,
            sample_count=500_000,
            format="int16",
            format_bytes=2,
            device_index=0,
            gain="auto",
            output_file="/tmp/o.bin",
            output_dir="/tmp/d",
            tool="rtl_sdr",
        )
        d = r.to_dict()
        stats = d["iq_stats"]
        assert "i_min" in stats
        assert "q_rms" in stats
        assert "dc_offset_i" in stats
        assert "peak_power_db" in stats
        assert "avg_power_db" in stats

    def test_to_dict_tool_only_keys_present(self):
        r = sc.CaptureResult(
            freq_hz=144_000_000,
            sample_rate=2_048_000,
            duration_sec=0.25,
            sample_count=512_000,
            format="int8",
            format_bytes=1,
            device_index=1,
            gain="manual",
            output_file="/tmp/x.bin",
            output_dir="/tmp/y",
            tool="rtl_sdr",
        )
        d = r.to_dict()
        for k in ("freq_hz", "sample_rate", "duration_sec", "sample_count", "format",
                  "format_bytes", "device_index", "gain", "output_file", "output_dir",
                  "tool", "rc", "stderr", "file_size_bytes", "actual_sample_count",
                  "actual_duration_sec", "iq_stats", "verdict"):
            assert k in d, f"missing top-level key {k}"

    def test_to_dict_actual_duration_sec_rounded(self):
        r = sc.CaptureResult(
            freq_hz=1_000_000,
            sample_rate=1_000,
            duration_sec=1.0,
            sample_count=1_000,
            format="int16",
            format_bytes=2,
            device_index=0,
            gain="auto",
            output_file="/tmp/o.bin",
            output_dir="/tmp/d",
            tool="rtl_sdr",
        )
        r.actual_duration_sec = 1.234567
        d = r.to_dict()
        assert d["actual_duration_sec"] == pytest.approx(1.2346, rel=1e-3)


# ============================================================================
# sdr_capture.py — FORMAT_BYTES
# ============================================================================


class TestFormatBytes:
    def test_int8(self):
        assert sc.FORMAT_BYTES["int8"] == 1

    def test_int16(self):
        assert sc.FORMAT_BYTES["int16"] == 2

    def test_float32(self):
        assert sc.FORMAT_BYTES["float32"] == 4


# ============================================================================
# sdr_capture.py — _compute_sample_stats
# ============================================================================


class TestComputeSampleStats:
    def test_empty_list_returns_zero_stats(self):
        stats = sc._compute_sample_stats([])
        assert stats["min"] == 0.0
        assert stats["max"] == 0.0
        assert stats["mean"] == 0.0
        assert stats["std"] == 0.0
        assert stats["rms"] == 0.0

    def test_single_value(self):
        stats = sc._compute_sample_stats([3.0])
        assert stats["min"] == 3.0
        assert stats["max"] == 3.0
        assert stats["mean"] == pytest.approx(3.0)
        assert stats["std"] == pytest.approx(0.0)
        assert stats["rms"] == pytest.approx(3.0)

    def test_multiple_identical_values(self):
        stats = sc._compute_sample_stats([5.0, 5.0, 5.0, 5.0])
        assert stats["min"] == 5.0
        assert stats["max"] == 5.0
        assert stats["mean"] == pytest.approx(5.0)
        assert stats["std"] == pytest.approx(0.0)
        assert stats["rms"] == pytest.approx(5.0)

    def test_positive_and_negative_values(self):
        stats = sc._compute_sample_stats([-1.0, 0.0, 1.0])
        assert stats["min"] == -1.0
        assert stats["max"] == 1.0
        assert stats["mean"] == pytest.approx(0.0)
        assert stats["std"] > 0.0
        assert stats["rms"] > 0.0

    def test_typical_sine_like_data(self):
        values = [math.sin(2 * math.pi * i / 100) for i in range(100)]
        stats = sc._compute_sample_stats(values)
        assert -1.1 <= stats["min"] <= 1.1
        assert -1.1 <= stats["max"] <= 1.1
        assert stats["mean"] == pytest.approx(0.0, abs=0.1)
        assert 0.6 < stats["rms"] < 0.8
        assert 0.6 < stats["std"] < 0.8

    def test_all_positive_values(self):
        stats = sc._compute_sample_stats([1.0, 2.0, 3.0])
        assert stats["min"] == 1.0
        assert stats["max"] == 3.0
        assert stats["mean"] == pytest.approx(2.0)
        assert stats["std"] > 0.0

    def test_large_numeric_range(self):
        stats = sc._compute_sample_stats([1e3, -1e3, 0.0])
        assert stats["min"] == -1000.0
        assert stats["max"] == 1000.0
        assert stats["rms"] > 500.0


# ============================================================================
# sdr_capture.py — _read_iq_samples
# ============================================================================


class TestReadIQSamples:
    @pytest.fixture
    def tmp_bin_file(self, tmp_path):
        def _write(fmt, samples):
            p = tmp_path / "iq.bin"
            if fmt == "int8":
                raw = struct.pack(f"{len(samples)}b", *samples)
            elif fmt == "int16":
                raw = struct.pack(f"{len(samples)}h", *samples)
            elif fmt == "float32":
                raw = struct.pack(f"{len(samples)}f", *samples)
            else:
                raw = b""
            p.write_bytes(raw)
            return str(p)
        return _write

    def test_missing_file_returns_empty(self):
        i_data, q_data = sc._read_iq_samples("/nonexistent/iq_file.bin", "int16")
        assert i_data == []
        assert q_data == []

    def test_int8_format(self, tmp_bin_file):
        path = tmp_bin_file("int8", [10, 20, 30, 40])
        i_data, q_data = sc._read_iq_samples(path, "int8")
        assert i_data == [10.0, 30.0]
        assert q_data == [20.0, 40.0]

    def test_int16_format(self, tmp_bin_file):
        path = tmp_bin_file("int16", [100, 200, 300, 400])
        i_data, q_data = sc._read_iq_samples(path, "int16")
        assert i_data == [100.0, 300.0]
        assert q_data == [200.0, 400.0]

    def test_float32_format(self, tmp_bin_file):
        path = tmp_bin_file("float32", [0.5, 1.5, 2.5, 3.5])
        i_data, q_data = sc._read_iq_samples(path, "float32")
        assert i_data == [0.5, 2.5]
        assert q_data == [1.5, 3.5]

    def test_unknown_format_returns_empty(self, tmp_bin_file):
        path = tmp_bin_file("int8", [1, 2, 3, 4])
        i_data, q_data = sc._read_iq_samples(path, "unknown_fmt")
        assert i_data == []
        assert q_data == []

    def test_odd_sample_count_ignores_last_incomplete_pair(self, tmp_bin_file):
        path = tmp_bin_file("int8", [1, 2, 3, 4, 99])
        i_data, q_data = sc._read_iq_samples(path, "int8")
        assert i_data == [1.0, 3.0]
        assert q_data == [2.0, 4.0]

    def test_single_sample_pair(self, tmp_bin_file):
        path = tmp_bin_file("int8", [77, 88])
        i_data, q_data = sc._read_iq_samples(path, "int8")
        assert i_data == [77.0]
        assert q_data == [88.0]

    def test_int16_negative_values(self, tmp_bin_file):
        path = tmp_bin_file("int16", [-32000, 32000, 0, -1])
        i_data, q_data = sc._read_iq_samples(path, "int16")
        assert i_data == [-32000.0, 0.0]
        assert q_data == [32000.0, -1.0]

    def test_empty_file_returns_empty(self, tmp_bin_file):
        path = tmp_bin_file("int8", [])
        i_data, q_data = sc._read_iq_samples(path, "int8")
        assert i_data == []
        assert q_data == []


# ============================================================================
# sdr_capture.py — capture_iq
# ============================================================================


class TestCaptureIQ:
    def test_tool_not_found_fallback(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setenv("SDR_CAPTURE_TOOL_PATH", "")
        result = sc.capture_iq(
            freq_hz=100_000_000,
            sample_rate=1_000_000,
            duration_sec=0.1,
            tool="nonexistent_tool_xyz",
        )
        assert result["verdict"] == "skipped"
        assert result["rc"] == -1
        assert result["stderr"] == "nonexistent_tool_xyz not found"

    def test_tool_not_found_stderr_truncated(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setenv("SDR_CAPTURE_TOOL_PATH", "")
        result = sc.capture_iq(
            freq_hz=100_000_000,
            sample_rate=1_000_000,
            duration_sec=0.1,
        )
        assert result["rc"] == -1
        assert "rtl_sdr not found" in result["stderr"]

    def test_returns_expected_top_level_keys(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setenv("SDR_CAPTURE_TOOL_PATH", "")
        result = sc.capture_iq(
            freq_hz=144_000_000,
            sample_rate=2_000_000,
            duration_sec=0.5,
            gain="30",
            device_index=1,
            fmt="int8",
        )
        assert result["freq_hz"] == 144_000_000
        assert result["sample_rate"] == 2_000_000
        assert result["duration_sec"] == 0.5
        assert result["sample_count"] == 1_000_000
        assert result["format"] == "int8"
        assert result["format_bytes"] == 1
        assert result["device_index"] == 1
        assert result["gain"] == "30"

    def test_env_tool_path_used(self, monkeypatch):
        monkeypatch.setenv("SDR_CAPTURE_TOOL_PATH", "/custom/path/tool")
        result = sc.capture_iq(freq_hz=100e6, sample_rate=1e6, duration_sec=0.1)
        assert result["rc"] == -1

    def test_unknown_format_defaults_bytes_2(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        monkeypatch.setenv("SDR_CAPTURE_TOOL_PATH", "")
        result = sc.capture_iq(
            freq_hz=100_000_000,
            sample_rate=1_000_000,
            duration_sec=0.1,
            fmt="nonexistent_fmt",
        )
        assert result["format_bytes"] == 2

# ============================================================================
# spectrum_scan.py — ScanResult
# ============================================================================


class TestScanResult:
    def test_to_dict_verdict_success(self):
        r = ss.ScanResult(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=10_000,
            integration_time_ms=100,
            gain="auto",
            device_index=0,
            tool="rtl_power",
            output_dir="/tmp/x",
        )
        r.rc = 0
        d = r.to_dict()
        assert d["verdict"] == "success"

    def test_to_dict_verdict_skipped(self):
        r = ss.ScanResult(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=10_000,
            integration_time_ms=100,
            gain="auto",
            device_index=0,
            tool="rtl_power",
            output_dir="/tmp/x",
        )
        r.rc = -1
        d = r.to_dict()
        assert d["verdict"] == "skipped"

    def test_to_dict_has_start_end_mhz(self):
        r = ss.ScanResult(
            start_freq_hz=144_000_000,
            end_freq_hz=148_000_000,
            bin_size_hz=10_000,
            integration_time_ms=100,
            gain="auto",
            device_index=0,
            tool="rtl_power",
            output_dir="/tmp/x",
        )
        d = r.to_dict()
        assert d["start_mhz"] == pytest.approx(144.0)
        assert d["end_mhz"] == pytest.approx(148.0)

    def test_to_dict_bandwidth_mhz(self):
        r = ss.ScanResult(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=10_000,
            integration_time_ms=100,
            gain="auto",
            device_index=0,
            tool="rtl_power",
            output_dir="/tmp/x",
        )
        r.bandwidth_mhz = 100.0
        d = r.to_dict()
        assert d["bandwidth_mhz"] == pytest.approx(100.0)

    def test_to_dict_dynamic_range_when_noise_valid(self):
        r = ss.ScanResult(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=10_000,
            integration_time_ms=100,
            gain="auto",
            device_index=0,
            tool="rtl_power",
            output_dir="/tmp/x",
        )
        r.noise_floor_dbm = -110.0
        r.max_power_dbm = -60.0
        d = r.to_dict()
        assert d["dynamic_range_db"] == pytest.approx(50.0)

    def test_to_dict_dynamic_range_when_noise_invalid(self):
        r = ss.ScanResult(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=10_000,
            integration_time_ms=100,
            gain="auto",
            device_index=0,
            tool="rtl_power",
            output_dir="/tmp/x",
        )
        r.noise_floor_dbm = -999.0
        r.max_power_dbm = -60.0
        d = r.to_dict()
        assert d["dynamic_range_db"] == 0.0

    def test_to_dict_peaks_capped_at_20(self):
        r = ss.ScanResult(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=10_000,
            integration_time_ms=100,
            gain="auto",
            device_index=0,
            tool="rtl_power",
            output_dir="/tmp/x",
        )
        r.peaks = [{"freq_hz": i} for i in range(50)]
        d = r.to_dict()
        assert len(d["peaks"]) == 20

    def test_to_dict_required_keys_present(self):
        r = ss.ScanResult(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=10_000,
            integration_time_ms=100,
            gain="auto",
            device_index=0,
            tool="rtl_power",
            output_dir="/tmp/x",
        )
        d = r.to_dict()
        for k in ("start_freq_hz", "end_freq_hz", "start_mhz", "end_mhz",
                  "bandwidth_mhz", "bin_size_hz", "integration_time_ms",
                  "num_bins", "total_sweep_time_s", "gain", "device_index",
                  "tool", "rc", "stderr", "noise_floor_dbm", "min_power_dbm",
                  "max_power_dbm", "avg_power_dbm", "dynamic_range_db",
                  "signals_detected", "peaks", "band_occupancy", "verdict"):
            assert k in d, f"missing key {k}"


# ============================================================================
# spectrum_scan.py — BandOccupancy
# ============================================================================


class TestBandOccupancy:
    def test_defaults(self):
        bo = ss.BandOccupancy(
            band_name="TestBand",
            start_hz=100_000_000,
            end_hz=200_000_000,
        )
        assert bo.num_bins == 0
        assert bo.bins_occupied == 0
        assert bo.occupancy_pct == 0.0
        assert bo.peak_power_dbm == -999.0
        assert bo.avg_power_dbm == -999.0


# ============================================================================
# spectrum_scan.py — _freq_to_mhz
# ============================================================================


class TestFreqToMHz:
    def test_zero(self):
        assert ss._freq_to_mhz(0) == 0.0

    def test_1_mhz(self):
        assert ss._freq_to_mhz(1_000_000) == 1.0

    def test_normal_frequency(self):
        assert ss._freq_to_mhz(146_000_000) == pytest.approx(146.0)

    def test_large_frequency(self):
        assert ss._freq_to_mhz(1_700_000_000) == pytest.approx(1700.0)


# ============================================================================
# spectrum_scan.py — _power_dbm_to_linear / _linear_to_power_dbm
# ============================================================================


class TestPowerConversions:
    def test_dbm_to_linear_zero(self):
        assert ss._power_dbm_to_linear(0.0) == 1.0

    def test_dbm_to_linear_negative(self):
        linear = ss._power_dbm_to_linear(-30.0)
        assert linear == pytest.approx(0.001, rel=1e-3)

    def test_dbm_to_linear_positive(self):
        linear = ss._power_dbm_to_linear(30.0)
        assert linear == pytest.approx(1000.0, rel=0.01)

    def test_linear_to_dbm_one(self):
        assert ss._linear_to_power_dbm(1.0) == pytest.approx(0.0, abs=0.01)

    def test_linear_to_dbm_very_small(self):
        dbm = ss._linear_to_power_dbm(0.0)
        assert dbm < -100.0

    def test_round_trip(self):
        for val in (-100.0, -50.0, 10.0, 30.0):
            linear = ss._power_dbm_to_linear(val)
            dbm = ss._linear_to_power_dbm(linear)
            assert dbm == pytest.approx(val, rel=0.01)


# ============================================================================
# spectrum_scan.py — _synthesize_sweep
# ============================================================================


class TestSynthesizeSweep:
    def test_empty_when_start_equals_end(self):
        bins = ss._synthesize_sweep(100_000_000, 100_000_000, 10_000)
        assert bins == []

    def test_empty_when_start_greater_than_end(self):
        bins = ss._synthesize_sweep(200_000_000, 100_000_000, 10_000)
        assert bins == []

    def test_single_bin(self):
        bins = ss._synthesize_sweep(100_000_000, 100_010_000, 10_000)
        assert len(bins) == 1
        assert bins[0]["freq_hz"] == 100_000_000
        assert "freq_mhz" in bins[0]
        assert "power_dbm" in bins[0]
        assert "noise_floor_dbm" in bins[0]

    def test_num_bins_is_correct(self):
        bins = ss._synthesize_sweep(100_000_000, 200_000_000, 10_000)
        expected = (200_000_000 - 100_000_000) // 10_000
        assert len(bins) == expected

    def test_bins_are_monotonic_freq(self):
        bins = ss._synthesize_sweep(100_000_000, 200_000_000, 100_000)
        freqs = [b["freq_hz"] for b in bins]
        assert freqs == sorted(freqs)

    def test_fm_band_has_signals(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = ss.sweep_spectrum(
            start_freq_hz=88_000_000,
            end_freq_hz=108_000_000,
            bin_size_hz=100_000,
            output_dir=str(tmp_path / "synth_fm"),
        )
        assert result["signals_detected"] > 0
        assert len(result["peaks"]) > 0

    def test_adsb_band_has_signals(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = ss.sweep_spectrum(
            start_freq_hz=1_088_000_000,
            end_freq_hz=1_092_000_000,
            bin_size_hz=500_000,
            output_dir=str(tmp_path / "synth_adsb"),
        )
        assert result["signals_detected"] > 0


# ============================================================================
# spectrum_scan.py — _classify_bands
# ============================================================================


class TestClassifyBands:
    def test_empty_bins_returns_empty(self):
        result = ss._classify_bands([], -100.0)
        assert result == []

    def test_known_band_classified(self):
        bins = [
            {"freq_hz": 146_000_000, "power_dbm": -50.0, "freq_mhz": 146.0, "noise_floor_dbm": -100.0},
            {"freq_hz": 147_000_000, "power_dbm": -105.0, "freq_mhz": 147.0, "noise_floor_dbm": -100.0},
        ]
        results = ss._classify_bands(bins, -100.0)
        assert len(results) > 0
        names = [r["band_name"] for r in results]
        assert "VHF-High" in names

    def test_band_verdict_active(self):
        bins = [
            {"freq_hz": 100_000_000, "power_dbm": -50.0, "freq_mhz": 100.0, "noise_floor_dbm": -100.0},
        ]
        results = ss._classify_bands(bins, -110.0)
        fm = [r for r in results if r["band_name"] == "FM Broadcast"]
        assert len(fm) == 1
        assert fm[0]["verdict"] == "active"

    def test_band_verdict_quiet(self):
        bins = [
            {"freq_hz": 100_000_000, "power_dbm": -120.0, "freq_mhz": 100.0, "noise_floor_dbm": -100.0},
        ]
        results = ss._classify_bands(bins, -110.0)
        fm = [r for r in results if r["band_name"] == "FM Broadcast"]
        assert len(fm) == 1
        assert fm[0]["verdict"] == "quiet"

    def test_band_has_occupancy_fields(self):
        bins = [
            {"freq_hz": 100_000_000, "power_dbm": -50.0, "freq_mhz": 100.0, "noise_floor_dbm": -100.0},
            {"freq_hz": 101_000_000, "power_dbm": -105.0, "freq_mhz": 101.0, "noise_floor_dbm": -100.0},
        ]
        results = ss._classify_bands(bins, -110.0)
        fm = [r for r in results if r["band_name"] == "FM Broadcast"]
        assert len(fm) == 1
        assert "occupancy_pct" in fm[0]
        assert "peak_power_dbm" in fm[0]
        assert "avg_power_dbm" in fm[0]
        assert "typical_uses" in fm[0]
        assert "verdict" in fm[0]


# ============================================================================
# spectrum_scan.py — sweep_spectrum
# ============================================================================


class TestSweepSpectrum:
    def test_tool_not_found_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=10_000,
            output_dir=str(tmp_path / "scan"),
        )
        assert result["verdict"] == "success"
        assert "rtl_power not found" in result["stderr"]
        assert os.path.isfile(os.path.join(str(tmp_path / "scan"), "spectrum_scan.json"))

    def test_creates_output_directory(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        out = str(tmp_path / "sweep_out")
        ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=10_000,
            output_dir=out,
        )
        assert os.path.isdir(out)

    def test_num_bins_calculated(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=50_000,
            output_dir=str(tmp_path / "s"),
        )
        assert result["num_bins"] == (200_000_000 - 100_000_000) // 50_000

    def test_total_sweep_time_calculated(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        num_bins = (200e6 - 100e6) // 10_000
        expected_time = round(num_bins * 100 / 1000.0, 2)
        result = ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=10_000,
            output_dir=str(tmp_path / "t"),
        )
        assert result["total_sweep_time_s"] == expected_time

    def test_csv_written_and_readable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        out = str(tmp_path / "csv_test")
        ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=100_000,
            output_dir=out,
        )
        csv_path = os.path.join(out, "scan.csv")
        assert os.path.isfile(csv_path)
        with open(csv_path) as f:
            lines = f.readlines()
        assert len(lines) > 0

    def test_sweep_with_env_tool_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "/tmp/nonexistent-tool")
        result = ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=50_000,
            output_dir=str(tmp_path / "env"),
        )
        assert "tool not found" in result["stderr"]

    def test_min_max_avg_power_populated(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=100_000,
            output_dir=str(tmp_path / "pow"),
        )
        assert result["min_power_dbm"] > -999.0
        assert result["max_power_dbm"] > -999.0
        assert result["avg_power_dbm"] > -999.0

    def test_noise_floor_populated(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=100_000,
            output_dir=str(tmp_path / "nf"),
        )
        assert result["noise_floor_dbm"] > -150.0

    def test_signals_detected_is_int(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=100_000,
            output_dir=str(tmp_path / "sd"),
        )
        assert isinstance(result["signals_detected"], int)

    def test_band_occupancy_in_result(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=500_000_000,
            bin_size_hz=500_000,
            output_dir=str(tmp_path / "bo"),
        )
        assert len(result["band_occupancy"]) > 0
        assert "band_name" in result["band_occupancy"][0]

    def test_default_params(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=200_000_000,
            bin_size_hz=50_000,
            output_dir=str(tmp_path / "def"),
        )
        assert result["gain"] == "auto"
        assert result["device_index"] == 0


# ============================================================================
# spectrum_scan.py — KNOWN_BANDS
# ============================================================================


class TestKnownBands:
    def test_all_bands_have_required_keys(self):
        for band in ss.KNOWN_BANDS:
            assert "name" in band
            assert "start_hz" in band
            assert "end_hz" in band
            assert "typical_uses" in band
            assert isinstance(band["typical_uses"], list)
            assert len(band["typical_uses"]) > 0

    def test_fm_broadcast_band(self):
        fm = [b for b in ss.KNOWN_BANDS if b["name"] == "FM Broadcast"]
        assert len(fm) == 1
        assert fm[0]["start_hz"] == 88_000_000
        assert fm[0]["end_hz"] == 108_000_000

    def test_air_band(self):
        ab = [b for b in ss.KNOWN_BANDS if b["name"] == "Air Band"]
        assert len(ab) == 1
        assert ab[0]["start_hz"] == 108_000_000
        assert ab[0]["end_hz"] == 137_000_000

    def test_no_overlapping_bands(self):
        sorted_bands = sorted(ss.KNOWN_BANDS, key=lambda b: b["start_hz"])
        for i in range(len(sorted_bands) - 1):
            assert sorted_bands[i]["end_hz"] <= sorted_bands[i + 1]["start_hz"], (
                f"Band {sorted_bands[i]['name']} overlaps with {sorted_bands[i + 1]['name']}"
            )

    def test_l_band_covers_gps_adsb(self):
        lb = [b for b in ss.KNOWN_BANDS if b["name"] == "L-Band"]
        assert len(lb) == 1
        uses = lb[0]["typical_uses"]
        assert "GPS" in uses
        assert "ADS-B" in uses


# ============================================================================
# Integration-style: end-to-end synthetic sweep with real assertions
# ============================================================================


class TestSweepSpectrumSyntheticIntegration:
    def test_full_sweep_fm_band_signals_match_synthesis(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        result = ss.sweep_spectrum(
            start_freq_hz=88_000_000,
            end_freq_hz=108_000_000,
            bin_size_hz=50_000,
            output_dir=str(tmp_path / "int_fm"),
        )
        assert result["verdict"] == "success"
        assert result["bandwidth_mhz"] == pytest.approx(20.0)
        peaks = result["peaks"]
        assert len(peaks) > 0
        fm_hz = {p["freq_hz"] for p in peaks}
        assert any(97_000_000 <= f <= 99_000_000 for f in fm_hz)

    def test_json_output_file_valid_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPECTRUM_SCAN_TOOL_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        out = str(tmp_path / "json_test")
        ss.sweep_spectrum(
            start_freq_hz=100_000_000,
            end_freq_hz=120_000_000,
            bin_size_hz=1_000_000,
            output_dir=out,
        )
        json_path = os.path.join(out, "spectrum_scan.json")
        with open(json_path) as f:
            data = json.load(f)
        assert data["verdict"] == "success"
        assert "peaks" in data
        assert "band_occupancy" in data


# ============================================================================
# sdr_capture.py — capture_iq with actual temp file (synthetic data)
# ============================================================================


class TestCaptureIQWithTempFile:
    def _write_iq_file(self, path, fmt, values):
        if fmt == "int8":
            raw = struct.pack(f"{len(values)}b", *values)
        elif fmt == "int16":
            raw = struct.pack(f"{len(values)}h", *values)
        elif fmt == "float32":
            raw = struct.pack(f"{len(values)}f", *values)
        else:
            raw = b""
        with open(path, "wb") as f:
            f.write(raw)

    def test_iq_statistics_from_temp_file_int8(self, monkeypatch, tmp_path):
        out_dir = str(tmp_path / "iq_capture")
        bin_path = os.path.join(out_dir, "iq_samples.bin")
        os.makedirs(out_dir, exist_ok=True)
        self._write_iq_file(bin_path, "int8", [10, -10, 20, -20, 30, -30, 40, -40])

        monkeypatch.setenv("SDR_CAPTURE_TOOL_PATH", "/fake/tool")
        result = sc.capture_iq(
            freq_hz=100_000_000,
            sample_rate=8,
            duration_sec=0.5,
            fmt="int8",
            output_dir=out_dir,
            tool="rtl_sdr",
        )
        assert result["verdict"] == "skipped"
        assert result["file_size_bytes"] > 0
        assert result["actual_sample_count"] > 0


# ============================================================================
# Verify modules can be imported and have expected public API
# ============================================================================


class TestModuleAPI:
    def test_sdr_capture_exports(self):
        assert hasattr(sc, "CaptureResult")
        assert hasattr(sc, "FORMAT_BYTES")
        assert hasattr(sc, "capture_iq")
        assert hasattr(sc, "main")
        assert callable(sc._read_iq_samples)
        assert callable(sc._compute_sample_stats)

    def test_spectrum_scan_exports(self):
        assert hasattr(ss, "ScanResult")
        assert hasattr(ss, "BandOccupancy")
        assert hasattr(ss, "KNOWN_BANDS")
        assert hasattr(ss, "sweep_spectrum")
        assert hasattr(ss, "main")
        assert callable(ss._synthesize_sweep)
        assert callable(ss._classify_bands)

    def test_capture_result_is_dataclass(self):
        from dataclasses import is_dataclass
        assert is_dataclass(sc.CaptureResult)

    def test_scan_result_is_dataclass(self):
        from dataclasses import is_dataclass
        assert is_dataclass(ss.ScanResult)

    def test_band_occupancy_is_dataclass(self):
        from dataclasses import is_dataclass
        assert is_dataclass(ss.BandOccupancy)
