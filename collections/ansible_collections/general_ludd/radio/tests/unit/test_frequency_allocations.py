"""Tests for frequency_allocations module."""

from __future__ import annotations

from plugins.module_utils.frequency_allocations import (
    ALLOCATIONS,
    allocations_for,
    bands_in_range,
    lookup_frequency,
    get_band_plan,
    get_marine_channel,
    get_itu_region2_bands,
    bands_by_privilege,
)


def test_allocations_is_non_empty_list():
    assert isinstance(ALLOCATIONS, list)
    assert len(ALLOCATIONS) >= 2


def test_allocations_for_us():
    bands = allocations_for("US")
    assert bands is not None
    known = {"160m", "80m", "40m", "20m", "15m", "10m", "6m", "2m", "70cm"}
    for b in known:
        assert b in bands


def test_allocations_for_ca():
    bands = allocations_for("CA")
    assert bands is not None
    known = {"160m", "80m", "40m", "20m", "15m", "10m", "6m", "2m", "70cm"}
    for b in known:
        assert b in bands


def test_allocations_for_unknown_country():
    assert allocations_for("atlantis") is None


def test_us_bands_have_license_classes():
    bands = allocations_for("US")
    for name, band in bands.items():
        assert "technician" in band
        assert "general" in band
        assert "extra" in band
        for cls_name in ("technician", "general", "extra"):
            cls_info = band[cls_name]
            assert "privileges" in cls_info
            assert "max_power_w" in cls_info
            if cls_info["max_power_w"] > 0:
                assert len(cls_info["privileges"]) > 0


def test_bands_in_range_overlap():
    result = bands_in_range(14_000_000, 14_350_000, "US")
    assert len(result) >= 1
    band_names = {b["display"] for b in result}
    assert any("20 meters" in n for n in band_names)


def test_bands_in_range_no_overlap():
    result = bands_in_range(100_000, 200_000, "US")
    assert result == []


def test_bands_in_range_unknown_country():
    result = bands_in_range(14_000_000, 14_350_000, "atlantis")
    assert result == []


def test_lookup_frequency_2m():
    result = lookup_frequency(146.520, "US")
    assert result is not None
    assert result["type"] == "amateur"
    assert result["band_name"] == "2m"


def test_lookup_frequency_20m_phone():
    result = lookup_frequency(14.250, "US")
    assert result is not None
    assert result["band_name"] == "20m"


def test_lookup_frequency_outside_amateur():
    result = lookup_frequency(100.0, "US")
    assert result is None


def test_lookup_frequency_marine_ch16():
    result = lookup_frequency(156.800, "FR")
    assert result is not None
    assert result["type"] == "marine_vhf"
    assert result["channel"] == 16


def test_lookup_frequency_unknown_country_falls_to_marine():
    result = lookup_frequency(156.800, "france")
    assert result is not None
    assert result["type"] == "marine_vhf"


def test_get_band_plan_us_10m():
    plan = get_band_plan("10m", "US")
    assert plan is not None
    assert plan["start_hz"] == 28_000_000
    assert plan["end_hz"] == 29_700_000


def test_get_band_plan_nonexistent():
    assert get_band_plan("200m", "US") is None


def test_get_band_plan_unknown_country():
    assert get_band_plan("10m", "atlantis") is None


def test_get_marine_channel_valid():
    ch = get_marine_channel(16)
    assert ch is not None
    assert ch["tx_mhz"] == 156.800
    assert ch["use"] and "DISTRESS" in ch["use"]


def test_get_marine_channel_70_dsc():
    ch = get_marine_channel(70)
    assert ch is not None
    assert "DSC" in ch["use"]


def test_get_marine_channel_invalid():
    assert get_marine_channel(999) is None


def test_get_itu_region2_bands():
    bands = get_itu_region2_bands()
    assert len(bands) >= 10
    known = {"160m", "80m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m"}
    band_names = {b["band"] for b in bands}
    for k in known:
        assert k in band_names


def test_itu_bands_have_hz_ranges():
    bands = get_itu_region2_bands()
    for b in bands:
        assert b["start_hz"] > 0
        assert b["end_hz"] > b["start_hz"]


def test_bands_by_privilege_us_technician():
    bands = bands_by_privilege("US", "technician")
    assert len(bands) > 0
    for b in bands:
        assert b["max_power_w"] > 0


def test_bands_by_privilege_us_technician_has_no_20m():
    bands = bands_by_privilege("US", "technician")
    band_names = {b["band_name"] for b in bands}
    assert "20m" not in band_names


def test_bands_by_privilege_us_general_has_20m():
    bands = bands_by_privilege("US", "general")
    band_names = {b["band_name"] for b in bands}
    assert "20m" in band_names


def test_bands_by_privilege_unknown_country():
    assert bands_by_privilege("atlantis", "general") == []


def test_us_10m_technician_privileges():
    plan = get_band_plan("10m", "US")
    assert plan is not None
    tech = plan["technician"]
    assert tech["max_power_w"] >= 200


def test_us_70cm_all_license_classes():
    plan = get_band_plan("70cm", "US")
    assert plan is not None
    for cls_name in ("technician", "general", "extra"):
        assert plan[cls_name]["max_power_w"] > 0
        assert len(plan[cls_name]["privileges"]) > 0
