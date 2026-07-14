"""Tests for modulation_schemes module."""

from __future__ import annotations

from plugins.module_utils.modulation_schemes import (
    MODULATION_SCHEMES,
    schemes_by_category,
    scheme_info,
    all_scheme_names,
    classify_signal,
)


def test_modulation_schemes_is_non_empty():
    assert isinstance(MODULATION_SCHEMES, list)
    assert len(MODULATION_SCHEMES) >= 20


def test_every_entry_has_required_keys():
    for s in MODULATION_SCHEMES:
        assert "scheme" in s
        assert "category" in s
        assert s["category"] in ("analog", "digital")
        assert "spectrum_shape" in s
        assert "typical_use" in s
        assert "bandwidth_hz_typical" in s


def test_scheme_names_are_unique():
    names = [s["scheme"] for s in MODULATION_SCHEMES]
    assert len(names) == len(set(names))


def test_schemes_by_category_analog():
    analog = schemes_by_category("analog")
    assert len(analog) >= 5
    for s in analog:
        assert s["category"] == "analog"


def test_schemes_by_category_digital():
    digital = schemes_by_category("digital")
    assert len(digital) >= 10
    for s in digital:
        assert s["category"] == "digital"


def test_schemes_by_category_unknown():
    assert schemes_by_category("quantum") == []


def test_scheme_info_known():
    info = scheme_info("DMR")
    assert info is not None
    assert info["category"] == "digital"
    assert "TDMA" in info["typical_use"]


def test_scheme_info_case_sensitive():
    assert scheme_info("dmr") is None


def test_scheme_info_nonexistent():
    assert scheme_info("nonexistent_scheme") is None


def test_all_scheme_names():
    names = all_scheme_names()
    assert len(names) >= 20
    assert "AM" in names
    assert "FM" in names
    assert "SSB-USB" in names
    assert "CW" in names
    assert "FT8" in names
    assert "DMR" in names
    assert "D-STAR" in names


def test_classify_signal_narrowband_fsk():
    results = classify_signal(bandwidth_hz=50.0, frequency_mhz=14.074)
    assert len(results) > 0
    names = {r["scheme"] for r in results}
    assert "FT8" in names, f"Expected FT8 in results, got {names}"


def test_classify_signal_wideband_fm():
    results = classify_signal(bandwidth_hz=12_500, spectrum_shape="fm")
    assert len(results) > 0
    names = {r["scheme"] for r in results}
    assert "FM" in names or "NBFM" in names


def test_classify_signal_dmr_narrowband():
    results = classify_signal(
        bandwidth_hz=6_250,
        symbol_rate_baud=4_800,
        spectrum_shape="tdma",
    )
    assert len(results) > 0
    names = {r["scheme"] for r in results}
    assert "DMR" in names, f"Expected DMR in results, got {names}"


def test_classify_signal_cw_narrow():
    results = classify_signal(bandwidth_hz=150, frequency_mhz=7.030)
    assert len(results) > 0
    names = {r["scheme"] for r in results}
    assert "CW" in names


def test_classify_signal_returns_sorted_by_score():
    results = classify_signal(bandwidth_hz=6_250, symbol_rate_baud=4_800)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_classify_signal_no_match():
    results = classify_signal(bandwidth_hz=2_000_000, symbol_rate_baud=2_000_000)
    assert isinstance(results, list)
    assert len(results) == 0


def test_classify_signal_hf_context_boosts():
    results = classify_signal(bandwidth_hz=2_700, spectrum_shape="sideband", frequency_mhz=14.200)
    assert len(results) > 0
    names = {r["scheme"] for r in results}
    assert "SSB-USB" in names or "SSB-LSB" in names


def test_contains_ft4():
    info = scheme_info("FT4")
    assert info is not None
    assert info["bandwidth_hz_typical"] == 90
    assert "contest" in info["typical_use"].lower()


def test_contains_wspr():
    info = scheme_info("WSPR")
    assert info is not None
    assert info["bandwidth_hz_typical"] == 6
