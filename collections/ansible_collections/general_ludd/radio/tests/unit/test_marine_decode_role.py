"""Tests for marine_decode role — validates task YAML, decoder logic, result shape."""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

import pytest
import yaml

_COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent


def _add_role_files_to_path():
    role_files = str(_COLLECTION_ROOT / "roles" / "marine_decode" / "files")
    if role_files not in sys.path:
        sys.path.insert(0, role_files)


def test_marine_decode_tasks_file_exists():
    tasks = _COLLECTION_ROOT / "roles" / "marine_decode" / "tasks" / "main.yml"
    assert tasks.exists()


def test_marine_decode_tasks_has_validate_step():
    tasks = _COLLECTION_ROOT / "roles" / "marine_decode" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "marine_decode_mode in [" in content
    assert "auto" in content
    assert "ais" in content
    assert "navtex" in content
    assert "dsc" in content


def test_marine_decode_tasks_calls_python_script():
    tasks = _COLLECTION_ROOT / "roles" / "marine_decode" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "marine_decode.py" in content


def test_marine_decode_tasks_has_verdict():
    tasks = _COLLECTION_ROOT / "roles" / "marine_decode" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "marine_decode_verdict" in content
    assert "role: marine_decode" in content
    assert "messages_found" in content or "call_sequences" in content


def test_marine_decode_defaults_file_exists():
    defaults = _COLLECTION_ROOT / "roles" / "marine_decode" / "defaults" / "main.yml"
    assert defaults.exists()
    data = yaml.safe_load(defaults.read_text())
    assert "marine_decode_enabled" in data
    assert "marine_decode_mode" in data
    assert "marine_decode_sample_rate" in data
    assert "marine_decode_output_dir" in data


def test_default_mode_is_auto():
    defaults = _COLLECTION_ROOT / "roles" / "marine_decode" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["marine_decode_mode"] == "auto"


def test_marine_decode_script_exists():
    script = _COLLECTION_ROOT / "roles" / "marine_decode" / "files" / "marine_decode.py"
    assert script.exists()
    content = script.read_text()
    assert "def decode_ais" in content
    assert "def decode_navtex" in content
    assert "def decode_dsc" in content
    assert "def decode_auto" in content
    assert "AIS_MESSAGE_STRUCTS" in content
    assert "NAV_STATUS" in content
    assert "SHIP_TYPES" in content


def test_ais_message_structures_have_types_1_through_27():
    _add_role_files_to_path()
    from marine_decode import AIS_MESSAGE_STRUCTS

    assert isinstance(AIS_MESSAGE_STRUCTS, dict)
    for msg_type in range(1, 28):
        assert msg_type in AIS_MESSAGE_STRUCTS, f"Missing AIS message type {msg_type}"
        assert "name" in AIS_MESSAGE_STRUCTS[msg_type]


def test_ais_type_1_has_position_fields():
    _add_role_files_to_path()
    from marine_decode import AIS_MESSAGE_STRUCTS

    type1_fields = {f[0] for f in AIS_MESSAGE_STRUCTS[1]["fields"]}
    assert "mmsi" in type1_fields
    assert "latitude" in type1_fields
    assert "longitude" in type1_fields
    assert "sog" in type1_fields
    assert "cog" in type1_fields
    assert "nav_status" in type1_fields


def test_ais_type_5_has_vessel_fields():
    _add_role_files_to_path()
    from marine_decode import AIS_MESSAGE_STRUCTS

    type5_fields = {f[0] for f in AIS_MESSAGE_STRUCTS[5]["fields"]}
    assert "callsign" in type5_fields
    assert "name" in type5_fields
    assert "ship_type" in type5_fields
    assert "destination" in type5_fields
    assert "draught" in type5_fields
    assert "imo" in type5_fields


def test_ais_type_27_has_position_fields():
    _add_role_files_to_path()
    from marine_decode import AIS_MESSAGE_STRUCTS

    type27_fields = {f[0] for f in AIS_MESSAGE_STRUCTS[27]["fields"]}
    assert "latitude" in type27_fields
    assert "longitude" in type27_fields
    assert "sog" in type27_fields
    assert "cog" in type27_fields


def test_nav_status_covers_known_states():
    _add_role_files_to_path()
    from marine_decode import NAV_STATUS

    assert NAV_STATUS[0] == "under way using engine"
    assert NAV_STATUS[1] == "at anchor"
    assert NAV_STATUS[5] == "moored"
    assert NAV_STATUS[8] == "under way sailing"
    assert len(NAV_STATUS) >= 15


def test_ship_types_covers_major_categories():
    _add_role_files_to_path()
    from marine_decode import SHIP_TYPES

    assert SHIP_TYPES[30] == "Fishing"
    assert SHIP_TYPES[60] == "Passenger"
    assert SHIP_TYPES[70] == "Cargo"
    assert SHIP_TYPES[80] == "Tanker"
    assert SHIP_TYPES[52] == "Tug"
    assert len(SHIP_TYPES) >= 20


def test_decode_ais_returns_structural_result():
    _add_role_files_to_path()
    from marine_decode import decode_ais

    data = b"\x00" * 1024
    result = decode_ais(data, 2_048_000)
    assert result["mode"] == "AIS"
    assert "standard" in result
    assert "ITU-R M.1371" in result["standard"]
    assert "messages" in result
    assert "summary" in result
    assert "messages_found" in result["summary"]


def test_decode_navtex_returns_structural_result():
    _add_role_files_to_path()
    from marine_decode import decode_navtex

    data = b"\x00" * 1024
    result = decode_navtex(data, 2_048_000)
    assert result["mode"] == "NAVTEX"
    assert "standard" in result
    assert "SITOR-B" in result["standard"]
    assert "message" in result
    assert "518 kHz" in result["frequency"]


def test_decode_dsc_returns_structural_result():
    _add_role_files_to_path()
    from marine_decode import decode_dsc

    data = b"\x00" * 1024
    result = decode_dsc(data, 2_048_000)
    assert result["mode"] == "DSC"
    assert "standard" in result
    assert "ITU-R M.493" in result["standard"]
    assert "call_sequences" in result
    assert len(result["call_sequences"]) >= 1
    call = result["call_sequences"][0]
    assert "category" in call or "format_specifier" in call


def test_decode_auto_returns_result():
    _add_role_files_to_path()
    from marine_decode import decode_auto

    data = b"\x00" * 1024
    result = decode_auto(data, 2_048_000, 162_000_000)
    assert "mode" in result
    assert result["mode"] in ("AIS", "NAVTEX", "DSC", "auto")


def test_decode_dsc_distress_call_has_all_fields():
    _add_role_files_to_path()
    from marine_decode import decode_dsc

    data = b"\x00" * 1024
    result = decode_dsc(data, 2_048_000)
    call = result["call_sequences"][0]
    if "type" in call:
        assert "nature_of_distress" in call or "category" in call
        assert "position" in call or "address" in call


def test_ais_decode_symbolic_field_values():
    _add_role_files_to_path()
    from marine_decode import NAV_STATUS, SHIP_TYPES

    for key, val in NAV_STATUS.items():
        assert isinstance(val, str)
    for key, val in SHIP_TYPES.items():
        assert isinstance(val, str)


def test_marine_decode_meta_exists():
    meta = _COLLECTION_ROOT / "roles" / "marine_decode" / "meta" / "main.yml"
    assert meta.exists()
    data = yaml.safe_load(meta.read_text())
    assert data["galaxy_info"]["role_name"] == "marine_decode"


def test_ais_field_decoder_handles_position():
    _add_role_files_to_path()
    from marine_decode import _decode_ais_field

    fields = [
        ("message_type", 6), ("repeat_indicator", 2), ("mmsi", 30),
        ("nav_status", 4), ("sog", 10), ("longitude", 28), ("latitude", 27),
        ("cog", 12), ("true_heading", 9),
    ]
    bits = [0] * 128
    bits[0:6] = [0, 0, 0, 0, 0, 1]  # type 1
    result = _decode_ais_field(bits, fields)
    assert result["message_type"] == 1
    assert "latitude" in result
    assert "longitude" in result
    assert isinstance(result["nav_status"], str)


def test_ais_field_decoder_header():
    _add_role_files_to_path()
    from marine_decode import _decode_ais_field

    fields = [("message_type", 6), ("mmsi", 30)]
    bits = [0] * 128
    bits[0:6] = [0, 0, 0, 0, 0, 1]  # type 1
    mmsi_bits = [0] * 24 + [1, 0, 0, 1, 0, 0]  # test mmsi
    bits[6:36] = mmsi_bits
    result = _decode_ais_field(bits, fields)
    assert result["message_type"] == 1
    assert isinstance(result["mmsi"], int)
