"""Tests for spectrum_scan role — validates task YAML, sweep logic, band classification."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import yaml

_COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent


def _add_role_files_to_path():
    role_files = str(_COLLECTION_ROOT / "roles" / "spectrum_scan" / "files")
    if role_files not in sys.path:
        sys.path.insert(0, role_files)


def test_spectrum_scan_tasks_file_exists():
    tasks = _COLLECTION_ROOT / "roles" / "spectrum_scan" / "tasks" / "main.yml"
    assert tasks.exists()


def test_spectrum_scan_tasks_has_validate_step():
    tasks = _COLLECTION_ROOT / "roles" / "spectrum_scan" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "spectrum_scan_start_freq_hz" in content
    assert "spectrum_scan_end_freq_hz" in content
    assert "spectrum_scan_bin_size_hz" in content


def test_spectrum_scan_tasks_has_verdict():
    tasks = _COLLECTION_ROOT / "roles" / "spectrum_scan" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "spectrum_scan_verdict" in content
    assert "role: spectrum_scan" in content
    assert "signals_detected" in content
    assert "bandwidth_mhz" in content


def test_spectrum_scan_tasks_has_tool_config():
    tasks = _COLLECTION_ROOT / "roles" / "spectrum_scan" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "--tool" in content
    assert "--tool-path" in content
    assert "--start-freq" in content
    assert "spectrum_scan_tool_path" in content


def test_spectrum_scan_tasks_calls_python_backend():
    tasks = _COLLECTION_ROOT / "roles" / "spectrum_scan" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "spectrum_scan.py" in content
    assert "--start-freq" in content
    assert "--end-freq" in content
    assert "--bin-size" in content
    assert "--integration-time" in content
    assert "--integration-time" in content
    assert "--gain" in content
    assert "--output-dir" in content
    assert "--tool" in content


def test_spectrum_scan_tasks_surfaces_band_occupancy():
    tasks = _COLLECTION_ROOT / "roles" / "spectrum_scan" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "band_occupancy" in content
    assert "noise_floor_dbm" in content


def test_spectrum_scan_defaults_file_exists():
    defaults = _COLLECTION_ROOT / "roles" / "spectrum_scan" / "defaults" / "main.yml"
    assert defaults.exists()
    data = yaml.safe_load(defaults.read_text())
    assert data["spectrum_scan_enabled"] is False
    assert data["spectrum_scan_start_freq_hz"] > 0
    assert data["spectrum_scan_end_freq_hz"] > data["spectrum_scan_start_freq_hz"]
    assert data["spectrum_scan_bin_size_hz"] > 0
    assert data["spectrum_scan_output_dir"]


def test_spectrum_scan_default_disabled():
    defaults = _COLLECTION_ROOT / "roles" / "spectrum_scan" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["spectrum_scan_enabled"] is False


def test_spectrum_scan_script_exists():
    script = _COLLECTION_ROOT / "roles" / "spectrum_scan" / "files" / "spectrum_scan.py"
    assert script.exists()
    content = script.read_text()
    assert "class ScanResult" in content
    assert "def sweep_spectrum" in content
    assert "KNOWN_BANDS" in content
    assert "def _synthesize_sweep" in content
    assert "def _classify_bands" in content


def test_known_bands_covers_major_ranges():
    _add_role_files_to_path()
    from spectrum_scan import KNOWN_BANDS

    names = {b["name"] for b in KNOWN_BANDS}
    assert "FM Broadcast" in names
    assert "Air Band" in names
    assert "VHF-High" in names
    assert "UHF" in names
    assert "L-Band" in names
    for b in KNOWN_BANDS:
        assert b["start_hz"] < b["end_hz"]
        assert "typical_uses" in b


def test_scan_result_dataclass_to_dict():
    _add_role_files_to_path()
    from spectrum_scan import ScanResult

    sr = ScanResult(
        start_freq_hz=88_000_000,
        end_freq_hz=108_000_000,
        bin_size_hz=10_000,
        integration_time_ms=100,
        gain="auto",
        device_index=0,
        tool="rtl_power",
        output_dir="/tmp/test",
    )
    d = sr.to_dict()
    assert d["start_freq_hz"] == 88_000_000
    assert d["end_freq_hz"] == 108_000_000
    assert d["verdict"] == "skipped"
    assert "bandwidth_mhz" in d
    assert "peaks" in d
    assert "band_occupancy" in d


def test_scan_result_success_verdict():
    _add_role_files_to_path()
    from spectrum_scan import ScanResult

    sr = ScanResult(
        start_freq_hz=88_000_000,
        end_freq_hz=108_000_000,
        bin_size_hz=10_000,
        integration_time_ms=100,
        gain="auto",
        device_index=0,
        tool="rtl_power",
        output_dir="/tmp/test",
        rc=0,
    )
    assert sr.to_dict()["verdict"] == "success"


def test_synthesize_sweep_fm_band():
    _add_role_files_to_path()
    from spectrum_scan import _synthesize_sweep

    bins = _synthesize_sweep(98_000_000, 98_200_000, 10_000, noise_floor_dbm=-110.0)
    assert len(bins) == 20
    for b in bins:
        assert "freq_hz" in b
        assert "freq_mhz" in b
        assert "power_dbm" in b
    fm_signal_bins = [b for b in bins if abs(b["freq_hz"] - 98_100_000) < 75_000]
    assert len(fm_signal_bins) > 0
    assert any(b["power_dbm"] > -80.0 for b in fm_signal_bins)


def test_synthesize_sweep_vhf_amateur():
    _add_role_files_to_path()
    from spectrum_scan import _synthesize_sweep

    bins = _synthesize_sweep(144_000_000, 148_000_000, 10_000, noise_floor_dbm=-110.0)
    signals = [b for b in bins if b["power_dbm"] > -90.0]
    assert len(signals) > 0
    assert any(abs(b["freq_hz"] - 145_500_000) < 20_000 for b in signals)


def test_classify_bands_finds_fm():
    _add_role_files_to_path()
    from spectrum_scan import _classify_bands, _synthesize_sweep

    bins = _synthesize_sweep(87_500_000, 108_500_000, 50_000, noise_floor_dbm=-110.0)
    classified = _classify_bands(bins, -110.0)
    fm = [c for c in classified if c["band_name"] == "FM Broadcast"]
    assert len(fm) == 1
    assert fm[0]["bins_occupied"] > 0
    assert fm[0]["verdict"] == "active"


def test_classify_bands_quiet_region():
    _add_role_files_to_path()
    from spectrum_scan import _classify_bands, _synthesize_sweep

    bins = _synthesize_sweep(50_000_000, 54_000_000, 50_000, noise_floor_dbm=-110.0)
    classified = _classify_bands(bins, -110.0)
    for c in classified:
        assert "occupancy_pct" in c
        assert "peak_power_dbm" in c
        assert "avg_power_dbm" in c


def test_sweep_spectrum_no_tool_generates_synthetic():
    _add_role_files_to_path()
    from spectrum_scan import sweep_spectrum

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["SPECTRUM_SCAN_TOOL_PATH"] = ""
        result = sweep_spectrum(
            start_freq_hz=88_000_000,
            end_freq_hz=108_000_000,
            bin_size_hz=100_000,
            output_dir=tmpdir,
            tool="nonexistent_tool_xyz",
        )
    assert result["start_freq_hz"] == 88_000_000
    assert result["end_freq_hz"] == 108_000_000
    assert result["num_bins"] == 200
    assert "peaks" in result
    assert "band_occupancy" in result


def test_sweep_spectrum_calculates_bandwidth():
    _add_role_files_to_path()
    from spectrum_scan import sweep_spectrum

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["SPECTRUM_SCAN_TOOL_PATH"] = ""
        result = sweep_spectrum(
            start_freq_hz=400_000_000,
            end_freq_hz=470_000_000,
            bin_size_hz=100_000,
            output_dir=tmpdir,
            tool="nonexistent_tool_xyz",
        )
    assert result["bandwidth_mhz"] == 70.0


def test_spectrum_scan_meta_exists():
    meta = _COLLECTION_ROOT / "roles" / "spectrum_scan" / "meta" / "main.yml"
    assert meta.exists()
    data = yaml.safe_load(meta.read_text())
    assert data["galaxy_info"]["role_name"] == "spectrum_scan"
