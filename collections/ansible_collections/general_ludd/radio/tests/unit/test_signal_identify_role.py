"""Tests for signal_identify role — validates task YAML structure, classification logic, result shape."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent


def _add_role_files_to_path():
    role_files = str(_COLLECTION_ROOT / "roles" / "signal_identify" / "files")
    if role_files not in sys.path:
        sys.path.insert(0, role_files)


def _add_module_utils_to_path():
    utils = str(_COLLECTION_ROOT / "plugins" / "module_utils")
    if utils not in sys.path:
        sys.path.insert(0, utils)


def test_signal_identify_tasks_file_exists():
    tasks = _COLLECTION_ROOT / "roles" / "signal_identify" / "tasks" / "main.yml"
    assert tasks.exists()


def test_signal_identify_tasks_has_validate_step():
    tasks = _COLLECTION_ROOT / "roles" / "signal_identify" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "signal_identify_sample_rate" in content
    assert "signal_identify_method" in content
    assert "signal_identify_center_freq_hz" in content


def test_signal_identify_tasks_calls_python_script():
    tasks = _COLLECTION_ROOT / "roles" / "signal_identify" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "general_ludd.radio.radio_runtime:" in content
    assert "operation: signal_identify" in content


def test_signal_identify_tasks_has_classify_call():
    tasks = _COLLECTION_ROOT / "roles" / "signal_identify" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "--bandwidth" in content or "signal_identify_bandwidth_hz" in content
    assert "--symbol-rate" in content or "signal_identify_symbol_rate_baud" in content
    assert "--spectrum-shape" in content or "signal_identify_spectrum_shape" in content


def test_signal_identify_tasks_has_verdict():
    tasks = _COLLECTION_ROOT / "roles" / "signal_identify" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "signal_identify_verdict" in content
    assert "role: signal_identify" in content
    assert "top_hit" in content
    assert "candidates" in content
    assert "verdict" in content


def test_signal_identify_defaults_file_exists():
    defaults = _COLLECTION_ROOT / "roles" / "signal_identify" / "defaults" / "main.yml"
    assert defaults.exists()
    data = yaml.safe_load(defaults.read_text())
    assert "signal_identify_enabled" in data
    assert "signal_identify_sample_rate" in data
    assert "signal_identify_center_freq_hz" in data
    assert "signal_identify_method" in data
    assert "signal_identify_output_dir" in data


def test_default_method_is_fft():
    defaults = _COLLECTION_ROOT / "roles" / "signal_identify" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["signal_identify_method"] in ("fft", "cyclostationary", "auto")


def test_default_sample_rate_is_positive():
    defaults = _COLLECTION_ROOT / "roles" / "signal_identify" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["signal_identify_sample_rate"] > 0


def test_signal_identify_script_exists():
    script = _COLLECTION_ROOT / "roles" / "signal_identify" / "files" / "signal_identify.py"
    assert script.exists()
    content = script.read_text()
    assert "plugins.module_utils.signal_identify_runtime" in content
    assert '"main"' in content


def test_classify_dmr_by_parameters():
    _add_module_utils_to_path()
    _add_role_files_to_path()
    from signal_identify import signal_identify

    result = signal_identify(
        bandwidth_hz=6_250,
        symbol_rate_baud=4_800,
        spectrum_shape="tdma_fsk",
        center_freq_hz=440_000_000,
    )
    assert result["verdict"] == "identified"
    assert result["classification"]["top_hit"] is not None
    candidates = {c["scheme"] for c in result["classification"]["candidates"]}
    assert "DMR" in candidates, f"Expected DMR in {candidates}"


def test_classify_narrowband_cw():
    _add_module_utils_to_path()
    _add_role_files_to_path()
    from signal_identify import signal_identify

    result = signal_identify(
        bandwidth_hz=150,
        center_freq_hz=7_030_000,
        spectrum_shape="carrier_on_off",
    )
    assert result["verdict"] == "identified"
    candidates = {c["scheme"] for c in result["classification"]["candidates"]}
    assert "CW" in candidates, f"Expected CW in {candidates}"


def test_classify_wideband_fm():
    _add_module_utils_to_path()
    _add_role_files_to_path()
    from signal_identify import signal_identify

    result = signal_identify(
        bandwidth_hz=12_500,
        spectrum_shape="fm",
        center_freq_hz=146_520_000,
    )
    assert result["verdict"] == "identified"
    names = {c["scheme"] for c in result["classification"]["candidates"]}
    assert ("FM" in names) or ("NBFM" in names)


def test_classify_no_match():
    _add_module_utils_to_path()
    _add_role_files_to_path()
    from signal_identify import signal_identify

    result = signal_identify(
        bandwidth_hz=2_000_000,
        symbol_rate_baud=2_000_000,
        center_freq_hz=2_000_000,
    )
    assert result["verdict"] == "unknown"
    assert result["classification"]["top_hit"] is None


def test_classify_returns_protocol_candidates():
    _add_module_utils_to_path()
    _add_role_files_to_path()
    from signal_identify import signal_identify

    result = signal_identify(
        bandwidth_hz=6_250,
        symbol_rate_baud=4_800,
        spectrum_shape="gmsk",
        center_freq_hz=440_000_000,
    )
    protos = [p["protocol"] for p in result["classification"]["protocol_candidates"]]
    assert len(protos) >= 1
    assert "D-STAR" in protos or "DMR" in protos


def test_classify_ssb_by_hf_context():
    _add_module_utils_to_path()
    _add_role_files_to_path()
    from signal_identify import signal_identify

    result = signal_identify(
        bandwidth_hz=2_700,
        spectrum_shape="sideband",
        center_freq_hz=14_200_000,
    )
    assert result["verdict"] == "identified"
    names = {c["scheme"] for c in result["classification"]["candidates"]}
    assert ("SSB-USB" in names) or ("SSB-LSB" in names)


def test_result_shape_has_all_fields():
    _add_module_utils_to_path()
    _add_role_files_to_path()
    from signal_identify import signal_identify

    result = signal_identify(bandwidth_hz=6_250, symbol_rate_baud=4_800)
    assert "role" in result
    assert "method" in result
    assert "input" in result
    assert "classification" in result
    assert "verdict" in result
    assert "candidates" in result["classification"]
    assert "top_hit" in result["classification"]
    assert "protocol_candidates" in result["classification"]
    for c in result["classification"]["candidates"]:
        assert "scheme" in c
        assert "confidence" in c
        assert 0.0 <= c["confidence"] <= 1.0


def test_signal_identify_meta_exists():
    meta = _COLLECTION_ROOT / "roles" / "signal_identify" / "meta" / "main.yml"
    assert meta.exists()
    data = yaml.safe_load(meta.read_text())
    assert data["galaxy_info"]["role_name"] == "signal_identify"
