"""Tests for frequency_allocations module."""

from __future__ import annotations

import pytest

from plugins.module_utils.frequency_allocations import (
    ALLOCATIONS,
    allocations_for,
    bands_in_range,
    lookup_frequency,
    get_band_plan,
    get_marine_channel,
    get_itu_region2_bands,
    get_itu_region1_bands,
    get_itu_region3_bands,
    get_itu_bands,
    ITU_R1_BANDS,
    ITU_R3_BANDS,
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


# ── ITU Region 1 (Europe / Africa / Middle East / Northern Asia) ──


def test_get_itu_region1_bands():
    bands = get_itu_region1_bands()
    assert len(bands) >= 10
    band_names = {b["band"] for b in bands}
    known = {"160m", "80m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "2m", "70cm"}
    for k in known:
        assert k in band_names


def test_itu_r1_bands_have_hz_ranges():
    for b in get_itu_region1_bands():
        assert b["start_hz"] > 0
        assert b["end_hz"] > b["start_hz"]


def test_itu_r1_80m_is_narrower_than_r2():
    """Region 1 80m is 3.500-3.800 MHz (shared with broadcast in R1).
    Region 2 80m extends to 4.000 MHz."""
    r1_80m = next(b for b in ITU_R1_BANDS if b["band"] == "80m")
    r2_80m = next(b for b in get_itu_region2_bands() if b["band"] == "80m")
    assert r1_80m["end_hz"] == 3_800_000
    assert r2_80m["end_hz"] == 4_000_000
    assert r1_80m["end_hz"] < r2_80m["end_hz"]


def test_itu_r1_40m_is_narrower_than_r2():
    """Region 1 40m is 7.000-7.200 MHz; 7200-7300 is broadcast in R1.
    Region 2 40m is 7.000-7.300 MHz."""
    r1_40m = next(b for b in ITU_R1_BANDS if b["band"] == "40m")
    r2_40m = next(b for b in get_itu_region2_bands() if b["band"] == "40m")
    assert r1_40m["end_hz"] == 7_200_000
    assert r2_40m["end_hz"] == 7_300_000


def test_itu_r1_20m_is_narrower_than_r2():
    """Region 1 20m phone band ends at 14.250 MHz; 14.250-14.350 is broadcast.
    Region 2 extends to 14.350 MHz."""
    r1_20m = next(b for b in ITU_R1_BANDS if b["band"] == "20m")
    r2_20m = next(b for b in get_itu_region2_bands() if b["band"] == "20m")
    assert r1_20m["end_hz"] == 14_250_000
    assert r2_20m["end_hz"] == 14_350_000


def test_itu_r1_2m_is_narrower_than_r2():
    """Region 1 2m is 144-146 MHz; Region 2 is 144-148 MHz."""
    r1_2m = next(b for b in ITU_R1_BANDS if b["band"] == "2m")
    r2_2m = next(b for b in get_itu_region2_bands() if b["band"] == "2m")
    assert r1_2m["end_hz"] == 146_000_000
    assert r2_2m["end_hz"] == 148_000_000


def test_itu_r1_has_4m_band_unique_to_region1():
    """70-70.5 MHz (4 meters) is a Region 1 only allocation (UK, Ireland)."""
    band_names = {b["band"] for b in ITU_R1_BANDS}
    assert "4m" in band_names
    band_4m = next(b for b in ITU_R1_BANDS if b["band"] == "4m")
    assert band_4m["start_hz"] == 70_000_000
    assert band_4m["end_hz"] == 70_500_000


def test_itu_r1_70cm_is_narrower_than_r2():
    """Region 1 70cm is 430-440 MHz; Region 2 is 420-450 MHz."""
    r1_70cm = next(b for b in ITU_R1_BANDS if b["band"] == "70cm")
    assert r1_70cm["start_hz"] == 430_000_000
    assert r1_70cm["end_hz"] == 440_000_000


# ── ITU Region 3 (Asia-Pacific / Oceania) ──


def test_get_itu_region3_bands():
    bands = get_itu_region3_bands()
    assert len(bands) >= 10
    band_names = {b["band"] for b in bands}
    known = {"160m", "80m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "2m", "70cm"}
    for k in known:
        assert k in band_names


def test_itu_r3_bands_have_hz_ranges():
    for b in get_itu_region3_bands():
        assert b["start_hz"] > 0
        assert b["end_hz"] > b["start_hz"]


def test_itu_r3_80m_between_r1_and_r2():
    """Region 3 80m is 3.500-3.900 MHz.
    Region 1 ends at 3.800, Region 2 at 4.000; Region 3 is between."""
    r3_80m = next(b for b in ITU_R3_BANDS if b["band"] == "80m")
    r1_80m = next(b for b in ITU_R1_BANDS if b["band"] == "80m")
    r2_80m = next(b for b in get_itu_region2_bands() if b["band"] == "80m")
    assert r3_80m["end_hz"] == 3_900_000
    assert r1_80m["end_hz"] < r3_80m["end_hz"] < r2_80m["end_hz"]


def test_itu_r3_40m_same_as_r1():
    """Both R1 and R3 have 40m at 7.000-7.200 MHz (broadcast above)."""
    r3_40m = next(b for b in ITU_R3_BANDS if b["band"] == "40m")
    r1_40m = next(b for b in ITU_R1_BANDS if b["band"] == "40m")
    assert r3_40m["end_hz"] == 7_200_000
    assert r3_40m["end_hz"] == r1_40m["end_hz"]


def test_itu_r3_20m_same_as_r1():
    """R3 20m ends at 14.250 (same as R1)."""
    r3_20m = next(b for b in ITU_R3_BANDS if b["band"] == "20m")
    assert r3_20m["end_hz"] == 14_250_000


def test_itu_r3_2m_same_as_r2():
    """Region 3 2m is 144-148 MHz (matches Region 2)."""
    r3_2m = next(b for b in ITU_R3_BANDS if b["band"] == "2m")
    assert r3_2m["start_hz"] == 144_000_000
    assert r3_2m["end_hz"] == 148_000_000


def test_itu_r3_70cm_same_as_r2():
    """Region 3 70cm is 420-450 MHz (matches Region 2)."""
    r3_70cm = next(b for b in ITU_R3_BANDS if b["band"] == "70cm")
    assert r3_70cm["start_hz"] == 420_000_000
    assert r3_70cm["end_hz"] == 450_000_000


def test_itu_r3_has_no_4m_band():
    """4m is a Region 1-only allocation; absent in R2 and R3."""
    band_names = {b["band"] for b in ITU_R3_BANDS}
    assert "4m" not in band_names


# ── Unified get_itu_bands(region) accessor ──


@pytest.mark.parametrize("region,expected_count", [(1, 11), (2, 11), (3, 11)])
def test_get_itu_bands_returns_non_empty_for_each_region(region, expected_count):
    bands = get_itu_bands(region)
    assert len(bands) >= expected_count


def test_get_itu_bands_region1_matches_dedicated_function():
    assert get_itu_bands(1) == get_itu_region1_bands()


def test_get_itu_bands_region2_matches_dedicated_function():
    assert get_itu_bands(2) == get_itu_region2_bands()


def test_get_itu_bands_region3_matches_dedicated_function():
    assert get_itu_bands(3) == get_itu_region3_bands()


def test_get_itu_bands_default_is_region2():
    """Default region is 2 (backwards compatibility)."""
    assert get_itu_bands() == get_itu_region2_bands()


def test_get_itu_bands_invalid_region_raises():
    with pytest.raises(ValueError):
        get_itu_bands(4)


def test_get_itu_bands_invalid_region_zero_raises():
    with pytest.raises(ValueError):
        get_itu_bands(0)


def test_itu_r1_bands_tagged_with_region():
    """Each band carries its source region for downstream consumers."""
    for b in ITU_R1_BANDS:
        assert b.get("region") == 1


def test_itu_r3_bands_tagged_with_region():
    for b in ITU_R3_BANDS:
        assert b.get("region") == 3
