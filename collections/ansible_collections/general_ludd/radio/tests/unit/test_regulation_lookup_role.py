"""Tests for regulation_lookup role — validates task YAML structure, param validation, result shape."""

from __future__ import annotations

from pathlib import Path

import yaml
from plugins.module_utils.frequency_allocations import (
    bands_by_privilege,
    get_band_plan,
    get_itu_region2_bands,
    get_marine_channel,
    lookup_frequency,
)

_COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent


def test_regulation_lookup_tasks_file_exists():
    tasks = _COLLECTION_ROOT / "roles" / "regulation_lookup" / "tasks" / "main.yml"
    assert tasks.exists()


def test_regulation_lookup_tasks_has_validate_step():
    tasks = _COLLECTION_ROOT / "roles" / "regulation_lookup" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "regulation_lookup_country" in content
    assert "regulation_lookup_freq_mhz" in content
    assert "regulation_lookup_band_name" in content


def test_regulation_lookup_tasks_has_lookup_step():
    tasks = _COLLECTION_ROOT / "roles" / "regulation_lookup" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "general_ludd.radio.radio_runtime:" in content
    assert "operation: regulation_lookup" in content
    assert "freq_mhz:" in content
    assert "band_name:" in content
    assert "artifact_content" in content


def test_regulation_lookup_tasks_has_privilege_query():
    tasks = _COLLECTION_ROOT / "roles" / "regulation_lookup" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "license_class:" in content
    assert "regulation_lookup_license_class" in content


def test_regulation_lookup_tasks_has_service_filter():
    tasks = _COLLECTION_ROOT / "roles" / "regulation_lookup" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "marine_channel:" in content
    assert "regulation_lookup_marine_channel" in content


def test_regulation_lookup_tasks_has_verdict():
    tasks = _COLLECTION_ROOT / "roles" / "regulation_lookup" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "regulation_lookup_verdict" in content
    assert "role: regulation_lookup" in content


def test_regulation_lookup_defaults_file_exists():
    defaults = _COLLECTION_ROOT / "roles" / "regulation_lookup" / "defaults" / "main.yml"
    assert defaults.exists()
    data = yaml.safe_load(defaults.read_text())
    assert "regulation_lookup_enabled" in data
    assert "regulation_lookup_country" in data


def test_default_country_is_us():
    defaults = _COLLECTION_ROOT / "roles" / "regulation_lookup" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["regulation_lookup_country"] == "US"


def test_lookup_frequency_us_2m():
    result = lookup_frequency(146.520, "US")
    assert result is not None
    assert result["band_name"] == "2m"
    assert result["type"] == "amateur"
    assert "technician" in result
    assert result["technician"]["max_power_w"] > 0


def test_lookup_frequency_us_20m():
    result = lookup_frequency(14.250, "US")
    assert result is not None
    assert result["band_name"] == "20m"
    assert result["technician"]["max_power_w"] == 0
    assert result["general"]["max_power_w"] == 1500


def test_lookup_frequency_ca_20m():
    result = lookup_frequency(14.250, "CA")
    assert result is not None
    assert result["band_name"] == "20m"
    assert result["country"] == "CA"
    assert result["technician"]["max_power_w"] > 0


def test_lookup_frequency_marine_ch16():
    result = lookup_frequency(156.800, "FR")
    assert result is not None
    assert result["type"] == "marine_vhf"
    assert result["channel"] == 16
    assert "DISTRESS" in result["use"]


def test_get_band_plan_returns_license_classes():
    plan = get_band_plan("2m", "US")
    assert plan is not None
    for cls_name in ("technician", "general", "extra"):
        assert cls_name in plan
        assert "privileges" in plan[cls_name]
        assert "max_power_w" in plan[cls_name]


def test_bands_by_privilege_technician_us():
    bands = bands_by_privilege("US", "technician")
    assert len(bands) > 0
    for b in bands:
        assert b["max_power_w"] > 0


def test_bands_by_privilege_extra_us_has_all():
    tech = {b["band_name"] for b in bands_by_privilege("US", "technician")}
    extra = {b["band_name"] for b in bands_by_privilege("US", "extra")}
    assert tech.issubset(extra)


def test_itu_region2_bands_covers_major_hf():
    bands = get_itu_region2_bands()
    band_names = {b["band"] for b in bands}
    for expected in ("160m", "80m", "40m", "20m", "15m", "10m"):
        assert expected in band_names


def test_regulation_result_shape():
    freq_result = lookup_frequency(146.520, "US")
    plan = get_band_plan("2m", "US")
    privs = bands_by_privilege("US", "technician")

    combined = {
        "frequency_lookup": freq_result,
        "band_plan": {"band": "2m", "start_hz": plan["start_hz"], "end_hz": plan["end_hz"]},
        "license_class_privileges": {
            "license_class": "technician",
            "bands_with_privileges": privs,
        },
    }
    assert combined["frequency_lookup"]["band_name"] == "2m"
    assert combined["license_class_privileges"]["license_class"] == "technician"
    assert len(combined["license_class_privileges"]["bands_with_privileges"]) > 0


def test_marine_channel_70_is_dsc():
    ch = get_marine_channel(70)
    assert ch is not None
    assert "DSC" in ch["use"]
    assert "NO VOICE" in ch["use"]


def test_lookup_null_for_out_of_band():
    result = lookup_frequency(100.0, "US")
    assert result is None


def test_regulation_lookup_meta_exists():
    meta = _COLLECTION_ROOT / "roles" / "regulation_lookup" / "meta" / "main.yml"
    assert meta.exists()
    data = yaml.safe_load(meta.read_text())
    assert data["galaxy_info"]["role_name"] == "regulation_lookup"
