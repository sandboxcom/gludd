"""TDD tests for signal analysis engine: IQ processing, FFT analysis,
signal detection, SNR estimation, bandwidth estimation."""

from __future__ import annotations

import json
import math

import pytest
from plugins.module_utils.signal_analysis import (
    AnalyzerConfig,
    SignalAnalyzer,
    compute_fft_magnitude,
    compute_noise_floor,
    detect_signals,
    estimate_bandwidth,
    estimate_center_freq,
    estimate_snr,
    normalize_power,
)

# ============================================================================
# AnalyzerConfig
# ============================================================================


class TestAnalyzerConfig:
    def test_defaults(self):
        cfg = AnalyzerConfig()
        assert cfg.sample_rate_hz == 2_048_000
        assert cfg.fft_size == 1024
        assert cfg.noise_percentile == 25.0
        assert cfg.detection_threshold_db == 6.0
        assert cfg.min_bandwidth_hz == 100
        assert cfg.max_bandwidth_hz == 1_000_000

    def test_custom_config(self):
        cfg = AnalyzerConfig(
            sample_rate_hz=10_000_000,
            fft_size=2048,
            noise_percentile=30.0,
            detection_threshold_db=10.0,
            min_bandwidth_hz=200,
            max_bandwidth_hz=500_000,
        )
        assert cfg.sample_rate_hz == 10_000_000
        assert cfg.fft_size == 2048
        assert cfg.noise_percentile == 30.0
        assert cfg.detection_threshold_db == 10.0
        assert cfg.min_bandwidth_hz == 200
        assert cfg.max_bandwidth_hz == 500_000

    def test_to_dict(self):
        cfg = AnalyzerConfig(
            sample_rate_hz=1_000_000,
            fft_size=512,
        )
        d = cfg.to_dict()
        assert d["sample_rate_hz"] == 1_000_000
        assert d["fft_size"] == 512
        assert d["noise_percentile"] == 25.0
        assert d["detection_threshold_db"] == 6.0

    def test_from_dict(self):
        d = {"sample_rate_hz": 5_000_000, "fft_size": 4096, "detection_threshold_db": 8.0}
        cfg = AnalyzerConfig.from_dict(d)
        assert cfg.sample_rate_hz == 5_000_000
        assert cfg.fft_size == 4096
        assert cfg.detection_threshold_db == 8.0

    def test_freq_resolution(self):
        cfg = AnalyzerConfig(sample_rate_hz=2_048_000, fft_size=1024)
        assert cfg.freq_resolution_hz == 2000  # 2_048_000 / 1024

    def test_json_roundtrip(self):
        cfg = AnalyzerConfig(sample_rate_hz=4_000_000, fft_size=2048, detection_threshold_db=9.0)
        recreated = AnalyzerConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))
        assert recreated.sample_rate_hz == cfg.sample_rate_hz
        assert recreated.fft_size == cfg.fft_size
        assert recreated.detection_threshold_db == cfg.detection_threshold_db
        assert recreated.freq_resolution_hz == cfg.freq_resolution_hz


# ============================================================================
# FFT Magnitude Computation
# ============================================================================


class TestFFTMagnitude:
    def test_simple_sine_magnitude(self):
        sample_rate = 1_000_000
        freq = 100_000
        n = 1024
        iq = [
            complex(
                math.cos(2.0 * math.pi * freq * t / sample_rate),
                math.sin(2.0 * math.pi * freq * t / sample_rate),
            )
            for t in range(n)
        ]
        mag_db, freq_bins = compute_fft_magnitude(iq, sample_rate)
        assert len(mag_db) == n // 2
        assert len(freq_bins) == n // 2
        max_idx = max(range(len(mag_db)), key=lambda i: mag_db[i])
        assert abs(freq_bins[max_idx] - freq) < sample_rate / n * 2

    def test_empty_input_returns_empty(self):
        mag_db, freq_bins = compute_fft_magnitude([], 1_000_000)
        assert mag_db == []
        assert freq_bins == []

    def test_no_energy_at_dc(self):
        n = 512
        iq = [complex(1.0, 0.0) for _ in range(n)]
        mag_db, _freq_bins = compute_fft_magnitude(iq, 1_000_000)
        assert len(mag_db) == n // 2

    def test_zero_padding_yields_full_bins(self):
        sample_rate = 500_000
        iq = [complex(0.0, 0.0) for _ in range(256)]
        mag_db, _freq_bins = compute_fft_magnitude(iq, sample_rate, fft_size=512)
        assert len(mag_db) == 256


# ============================================================================
# Noise Floor Estimation
# ============================================================================


class TestNoiseFloor:
    def test_uniform_noise(self):
        import random

        random.seed(42)
        mag_db = [random.gauss(-110.0, 3.0) for _ in range(200)]
        nf = compute_noise_floor(mag_db, percentile=25.0)
        assert -115.0 < nf < -105.0

    def test_signal_plus_noise(self):
        import random

        random.seed(42)
        noise = [random.gauss(-110.0, 2.0) for _ in range(180)]
        signal = [*[-30.0, -32.0, -31.0, -33.0], *[-28.0, -29.0, -30.0], *[-35.0, -34.0, -36.0]]
        mag_db = noise + signal + [random.gauss(-110.0, 2.0) for _ in range(10)]
        nf = compute_noise_floor(mag_db, percentile=25.0)
        assert nf < -60.0  # Signal should not dominate the 25th percentile

    def test_empty_returns_zero(self):
        assert compute_noise_floor([], percentile=25.0) == 0.0

    def test_single_value(self):
        assert compute_noise_floor([-100.0], percentile=25.0) == -100.0


# ============================================================================
# Signal Detection
# ============================================================================


class TestDetectSignals:
    def test_detects_strong_signal(self):
        n = 256
        mag_db = [-120.0] * n
        mag_db[50] = -30.0
        mag_db[51] = -32.0
        mag_db[52] = -31.0
        freq_bins = [i * 10_000 for i in range(n)]
        signals = detect_signals(mag_db, freq_bins, noise_floor_db=-110.0, threshold_db=6.0)
        assert len(signals) >= 1
        assert any(490_000 <= s["freq_hz"] <= 530_000 for s in signals)

    def test_no_signals_below_threshold(self):
        mag_db = [-120.0] * 100
        mag_db[40] = -114.0  # Only 6 dB above -120
        freq_bins = [i * 10_000 for i in range(100)]
        signals = detect_signals(mag_db, freq_bins, noise_floor_db=-120.0, threshold_db=10.0)
        assert len(signals) == 0

    def test_multiple_signals(self):
        n = 256
        mag_db = [-120.0] * n
        for i in [20, 21, 22, 100, 101, 200, 201, 202]:
            mag_db[i] = -40.0
        freq_bins = [i * 10_000 for i in range(n)]
        signals = detect_signals(mag_db, freq_bins, noise_floor_db=-110.0, threshold_db=6.0)
        assert len(signals) == 3

    def test_returns_empty_list_on_empty(self):
        signals = detect_signals([], [], noise_floor_db=-110.0, threshold_db=6.0)
        assert signals == []

    def test_returned_signal_shape(self):
        n = 256
        mag_db = [-120.0] * n
        mag_db[60] = -35.0
        mag_db[61] = -40.0
        freq_bins = [i * 10_000 for i in range(n)]
        signals = detect_signals(mag_db, freq_bins, noise_floor_db=-110.0, threshold_db=6.0)
        assert len(signals) >= 1
        s = signals[0]
        assert "freq_hz" in s
        assert "power_dbm" in s
        assert "bandwidth_hz" in s
        assert "snr_db" in s
        assert s["power_dbm"] <= 0
        assert s["bandwidth_hz"] > 0

    def test_adjacent_bins_merged(self):
        n = 256
        mag_db = [-120.0] * n
        for i in range(50, 55):
            mag_db[i] = -45.0
        freq_bins = [i * 10_000 for i in range(n)]
        signals = detect_signals(mag_db, freq_bins, noise_floor_db=-110.0, threshold_db=6.0)
        assert len(signals) == 1


# ============================================================================
# SNR Estimation
# ============================================================================


class TestEstimateSNR:
    def test_snr_computation(self):
        snr = estimate_snr(signal_power_dbm=-40.0, noise_floor_dbm=-110.0)
        assert snr == pytest.approx(70.0, rel=0.01)

    def test_snr_zero(self):
        snr = estimate_snr(signal_power_dbm=-50.0, noise_floor_dbm=-50.0)
        assert snr == pytest.approx(0.0, abs=0.1)

    def test_negative_snr(self):
        snr = estimate_snr(signal_power_dbm=-120.0, noise_floor_dbm=-100.0)
        assert snr == pytest.approx(-20.0, rel=0.01)

    def test_snr_not_none_values(self):
        snr = estimate_snr(signal_power_dbm=-33.0, noise_floor_dbm=-108.0)
        assert snr == pytest.approx(75.0)


# ============================================================================
# Bandwidth Estimation
# ============================================================================


class TestEstimateBandwidth:
    def test_narrowband_signal(self):
        bw = estimate_bandwidth(bin_indices=[50, 51, 52], freq_resolution_hz=2000)
        assert bw == 3 * 2000

    def test_single_bin(self):
        bw = estimate_bandwidth(bin_indices=[100], freq_resolution_hz=5000)
        assert bw == 5000

    def test_empty_indices(self):
        bw = estimate_bandwidth(bin_indices=[], freq_resolution_hz=2000)
        assert bw == 0

    def test_wideband_signal(self):
        bw = estimate_bandwidth(bin_indices=list(range(100, 200)), freq_resolution_hz=10000)
        assert bw == 100 * 10000


# ============================================================================
# Center Frequency Estimation
# ============================================================================


class TestEstimateCenterFreq:
    def test_center(self):
        freq_bins = [1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000]
        center = estimate_center_freq(freq_bins)
        assert center == 3_000_000

    def test_single_bin(self):
        freq_bins = [100_000_000]
        center = estimate_center_freq(freq_bins)
        assert center == 100_000_000

    def test_empty(self):
        assert estimate_center_freq([]) == 0

    def test_even_count(self):
        freq_bins = [1_000_000, 2_000_000, 3_000_000, 4_000_000]
        center = estimate_center_freq(freq_bins)
        assert center == 2_500_000


# ============================================================================
# Power Normalization
# ============================================================================


class TestNormalizePower:
    def test_normalize_linear_scale(self):
        powers = [1.0, 2.0, 3.0, 4.0, 5.0]
        norm = normalize_power(powers)
        assert max(norm) == pytest.approx(1.0)
        assert min(norm) == pytest.approx(0.0)

    def test_single_value(self):
        norm = normalize_power([5.0])
        assert norm == [0.0]

    def test_empty(self):
        assert normalize_power([]) == []

    def test_all_same_value(self):
        norm = normalize_power([3.0, 3.0, 3.0])
        assert norm == [0.0, 0.0, 0.0]


# ============================================================================
# SignalAnalyzer (Orchestrator)
# ============================================================================


class TestSignalAnalyzer:
    def test_construction(self):
        cfg = AnalyzerConfig(sample_rate_hz=2_000_000, fft_size=1024)
        analyzer = SignalAnalyzer(cfg)
        assert analyzer.config.sample_rate_hz == 2_000_000
        assert analyzer.config.fft_size == 1024

    def test_analyze_iq_samples(self):
        sample_rate = 2_000_000
        n = 1024
        target_freq = 100_000
        iq = [
            complex(
                math.cos(2.0 * math.pi * target_freq * t / sample_rate),
                math.sin(2.0 * math.pi * target_freq * t / sample_rate),
            )
            for t in range(n)
        ]
        cfg = AnalyzerConfig(sample_rate_hz=sample_rate, fft_size=n, detection_threshold_db=3.0)
        analyzer = SignalAnalyzer(cfg)
        result = analyzer.analyze(iq)
        assert "fft_magnitude_db" in result
        assert "freq_bins" in result
        assert "noise_floor_dbm" in result
        assert "detected_signals" in result
        assert isinstance(result["detected_signals"], list)
        assert len(result["detected_signals"]) >= 1

    def test_analyze_noise_only(self):
        import random

        random.seed(42)
        n = 512
        iq = [complex(random.gauss(0, 0.001), random.gauss(0, 0.001)) for _ in range(n)]
        cfg = AnalyzerConfig(sample_rate_hz=1_000_000, fft_size=n, detection_threshold_db=30.0)
        analyzer = SignalAnalyzer(cfg)
        result = analyzer.analyze(iq)
        assert len(result["detected_signals"]) == 0
        assert result["noise_floor_dbm"] is not None

    def test_to_dict(self):
        cfg = AnalyzerConfig(sample_rate_hz=2_000_000, fft_size=1024)
        analyzer = SignalAnalyzer(cfg)
        sample_rate = 2_000_000
        n = 1024
        freq = 500_000
        iq = [
            complex(
                math.cos(2.0 * math.pi * freq * t / sample_rate),
                math.sin(2.0 * math.pi * freq * t / sample_rate),
            )
            for t in range(n)
        ]
        result = analyzer.analyze(iq)
        d = analyzer.to_dict(result)
        assert "fft_magnitude_db" in d
        assert "freq_bins" in d
        assert "noise_floor_dbm" in d
        assert "detected_signals" in d
        assert "config" in d

    def test_json_exportable(self):
        cfg = AnalyzerConfig(sample_rate_hz=2_000_000, fft_size=1024)
        analyzer = SignalAnalyzer(cfg)
        sample_rate = 2_000_000
        n = 1024
        freq = 300_000
        iq = [
            complex(
                math.cos(2.0 * math.pi * freq * t / sample_rate),
                math.sin(2.0 * math.pi * freq * t / sample_rate),
            )
            for t in range(n)
        ]
        result = analyzer.analyze(iq)
        d = analyzer.to_dict(result)
        exported = json.dumps(d)
        reloaded = json.loads(exported)
        assert reloaded["noise_floor_dbm"] == d["noise_floor_dbm"]
        assert len(reloaded["detected_signals"]) == len(d["detected_signals"])
