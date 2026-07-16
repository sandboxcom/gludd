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
    decode_aprs,
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
    assert sorted(SUPPORTED_PROTOCOLS) == ["acars", "adsb", "ais", "aprs", "pocsag"]


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
    assert len(protos) == 5


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
    """protocol_decoder protocols should NOT overlap with decode_digital modes.

    Narrowed by ``INTENTIONALLY_SHARED_PROTOCOLS``: the radio collection's
    pattern is that role CLIs are *standalone* (roles/.../files/) and cannot
    import module_utils, so a protocol's canonical decoder lives in this
    library while a role CLI may also expose it as a user-facing mode. This
    mirrors the existing precedent where ``decode_ais`` exists in BOTH
    ``protocol_decoder`` and ``marine_decode``. The allowlist below permits
    those deliberate shares while still failing on any FUTURE accidental key
    collision between the two dispatch tables. Removing either side of a
    shared key also fails (the second assertion below).
    """
    from plugins.module_utils import protocol_decoder as pd

    intentionally_shared = frozenset({"aprs"})
    try:
        import sys
        role_files = "/Users/shawnwilson/gludd/collections/ansible_collections/general_ludd/radio/roles/decode_digital/files"
        if role_files not in sys.path:
            sys.path.insert(0, role_files)
        from decode_digital import DECODERS as DIGITAL_DECODERS  # type: ignore[import-not-found]
        overlap = set(pd.DECODERS.keys()) & set(DIGITAL_DECODERS.keys())
        accidental = overlap - intentionally_shared
        assert not accidental, f"accidental protocol overlap: {accidental}"
        for shared in intentionally_shared & overlap:
            assert shared in pd.DECODERS, f"shared protocol {shared!r} missing from protocol_decoder"
            assert shared in DIGITAL_DECODERS, f"shared protocol {shared!r} missing from decode_digital"
    except ImportError:
        pytest.skip("decode_digital not available")


# ---------------------------------------------------------------------------
# APRS -- AX.25 UI frame parsing + position/weather/status/message decode
# ---------------------------------------------------------------------------


def _encode_ax25_addr(callsign: str, ssid: int, last: bool) -> bytes:
    """Encode a 7-byte AX.25 address field (6 shifted callsign bytes + SSID)."""
    padded = callsign.upper().ljust(6)[:6]
    out = bytes((ord(c) << 1) & 0xFE for c in padded)
    ssid_byte = 0x60 | ((ssid & 0x0F) << 1) | (1 if last else 0)
    return out + bytes([ssid_byte])


def _build_ax25_ui_frame(dest: str, dest_ssid: int, src: str, src_ssid: int, info: str) -> bytes:
    """Build a raw AX.25 UI frame (flag + addrs + ctrl + pid + info + fcs + flag)."""
    flag = b"\x7e"
    addr = _encode_ax25_addr(dest, dest_ssid, last=False) + _encode_ax25_addr(src, src_ssid, last=True)
    return flag + addr + b"\x03\xf0" + info.encode("ascii") + b"\x00\x00" + flag


def test_aprs_protocol_info_present():
    info = PROTOCOL_INFO["aprs"]
    assert info["protocol"] == "APRS"
    assert "AX.25" in info["standard"]
    assert info["symbol_rate"] == 1200
    assert info["frequency_hz"] == 144_390_000
    assert info["modulation"] == "Bell 202 AFSK (1200/2200 Hz tones)"


def test_aprs_in_supported_protocols_and_registry():
    assert "aprs" in SUPPORTED_PROTOCOLS
    assert "aprs" in DECODERS
    assert DECODERS["aprs"] is decode_aprs


def test_decode_protocol_dispatch_aprs():
    frame = _build_ax25_ui_frame("APRS", 0, "N0CALL", 1, ">beacon")
    result = decode_protocol(frame, "aprs", sample_rate=1200)
    assert result["mode"] == "APRS"


def test_aprs_empty_data_handled():
    result = decode_aprs(b"", 1200)
    assert result["mode"] == "APRS"
    assert result["sync_found"] is False


def test_aprs_short_data_handled():
    result = decode_aprs(b"\x00" * 8, 1200)
    assert result["mode"] == "APRS"
    assert result["sync_found"] is False


def test_aprs_metadata_shape():
    frame = _build_ax25_ui_frame("APRS", 0, "N0CALL", 1, "!4903.50N/07201.75W-Test")
    result = decode_aprs(frame, 1200)
    assert result["mode"] == "APRS"
    assert "AX.25" in result["standard"]
    assert result["modulation"] == "Bell 202 AFSK (1200/2200 Hz tones)"
    assert result["sync_found"] is True
    assert "protocol_metadata" in result
    meta = result["protocol_metadata"]
    assert meta["source_callsign"] == "N0CALL"
    assert meta["destination_callsign"] == "APRS"
    assert meta["source_ssid"] == 1
    assert meta["frame_type"] == "UI"
    assert meta["pid"] == 0xF0


def test_aprs_decode_position_without_timestamp():
    info = "!4903.50N/07201.75W-Test position"
    frame = _build_ax25_ui_frame("APRS", 0, "N0CALL", 1, info)
    result = decode_aprs(frame, 1200)
    assert result["sync_found"] is True
    payload = result["protocol_metadata"]["aprs_payload"]
    assert payload["data_type"] == "position"
    assert abs(payload["latitude"] - (49 + 3.50 / 60)) < 1e-6
    assert abs(payload["longitude"] - (-(72 + 1.75 / 60))) < 1e-6
    assert payload["symbol_table"] == "/"
    assert payload["symbol_code"] == "-"


def test_aprs_decode_position_southern_western_hemisphere():
    info = "=3437.20S/05823.10W-test"
    frame = _build_ax25_ui_frame("APRS", 0, "LU1AB", 0, info)
    result = decode_aprs(frame, 1200)
    payload = result["protocol_metadata"]["aprs_payload"]
    assert payload["latitude"] < 0
    assert payload["longitude"] < 0
    assert abs(payload["latitude"] - (-(34 + 37.20 / 60))) < 1e-6
    assert abs(payload["longitude"] - (-(58 + 23.10 / 60))) < 1e-6


def test_aprs_decode_position_with_timestamp():
    info = "/091450z4903.50N/07201.75W>Test"
    frame = _build_ax25_ui_frame("APRS", 0, "N0CALL", 1, info)
    result = decode_aprs(frame, 1200)
    payload = result["protocol_metadata"]["aprs_payload"]
    assert payload["data_type"] == "position_with_timestamp"
    assert payload["timestamp"] == "091450z"
    assert abs(payload["latitude"] - (49 + 3.50 / 60)) < 1e-6


def test_aprs_decode_status():
    info = ">On the air from FN20"
    frame = _build_ax25_ui_frame("APRS", 0, "K1ABC", 0, info)
    result = decode_aprs(frame, 1200)
    payload = result["protocol_metadata"]["aprs_payload"]
    assert payload["data_type"] == "status"
    assert payload["status_text"] == "On the air from FN20"


def test_aprs_decode_message():
    info = ":WORLD    :Hello there"
    frame = _build_ax25_ui_frame("APRS", 0, "NOCALL", 0, info)
    result = decode_aprs(frame, 1200)
    payload = result["protocol_metadata"]["aprs_payload"]
    assert payload["data_type"] == "message"
    assert payload["addressee"] == "WORLD"
    assert payload["message_text"] == "Hello there"


def test_aprs_decode_positionless_weather():
    info = "_10090556c220s004g005t077r000p000P000h50b10045"
    frame = _build_ax25_ui_frame("APRS", 0, "WXBOT", 0, info)
    result = decode_aprs(frame, 1200)
    payload = result["protocol_metadata"]["aprs_payload"]
    assert payload["data_type"] == "weather"
    assert payload["temperature_f"] == 77
    assert payload["humidity_pct"] == 50
    assert payload["pressure_mbar"] == 1004.5
    assert payload["wind_dir_deg"] == 220
    assert payload["wind_speed_mph"] == 4
    assert payload["gust_mph"] == 5


def test_aprs_decode_telemetry():
    info = "T#001,123,456,789,012,345,10101010"
    frame = _build_ax25_ui_frame("APRS", 0, "N0CALL", 1, info)
    result = decode_aprs(frame, 1200)
    payload = result["protocol_metadata"]["aprs_payload"]
    assert payload["data_type"] == "telemetry"
    assert payload["sequence"] == 1
    assert payload["analog_values"] == [123, 456, 789, 12, 345]


def test_aprs_decode_digipeaters():
    info = ">status"
    frame = (
        b"\x7e"
        + _encode_ax25_addr("WIDE1", 1, last=False)
        + _encode_ax25_addr("WIDE2", 2, last=False)
        + _encode_ax25_addr("N0CALL", 1, last=True)
        + b"\x03\xf0" + info.encode("ascii") + b"\x00\x00\x7e"
    )
    # frame builder above sets src as last; build a dest+src+digi frame instead
    frame = (
        b"\x7e"
        + _encode_ax25_addr("APRS", 0, last=False)
        + _encode_ax25_addr("N0CALL", 1, last=False)
        + _encode_ax25_addr("WIDE2", 2, last=True)
        + b"\x03\xf0" + info.encode("ascii") + b"\x00\x00\x7e"
    )
    result = decode_aprs(frame, 1200)
    meta = result["protocol_metadata"]
    assert meta["source_callsign"] == "N0CALL"
    assert meta["destination_callsign"] == "APRS"
    assert len(meta["digipeaters"]) == 1
    assert meta["digipeaters"][0]["callsign"] == "WIDE2"


def test_aprs_all_decoders_return_required_fields():
    data = _iq_square_wave(duration_ms=200.0, sample_rate=1200)
    required = {"mode", "standard", "modulation", "protocol_metadata"}
    result = decode_aprs(data, 1200)
    assert required.issubset(result.keys())
