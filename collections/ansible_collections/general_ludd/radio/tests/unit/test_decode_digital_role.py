"""Tests for decode_digital role — validates task YAML, decoder logic, result shape."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import yaml

_COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent


def _add_role_files_to_path():
    role_files = str(_COLLECTION_ROOT / "roles" / "decode_digital" / "files")
    if role_files not in sys.path:
        sys.path.insert(0, role_files)


def _generate_test_iq(duration_ms: float = 100.0, sample_rate: int = 9600) -> bytes:
    num_samples = int(sample_rate * duration_ms / 1000.0)
    samples = bytearray()
    for i in range(num_samples):
        val = 127 * (1 if (i % 20 < 10) else -1)
        le = struct.pack("<h", val)
        samples.extend(le)
        samples.extend(le)
    return bytes(samples)


def test_decode_digital_tasks_file_exists():
    tasks = _COLLECTION_ROOT / "roles" / "decode_digital" / "tasks" / "main.yml"
    assert tasks.exists()


def test_decode_digital_tasks_has_validate_step():
    tasks = _COLLECTION_ROOT / "roles" / "decode_digital" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "decode_digital_mode in [" in content
    assert "auto" in content
    assert "dmr" in content
    assert "p25" in content
    assert "nxdn" in content
    assert "dstar" in content
    assert "aprs" in content
    assert "ft8" in content
    assert "rtty" in content


def test_decode_digital_tasks_calls_python_script():
    tasks = _COLLECTION_ROOT / "roles" / "decode_digital" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "decode_digital.py" in content


def test_decode_digital_tasks_has_verdict():
    tasks = _COLLECTION_ROOT / "roles" / "decode_digital" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "decode_digital_verdict" in content
    assert "role: decode_digital" in content
    assert "ber_estimate" in content


def test_decode_digital_defaults_file_exists():
    defaults = _COLLECTION_ROOT / "roles" / "decode_digital" / "defaults" / "main.yml"
    assert defaults.exists()
    data = yaml.safe_load(defaults.read_text())
    assert "decode_digital_enabled" in data
    assert "decode_digital_mode" in data
    assert "decode_digital_sample_rate" in data
    assert "decode_digital_output_dir" in data


def test_default_mode_is_auto():
    defaults = _COLLECTION_ROOT / "roles" / "decode_digital" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["decode_digital_mode"] == "auto"


def test_decode_digital_script_exists():
    script = _COLLECTION_ROOT / "roles" / "decode_digital" / "files" / "decode_digital.py"
    assert script.exists()
    content = script.read_text()
    assert "def decode_dmr" in content
    assert "def decode_p25" in content
    assert "def decode_nxdn" in content
    assert "def decode_dstar" in content
    assert "def decode_aprs" in content
    assert "def decode_ft8" in content
    assert "def decode_rtty" in content
    assert "def decode_auto" in content


def test_decode_dmr_stub():
    _add_role_files_to_path()
    from decode_digital import decode_dmr

    data = _generate_test_iq(duration_ms=100.0, sample_rate=9600)
    result = decode_dmr(data, 9600)
    assert result["mode"] == "DMR"
    assert result["standard"] == "ETSI TS 102 361"
    assert "modulation" in result
    assert isinstance(result["sync_found"], bool)
    assert isinstance(result["ber_estimate"], float)
    assert "protocol_metadata" in result
    assert "color_code" in result["protocol_metadata"]
    assert "tdma_structure" in result


def test_decode_p25_metadata():
    _add_role_files_to_path()
    from decode_digital import decode_p25

    data = _generate_test_iq(duration_ms=100.0, sample_rate=9600)
    result = decode_p25(data, 9600)
    assert result["mode"] == "P25 Phase 1"
    assert result["standard"] == "TIA-102 (APCO Project 25)"
    assert "modulation" in result
    assert "protocol_metadata" in result
    assert "nac" in result["protocol_metadata"]


def test_decode_nxdn_metadata():
    _add_role_files_to_path()
    from decode_digital import decode_nxdn

    data = _generate_test_iq(duration_ms=100.0, sample_rate=9600)
    result = decode_nxdn(data, 9600)
    assert result["mode"] == "NXDN"
    assert result["standard"] == "NXDN Forum CAI"
    assert "fdma_structure" in result
    assert "protocol_metadata" in result
    assert "ran" in result["protocol_metadata"]


def test_decode_dstar_metadata():
    _add_role_files_to_path()
    from decode_digital import decode_dstar

    data = b"\x00" * 48
    result = decode_dstar(data, 2_048_000)
    assert result["mode"] == "D-STAR"
    assert result["standard"] == "JARL D-STAR specification"
    assert "modulation" in result
    assert "protocol_metadata" in result
    assert "callsign_format" in result["protocol_metadata"]


def test_decode_aprs_metadata():
    _add_role_files_to_path()
    from decode_digital import decode_aprs

    data = _generate_test_iq(duration_ms=200.0, sample_rate=1200)
    result = decode_aprs(data, 1200)
    assert result["mode"] == "APRS"
    assert result["standard"] == "APRS Protocol Reference 1.0.1 (AX.25 UI frames)"
    assert "modulation" in result
    assert "protocol_metadata" in result
    assert "frame_type" in result["protocol_metadata"]


def test_decode_ft8_metadata():
    _add_role_files_to_path()
    from decode_digital import decode_ft8

    data = _generate_test_iq(duration_ms=150.0, sample_rate=16000)
    result = decode_ft8(data, 16000)
    assert result["mode"] == "FT8"
    assert result["standard"] == "WSJT-X (K1JT, G4WJS)"
    assert "tone_spacing_hz" in result
    assert result["t_r_cycle_sec"] == 15.0
    assert "codeword_structure" in result
    assert "crc" in result["codeword_structure"]


def test_decode_rtty_metadata():
    _add_role_files_to_path()
    from decode_digital import decode_rtty

    data = _generate_test_iq(duration_ms=500.0, sample_rate=200)
    result = decode_rtty(data, 200)
    assert result["mode"] == "RTTY"
    assert result["standard"] == "ITA2 Baudot (Baudot-Murray code)"
    assert result["shift_hz"] == 170
    assert "decoded_text" in result
    assert "encoding" in result["protocol_metadata"]


def test_decode_auto_returns_analysis():
    _add_role_files_to_path()
    from decode_digital import decode_auto

    data = _generate_test_iq(duration_ms=50.0, sample_rate=48000)
    result = decode_auto(data, 48000, 144_000_000)
    assert result["mode"] == "auto"
    assert "analysis" in result
    assert "sample_count" in result["analysis"]


def test_empty_data_handled():
    _add_role_files_to_path()
    from decode_digital import decode_dmr

    result = decode_dmr(b"", 9600)
    assert "mode" in result
    assert result["sync_found"] is False


def test_result_fields_present():
    _add_role_files_to_path()
    from decode_digital import decode_dmr

    data = _generate_test_iq(duration_ms=100.0, sample_rate=9600)
    result = decode_dmr(data, 9600)
    required = {"mode", "standard", "modulation", "protocol_metadata"}
    assert required.issubset(set(result.keys()))


def test_decode_digital_meta_exists():
    meta = _COLLECTION_ROOT / "roles" / "decode_digital" / "meta" / "main.yml"
    assert meta.exists()
    data = yaml.safe_load(meta.read_text())
    assert data["galaxy_info"]["role_name"] == "decode_digital"
