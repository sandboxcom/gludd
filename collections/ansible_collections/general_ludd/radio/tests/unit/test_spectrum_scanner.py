"""TDD tests for spectrum scanner: sweep orchestration, peak detection,
signal classification, band occupancy statistics."""

from __future__ import annotations

import json

import pytest
from plugins.module_utils.spectrum_scanner import (
    ScannerConfig,
    SpectrumScanner,
    SweepResult,
    classify_peaks,
    compute_band_occupancy,
    find_peak_by_freq,
    merge_sweep_results,
    simulate_spectrum_sweep,
)

# ============================================================================
# ScannerConfig
# ============================================================================


class TestScannerConfig:
    def test_defaults(self):
        cfg = ScannerConfig()
        assert cfg.freq_start_hz == 1_000_000
        assert cfg.freq_end_hz == 30_000_000
        assert cfg.step_hz == 100_000
        assert cfg.dwell_ms == 10
        assert cfg.detection_threshold_db == 6.0
        assert cfg.noise_floor_dbm == -110.0

    def test_custom_config(self):
        cfg = ScannerConfig(
            freq_start_hz=144_000_000,
            freq_end_hz=148_000_000,
            step_hz=25_000,
            dwell_ms=5,
            detection_threshold_db=8.0,
            noise_floor_dbm=-115.0,
        )
        assert cfg.freq_start_hz == 144_000_000
        assert cfg.freq_end_hz == 148_000_000
        assert cfg.step_hz == 25_000
        assert cfg.dwell_ms == 5
        assert cfg.detection_threshold_db == 8.0
        assert cfg.noise_floor_dbm == -115.0

    def test_total_steps(self):
        cfg = ScannerConfig(freq_start_hz=1_000_000, freq_end_hz=10_000_000, step_hz=1_000_000)
        assert cfg.total_steps == 9  # ceil((10M-1M)/1M) = ceil(9) = 9

    def test_step_validation(self):
        with pytest.raises(ValueError, match="step_hz"):
            ScannerConfig(freq_start_hz=1_000_000, freq_end_hz=10_000_000, step_hz=0)

    def test_freq_range_validation(self):
        with pytest.raises(ValueError, match="freq_end_hz"):
            ScannerConfig(freq_start_hz=10_000_000, freq_end_hz=1_000_000, step_hz=100_000)

    def test_to_dict(self):
        cfg = ScannerConfig(freq_start_hz=50_000_000, freq_end_hz=54_000_000, step_hz=50_000)
        d = cfg.to_dict()
        assert d["freq_start_hz"] == 50_000_000
        assert d["freq_end_hz"] == 54_000_000
        assert d["step_hz"] == 50_000
        assert d["detection_threshold_db"] == 6.0

    def test_from_dict(self):
        d = {
            "freq_start_hz": 144_000_000,
            "freq_end_hz": 148_000_000,
            "step_hz": 25_000,
            "noise_floor_dbm": -120.0,
        }
        cfg = ScannerConfig.from_dict(d)
        assert cfg.freq_start_hz == 144_000_000
        assert cfg.freq_end_hz == 148_000_000
        assert cfg.noise_floor_dbm == -120.0

    def test_json_roundtrip(self):
        cfg = ScannerConfig(
            freq_start_hz=88_000_000,
            freq_end_hz=108_000_000,
            step_hz=200_000,
            dwell_ms=20,
        )
        recreated = ScannerConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))
        assert recreated.freq_start_hz == cfg.freq_start_hz
        assert recreated.freq_end_hz == cfg.freq_end_hz
        assert recreated.step_hz == cfg.step_hz
        assert recreated.total_steps == cfg.total_steps


# ============================================================================
# SweepResult
# ============================================================================


class TestSweepResult:
    def test_empty_result(self):
        sr = SweepResult()
        assert sr.peaks == []
        assert sr.noise_floor_dbm is None
        assert sr.freq_start_hz is None
        assert sr.freq_end_hz is None

    def test_with_peaks(self):
        sr = SweepResult(
            freq_start_hz=144_000_000,
            freq_end_hz=148_000_000,
            step_hz=25_000,
        )
        sr.add_peak(freq_hz=146_520_000, power_dbm=-45.0, bandwidth_hz=12_500, modulation_guess="FM")
        sr.add_peak(freq_hz=146_000_000, power_dbm=-60.0, bandwidth_hz=6_250, modulation_guess="DMR")
        assert len(sr.peaks) == 2
        assert sr.total_peaks == 2

    def test_strongest_peak(self):
        sr = SweepResult(freq_start_hz=100_000_000, freq_end_hz=200_000_000, step_hz=100_000)
        sr.add_peak(freq_hz=110_000_000, power_dbm=-70.0, bandwidth_hz=10_000)
        sr.add_peak(freq_hz=150_000_000, power_dbm=-30.0, bandwidth_hz=20_000)
        assert sr.strongest_peak is not None
        assert sr.strongest_peak["freq_hz"] == 150_000_000
        assert sr.strongest_peak["power_dbm"] == -30.0

    def test_to_dict(self):
        sr = SweepResult(
            freq_start_hz=30_000_000,
            freq_end_hz=60_000_000,
            step_hz=500_000,
            noise_floor_dbm=-110.0,
            scan_timestamp=1735689600.0,
        )
        sr.add_peak(freq_hz=50_100_000, power_dbm=-55.0, bandwidth_hz=10_000, snr_db=15.0)
        d = sr.to_dict()
        assert d["freq_start_hz"] == 30_000_000
        assert d["freq_end_hz"] == 60_000_000
        assert d["step_hz"] == 500_000
        assert d["noise_floor_dbm"] == -110.0
        assert d["total_peaks"] == 1
        assert len(d["peaks"]) == 1


# ============================================================================
# simulate_spectrum_sweep
# ============================================================================


class TestSimulateSpectrumSweep:
    def test_simulates_sweep(self):
        cfg = ScannerConfig(
            freq_start_hz=1_000_000,
            freq_end_hz=30_000_000,
            step_hz=500_000,
            detection_threshold_db=3.0,
            noise_floor_dbm=-110.0,
        )
        injected_signals = [
            {"freq_hz": 7_150_000, "power_dbm": -50.0, "bandwidth_hz": 3_000},
            {"freq_hz": 14_200_000, "power_dbm": -45.0, "bandwidth_hz": 3_000},
            {"freq_hz": 21_200_000, "power_dbm": -55.0, "bandwidth_hz": 3_000},
        ]
        sweep = simulate_spectrum_sweep(cfg, injected_signals)
        assert sweep.freq_start_hz == 1_000_000
        assert sweep.freq_end_hz == 30_000_000
        assert sweep.total_peaks >= 0

    def test_empty_sweep(self):
        cfg = ScannerConfig(freq_start_hz=1_000_000, freq_end_hz=10_000_000, step_hz=1_000_000)
        sweep = simulate_spectrum_sweep(cfg, [])
        assert sweep.total_peaks == 0


# ============================================================================
# classify_peaks
# ============================================================================


class TestClassifyPeaks:
    def test_classify_fm_narrowband(self):
        peaks = [
            {"freq_hz": 162_400_000, "power_dbm": -40.0, "bandwidth_hz": 12_500},
        ]
        result = classify_peaks(peaks)
        assert len(result) == 1
        assert "modulation_guess" in result[0]
        assert result[0]["modulation_guess"] is not None

    def test_classify_wideband(self):
        peaks = [
            {"freq_hz": 100_000_000, "power_dbm": -60.0, "bandwidth_hz": 200_000},
        ]
        result = classify_peaks(peaks)
        assert len(result) == 1

    def test_empty_peaks(self):
        assert classify_peaks([]) == []

    def test_classify_preserves_existing_guess(self):
        peaks = [
            {"freq_hz": 146_000_000, "power_dbm": -50.0, "bandwidth_hz": 6_250, "modulation_guess": "DMR"},
        ]
        result = classify_peaks(peaks)
        assert result[0]["modulation_guess"] == "DMR"

    def test_classify_cw_like(self):
        peaks = [
            {"freq_hz": 7_025_000, "power_dbm": -80.0, "bandwidth_hz": 200},
        ]
        result = classify_peaks(peaks)
        assert len(result) == 1


# ============================================================================
# compute_band_occupancy
# ============================================================================


class TestComputeBandOccupancy:
    def test_occupancy_calculation(self):
        peaks = [
            {"freq_hz": 146_000_000, "power_dbm": -40.0, "bandwidth_hz": 12_500},
            {"freq_hz": 146_800_000, "power_dbm": -50.0, "bandwidth_hz": 6_250},
        ]
        occupancy = compute_band_occupancy(
            start_hz=144_000_000,
            end_hz=148_000_000,
            peaks=peaks,
        )
        assert "total_hz" in occupancy
        assert "occupied_hz" in occupancy
        assert "occupancy_pct" in occupancy
        assert occupancy["total_hz"] == 4_000_000
        assert occupancy["occupied_hz"] >= 12_500
        assert 0 < occupancy["occupancy_pct"] < 100

    def test_empty_band(self):
        occupancy = compute_band_occupancy(
            start_hz=50_000_000,
            end_hz=54_000_000,
            peaks=[],
        )
        assert occupancy["occupied_hz"] == 0
        assert occupancy["occupancy_pct"] == 0.0

    def test_peak_count(self):
        peaks = [
            {"freq_hz": 100_000_000, "power_dbm": -40.0, "bandwidth_hz": 10_000},
            {"freq_hz": 150_000_000, "power_dbm": -40.0, "bandwidth_hz": 10_000},
            {"freq_hz": 200_000_000, "power_dbm": -40.0, "bandwidth_hz": 10_000},
        ]
        occupancy = compute_band_occupancy(
            start_hz=50_000_000,
            end_hz=250_000_000,
            peaks=peaks,
        )
        assert occupancy["num_peaks"] == 3


# ============================================================================
# find_peak_by_freq
# ============================================================================


class TestFindPeakByFreq:
    def test_finds_peak(self):
        peaks = [
            {"freq_hz": 146_520_000, "power_dbm": -45.0, "bandwidth_hz": 12_500},
            {"freq_hz": 446_000_000, "power_dbm": -55.0, "bandwidth_hz": 25_000},
        ]
        result = find_peak_by_freq(peaks, 146_520_000, tolerance_hz=10_000)
        assert result is not None
        assert result["freq_hz"] == 146_520_000

    def test_no_match(self):
        peaks = [
            {"freq_hz": 100_000_000, "power_dbm": -50.0, "bandwidth_hz": 10_000},
        ]
        assert find_peak_by_freq(peaks, 200_000_000, tolerance_hz=10_000) is None

    def test_empty(self):
        assert find_peak_by_freq([], 146_000_000) is None

    def test_tolerance(self):
        peaks = [
            {"freq_hz": 146_500_000, "power_dbm": -50.0, "bandwidth_hz": 10_000},
        ]
        assert find_peak_by_freq(peaks, 146_505_000, tolerance_hz=10_000) is not None
        assert find_peak_by_freq(peaks, 146_600_000, tolerance_hz=10_000) is None


# ============================================================================
# merge_sweep_results
# ============================================================================


class TestMergeSweepResults:
    def test_merges_two_sweeps(self):
        a = SweepResult(freq_start_hz=1_000_000, freq_end_hz=15_000_000, step_hz=500_000)
        a.add_peak(freq_hz=7_150_000, power_dbm=-50.0, bandwidth_hz=3_000)
        b = SweepResult(freq_start_hz=15_000_000, freq_end_hz=30_000_000, step_hz=500_000)
        b.add_peak(freq_hz=21_200_000, power_dbm=-55.0, bandwidth_hz=3_000)
        merged = merge_sweep_results([a, b])
        assert merged.total_peaks == 2

    def test_dedup_overlapping_peaks(self):
        a = SweepResult(freq_start_hz=1_000_000, freq_end_hz=15_000_000, step_hz=500_000)
        a.add_peak(freq_hz=7_150_000, power_dbm=-50.0, bandwidth_hz=3_000)
        b = SweepResult(freq_start_hz=1_000_000, freq_end_hz=15_000_000, step_hz=500_000)
        b.add_peak(freq_hz=7_150_100, power_dbm=-51.0, bandwidth_hz=3_000)  # same signal
        merged = merge_sweep_results([a, b], dedup_tolerance_hz=5_000)
        assert merged.total_peaks == 1

    def test_empty_list(self):
        merged = merge_sweep_results([])
        assert merged.total_peaks == 0

    def test_single_result(self):
        sr = SweepResult(freq_start_hz=1_000_000, freq_end_hz=10_000_000, step_hz=1_000_000)
        sr.add_peak(freq_hz=5_000_000, power_dbm=-40.0, bandwidth_hz=20_000)
        merged = merge_sweep_results([sr])
        assert merged.total_peaks == 1


# ============================================================================
# SpectrumScanner Orchestrator
# ============================================================================


class TestSpectrumScanner:
    def test_construction(self):
        cfg = ScannerConfig(freq_start_hz=50_000_000, freq_end_hz=54_000_000, step_hz=50_000)
        scanner = SpectrumScanner(cfg)
        assert scanner.config.freq_start_hz == 50_000_000
        assert scanner.config.freq_end_hz == 54_000_000

    def test_sweep(self):
        cfg = ScannerConfig(
            freq_start_hz=1_000_000,
            freq_end_hz=30_000_000,
            step_hz=1_000_000,
            detection_threshold_db=3.0,
            noise_floor_dbm=-110.0,
        )
        scanner = SpectrumScanner(cfg)
        sweep = scanner.sweep()
        assert sweep.freq_start_hz == 1_000_000
        assert sweep.freq_end_hz == 30_000_000

    def test_classify_sweep_peaks(self):
        cfg = ScannerConfig(
            freq_start_hz=144_000_000,
            freq_end_hz=148_000_000,
            step_hz=100_000,
        )
        scanner = SpectrumScanner(cfg)
        sweep = SweepResult(freq_start_hz=144_000_000, freq_end_hz=148_000_000, step_hz=100_000)
        sweep.add_peak(freq_hz=146_520_000, power_dbm=-45.0, bandwidth_hz=12_500)
        classified = scanner.classify(sweep)
        assert len(classified) == 1
        assert classified[0]["modulation_guess"] is not None

    def test_band_occupancy(self):
        cfg = ScannerConfig(
            freq_start_hz=144_000_000,
            freq_end_hz=148_000_000,
            step_hz=100_000,
        )
        scanner = SpectrumScanner(cfg)
        sweep = SweepResult(freq_start_hz=144_000_000, freq_end_hz=148_000_000, step_hz=100_000)
        sweep.add_peak(freq_hz=146_520_000, power_dbm=-45.0, bandwidth_hz=12_500)
        sweep.add_peak(freq_hz=146_000_000, power_dbm=-55.0, bandwidth_hz=6_250)
        occ = scanner.occupancy(sweep, band_start_hz=144_000_000, band_end_hz=148_000_000)
        assert occ["num_peaks"] == 2
        assert occ["occupied_hz"] > 0

    def test_to_dict(self):
        cfg = ScannerConfig(
            freq_start_hz=1_000_000,
            freq_end_hz=30_000_000,
            step_hz=1_000_000,
        )
        scanner = SpectrumScanner(cfg)
        sweep = scanner.sweep()
        d = scanner.to_dict(sweep)
        assert d["config"]["freq_start_hz"] == 1_000_000
        assert d["freq_start_hz"] == 1_000_000
        assert "peaks" in d
        assert "total_peaks" in d

    def test_json_exportable(self):
        cfg = ScannerConfig(
            freq_start_hz=144_000_000,
            freq_end_hz=148_000_000,
            step_hz=100_000,
            noise_floor_dbm=-110.0,
        )
        scanner = SpectrumScanner(cfg)
        sweep = scanner.sweep()
        d = scanner.to_dict(sweep)
        exported = json.dumps(d)
        reloaded = json.loads(exported)
        assert reloaded["config"]["freq_start_hz"] == 144_000_000
        assert isinstance(reloaded["peaks"], list)
