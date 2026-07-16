"""Tests for protocol_decoder module — ADS-B, AIS, POCSAG, ACARS decoding."""

from __future__ import annotations

import struct

import pytest

from plugins.module_utils.protocol_decoder import (
    ACARS_ALPHABET,
    ADS_B_CALLSIGN_ALPHABET,
    AIS_FLAG_BYTE,
    DECODERS,
    POCSAG_NUM_ALPHABET,
    POCSAG_SYNC_WORD,
    PROTOCOL_INFO,
    SUPPORTED_PROTOCOLS,
    decode_acars,
    decode_adsb,
    decode_ais,
    decode_pocsag,
    decode_protocol,
    list_protocols,
)


def _iq_square_wave(duration_ms: float, sample_rate: int, period: int = 20) -> bytes:
    """Generate a deterministic IQ buffer (int16 interleaved I/Q samples)."""
    n = int(sample_rate * duration_ms / 1000.0)
    out = bytearray()
    for i in range(n):
        v = struct.pack("<h", int(127 * (1 if (i % period < period // 2) else -1)))
        out += v + v
    return bytes(out)


def test_supported_protocols_complete():
    assert sorted(SUPPORTED_PROTOCOLS) == ["acars", "adsb", "ais", "pocsag"]


def test_protocol_info_has_all_protocols():
    for name in SUPPORTED_PROTOCOLS:
        assert name in PROTOCOL_INFO
        info = PROTOCOL_INFO[name]
        assert "protocol" in info
        assert "standard" in info
        assert "modulation" in info
        assert "frequency_hz" in info
        assert "symbol_rate" in info
        assert "typical_use" in info


def test_protocol_info_adsb_frequencies_correct():
    assert PROTOCOL_INFO["adsb"]["frequency_hz"] == 1_090_000_000
    assert PROTOCOL_INFO["ais"]["frequency_hz"] == 161_975_000


def test_list_protocols():
    protos = list_protocols()
    assert set(protos) == set(SUPPORTED_PROTOCOLS)
    assert len(protos) == 4


def test_decoders_registry_matches_protocols():
    assert set(DECODERS.keys()) == set(SUPPORTED_PROTOCOLS)


def test_decode_protocol_dispatch_unknown():
    result = decode_protocol(b"\x00" * 100, "nonexistent")
    assert "error" in result
    assert "nonexistent" in result["error"]


def test_decode_protocol_dispatch_adsb():
    data = _iq_square_wave(duration_ms=200.0, sample_rate=2_000_000)
    result = decode_protocol(data, "adsb", sample_rate=2_000_000)
    assert result["mode"] in ("ADS-B", "ADSB")


def test_adsb_metadata_shape():
    data = _iq_square_wave(duration_ms=200.0, sample_rate=2_000_000)
    result = decode_adsb(data, 2_000_000)
    assert result["mode"] in ("ADS-B", "ADSB")
    assert "RTCA" in result["standard"] or "ICAO" in result["standard"]
    assert "PPM" in result["modulation"]
    assert isinstance(result["sync_found"], bool)
    assert isinstance(result["ber_estimate"], float)
    assert "protocol_metadata" in result
    meta = result["protocol_metadata"]
    assert "downlink_format" in meta
    assert "icao_address" in meta


def test_adsb_callsign_alphabet_valid():
    assert ADS_B_CALLSIGN_ALPHABET[1] == "A"
    assert ADS_B_CALLSIGN_ALPHABET[26] == "Z"
    assert ADS_B_CALLSIGN_ALPHABET[48] == "0"
    assert ADS_B_CALLSIGN_ALPHABET[57] == "9"


def test_adsb_short_data_handled():
    result = decode_adsb(b"\x00" * 10, 2_000_000)
    assert result["mode"] in ("ADS-B", "ADSB")
    assert result["sync_found"] is False


def test_adsb_empty_data_handled():
    result = decode_adsb(b"", 2_000_000)
    assert "mode" in result
    assert result["sync_found"] is False


def test_ais_metadata_shape():
    data = _iq_square_wave(duration_ms=300.0, sample_rate=9600)
    result = decode_ais(data, 9600)
    assert result["mode"] == "AIS"
    assert "ITU-R" in result["standard"] or "IEC" in result["standard"]
    assert "GMSK" in result["modulation"]
    assert isinstance(result["sync_found"], bool)
    assert "protocol_metadata" in result
    meta = result["protocol_metadata"]
    assert "mmsi" in meta
    assert "message_type" in meta


def test_ais_flag_byte_is_hdlc():
    assert AIS_FLAG_BYTE == 0x7E


def test_ais_short_data_handled():
    result = decode_ais(b"\x00" * 5, 9600)
    assert result["mode"] == "AIS"
    assert result["sync_found"] is False


def test_ais_empty_data_handled():
    result = decode_ais(b"", 9600)
    assert result["mode"] == "AIS"
    assert result["sync_found"] is False


def test_pocsag_sync_word_value():
    assert POCSAG_SYNC_WORD == 0x7CD215D8


def test_pocsag_numeric_alphabet():
    assert POCSAG_NUM_ALPHABET[0] == "0"
    assert POCSAG_NUM_ALPHABET[9] == "9"
    assert POCSAG_NUM_ALPHABET[10] == " "
    assert len(POCSAG_NUM_ALPHABET) == 16


def test_pocsag_metadata_shape():
    data = _iq_square_wave(duration_ms=400.0, sample_rate=1200)
    result = decode_pocsag(data, 1200)
    assert result["mode"] == "POCSAG"
    assert "CCIR" in result["standard"] or "POCSAG" in result["standard"].upper() or "Rec" in result["standard"]
    assert "FSK" in result["modulation"]
    assert isinstance(result["sync_found"], bool)
    assert "protocol_metadata" in result
    meta = result["protocol_metadata"]
    assert "batch_count" in meta
    assert "address" in meta


def test_pocsag_short_data_handled():
    result = decode_pocsag(b"\x00" * 8, 1200)
    assert result["mode"] == "POCSAG"
    assert result["sync_found"] is False


def test_pocsag_empty_data_handled():
    result = decode_pocsag(b"", 1200)
    assert result["mode"] == "POCSAG"
    assert result["sync_found"] is False


def test_acars_alphabet_contains_control_chars():
    assert ACARS_ALPHABET[0x01] == "<SOH>"
    assert ACARS_ALPHABET[0x02] == "<STX>"
    assert ACARS_ALPHABET[0x03] == "<ETX>"


def test_acars_metadata_shape():
    data = _iq_square_wave(duration_ms=400.0, sample_rate=2400)
    result = decode_acars(data, 2400)
    assert result["mode"] == "ACARS"
    assert "ARINC" in result["standard"] or "AEEC" in result["standard"] or "ARINC" in result.get("standard", "")
    assert isinstance(result["sync_found"], bool)
    assert "protocol_metadata" in result
    meta = result["protocol_metadata"]
    assert "registration" in meta
    assert "label" in meta


def test_acars_short_data_handled():
    result = decode_acars(b"\x00" * 8, 2400)
    assert result["mode"] == "ACARS"
    assert result["sync_found"] is False


def test_acars_empty_data_handled():
    result = decode_acars(b"", 2400)
    assert result["mode"] == "ACARS"
    assert result["sync_found"] is False


def test_all_decoders_return_required_fields():
    data = _iq_square_wave(duration_ms=200.0, sample_rate=9600)
    required = {"mode", "standard", "modulation", "protocol_metadata"}
    for proto, fn in DECODERS.items():
        result = fn(data, 9600)
        assert required.issubset(result.keys()), f"{proto} missing required fields: {required - set(result.keys())}"


def test_decoders_disjoint_from_decode_digital():
    """protocol_decoder protocols should NOT overlap with decode_digital modes."""
    from plugins.module_utils import protocol_decoder as pd

    try:
        import sys
        role_files = "/Users/shawnwilson/gludd/collections/ansible_collections/general_ludd/radio/roles/decode_digital/files"
        if role_files not in sys.path:
            sys.path.insert(0, role_files)
        from decode_digital import DECODERS as DIGITAL_DECODERS  # type: ignore[import-not-found]
        overlap = set(pd.DECODERS.keys()) & set(DIGITAL_DECODERS.keys())
        assert not overlap, f"protocols overlap: {overlap}"
    except ImportError:
        pytest.skip("decode_digital not available")
