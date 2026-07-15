"""Tests for sdr_capture role — validates task YAML structure, capture logic, result shape."""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

_COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent


def _add_role_files_to_path():
    role_files = str(_COLLECTION_ROOT / "roles" / "sdr_capture" / "files")
    if role_files not in sys.path:
        sys.path.insert(0, role_files)


def test_sdr_capture_tasks_file_exists():
    tasks = _COLLECTION_ROOT / "roles" / "sdr_capture" / "tasks" / "main.yml"
    assert tasks.exists()


def test_sdr_capture_tasks_has_validate_step():
    tasks = _COLLECTION_ROOT / "roles" / "sdr_capture" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "sdr_capture_freq_hz" in content
    assert "sdr_capture_sample_rate" in content
    assert "sdr_capture_format" in content
    assert "sdr_capture_duration_sec" in content


def test_sdr_capture_tasks_has_verdict():
    tasks = _COLLECTION_ROOT / "roles" / "sdr_capture" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "sdr_capture_verdict" in content
    assert "role: sdr_capture" in content
    assert "sample_count" in content
    assert "output_dir" in content


def test_sdr_capture_tasks_has_tool_check():
    tasks = _COLLECTION_ROOT / "roles" / "sdr_capture" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "_tool_check" in content
    assert "sdr_capture_tool" in content


def test_sdr_capture_tasks_calls_python_backend():
    tasks = _COLLECTION_ROOT / "roles" / "sdr_capture" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "sdr_capture.py" in content
    assert "capture_iq" in content
    assert "--sample-rate" in content
    assert "--duration" in content


def test_sdr_capture_tasks_surfaces_iq_stats():
    tasks = _COLLECTION_ROOT / "roles" / "sdr_capture" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "iq_stats" in content
    assert "avg_power_db" in content


def test_sdr_capture_defaults_file_exists():
    defaults = _COLLECTION_ROOT / "roles" / "sdr_capture" / "defaults" / "main.yml"
    assert defaults.exists()
    data = yaml.safe_load(defaults.read_text())
    assert data["sdr_capture_enabled"] is False
    assert data["sdr_capture_freq_hz"] > 0
    assert data["sdr_capture_sample_rate"] > 0
    assert data["sdr_capture_format"] in ("int8", "int16", "float32")
    assert data["sdr_capture_output_dir"]


def test_sdr_capture_default_disabled():
    defaults = _COLLECTION_ROOT / "roles" / "sdr_capture" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["sdr_capture_enabled"] is False


def test_sdr_capture_script_exists():
    script = _COLLECTION_ROOT / "roles" / "sdr_capture" / "files" / "sdr_capture.py"
    assert script.exists()
    content = script.read_text()
    assert "class CaptureResult" in content
    assert "def capture_iq" in content
    assert "FORMAT_BYTES" in content


def test_capture_result_dataclass_fields():
    _add_role_files_to_path()
    from sdr_capture import CaptureResult

    cr = CaptureResult(
        freq_hz=100_000_000,
        sample_rate=2_048_000,
        duration_sec=1.0,
        sample_count=2_048_000,
        format="int16",
        format_bytes=2,
        device_index=0,
        gain="auto",
        output_file="/tmp/test.bin",
        output_dir="/tmp",
        tool="rtl_sdr",
    )
    d = cr.to_dict()
    assert d["freq_hz"] == 100_000_000
    assert d["sample_rate"] == 2_048_000
    assert d["format"] == "int16"
    assert "iq_stats" in d
    assert "verdict" in d
    assert d["verdict"] == "skipped"


def test_capture_result_success_verdict():
    _add_role_files_to_path()
    from sdr_capture import CaptureResult

    cr = CaptureResult(
        freq_hz=440_000_000,
        sample_rate=2_400_000,
        duration_sec=0.5,
        sample_count=1_200_000,
        format="int16",
        format_bytes=2,
        device_index=0,
        gain="40",
        output_file="/tmp/test.bin",
        output_dir="/tmp",
        tool="rtl_sdr",
        rc=0,
    )
    assert cr.to_dict()["verdict"] == "success"


def test_format_bytes_mapping():
    _add_role_files_to_path()
    from sdr_capture import FORMAT_BYTES

    assert FORMAT_BYTES["int8"] == 1
    assert FORMAT_BYTES["int16"] == 2
    assert FORMAT_BYTES["float32"] == 4


def test_compute_sample_stats_basic():
    _add_role_files_to_path()
    from sdr_capture import _compute_sample_stats

    stats = _compute_sample_stats([1.0, 2.0, 3.0, 4.0, 5.0])
    assert stats["min"] == 1.0
    assert stats["max"] == 5.0
    assert stats["mean"] == 3.0
    assert stats["std"] > 0
    assert stats["rms"] > 0


def test_compute_sample_stats_empty():
    _add_role_files_to_path()
    from sdr_capture import _compute_sample_stats

    stats = _compute_sample_stats([])
    assert stats["min"] == 0.0
    assert stats["max"] == 0.0
    assert stats["mean"] == 0.0


def test_read_iq_samples_int16():
    _add_role_files_to_path()
    from sdr_capture import _read_iq_samples

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        data = struct.pack("8h", 100, 200, -50, 300, 0, -100, 500, 50)
        f.write(data)
        f.flush()
        path = f.name

    try:
        i_samples, q_samples = _read_iq_samples(path, "int16")
        assert len(i_samples) == 4
        assert len(q_samples) == 4
        assert i_samples[0] == 100.0
        assert q_samples[0] == 200.0
        assert i_samples[1] == -50.0
    finally:
        os.unlink(path)


def test_read_iq_samples_missing_file():
    _add_role_files_to_path()
    from sdr_capture import _read_iq_samples

    i_samples, q_samples = _read_iq_samples("/nonexistent/path/file.bin", "int16")
    assert len(i_samples) == 0
    assert len(q_samples) == 0


def test_capture_iq_no_tool_returns_skip():
    _add_role_files_to_path()
    from sdr_capture import capture_iq

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["SDR_CAPTURE_TOOL_PATH"] = ""
        result = capture_iq(
            freq_hz=100_000_000,
            sample_rate=2_048_000,
            duration_sec=0.1,
            output_dir=tmpdir,
            tool="nonexistent_tool_xyz",
        )
    assert result["verdict"] == "skipped"
    assert result["rc"] == -1
    assert "not found" in result["stderr"]
    assert result["freq_hz"] == 100_000_000


def test_capture_iq_calculates_sample_count():
    _add_role_files_to_path()
    from sdr_capture import capture_iq

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["SDR_CAPTURE_TOOL_PATH"] = ""
        result = capture_iq(
            freq_hz=440_000_000,
            sample_rate=2_400_000,
            duration_sec=2.5,
            output_dir=tmpdir,
            tool="nonexistent_tool_xyz",
        )
    assert result["sample_count"] == 6_000_000
    assert result["duration_sec"] == 2.5


def test_sdr_capture_meta_exists():
    meta = _COLLECTION_ROOT / "roles" / "sdr_capture" / "meta" / "main.yml"
    assert meta.exists()
    data = yaml.safe_load(meta.read_text())
    assert data["galaxy_info"]["role_name"] == "sdr_capture"
