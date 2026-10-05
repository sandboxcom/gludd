"""Edge and frame-level contracts for the radio collection decoders."""

from __future__ import annotations

import builtins
import importlib.util
import json
import struct
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from ansible_collections.general_ludd.radio.plugins.module_utils import (
    protocol_decoder as protocol,
)

_COLLECTION_ROOT = Path(__file__).resolve().parents[2]


def _load_role_module(name: str, relative_path: str) -> ModuleType:
    path = _COLLECTION_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load role module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DIGITAL = _load_role_module(
    "radio_decoder_edge_digital",
    "roles/decode_digital/files/decode_digital.py",
)
MARINE = _load_role_module(
    "radio_decoder_edge_marine",
    "roles/marine_decode/files/marine_decode.py",
)


def _int16_iq(amplitudes: Sequence[int]) -> bytes:
    values: list[int] = []
    for amplitude in amplitudes:
        values.extend((amplitude, 0))
    return struct.pack(f"<{len(values)}h", *values)


def _without_numpy(
    name: str,
    globals: Mapping[str, object] | None = None,
    locals: Mapping[str, object] | None = None,
    fromlist: Sequence[str] = (),
    level: int = 0,
) -> object:
    if name == "numpy":
        raise ImportError("numpy unavailable")
    return _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)


_ORIGINAL_IMPORT = builtins.__import__


def _to_bits(value: int, width: int) -> list[int]:
    return [(value >> shift) & 1 for shift in range(width - 1, -1, -1)]


def _seven_bit_text(text: str) -> list[int]:
    return [bit for char in text for bit in _to_bits(ord(char), 7)]


def test_digital_bit_and_frame_helpers_cover_boundary_values() -> None:
    bits = DIGITAL.bytes_to_bits(b"\x81")
    assert bits == [1, 0, 0, 0, 0, 0, 0, 1]
    assert DIGITAL.bits_to_bytes([*bits, 1]) == b"\x81"
    assert DIGITAL.hamming_weight(bits) == 2
    assert DIGITAL.ber_estimate(bits, bits) == 0.0
    assert DIGITAL.ber_estimate([], []) == 1.0
    assert DIGITAL.correlate_sync([0, 1, 0], [1, 0]) == 1
    assert DIGITAL.correlate_sync([], [1]) == -1
    assert DIGITAL._extract_field([1, 0, 1], 0, 3) == 5
    assert DIGITAL._ascii_decode(b"AB\x00") == "AB?"
    assert DIGITAL._nrzi_decode([1, 1, 0]) == [1, 1, 0]
    assert DIGITAL._hdlc_unstuff([1, 1, 1, 1, 1, 0, 1] * 2)
    assert DIGITAL._ax25_addr(bytes((ord("A") << 1,)) * 6, 0) == "AAAAAA"


def test_digital_sync_paths_return_payload_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _int16_iq([100, 0] * 96)

    def _dmr_sync(_bits: list[int], pattern: list[int]) -> int:
        return 0 if pattern is DIGITAL.DMR_DATA_SYNC else -1

    monkeypatch.setattr(DIGITAL, "correlate_sync", _dmr_sync)
    dmr = DIGITAL.decode_dmr(data, 9_600)
    assert dmr["sync_type"] == "data"
    assert dmr["slot_active"] == 1

    monkeypatch.setattr(DIGITAL, "correlate_sync", lambda _bits, _pattern: 0)
    p25 = DIGITAL.decode_p25(_int16_iq([100, 0] * 384), 9_600)
    assert p25["sync_found"] is True
    assert "nac" in p25["protocol_metadata"]


def test_digital_decoders_preserve_fallback_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builtins, "__import__", _without_numpy)
    data = bytes(range(128))
    assert DIGITAL.decode_dmr(data, 9_600)["mode"] == "DMR"
    assert DIGITAL.decode_p25(data, 9_600)["mode"] == "P25 Phase 1"
    assert DIGITAL.decode_nxdn(data, 9_600)["mode"] == "NXDN"
    assert DIGITAL.decode_aprs(data, 1_200)["mode"] == "APRS"
    assert DIGITAL.decode_ft8(data, 200)["standard"].startswith("WSJT-X")
    assert "requires numpy" in DIGITAL.decode_rtty(data, 200)["decoded_text"]
    assert DIGITAL.decode_auto(data, 200, 144_000_000)["mode"] == "auto"


def test_digital_aprs_long_ft8_and_rtty_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _int16_iq([100, 0] * 120)
    monkeypatch.setattr(DIGITAL, "correlate_sync", lambda _bits, _pattern: 0)
    monkeypatch.setattr(DIGITAL, "_nrzi_decode", lambda bits: bits)
    hdlc = (bytes((ord("A") << 1,)) * 6) + b"\x00"
    hdlc += (bytes((ord("B") << 1,)) * 6) + b"\x00\x03HELLO"
    monkeypatch.setattr(DIGITAL, "_hdlc_unstuff", lambda _bits: hdlc)
    aprs = DIGITAL.decode_aprs(data, 1_200)
    assert aprs["protocol_metadata"]["source_callsign"] == "BBBBBB"
    assert "HELLO" in aprs["protocol_metadata"]["info_field"]

    ft8_data = struct.pack("<4800h", *([100] * 4_800))
    ft8 = DIGITAL.decode_ft8(ft8_data, 400)
    assert len(ft8["detected_tones"]) == 8
    assert "error" not in ft8

    chars = (0x1B, 0x01, 0x1F, 0x03)
    amplitudes = [
        100 if bit else 0
        for char in chars
        for bit in reversed(_to_bits(char, 8))
    ]
    rtty = DIGITAL.decode_rtty(_int16_iq(amplitudes), 200)
    assert "3" in rtty["decoded_text"]
    assert "A" in rtty["decoded_text"]


def test_digital_auto_handles_empty_input_without_division() -> None:
    result = DIGITAL.decode_auto(b"", 2_048_000, 144_000_000)
    assert result["analysis"]["estimated_bandwidth_hz"] == 0.0


def test_digital_cli_reads_inputs_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample = tmp_path / "sample.iq"
    sample.write_bytes(b"sample")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "decode_digital",
            "--mode",
            "dstar",
            "--input-file",
            str(sample),
            "--sample-rate",
            "4800",
        ],
    )
    DIGITAL.main()
    assert json.loads(capsys.readouterr().out)["input_file"] == str(sample)

    monkeypatch.setitem(
        DIGITAL.DECODERS,
        "dmr",
        lambda _data, _rate: (_ for _ in ()).throw(RuntimeError("decode failed")),
    )
    monkeypatch.setattr(sys, "argv", ["decode_digital", "--mode", "dmr"])
    DIGITAL.main()
    assert json.loads(capsys.readouterr().out)["error"] == "decode failed"

    monkeypatch.setattr(
        sys,
        "argv",
        ["decode_digital", "--mode", "auto", "--center-freq", "100000000"],
    )
    DIGITAL.main()
    assert json.loads(capsys.readouterr().out)["mode"] == "auto"


def test_marine_bit_helpers_and_all_field_kinds() -> None:
    bits = MARINE.bytes_to_bits(b"\x80")
    assert bits[0] == 1
    assert MARINE.bits_to_int([1, 0, 0, 0], signed=True) == -8
    assert MARINE.bits_to_str(_to_bits(1, 6)) == "A"
    assert MARINE._nrzi_decode([1, 0, 0]) == [1, 0, 1]
    assert MARINE._hdlc_unstuff([1, 1, 1, 1, 1, 0, 1]) == [1, 1, 1, 1, 1, 1]

    fields = [
        ("rot_raw", 8),
        ("true_heading", 9),
        ("nav_status", 4),
        ("callsign", 6),
        ("ship_type", 8),
        ("draught", 8),
        ("aton_type", 5),
        ("spare", 2),
    ]
    field_bits = (
        [1] + [0] * 7
        + [1] * 9
        + _to_bits(15, 4)
        + _to_bits(1, 6)
        + _to_bits(30, 8)
        + _to_bits(25, 8)
        + _to_bits(8, 5)
        + [1, 0]
    )
    decoded = MARINE._decode_ais_field(field_bits, fields)
    assert decoded["rot_raw"] is None
    assert decoded["true_heading"] is None
    assert decoded["callsign"] == "A"
    assert decoded["ship_type"] == "Fishing"
    assert decoded["draught"] == 2.5
    assert decoded["aton_type"] == "AIS-SART"
    assert MARINE._decode_ais_field([], [("mmsi", 30)]) == {}


def _decode_marine_frame(
    monkeypatch: pytest.MonkeyPatch,
    frame: list[int],
) -> dict[str, Any]:
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    monkeypatch.setattr(MARINE, "_nrzi_decode", lambda _bits: flag + frame + flag)
    monkeypatch.setattr(MARINE, "_hdlc_unstuff", lambda bits: bits)
    return cast(
        dict[str, Any],
        MARINE.decode_ais(_int16_iq([100, 0] * 384), 19_200),
    )


def test_marine_ais_known_static_unknown_and_short_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    type_one = [0] * 168
    type_one[:6] = _to_bits(1, 6)
    type_one[8:38] = _to_bits(1, 30)
    position = _decode_marine_frame(monkeypatch, type_one)
    assert position["summary"]["unique_mmsi"] == [1]
    assert position["messages"][0]["position"] is not None
    assert "sog_knots" in position["messages"][0]["vessel_info"]

    type_five = [0] * 424
    type_five[:6] = _to_bits(5, 6)
    static = _decode_marine_frame(monkeypatch, type_five)
    vessel = static["messages"][0]["vessel_info"]
    assert {"callsign", "vessel_name", "ship_type", "destination"} <= set(vessel)

    unknown = [0] * 40
    unknown[:6] = _to_bits(31, 6)
    assert _decode_marine_frame(monkeypatch, unknown)["messages"][0]["type"] == "Unknown type 31"

    short = _decode_marine_frame(monkeypatch, [0] * 10)
    assert short["summary"]["messages_found"] == 0


def test_marine_navtex_and_dsc_demodulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    numpy = pytest.importorskip("numpy")
    navtex_bits = _seven_bit_text("ABA01 TEST")
    monkeypatch.setattr(numpy, "abs", lambda _iq: numpy.asarray(navtex_bits, dtype=float))
    monkeypatch.setattr(numpy, "mean", lambda _values: 0.0)
    monkeypatch.setattr(numpy, "std", lambda _values: 0.0)
    navtex = MARINE.decode_navtex(_int16_iq([1] * 64), 200)
    assert navtex["message"]["subject_indicator"] == "A"
    assert navtex["message"]["message_number"] == "01"

    call_bits = _to_bits(10, 6) + _to_bits(106, 10) + ([0] * 112)
    dsc_bits = [1, 0] * 5 + call_bits
    monkeypatch.setattr(numpy, "abs", lambda _iq: numpy.asarray(dsc_bits, dtype=float))
    dsc = MARINE.decode_dsc(_int16_iq([1] * 80), 200)
    assert dsc["call_sequences"][0]["category"] == "Distress"


def test_marine_fallback_auto_routing_and_unknown_frequency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builtins, "__import__", _without_numpy)
    assert "warning" in MARINE.decode_dsc(b"x" * 128, 200)
    assert "requires numpy" in MARINE.decode_navtex(b"x" * 128, 200)["warning"]
    assert MARINE.decode_ais(b"x", 200)["summary"]["messages_found"] == 0
    monkeypatch.undo()

    monkeypatch.setattr(MARINE, "decode_ais", lambda *_args: {"mode": "AIS", "summary": {"messages_found": 0}})
    monkeypatch.setattr(MARINE, "decode_navtex", lambda *_args: {"mode": "NAVTEX"})
    monkeypatch.setattr(MARINE, "decode_dsc", lambda *_args: {"mode": "DSC"})
    assert MARINE.decode_auto(b"", 200, 518_000)["mode"] == "NAVTEX"
    assert MARINE.decode_auto(b"", 200, 156_525_000)["mode"] == "DSC"
    assert MARINE.decode_auto(b"", 200, 100_000_000)["mode"] == "auto"


def test_marine_cli_reads_inputs_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample = tmp_path / "sample.iq"
    sample.write_bytes(b"sample")
    monkeypatch.setitem(MARINE.DECODERS, "navtex", lambda _data, _rate: {"mode": "NAVTEX"})
    monkeypatch.setattr(
        sys,
        "argv",
        ["marine_decode", "--mode", "navtex", "--input-file", str(sample)],
    )
    MARINE.main()
    assert json.loads(capsys.readouterr().out)["input_file"] == str(sample)

    monkeypatch.setitem(
        MARINE.DECODERS,
        "dsc",
        lambda _data, _rate: (_ for _ in ()).throw(RuntimeError("decode failed")),
    )
    monkeypatch.setattr(sys, "argv", ["marine_decode", "--mode", "dsc"])
    MARINE.main()
    assert json.loads(capsys.readouterr().out)["error"] == "decode failed"

    monkeypatch.setattr(
        sys,
        "argv",
        ["marine_decode", "--mode", "auto", "--center-freq", "100000000"],
    )
    MARINE.main()
    assert json.loads(capsys.readouterr().out)["mode"] == "auto"


def _manchester(bits: Sequence[int]) -> list[int]:
    return [symbol for bit in bits for symbol in ((1, 0) if bit else (0, 1))]


def test_protocol_primitive_and_demodulation_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert protocol._bits_to_int([1, 0, 1]) == 5
    bits = protocol._bytes_to_bits(b"\x81")
    assert protocol._bits_to_bytes([*bits, 1]) == b"\x81"
    assert protocol._hamming_weight(bits) == 2
    assert protocol._correlate([0, 1, 0], [1, 0]) == 1
    assert protocol._correlate([], [1]) == -1
    assert protocol._manchester_decode([1, 0, 0, 1, 1, 1]) == [1, 0]
    assert protocol._nrzi_decode([1, 1, 0]) == [1, 1, 0]
    assert protocol._hdlc_unstuff([1, 1, 1, 1, 1, 0, 1])
    assert protocol._twos_complement([1, 1, 1, 1], 4) == -1
    assert protocol._ber_estimate([1, 0], [1, 1]) == 0.5
    assert protocol._ber_estimate([], []) == 1.0

    assert protocol._demod_iq(b"\x00", 1_200, 1_200) == []
    monkeypatch.setattr(builtins, "__import__", _without_numpy)
    assert protocol._demod_iq(bytes((0, 255, 0, 255)), 1_200, 1_200)


def test_protocol_adsb_long_and_short_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(protocol, "_demod_iq", lambda *_args: [1])
    long_frame = [0] * protocol.ADS_B_LONG_BITS
    long_frame[:5] = _to_bits(17, 5)
    long_frame[8:32] = _to_bits(0xABCDEF, 24)
    long_frame[32:37] = _to_bits(1, 5)
    long_frame[40:88] = _to_bits(1, 6) * 8
    monkeypatch.setattr(protocol, "_manchester_decode", lambda _bits: long_frame)
    result = protocol.decode_adsb(b"x", 2_000_000)
    assert result["sync_found"] is True
    assert result["callsign"] == "AAAAAAAA"

    short_frame = [0] * protocol.ADS_B_SHORT_BITS
    short_frame[:5] = _to_bits(11, 5)
    short_frame[8:32] = _to_bits(0x123456, 24)
    monkeypatch.setattr(protocol, "_manchester_decode", lambda _bits: short_frame)
    assert protocol.decode_adsb(b"x", 2_000_000)["sync_found"] is True


def test_protocol_ais_pocsag_and_acars_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(protocol, "_demod_iq", lambda *_args: [1] * 256)
    ais_frame = [0] * 120
    ais_frame[:6] = _to_bits(1, 6)
    ais_frame[8:38] = _to_bits(123_456_789, 30)
    monkeypatch.setattr(protocol, "_nrzi_decode", lambda _bits: protocol.AIS_FLAG_BITS + ais_frame)
    monkeypatch.setattr(protocol, "_hdlc_unstuff", lambda bits: bits)
    ais = protocol.decode_ais(b"x", 9_600)
    assert ais["protocol_metadata"]["mmsi"] == 123_456_789
    assert ais["protocol_metadata"]["navigation_status"] == 0

    address_word = [0] + _to_bits(123, 18) + _to_bits(2, 2) + ([0] * 11)
    monkeypatch.setattr(
        protocol,
        "_demod_iq",
        lambda *_args: protocol.POCSAG_SYNC_BITS + address_word,
    )
    pocsag = protocol.decode_pocsag(b"x", 1_200)
    assert pocsag["protocol_metadata"]["address"] == 123 << 3

    message_word = [1] + ([0] * 10) + _to_bits(5, 4) + ([0] * 17)
    monkeypatch.setattr(
        protocol,
        "_demod_iq",
        lambda *_args: protocol.POCSAG_SYNC_BITS + message_word,
    )
    assert protocol.decode_pocsag(b"x", 1_200)["protocol_metadata"]["message"] == "5"

    monkeypatch.setattr(protocol, "_demod_iq", lambda *_args: [])
    acars_data = b"\x01AABCDEFGYQ0\x02HELLO\x03"
    acars = protocol.decode_acars(acars_data, 2_400)
    assert acars["sync_found"] is True
    assert acars["protocol_metadata"]["message"] == "HELLO"


def test_protocol_aprs_helpers_cover_invalid_and_optional_payloads() -> None:
    assert protocol._decode_ax25_address(b"", 0) == {"callsign": "", "ssid": 0}
    assert protocol._parse_ax25_frame(b"short") is None
    assert protocol._extract_aprs_from_bits([]) is None
    assert protocol._extract_aprs_from_bits([0] * 32) is None
    assert protocol._extract_aprs_from_bytes(b"no flags") is None
    assert protocol._decode_aprs_payload("")["data_type"] == "unknown"
    assert protocol._decode_aprs_payload(";OBJECT   *")["data_type"] == "object"
    assert protocol._decode_aprs_payload("'mice")["data_type"] == "mice"
    assert protocol._decode_aprs_payload("?other")["data_type"] == "other"
    assert protocol._parse_aprs_position("12345678")["latitude"] is not None
    assert protocol._parse_aprs_position("bad")["raw_position"]["latitude"] == "bad"
    assert protocol._ddmm_to_decimal("invalid", "lat") is None
    weather = protocol._parse_aprs_weather("t-05c180x")
    assert weather["temperature_f"] == -5
    assert weather["wind_dir_deg"] == 180
    telemetry = protocol._parse_aprs_telemetry("bad,1,two")
    assert telemetry["sequence"] is None
    assert telemetry["analog_values"] == []
