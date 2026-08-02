"""Tests for radio contracts: FrequencyAllocation, ModulationScheme,
SignalStrength, SpectrumAnalysis."""

from __future__ import annotations

import json

import pytest
from plugins.module_utils.contracts import (
    FrequencyAllocation,
    ModulationScheme,
    SignalStrength,
    SpectrumAnalysis,
    SpectrumBand,
    SpectrumPeak,
    validate_contracts_schema_version,
)

# ============================================================================
# FrequencyAllocation
# ============================================================================


class TestFrequencyAllocation:
    def test_minimal_construction(self):
        fa = FrequencyAllocation(
            band_name="2m",
            start_hz=144_000_000,
            end_hz=148_000_000,
            country="US",
            itu_region=2,
        )
        assert fa.band_name == "2m"
        assert fa.start_hz == 144_000_000
        assert fa.end_hz == 148_000_000
        assert fa.country == "US"
        assert fa.itu_region == 2
        assert fa.service == "amateur"
        assert fa.license_class is None
        assert fa.privileges == []
        assert fa.notes is None
        assert fa.max_power_w is None

    def test_full_construction(self):
        fa = FrequencyAllocation(
            band_name="20m",
            start_hz=14_000_000,
            end_hz=14_350_000,
            country="US",
            itu_region=2,
            service="amateur",
            license_class="General",
            privileges=["CW", "RTTY", "Phone", "Data"],
            max_power_w=1500,
            notes="Primary amateur allocation",
        )
        assert fa.license_class == "General"
        assert fa.privileges == ["CW", "RTTY", "Phone", "Data"]
        assert fa.max_power_w == 1500
        assert fa.notes == "Primary amateur allocation"

    def test_bandwidth_hz(self):
        fa = FrequencyAllocation(
            band_name="70cm",
            start_hz=420_000_000,
            end_hz=450_000_000,
            country="US",
            itu_region=2,
        )
        assert fa.bandwidth_hz == 30_000_000

    def test_center_freq_hz(self):
        fa = FrequencyAllocation(
            band_name="40m",
            start_hz=7_000_000,
            end_hz=7_300_000,
            country="US",
            itu_region=2,
        )
        assert fa.center_freq_hz == 7_150_000

    def test_contains_freq(self):
        fa = FrequencyAllocation(
            band_name="10m",
            start_hz=28_000_000,
            end_hz=29_700_000,
            country="US",
            itu_region=2,
        )
        assert fa.contains_freq(28_400_000) is True
        assert fa.contains_freq(27_000_000) is False
        assert fa.contains_freq(28_000_000) is True
        assert fa.contains_freq(29_700_000) is True
        assert fa.contains_freq(29_700_001) is False

    def test_display(self):
        fa = FrequencyAllocation(
            band_name="6m",
            start_hz=50_000_000,
            end_hz=54_000_000,
            country="US",
            itu_region=2,
        )
        assert "6m" in fa.display
        assert "50.0" in fa.display
        assert "54.0" in fa.display

    def test_to_dict(self):
        fa = FrequencyAllocation(
            band_name="80m",
            start_hz=3_500_000,
            end_hz=4_000_000,
            country="US",
            itu_region=2,
            service="amateur",
            license_class="Extra",
            privileges=["CW", "RTTY", "Data", "Phone"],
            max_power_w=1500,
        )
        d = fa.to_dict()
        assert d["band_name"] == "80m"
        assert d["start_hz"] == 3_500_000
        assert d["end_hz"] == 4_000_000
        assert d["country"] == "US"
        assert d["itu_region"] == 2
        assert d["service"] == "amateur"
        assert d["license_class"] == "Extra"
        assert d["privileges"] == ["CW", "RTTY", "Data", "Phone"]
        assert d["max_power_w"] == 1500
        assert "bandwidth_hz" in d
        assert "center_freq_hz" in d

    def test_from_dict_minimal(self):
        d = {
            "band_name": "23cm",
            "start_hz": 1_240_000_000,
            "end_hz": 1_300_000_000,
            "country": "US",
            "itu_region": 2,
        }
        fa = FrequencyAllocation.from_dict(d)
        assert fa.band_name == "23cm"
        assert fa.start_hz == 1_240_000_000
        assert fa.itu_region == 2
        assert fa.service == "amateur"

    def test_from_dict_full(self):
        d = {
            "band_name": "15m",
            "start_hz": 21_000_000,
            "end_hz": 21_450_000,
            "country": "DE",
            "itu_region": 1,
            "service": "amateur",
            "license_class": "Class A",
            "privileges": ["CW", "SSB"],
            "max_power_w": 750,
            "notes": "CEPT TR 61-01",
        }
        fa = FrequencyAllocation.from_dict(d)
        assert fa.country == "DE"
        assert fa.itu_region == 1
        assert fa.license_class == "Class A"
        assert fa.max_power_w == 750

    def test_from_dict_missing_required_raises(self):
        with pytest.raises(ValueError, match="band_name"):
            FrequencyAllocation.from_dict({"start_hz": 100_000, "end_hz": 200_000})
        with pytest.raises(ValueError, match="start_hz"):
            FrequencyAllocation.from_dict({"band_name": "x", "end_hz": 200_000})

    def test_from_dict_unknown_keys_ignored(self):
        d = {
            "band_name": "2m",
            "start_hz": 144_000_000,
            "end_hz": 148_000_000,
            "country": "US",
            "itu_region": 2,
            "extra_field": "should be ignored",
        }
        fa = FrequencyAllocation.from_dict(d)
        assert fa.band_name == "2m"

    def test_invalid_freq_range_raises(self):
        with pytest.raises(ValueError, match="must be less than end_hz"):
            FrequencyAllocation(
                band_name="bad",
                start_hz=200_000_000,
                end_hz=100_000_000,
                country="US",
                itu_region=2,
            )

    def test_negative_freq_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            FrequencyAllocation(
                band_name="bad",
                start_hz=-100,
                end_hz=200_000_000,
                country="US",
                itu_region=2,
            )

    def test_itu_region_must_be_1_2_or_3(self):
        with pytest.raises(ValueError, match="1, 2, or 3"):
            FrequencyAllocation(
                band_name="bad",
                start_hz=100_000_000,
                end_hz=200_000_000,
                country="US",
                itu_region=0,
            )
        with pytest.raises(ValueError, match="1, 2, or 3"):
            FrequencyAllocation(
                band_name="bad",
                start_hz=100_000_000,
                end_hz=200_000_000,
                country="US",
                itu_region=4,
            )

    def test_json_roundtrip(self):
        fa = FrequencyAllocation(
            band_name="13cm",
            start_hz=2_300_000_000,
            end_hz=2_450_000_000,
            country="US",
            itu_region=2,
            service="amateur",
            license_class="Extra",
            privileges=["CW", "SSB", "Data"],
            max_power_w=1500,
        )
        json_str = json.dumps(fa.to_dict())
        recreated = FrequencyAllocation.from_dict(json.loads(json_str))
        assert recreated.band_name == fa.band_name
        assert recreated.start_hz == fa.start_hz
        assert recreated.end_hz == fa.end_hz
        assert recreated.privileges == fa.privileges

    def test_equality(self):
        fa1 = FrequencyAllocation(
            band_name="40m",
            start_hz=7_000_000,
            end_hz=7_300_000,
            country="US",
            itu_region=2,
        )
        fa2 = FrequencyAllocation(
            band_name="40m",
            start_hz=7_000_000,
            end_hz=7_300_000,
            country="US",
            itu_region=2,
        )
        fa3 = FrequencyAllocation(
            band_name="40m",
            start_hz=7_000_000,
            end_hz=7_300_000,
            country="CA",
            itu_region=2,
        )
        assert fa1 == fa2
        assert fa1 != fa3
        assert fa1 != "not a contract"


# ============================================================================
# ModulationScheme
# ============================================================================


class TestModulationScheme:
    def test_minimal_construction(self):
        ms = ModulationScheme(
            scheme="AM",
            category="analog",
            bandwidth_hz_typical=10_000,
        )
        assert ms.scheme == "AM"
        assert ms.category == "analog"
        assert ms.bandwidth_hz_typical == 10_000
        assert ms.symbol_rate_baud_min is None
        assert ms.symbol_rate_baud_max is None
        assert ms.bandwidth_hz_min is None
        assert ms.bandwidth_hz_max is None
        assert ms.spectrum_shape == "unknown"
        assert ms.spectral_efficiency_bps_hz is None
        assert ms.typical_use == ""
        assert ms.is_digital is False

    def test_full_construction(self):
        ms = ModulationScheme(
            scheme="QPSK",
            category="digital",
            bandwidth_hz_typical=25_000,
            symbol_rate_baud_min=1200,
            symbol_rate_baud_max=9600,
            bandwidth_hz_min=12_500,
            bandwidth_hz_max=100_000,
            spectrum_shape="phase_shift_keying",
            spectral_efficiency_bps_hz=2.0,
            typical_use="Satellite, DVB-S2, PSK31",
        )
        assert ms.scheme == "QPSK"
        assert ms.category == "digital"
        assert ms.is_digital is True
        assert ms.symbol_rate_baud_min == 1200
        assert ms.symbol_rate_baud_max == 9600
        assert ms.spectral_efficiency_bps_hz == 2.0

    def test_is_digital_property(self):
        assert ModulationScheme(scheme="FM", category="analog", bandwidth_hz_typical=12_500).is_digital is False
        assert ModulationScheme(scheme="QAM64", category="digital", bandwidth_hz_typical=6_000).is_digital is True

    def test_bandwidth_range(self):
        ms = ModulationScheme(
            scheme="CW",
            category="analog",
            bandwidth_hz_typical=50,
            bandwidth_hz_min=30,
            bandwidth_hz_max=100,
        )
        assert ms.bandwidth_range == (30, 100)
        # No min or max set:
        ms2 = ModulationScheme(
            scheme="NBFM",
            category="analog",
            bandwidth_hz_typical=6_250,
        )
        assert ms2.bandwidth_range == (None, None)

    def test_symbol_rate_range(self):
        ms = ModulationScheme(
            scheme="GMSK",
            category="digital",
            bandwidth_hz_typical=12_500,
            symbol_rate_baud_min=1200,
            symbol_rate_baud_max=19_200,
        )
        assert ms.symbol_rate_range == (1200, 19_200)
        ms2 = ModulationScheme(
            scheme="AM",
            category="analog",
            bandwidth_hz_typical=10_000,
        )
        assert ms2.symbol_rate_range == (None, None)

    def test_category_must_be_analog_or_digital(self):
        with pytest.raises(ValueError, match=r"analog.*digital"):
            ModulationScheme(scheme="X", category="hybrid", bandwidth_hz_typical=1000)

    def test_bandwidth_must_be_positive(self):
        with pytest.raises(ValueError, match="positive"):
            ModulationScheme(scheme="X", category="analog", bandwidth_hz_typical=0)

    def test_symbol_rate_bounds_validation(self):
        with pytest.raises(ValueError, match="symbol_rate"):
            ModulationScheme(
                scheme="X",
                category="digital",
                bandwidth_hz_typical=1000,
                symbol_rate_baud_min=9600,
                symbol_rate_baud_max=1200,
            )

    def test_to_dict(self):
        ms = ModulationScheme(
            scheme="FSK",
            category="digital",
            bandwidth_hz_typical=12_500,
            symbol_rate_baud_min=300,
            symbol_rate_baud_max=1200,
            spectrum_shape="frequency_shift_keying",
            spectral_efficiency_bps_hz=0.8,
            typical_use="Packet radio, AX.25, APRS",
        )
        d = ms.to_dict()
        assert d["scheme"] == "FSK"
        assert d["category"] == "digital"
        assert d["is_digital"] is True
        assert d["bandwidth_hz_typical"] == 12_500
        assert d["symbol_rate_baud_min"] == 300
        assert d["spectral_efficiency_bps_hz"] == 0.8

    def test_from_dict_minimal(self):
        d = {"scheme": "BPSK", "category": "digital", "bandwidth_hz_typical": 31}
        ms = ModulationScheme.from_dict(d)
        assert ms.scheme == "BPSK"
        assert ms.category == "digital"
        assert ms.bandwidth_hz_typical == 31

    def test_from_dict_full(self):
        d = {
            "scheme": "OFDM",
            "category": "digital",
            "bandwidth_hz_typical": 7_610_000,
            "symbol_rate_baud_min": 6_000,
            "symbol_rate_baud_max": 312_500,
            "bandwidth_hz_min": 1_250_000,
            "bandwidth_hz_max": 20_000_000,
            "spectrum_shape": "multicarrier",
            "spectral_efficiency_bps_hz": 3.75,
            "typical_use": "LTE, WiFi, DAB, DRM",
        }
        ms = ModulationScheme.from_dict(d)
        assert ms.spectrum_shape == "multicarrier"
        assert ms.typical_use == "LTE, WiFi, DAB, DRM"

    def test_from_dict_missing_required_raises(self):
        with pytest.raises(ValueError, match="scheme"):
            ModulationScheme.from_dict({"category": "analog"})
        with pytest.raises(ValueError, match="bandwidth_hz_typical"):
            ModulationScheme.from_dict({"scheme": "X", "category": "analog"})

    def test_from_dict_unknown_keys_ignored(self):
        d = {
            "scheme": "ASK",
            "category": "digital",
            "bandwidth_hz_typical": 1000,
            "deprecated_field": True,
        }
        ms = ModulationScheme.from_dict(d)
        assert ms.scheme == "ASK"

    def test_equality(self):
        a = ModulationScheme(scheme="CW", category="analog", bandwidth_hz_typical=50)
        b = ModulationScheme(scheme="CW", category="analog", bandwidth_hz_typical=50)
        c = ModulationScheme(scheme="CW", category="analog", bandwidth_hz_typical=100)
        assert a == b
        assert a != c


# ============================================================================
# SignalStrength
# ============================================================================


class TestSignalStrength:
    def test_minimal_construction(self):
        ss = SignalStrength(
            rssi_dbm=-75.0,
            noise_floor_dbm=-110.0,
        )
        assert ss.rssi_dbm == -75.0
        assert ss.noise_floor_dbm == -110.0
        assert ss.snr_db == pytest.approx(35.0, rel=0.01)
        assert ss.signal_db is None
        assert ss.timestamp is None

    def test_full_construction(self):
        ss = SignalStrength(
            rssi_dbm=-60.0,
            noise_floor_dbm=-105.0,
            signal_db=45.0,
            timestamp=1234567890.0,
        )
        assert ss.rssi_dbm == -60.0
        assert ss.noise_floor_dbm == -105.0
        assert ss.snr_db == pytest.approx(45.0, rel=0.01)
        assert ss.signal_db == 45.0
        assert ss.timestamp == 1234567890.0

    def test_rssi_zero_or_negative(self):
        SignalStrength(rssi_dbm=0.0, noise_floor_dbm=-100.0)
        SignalStrength(rssi_dbm=-25.0, noise_floor_dbm=-100.0)
        with pytest.raises(ValueError, match="<= 0"):
            SignalStrength(rssi_dbm=1.0, noise_floor_dbm=-100.0)

    def test_rssi_not_below_minimum(self):
        with pytest.raises(ValueError, match="RSSI"):
            SignalStrength(rssi_dbm=-201.0, noise_floor_dbm=-100.0)

    def test_noise_floor_must_be_negative_or_zero(self):
        with pytest.raises(ValueError, match="<= 0"):
            SignalStrength(rssi_dbm=-100.0, noise_floor_dbm=5.0)

    def test_snr_not_negative(self):
        ss = SignalStrength(rssi_dbm=-120.0, noise_floor_dbm=-100.0)
        assert ss.rssi_dbm == -120.0
        assert ss.snr_db == pytest.approx(-20.0)

    def test_quality_scale(self):
        strong = SignalStrength(rssi_dbm=-40.0, noise_floor_dbm=-110.0)
        assert strong.quality_rating == "excellent"
        good = SignalStrength(rssi_dbm=-85.0, noise_floor_dbm=-110.0)
        assert good.quality_rating == "good"
        weak = SignalStrength(rssi_dbm=-100.0, noise_floor_dbm=-110.0)
        assert weak.quality_rating == "fair"
        very_weak = SignalStrength(rssi_dbm=-115.0, noise_floor_dbm=-110.0)
        assert very_weak.quality_rating == "poor"
        noisy = SignalStrength(rssi_dbm=-90.0, noise_floor_dbm=-95.0)
        assert noisy.quality_rating == "poor"

    def test_snr_db_computed(self):
        ss = SignalStrength(rssi_dbm=-70.0, noise_floor_dbm=-100.0)
        assert ss.snr_db == pytest.approx(30.0, rel=0.01)

    def test_snr_db_with_signal_db(self):
        ss = SignalStrength(
            rssi_dbm=-80.0,
            noise_floor_dbm=-120.0,
            signal_db=35.0,
        )
        assert ss.snr_db == pytest.approx(35.0, rel=0.01)

    def test_to_dict(self):
        ss = SignalStrength(
            rssi_dbm=-82.5,
            noise_floor_dbm=-108.3,
            signal_db=25.8,
            timestamp=1735689600.0,
        )
        d = ss.to_dict()
        assert d["rssi_dbm"] == -82.5
        assert d["noise_floor_dbm"] == -108.3
        assert d["snr_db"] == pytest.approx(25.8, rel=0.01)
        assert d["signal_db"] == 25.8
        assert d["quality_rating"] == "good"

    def test_from_dict_minimal(self):
        d = {"rssi_dbm": -90.0, "noise_floor_dbm": -110.0}
        ss = SignalStrength.from_dict(d)
        assert ss.rssi_dbm == -90.0
        assert ss.snr_db is not None

    def test_from_dict_full(self):
        d = {
            "rssi_dbm": -65.0,
            "noise_floor_dbm": -120.0,
            "signal_db": 55.0,
            "timestamp": 1735689600.0,
        }
        ss = SignalStrength.from_dict(d)
        assert ss.signal_db == 55.0
        assert ss.timestamp == 1735689600.0

    def test_from_dict_missing_required_raises(self):
        with pytest.raises(ValueError, match="rssi_dbm"):
            SignalStrength.from_dict({"noise_floor_dbm": -110.0})

    def test_from_dict_unknown_keys_ignored(self):
        d = {
            "rssi_dbm": -50.0,
            "noise_floor_dbm": -100.0,
            "frequency_hz": 146_520_000,
        }
        ss = SignalStrength.from_dict(d)
        assert ss.rssi_dbm == -50.0

    def test_json_roundtrip(self):
        ss = SignalStrength(
            rssi_dbm=-73.0,
            noise_floor_dbm=-115.0,
            signal_db=42.0,
            timestamp=1735689600.0,
        )
        recreated = SignalStrength.from_dict(json.loads(json.dumps(ss.to_dict())))
        assert recreated.rssi_dbm == ss.rssi_dbm
        assert recreated.snr_db == pytest.approx(ss.snr_db)

    def test_equality(self):
        a = SignalStrength(rssi_dbm=-80.0, noise_floor_dbm=-110.0)
        b = SignalStrength(rssi_dbm=-80.0, noise_floor_dbm=-110.0)
        c = SignalStrength(rssi_dbm=-80.0, noise_floor_dbm=-100.0)
        assert a == b
        assert a != c


# ============================================================================
# SpectrumBand
# ============================================================================


class TestSpectrumBand:
    def test_construction(self):
        sb = SpectrumBand(
            start_hz=88_000_000,
            end_hz=108_000_000,
            label="FM Broadcast",
        )
        assert sb.start_hz == 88_000_000
        assert sb.end_hz == 108_000_000
        assert sb.label == "FM Broadcast"
        assert sb.bandwidth_hz == 20_000_000

    def test_center_freq(self):
        sb = SpectrumBand(start_hz=100_000_000, end_hz=200_000_000, label="Test")
        assert sb.center_freq_hz == 150_000_000

    def test_contains(self):
        sb = SpectrumBand(start_hz=144_000_000, end_hz=148_000_000, label="2m Amateur")
        assert sb.contains(146_000_000) is True
        assert sb.contains(143_000_000) is False
        assert sb.contains(144_000_000) is True
        assert sb.contains(148_000_000) is True

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError, match="less than end_hz"):
            SpectrumBand(start_hz=200_000_000, end_hz=100_000_000, label="bad")

    def test_to_dict(self):
        sb = SpectrumBand(start_hz=1_000_000, end_hz=30_000_000, label="HF")
        d = sb.to_dict()
        assert d["start_hz"] == 1_000_000
        assert d["end_hz"] == 30_000_000
        assert d["label"] == "HF"
        assert d["bandwidth_hz"] == 29_000_000


# ============================================================================
# SpectrumPeak
# ============================================================================


class TestSpectrumPeak:
    def test_construction(self):
        sp = SpectrumPeak(
            freq_hz=146_520_000,
            power_dbm=-45.0,
            bandwidth_hz=12_500,
        )
        assert sp.freq_hz == 146_520_000
        assert sp.power_dbm == -45.0
        assert sp.bandwidth_hz == 12_500
        assert sp.snr_db is None
        assert sp.modulation_guess is None

    def test_full_construction(self):
        sp = SpectrumPeak(
            freq_hz=162_400_000,
            power_dbm=-30.0,
            bandwidth_hz=6_250,
            snr_db=20.0,
            modulation_guess="NBFM",
        )
        assert sp.power_dbm == -30.0
        assert sp.snr_db == 20.0
        assert sp.modulation_guess == "NBFM"

    def test_power_must_be_negative_or_zero(self):
        with pytest.raises(ValueError, match="<= 0"):
            SpectrumPeak(freq_hz=100_000_000, power_dbm=5.0, bandwidth_hz=10_000)

    def test_bandwidth_positive(self):
        with pytest.raises(ValueError, match="positive"):
            SpectrumPeak(freq_hz=100_000_000, power_dbm=-50.0, bandwidth_hz=0)

    def test_freq_must_be_non_negative(self):
        with pytest.raises(ValueError, match="non-negative"):
            SpectrumPeak(freq_hz=-1, power_dbm=-50.0, bandwidth_hz=10_000)

    def test_to_dict(self):
        sp = SpectrumPeak(
            freq_hz=446_000_000,
            power_dbm=-55.0,
            bandwidth_hz=25_000,
            snr_db=15.0,
            modulation_guess="FM",
        )
        d = sp.to_dict()
        assert d["freq_hz"] == 446_000_000
        assert d["power_dbm"] == -55.0
        assert d["snr_db"] == 15.0
        assert d["modulation_guess"] == "FM"

    def test_from_dict(self):
        d = {
            "freq_hz": 27_185_000,
            "power_dbm": -70.0,
            "bandwidth_hz": 10_000,
            "snr_db": 12.0,
        }
        sp = SpectrumPeak.from_dict(d)
        assert sp.freq_hz == 27_185_000
        assert sp.power_dbm == -70.0

    def test_from_dict_missing_required_raises(self):
        with pytest.raises(ValueError, match="freq_hz"):
            SpectrumPeak.from_dict({"power_dbm": -50.0, "bandwidth_hz": 10_000})

    def test_from_dict_unknown_keys_ignored(self):
        d = {
            "freq_hz": 100_000_000,
            "power_dbm": -50.0,
            "bandwidth_hz": 10_000,
            "extra_field": 42,
        }
        sp = SpectrumPeak.from_dict(d)
        assert sp.freq_hz == 100_000_000

    def test_equality(self):
        a = SpectrumPeak(freq_hz=100_000_000, power_dbm=-50.0, bandwidth_hz=10_000)
        b = SpectrumPeak(freq_hz=100_000_000, power_dbm=-50.0, bandwidth_hz=10_000)
        c = SpectrumPeak(freq_hz=200_000_000, power_dbm=-50.0, bandwidth_hz=10_000)
        assert a == b
        assert a != c


# ============================================================================
# SpectrumAnalysis
# ============================================================================


class TestSpectrumAnalysis:
    def test_minimal_construction(self):
        sa = SpectrumAnalysis(
            freq_start_hz=100_000_000,
            freq_end_hz=200_000_000,
            resolution_bin_hz=1_000,
        )
        assert sa.freq_start_hz == 100_000_000
        assert sa.freq_end_hz == 200_000_000
        assert sa.resolution_bin_hz == 1_000
        assert sa.peaks == []
        assert sa.bands == []
        assert sa.noise_floor_dbm is None
        assert sa.scan_timestamp is None

    def test_full_construction(self):
        peaks = [
            SpectrumPeak(freq_hz=146_520_000, power_dbm=-40.0, bandwidth_hz=12_500),
            SpectrumPeak(freq_hz=446_000_000, power_dbm=-55.0, bandwidth_hz=25_000),
        ]
        bands = [
            SpectrumBand(start_hz=144_000_000, end_hz=148_000_000, label="2m"),
            SpectrumBand(start_hz=430_000_000, end_hz=450_000_000, label="70cm"),
        ]
        sa = SpectrumAnalysis(
            freq_start_hz=30_000_000,
            freq_end_hz=500_000_000,
            resolution_bin_hz=10_000,
            peaks=peaks,
            bands=bands,
            noise_floor_dbm=-110.0,
            scan_timestamp=1735689600.0,
        )
        assert len(sa.peaks) == 2
        assert len(sa.bands) == 2
        assert sa.noise_floor_dbm == -110.0
        assert sa.scan_timestamp == 1735689600.0

    def test_freq_range_properties(self):
        sa = SpectrumAnalysis(
            freq_start_hz=88_000_000,
            freq_end_hz=108_000_000,
            resolution_bin_hz=100_000,
        )
        assert sa.bandwidth_hz == 20_000_000
        assert sa.center_freq_hz == 98_000_000
        assert sa.num_bins == 200

    def test_invalid_freq_range_raises(self):
        with pytest.raises(ValueError, match="less than freq_end_hz"):
            SpectrumAnalysis(
                freq_start_hz=200_000_000,
                freq_end_hz=100_000_000,
                resolution_bin_hz=1_000,
            )

    def test_resolution_must_be_positive(self):
        with pytest.raises(ValueError, match="positive"):
            SpectrumAnalysis(
                freq_start_hz=100_000_000,
                freq_end_hz=200_000_000,
                resolution_bin_hz=0,
            )

    def test_resolution_not_larger_than_span(self):
        with pytest.raises(ValueError, match="less than or equal to the span"):
            SpectrumAnalysis(
                freq_start_hz=100_000_000,
                freq_end_hz=100_100_000,
                resolution_bin_hz=200_000,
            )

    def test_add_peak(self):
        sa = SpectrumAnalysis(
            freq_start_hz=100_000_000,
            freq_end_hz=200_000_000,
            resolution_bin_hz=1_000,
        )
        assert len(sa.peaks) == 0
        sa.add_peak(freq_hz=146_520_000, power_dbm=-45.0, bandwidth_hz=12_500)
        assert len(sa.peaks) == 1
        assert sa.peaks[0].freq_hz == 146_520_000

    def test_add_band(self):
        sa = SpectrumAnalysis(
            freq_start_hz=100_000_000,
            freq_end_hz=200_000_000,
            resolution_bin_hz=1_000,
        )
        sa.add_band(start_hz=144_000_000, end_hz=148_000_000, label="2m Amateur")
        assert len(sa.bands) == 1
        assert sa.bands[0].label == "2m Amateur"

    def test_find_peaks_above_threshold(self):
        sa = SpectrumAnalysis(
            freq_start_hz=100_000_000,
            freq_end_hz=200_000_000,
            resolution_bin_hz=1_000,
        )
        sa.add_peak(freq_hz=110_000_000, power_dbm=-30.0, bandwidth_hz=10_000)
        sa.add_peak(freq_hz=150_000_000, power_dbm=-60.0, bandwidth_hz=10_000)
        sa.add_peak(freq_hz=180_000_000, power_dbm=-75.0, bandwidth_hz=10_000)
        strong = sa.peaks_above_threshold(threshold_dbm=-50.0)
        assert len(strong) == 1
        assert strong[0].freq_hz == 110_000_000

    def test_find_bands_containing(self):
        sa = SpectrumAnalysis(
            freq_start_hz=100_000_000,
            freq_end_hz=200_000_000,
            resolution_bin_hz=1_000,
        )
        sa.add_band(start_hz=144_000_000, end_hz=148_000_000, label="2m")
        sa.add_band(start_hz=174_000_000, end_hz=216_000_000, label="VHF TV")
        matches = sa.bands_containing(146_000_000)
        assert len(matches) == 1
        assert matches[0].label == "2m"
        assert sa.bands_containing(50_000_000) == []

    def test_summary(self):
        sa = SpectrumAnalysis(
            freq_start_hz=88_000_000,
            freq_end_hz=108_000_000,
            resolution_bin_hz=200_000,
            noise_floor_dbm=-105.0,
        )
        sa.add_peak(freq_hz=98_500_000, power_dbm=-40.0, bandwidth_hz=200_000, modulation_guess="FM")
        sa.add_band(start_hz=88_000_000, end_hz=108_000_000, label="FM Broadcast")
        summary = sa.summary()
        assert summary["num_peaks"] == 1
        assert summary["num_bands"] == 1
        assert summary["bandwidth_hz"] == 20_000_000
        assert summary["center_freq_hz"] == 98_000_000
        assert summary["noise_floor_dbm"] == -105.0
        assert summary["peak_freqs"] == [98_500_000]
        assert summary["strongest_peak_dbm"] == -40.0

    def test_to_dict(self):
        sa = SpectrumAnalysis(
            freq_start_hz=30_000_000,
            freq_end_hz=60_000_000,
            resolution_bin_hz=500_000,
            noise_floor_dbm=-110.0,
            scan_timestamp=1735689600.0,
        )
        sa.add_peak(freq_hz=50_000_000, power_dbm=-45.0, bandwidth_hz=10_000)
        d = sa.to_dict()
        assert d["freq_start_hz"] == 30_000_000
        assert d["freq_end_hz"] == 60_000_000
        assert d["resolution_bin_hz"] == 500_000
        assert d["noise_floor_dbm"] == -110.0
        assert len(d["peaks"]) == 1
        assert len(d["bands"]) == 0

    def test_from_dict_minimal(self):
        d = {
            "freq_start_hz": 100_000_000,
            "freq_end_hz": 200_000_000,
            "resolution_bin_hz": 1_000,
        }
        sa = SpectrumAnalysis.from_dict(d)
        assert sa.freq_start_hz == 100_000_000
        assert sa.peaks == []
        assert sa.bands == []

    def test_from_dict_with_nested(self):
        d = {
            "freq_start_hz": 100_000_000,
            "freq_end_hz": 200_000_000,
            "resolution_bin_hz": 1_000,
            "noise_floor_dbm": -115.0,
            "peaks": [
                {"freq_hz": 150_000_000, "power_dbm": -40.0, "bandwidth_hz": 12_500, "snr_db": 25.0},
            ],
            "bands": [
                {"start_hz": 144_000_000, "end_hz": 148_000_000, "label": "2m"},
            ],
        }
        sa = SpectrumAnalysis.from_dict(d)
        assert len(sa.peaks) == 1
        assert sa.peaks[0].freq_hz == 150_000_000
        assert sa.peaks[0].snr_db == 25.0
        assert len(sa.bands) == 1
        assert sa.bands[0].label == "2m"

    def test_from_dict_missing_required_raises(self):
        with pytest.raises(ValueError, match="freq_start_hz"):
            SpectrumAnalysis.from_dict({"freq_end_hz": 200_000_000, "resolution_bin_hz": 1_000})

    def test_from_dict_unknown_keys_ignored(self):
        d = {
            "freq_start_hz": 100_000_000,
            "freq_end_hz": 200_000_000,
            "resolution_bin_hz": 1_000,
            "peaks": [],
            "bands": [],
            "extra_field": {"nested": True},
        }
        sa = SpectrumAnalysis.from_dict(d)
        assert sa.freq_start_hz == 100_000_000

    def test_json_roundtrip(self):
        sa = SpectrumAnalysis(
            freq_start_hz=1_000_000,
            freq_end_hz=30_000_000,
            resolution_bin_hz=100_000,
            noise_floor_dbm=-120.0,
            scan_timestamp=1735689600.0,
        )
        sa.add_peak(freq_hz=7_150_000, power_dbm=-50.0, bandwidth_hz=3_000, snr_db=20.0, modulation_guess="LSB")
        sa.add_peak(freq_hz=14_200_000, power_dbm=-45.0, bandwidth_hz=3_000, snr_db=25.0, modulation_guess="USB")
        sa.add_band(start_hz=7_000_000, end_hz=7_300_000, label="40m")
        json_str = json.dumps(sa.to_dict())
        recreated = SpectrumAnalysis.from_dict(json.loads(json_str))
        assert recreated.freq_start_hz == sa.freq_start_hz
        assert len(recreated.peaks) == 2
        assert len(recreated.bands) == 1
        assert recreated.peaks[0].modulation_guess == "LSB"

    def test_equality(self):
        a = SpectrumAnalysis(
            freq_start_hz=100_000_000,
            freq_end_hz=200_000_000,
            resolution_bin_hz=1_000,
        )
        b = SpectrumAnalysis(
            freq_start_hz=100_000_000,
            freq_end_hz=200_000_000,
            resolution_bin_hz=1_000,
        )
        c = SpectrumAnalysis(
            freq_start_hz=300_000_000,
            freq_end_hz=400_000_000,
            resolution_bin_hz=1_000,
        )
        assert a == b
        assert a != c


# ============================================================================
# Schema Version
# ============================================================================


class TestSchemaVersion:
    def test_schema_version_exists(self):
        from plugins.module_utils.contracts import SCHEMA_VERSION

        assert isinstance(SCHEMA_VERSION, str)

    def test_validate_contracts_schema_version(self):
        assert validate_contracts_schema_version("1.0") is True
        assert validate_contracts_schema_version("2.0") is False
        assert validate_contracts_schema_version("1.1") is False

    def test_validate_contracts_schema_version_bad_type(self):
        with pytest.raises(TypeError):
            validate_contracts_schema_version(1.0)  # type: ignore[arg-type]


# ============================================================================
# Immutability / Copy Protection (Deep copy on read)
# ============================================================================


class TestImmutabilityGuards:
    def test_frequency_allocation_privileges_copy_via_to_dict(self):
        fa = FrequencyAllocation(
            band_name="2m",
            start_hz=144_000_000,
            end_hz=148_000_000,
            country="US",
            itu_region=2,
            privileges=["CW", "SSB"],
        )
        d = fa.to_dict()
        d["privileges"].append("FM")
        assert fa.to_dict()["privileges"] == ["CW", "SSB"]

    def test_spectrum_analysis_peaks_copy_via_to_dict(self):
        sa = SpectrumAnalysis(
            freq_start_hz=100_000_000,
            freq_end_hz=200_000_000,
            resolution_bin_hz=1_000,
        )
        sa.add_peak(freq_hz=150_000_000, power_dbm=-40.0, bandwidth_hz=10_000)
        d = sa.to_dict()
        d["peaks"].clear()
        assert len(sa.to_dict()["peaks"]) == 1

    def test_spectrum_analysis_bands_copy_via_to_dict(self):
        sa = SpectrumAnalysis(
            freq_start_hz=100_000_000,
            freq_end_hz=200_000_000,
            resolution_bin_hz=1_000,
        )
        sa.add_band(start_hz=144_000_000, end_hz=148_000_000, label="2m")
        d = sa.to_dict()
        d["bands"].clear()
        assert len(sa.to_dict()["bands"]) == 1

    def test_signal_strength_immutable(self):
        ss = SignalStrength(rssi_dbm=-80.0, noise_floor_dbm=-110.0)
        d = ss.to_dict()
        d["rssi_dbm"] = 999.0
        assert ss.rssi_dbm == -80.0
