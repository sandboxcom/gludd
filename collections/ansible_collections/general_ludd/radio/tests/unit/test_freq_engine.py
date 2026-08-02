"""TDD tests for frequency allocation engine: band plan management,
compliance checking, frequency coordination, ITU region logic."""

from __future__ import annotations

import json

import pytest
from plugins.module_utils.freq_engine import (
    AllocationRequest,
    AllocationResult,
    BandPlan,
    ComplianceCheck,
    FreqEngine,
    allocate_frequency,
    check_compliance,
    compute_channel_spacing,
    find_allocated_bands,
    find_vacant_span,
    is_within_band,
    itu_region_for_country,
)

# ============================================================================
# BandPlan
# ============================================================================


class TestBandPlan:
    def test_minimal_construction(self):
        bp = BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2)
        assert bp.name == "2m"
        assert bp.start_hz == 144_000_000
        assert bp.end_hz == 148_000_000
        assert bp.itu_region == 2
        assert bp.service == "amateur"
        assert bp.max_power_w is None

    def test_full_construction(self):
        bp = BandPlan(
            name="70cm",
            start_hz=420_000_000,
            end_hz=450_000_000,
            itu_region=2,
            service="amateur",
            max_power_w=1500,
            privileges=["CW", "SSB", "FM", "Data"],
            notes="70cm primary amateur",
        )
        assert bp.service == "amateur"
        assert bp.max_power_w == 1500
        assert bp.privileges == ["CW", "SSB", "FM", "Data"]
        assert bp.notes == "70cm primary amateur"

    def test_bandwidth_hz(self):
        bp = BandPlan(name="6m", start_hz=50_000_000, end_hz=54_000_000, itu_region=2)
        assert bp.bandwidth_hz == 4_000_000

    def test_center_freq_hz(self):
        bp = BandPlan(name="10m", start_hz=28_000_000, end_hz=29_700_000, itu_region=2)
        assert bp.center_freq_hz == 28_850_000

    def test_contains_freq(self):
        bp = BandPlan(name="40m", start_hz=7_000_000, end_hz=7_300_000, itu_region=2)
        assert bp.contains(7_150_000) is True
        assert bp.contains(7_000_000) is True
        assert bp.contains(7_300_000) is True
        assert bp.contains(6_999_999) is False
        assert bp.contains(14_200_000) is False

    def test_overlaps(self):
        a = BandPlan(name="A", start_hz=100_000_000, end_hz=200_000_000, itu_region=2)
        b = BandPlan(name="B", start_hz=150_000_000, end_hz=250_000_000, itu_region=2)
        c = BandPlan(name="C", start_hz=300_000_000, end_hz=400_000_000, itu_region=2)
        assert a.overlaps(b) is True
        assert b.overlaps(a) is True
        assert a.overlaps(c) is False
        assert a.overlaps(a) is True

    def test_itu_region_validation(self):
        with pytest.raises(ValueError, match="1, 2, or 3"):
            BandPlan(name="bad", start_hz=100, end_hz=200, itu_region=0)
        with pytest.raises(ValueError, match="1, 2, or 3"):
            BandPlan(name="bad", start_hz=100, end_hz=200, itu_region=5)

    def test_freq_range_validation(self):
        with pytest.raises(ValueError, match="less than end_hz"):
            BandPlan(name="bad", start_hz=200_000_000, end_hz=100_000_000, itu_region=2)

    def test_to_dict(self):
        bp = BandPlan(
            name="80m",
            start_hz=3_500_000,
            end_hz=4_000_000,
            itu_region=2,
            service="amateur",
            max_power_w=1500,
            privileges=["CW", "SSB", "Data"],
        )
        d = bp.to_dict()
        assert d["name"] == "80m"
        assert d["start_hz"] == 3_500_000
        assert d["end_hz"] == 4_000_000
        assert d["itu_region"] == 2
        assert d["service"] == "amateur"
        assert d["max_power_w"] == 1500
        assert d["privileges"] == ["CW", "SSB", "Data"]
        assert "bandwidth_hz" in d
        assert "center_freq_hz" in d

    def test_from_dict(self):
        d = {
            "name": "23cm",
            "start_hz": 1_240_000_000,
            "end_hz": 1_300_000_000,
            "itu_region": 2,
            "service": "amateur",
            "max_power_w": 1500,
        }
        bp = BandPlan.from_dict(d)
        assert bp.name == "23cm"
        assert bp.itu_region == 2
        assert bp.privileges == []

    def test_json_roundtrip(self):
        bp = BandPlan(
            name="15m",
            start_hz=21_000_000,
            end_hz=21_450_000,
            itu_region=1,
            service="amateur",
            max_power_w=750,
            privileges=["CW", "SSB"],
        )
        recreated = BandPlan.from_dict(json.loads(json.dumps(bp.to_dict())))
        assert recreated.name == bp.name
        assert recreated.start_hz == bp.start_hz
        assert recreated.end_hz == bp.end_hz
        assert recreated.privileges == bp.privileges


# ============================================================================
# AllocationRequest / AllocationResult / ComplianceCheck
# ============================================================================


class TestAllocationRequest:
    def test_construction(self):
        req = AllocationRequest(
            center_freq_hz=146_520_000,
            bandwidth_hz=12_500,
            service="amateur",
            itu_region=2,
        )
        assert req.center_freq_hz == 146_520_000
        assert req.bandwidth_hz == 12_500
        assert req.country == "US"
        assert req.priority == "normal"

    def test_start_end_freqs(self):
        req = AllocationRequest(
            center_freq_hz=100_000_000,
            bandwidth_hz=20_000,
            itu_region=2,
        )
        assert req.start_freq_hz == 99_990_000
        assert req.end_freq_hz == 100_010_000

    def test_to_dict(self):
        req = AllocationRequest(
            center_freq_hz=446_000_000,
            bandwidth_hz=25_000,
            service="amateur",
            itu_region=2,
            country="CA",
            priority="high",
        )
        d = req.to_dict()
        assert d["center_freq_hz"] == 446_000_000
        assert d["bandwidth_hz"] == 25_000
        assert d["country"] == "CA"
        assert d["priority"] == "high"


class TestAllocationResult:
    def test_construction(self):
        result = AllocationResult(
            approved=True,
            center_freq_hz=146_520_000,
            bandwidth_hz=12_500,
            reason="Allocated in 2m band",
            band_name="2m",
        )
        assert result.approved is True
        assert result.center_freq_hz == 146_520_000
        assert result.band_name == "2m"
        assert result.interference_warning is None

    def test_denied_result(self):
        result = AllocationResult(
            approved=False,
            center_freq_hz=100_000_000,
            bandwidth_hz=50_000,
            reason="Frequency outside amateur allocations",
        )
        assert result.approved is False
        assert result.band_name is None

    def test_to_dict(self):
        result = AllocationResult(
            approved=True,
            center_freq_hz=7_150_000,
            bandwidth_hz=3_000,
            reason="Allocated in 40m band",
            band_name="40m",
            max_power_w=1500,
            interference_warning="Adjacent to broadcast band",
        )
        d = result.to_dict()
        assert d["approved"] is True
        assert d["center_freq_hz"] == 7_150_000
        assert d["band_name"] == "40m"
        assert d["interference_warning"] is not None


class TestComplianceCheck:
    def test_construction(self):
        cc = ComplianceCheck(passes=True, rules_checked=3, violations=[])
        assert cc.passes is True
        assert cc.rules_checked == 3
        assert cc.violations == []

    def test_with_violations(self):
        cc = ComplianceCheck(
            passes=False,
            rules_checked=3,
            violations=["Power exceeds band limit", "Frequency outside allocation"],
        )
        assert cc.passes is False
        assert len(cc.violations) == 2

    def test_to_dict(self):
        cc = ComplianceCheck(passes=True, rules_checked=2, violations=[], notes="All clear")
        d = cc.to_dict()
        assert d["passes"] is True
        assert d["rules_checked"] == 2
        assert d["violations"] == []
        assert d["notes"] == "All clear"


# ============================================================================
# is_within_band
# ============================================================================


class TestIsWithinBand:
    def test_within(self):
        assert is_within_band(146_000_000, start_hz=144_000_000, end_hz=148_000_000) is True
        assert is_within_band(144_000_000, start_hz=144_000_000, end_hz=148_000_000) is True
        assert is_within_band(148_000_000, start_hz=144_000_000, end_hz=148_000_000) is True

    def test_outside(self):
        assert is_within_band(143_000_000, start_hz=144_000_000, end_hz=148_000_000) is False
        assert is_within_band(149_000_000, start_hz=144_000_000, end_hz=148_000_000) is False


# ============================================================================
# find_allocated_bands
# ============================================================================


class TestFindAllocatedBands:
    def test_finds_matching_bands(self):
        bands = [
            BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2),
            BandPlan(name="70cm", start_hz=420_000_000, end_hz=450_000_000, itu_region=2),
        ]
        result = find_allocated_bands(146_000_000, bands)
        assert len(result) == 1
        assert result[0].name == "2m"

        result = find_allocated_bands(440_000_000, bands)
        assert len(result) == 1
        assert result[0].name == "70cm"

    def test_edge_hits(self):
        bands = [
            BandPlan(name="10m", start_hz=28_000_000, end_hz=29_700_000, itu_region=2),
        ]
        assert len(find_allocated_bands(28_000_000, bands)) == 1
        assert len(find_allocated_bands(29_700_000, bands)) == 1

    def test_no_match(self):
        bands = [
            BandPlan(name="6m", start_hz=50_000_000, end_hz=54_000_000, itu_region=2),
        ]
        assert find_allocated_bands(7_000_000, bands) == []

    def test_empty_bands(self):
        assert find_allocated_bands(146_000_000, []) == []


# ============================================================================
# check_compliance
# ============================================================================


class TestCheckCompliance:
    def test_passes_for_valid_allocation(self):
        bands = [
            BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2, max_power_w=1500),
        ]
        result = check_compliance(146_520_000, 50, bands)
        assert result.passes is True
        assert result.rules_checked >= 1

    def test_fails_if_no_matching_band(self):
        bands = [
            BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2),
        ]
        result = check_compliance(440_000_000, 50, bands)
        assert result.passes is False
        assert any("no matching band" in v.lower() for v in result.violations)

    def test_power_exceeded(self):
        bands = [
            BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2, max_power_w=100),
        ]
        result = check_compliance(146_000_000, 500, bands)
        assert result.passes is False
        assert any("power" in v.lower() for v in result.violations)


# ============================================================================
# allocate_frequency
# ============================================================================


class TestAllocateFrequency:
    def test_successful_allocation(self):
        bands = [
            BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2, max_power_w=1500),
        ]
        req = AllocationRequest(
            center_freq_hz=146_000_000,
            bandwidth_hz=12_500,
            itu_region=2,
        )
        result = allocate_frequency(req, bands)
        assert result.approved is True
        assert result.center_freq_hz == 146_000_000
        assert result.band_name == "2m"

    def test_denied_outside_bands(self):
        bands = [
            BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2),
        ]
        req = AllocationRequest(
            center_freq_hz=500_000_000,
            bandwidth_hz=12_500,
            itu_region=2,
        )
        result = allocate_frequency(req, bands)
        assert result.approved is False

    def test_max_power_propagates(self):
        bands = [
            BandPlan(name="70cm", start_hz=420_000_000, end_hz=450_000_000, itu_region=2, max_power_w=1500),
        ]
        req = AllocationRequest(
            center_freq_hz=446_000_000,
            bandwidth_hz=25_000,
            itu_region=2,
        )
        result = allocate_frequency(req, bands)
        assert result.approved is True
        assert result.max_power_w == 1500


# ============================================================================
# compute_channel_spacing
# ============================================================================


class TestComputeChannelSpacing:
    def test_standard_fm_spacing(self):
        spacing = compute_channel_spacing(bandwidth_hz=12_500, guard_band_hz=2_500)
        assert spacing == 15_000

    def test_narrowband_spacing(self):
        spacing = compute_channel_spacing(bandwidth_hz=6_250, guard_band_hz=6_250)
        assert spacing == 12_500

    def test_no_guard_band(self):
        spacing = compute_channel_spacing(bandwidth_hz=25_000, guard_band_hz=0)
        assert spacing == 25_000

    def test_negative_guard_band(self):
        with pytest.raises(ValueError, match="non-negative"):
            compute_channel_spacing(bandwidth_hz=10_000, guard_band_hz=-100)


# ============================================================================
# find_vacant_span
# ============================================================================


class TestFindVacantSpan:
    def test_finds_vacant_span(self):
        occupied = [(100_000_000, 102_000_000), (110_000_000, 112_000_000)]
        vacants = find_vacant_span(
            start_hz=95_000_000,
            end_hz=120_000_000,
            occupied_ranges=occupied,
            min_bandwidth_hz=1_000_000,
        )
        assert len(vacants) >= 1
        assert any(v["start_hz"] >= 102_000_000 and v["end_hz"] <= 110_000_000 for v in vacants)

    def test_no_vacant_span_large_enough(self):
        occupied = [(100_000_000, 110_000_000)]
        vacants = find_vacant_span(
            start_hz=100_000_000,
            end_hz=110_000_000,
            occupied_ranges=occupied,
            min_bandwidth_hz=5_000_000,
        )
        assert vacants == []

    def test_full_range_vacant(self):
        vacants = find_vacant_span(
            start_hz=144_000_000,
            end_hz=148_000_000,
            occupied_ranges=[],
            min_bandwidth_hz=1_000_000,
        )
        assert len(vacants) >= 1


# ============================================================================
# itu_region_for_country
# ============================================================================


class TestITURegionForCountry:
    def test_us_is_region_2(self):
        assert itu_region_for_country("US") == 2

    def test_de_is_region_1(self):
        assert itu_region_for_country("DE") == 1
        assert itu_region_for_country("GB") == 1
        assert itu_region_for_country("FR") == 1

    def test_jp_is_region_3(self):
        assert itu_region_for_country("JP") == 3
        assert itu_region_for_country("AU") == 3

    def test_none_for_unknown(self):
        assert itu_region_for_country("ZZ") is None


# ============================================================================
# FreqEngine Orchestrator
# ============================================================================


class TestFreqEngine:
    def test_construction(self):
        engine = FreqEngine(itu_region=2)
        assert engine.itu_region == 2
        assert engine.bands == []

    def test_register_band(self):
        engine = FreqEngine(itu_region=2)
        bp = BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2)
        engine.register_band(bp)
        assert len(engine.bands) == 1

    def test_register_band_wrong_region(self):
        engine = FreqEngine(itu_region=2)
        bp = BandPlan(name="4m", start_hz=70_000_000, end_hz=70_500_000, itu_region=1)
        with pytest.raises(ValueError, match="does not match engine region"):
            engine.register_band(bp)

    def test_lookup_frequency(self):
        engine = FreqEngine(itu_region=2)
        engine.register_band(BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2))
        result = engine.lookup(146_520_000)
        assert result is not None
        assert result.name == "2m"

    def test_lookup_miss(self):
        engine = FreqEngine(itu_region=2)
        result = engine.lookup(500_000_000)
        assert result is None

    def test_request_allocation(self):
        engine = FreqEngine(itu_region=2)
        engine.register_band(
            BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2, max_power_w=1500)
        )
        req = AllocationRequest(center_freq_hz=146_520_000, bandwidth_hz=12_500, itu_region=2)
        result = engine.request_allocation(req)
        assert result.approved is True
        assert result.center_freq_hz == 146_520_000

    def test_list_bands(self):
        engine = FreqEngine(itu_region=2)
        engine.register_band(BandPlan(name="6m", start_hz=50_000_000, end_hz=54_000_000, itu_region=2))
        engine.register_band(BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2))
        names = engine.list_bands()
        assert "6m" in names
        assert "2m" in names

    def test_to_dict(self):
        engine = FreqEngine(itu_region=2)
        engine.register_band(
            BandPlan(name="2m", start_hz=144_000_000, end_hz=148_000_000, itu_region=2, max_power_w=1500)
        )
        d = engine.to_dict()
        assert d["itu_region"] == 2
        assert len(d["bands"]) == 1
        assert d["bands"][0]["name"] == "2m"
