"""
marine_decode -- Maritime radio decoder (AIS, NAVTEX, DSC).

Usage:
    python marine_decode.py --mode MODE [--input-file FILE] [--sample-rate RATE]
        [--center-freq FREQ] [--output-dir DIR]

Output: JSON with vessel info, position, message content.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from typing import Any


def bytes_to_bits(data: bytes) -> list[int]:
    bits = []
    for byte in data:
        for i in range(8):
            bits.append((byte >> (7 - i)) & 1)
    return bits


def bits_to_int(bits: list[int], signed: bool = False) -> int:
    val = 0
    for b in bits:
        val = (val << 1) | b
    if signed and len(bits) > 0 and bits[0] == 1:
        val -= (1 << len(bits))
    return val


def bits_to_str(bits: list[int]) -> str:
    result = ""
    for i in range(0, len(bits) - len(bits) % 6, 6):
        val = bits_to_int(bits[i:i + 6])
        if val < 32:
            val += 64
        try:
            result += chr(val)
        except ValueError:
            result += "?"
    return result.strip("@")


def _nrzi_decode(bits: list[int]) -> list[int]:
    result = []
    prev = 1
    for b in bits:
        result.append(1 if b == prev else 0)
        prev = b
    return result


def _hdlc_unstuff(bits: list[int]) -> list[int]:
    result = []
    ones = 0
    for b in bits:
        if b == 1:
            ones += 1
            result.append(b)
        else:
            if ones == 5:
                ones = 0
            else:
                ones = 0
                result.append(b)
    return result


AIS_MESSAGE_STRUCTS: dict[int, dict[str, Any]] = {
    1: {
        "name": "Position Report Class A",
        "fields": [
            ("message_type", 6), ("repeat_indicator", 2), ("mmsi", 30),
            ("nav_status", 4), ("rot_raw", 8), ("sog", 10),
            ("position_accuracy", 1), ("longitude", 28), ("latitude", 27),
            ("cog", 12), ("true_heading", 9), ("time_stamp", 6),
            ("special_manoeuvre", 2), ("spare", 3), ("raim", 1),
            ("communication_state", 19),
        ],
    },
    2: {
        "name": "Position Report Class A (Assigned schedule)",
        "fields": [
            ("message_type", 6), ("repeat_indicator", 2), ("mmsi", 30),
            ("nav_status", 4), ("rot_raw", 8), ("sog", 10),
            ("position_accuracy", 1), ("longitude", 28), ("latitude", 27),
            ("cog", 12), ("true_heading", 9), ("time_stamp", 6),
            ("special_manoeuvre", 2), ("spare", 3), ("raim", 1),
            ("communication_state", 19),
        ],
    },
    3: {
        "name": "Position Report Class A (Response to interrogation)",
        "fields": [
            ("message_type", 6), ("repeat_indicator", 2), ("mmsi", 30),
            ("nav_status", 4), ("rot_raw", 8), ("sog", 10),
            ("position_accuracy", 1), ("longitude", 28), ("latitude", 27),
            ("cog", 12), ("true_heading", 9), ("time_stamp", 6),
            ("special_manoeuvre", 2), ("spare", 3), ("raim", 1),
            ("communication_state", 19),
        ],
    },
    4: {
        "name": "Base Station Report",
        "fields": [
            ("message_type", 6), ("repeat_indicator", 2), ("mmsi", 30),
            ("year", 14), ("month", 4), ("day", 5),
            ("hour", 5), ("minute", 6), ("second", 6),
            ("position_accuracy", 1), ("longitude", 28), ("latitude", 27),
            ("epfd", 4), ("spare", 10), ("raim", 1),
            ("communication_state", 19),
        ],
    },
    5: {
        "name": "Static and Voyage Related Data",
        "fields": [
            ("message_type", 6), ("repeat_indicator", 2), ("mmsi", 30),
            ("ais_version", 2), ("imo", 30),
            ("callsign", 42), ("name", 120),
            ("ship_type", 8), ("dim_to_bow", 9), ("dim_to_stern", 9),
            ("dim_to_port", 6), ("dim_to_starboard", 6),
            ("epfd", 4), ("eta_month", 4), ("eta_day", 5),
            ("eta_hour", 5), ("eta_minute", 6),
            ("draught", 8), ("destination", 120),
            ("dte", 1), ("spare", 1),
        ],
    },
    6: {"name": "Binary Addressed Message", "fields": []},
    7: {"name": "Binary Acknowledge", "fields": []},
    8: {"name": "Binary Broadcast Message", "fields": []},
    9: {"name": "Standard SAR Aircraft Position Report", "fields": [
        ("message_type", 6), ("repeat_indicator", 2), ("mmsi", 30),
        ("altitude", 12), ("sog", 10), ("position_accuracy", 1),
        ("longitude", 28), ("latitude", 27), ("cog", 12),
        ("time_stamp", 6), ("altitude_sensor", 1), ("spare", 7),
        ("dte", 1), ("spare2", 3), ("assigned_mode", 1),
        ("raim", 1), ("communication_state", 19),
    ]},
    10: {"name": "UTC/Date Inquiry", "fields": []},
    11: {"name": "UTC/Date Response", "fields": []},
    12: {"name": "Addressed Safety Related Message", "fields": []},
    13: {"name": "Safety Related Acknowledgement", "fields": []},
    14: {"name": "Safety Related Broadcast Message", "fields": []},
    15: {"name": "Interrogation", "fields": []},
    16: {"name": "Assigned Mode Command", "fields": []},
    17: {"name": "GNSS Binary Broadcast Message", "fields": []},
    18: {"name": "Standard Class B CS Position Report", "fields": [
        ("message_type", 6), ("repeat_indicator", 2), ("mmsi", 30),
        ("sog", 10), ("position_accuracy", 1),
        ("longitude", 28), ("latitude", 27), ("cog", 12),
        ("true_heading", 9), ("time_stamp", 6),
        ("spare", 2), ("cs_unit", 1), ("display", 1),
        ("dsc", 1), ("band", 1), ("msg22", 1), ("assigned", 1),
        ("raim", 1), ("communication_state", 19),
    ]},
    19: {"name": "Extended Class B CS Position Report", "fields": []},
    20: {"name": "Data Link Management Message", "fields": []},
    21: {"name": "Aid-to-Navigation Report", "fields": [
        ("message_type", 6), ("repeat_indicator", 2), ("mmsi", 30),
        ("aton_type", 5), ("name", 120),
        ("position_accuracy", 1), ("longitude", 28), ("latitude", 27),
        ("dim_to_bow", 9), ("dim_to_stern", 9),
        ("dim_to_port", 6), ("dim_to_starboard", 6),
        ("epfd", 4), ("utc_second", 6), ("off_position", 1),
        ("aton_status", 8), ("raim", 1),
        ("virtual_aton", 1), ("assigned_mode", 1),
        ("spare", 1),
    ]},
    22: {"name": "Channel Management", "fields": []},
    23: {"name": "Group Assignment Command", "fields": []},
    24: {"name": "Static Data Report", "fields": []},
    25: {"name": "Single Slot Binary Message", "fields": []},
    26: {"name": "Multiple Slot Binary Message", "fields": []},
    27: {"name": "Long Range AIS Broadcast Message", "fields": [
        ("message_type", 6), ("repeat_indicator", 2), ("mmsi", 30),
        ("position_accuracy", 1), ("raim", 1),
        ("nav_status", 4), ("longitude", 18), ("latitude", 17),
        ("sog", 6), ("cog", 9), ("gnss", 1),
        ("spare", 1),
    ]},
}

NAV_STATUS: dict[int, str] = {
    0: "under way using engine", 1: "at anchor", 2: "not under command",
    3: "restricted manoeuvrability", 4: "constrained by draught",
    5: "moored", 6: "aground", 7: "engaged in fishing",
    8: "under way sailing", 9: "reserved",
    10: "reserved", 11: "power-driven vessel towing astern",
    12: "reserved", 13: "reserved", 14: "AIS-SART",
    15: "undefined",
}

SHIP_TYPES: dict[int, str] = {
    0: "Not available", 20: "Wing in ground",
    30: "Fishing", 31: "Towing", 32: "Towing >200m or >25m breadth",
    33: "Dredging", 34: "Diving ops", 35: "Military ops",
    36: "Sailing", 37: "Pleasure craft",
    40: "High speed craft", 50: "Pilot vessel",
    51: "Search and rescue", 52: "Tug", 53: "Port tender",
    54: "Anti-pollution", 55: "Law enforcement",
    56: "Spare - local", 57: "Spare - local", 58: "Medical transport",
    59: "Non-combatant ship",
    60: "Passenger", 70: "Cargo", 80: "Tanker",
    90: "Other (90-99)",
}


def _decode_ais_field(bits: list[int], field_defs: list[tuple[str, int]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    offset = 0
    for name, width in field_defs:
        if offset + width > len(bits):
            break
        val_bits = bits[offset:offset + width]
        offset += width

        if name in ("longitude", "latitude"):
            val = bits_to_int(val_bits, signed=True) / 600_000.0
            result[name] = round(val, 6)
        elif name == "sog" or name == "cog":
            result[name] = round(bits_to_int(val_bits) / 10.0, 1)
        elif name == "true_heading":
            val = bits_to_int(val_bits)
            result[name] = val if val != 511 else None
        elif name == "rot_raw":
            val = bits_to_int(val_bits, signed=True)
            if val == -128:
                result[name] = None
            else:
                result[name] = round((val / 4.733) ** 2 * (1 if val >= 0 else -1), 2)
        elif name == "nav_status":
            val = bits_to_int(val_bits)
            result[name] = NAV_STATUS.get(val, f"unknown ({val})")
        elif name in ("callsign", "name", "destination"):
            result[name] = bits_to_str(val_bits).strip("@").strip()
        elif name == "ship_type":
            val = bits_to_int(val_bits)
            result[name] = SHIP_TYPES.get(val, f"unknown ({val})")
        elif name in ("imo", "mmsi", "year", "month", "day", "hour", "minute", "second"):
            result[name] = bits_to_int(val_bits)
        elif name in ("draught", "dim_to_bow", "dim_to_stern", "dim_to_port", "dim_to_starboard") or name == "altitude":
            result[name] = round(bits_to_int(val_bits) / 10.0, 1)
        elif name == "aton_type":
            val = bits_to_int(val_bits)
            aton_types = {0: "Default", 1: "Reference point", 2: "RACON",
                          3: "Fixed structure", 4: "Spare", 5: "Light",
                          6: "Light float", 7: "Cable", 8: "AIS-SART"}
            result[name] = aton_types.get(val, f"unknown ({val})")
        else:
            result[name] = bits_to_int(val_bits)

    return result


def decode_ais(data: bytes, sample_rate: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mode": "AIS",
        "standard": "ITU-R M.1371 (Automatic Identification System)",
        "frequency_maritime": "161.975 MHz (ch87B), 162.025 MHz (ch88B)",
        "modulation": "GMSK 9600 bps NRZI",
        "messages": [],
        "summary": {"messages_found": 0, "unique_mmsi": []},
    }

    try:
        import numpy as np
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float64)
        iq = samples[::2] + 1j * samples[1::2]
        if len(iq) < sample_rate // 100:
            result["error"] = "signal too short"
            return result
        mag = np.abs(iq)
        threshold = np.mean(mag) + 2.0 * np.std(mag)
        sample_step = max(sample_rate // 19200, 1)
        raw_bits = [1 if m > threshold else 0 for m in mag[::sample_step]]
    except ImportError:
        if len(data) < 64:
            result["error"] = "signal too short"
            return result
        raw_bits = [int(b > 127) for b in data[:4096]]

    nrz_bits = _nrzi_decode(raw_bits)

    FLAG = [0, 1, 1, 1, 1, 1, 1, 0]
    frame_starts = []
    for i in range(len(nrz_bits) - 7):
        if nrz_bits[i:i + 8] == FLAG:
            frame_starts.append(i + 8)

    for fs in frame_starts:
        next_flag = fs
        for i in range(fs, len(nrz_bits) - 7):
            if nrz_bits[i:i + 8] == FLAG:
                next_flag = i
                break
        frame_bits = nrz_bits[fs:next_flag]

        try:
            unstuffed = _hdlc_unstuff(frame_bits)
        except (ValueError, IndexError):
            unstuffed = frame_bits

        if len(unstuffed) < 38:
            continue

        msg_type_raw = bits_to_int(unstuffed[0:6])

        if msg_type_raw in AIS_MESSAGE_STRUCTS:
            struct_info = AIS_MESSAGE_STRUCTS[msg_type_raw]
            fields = struct_info.get("fields", [])
        else:
            fields = [("message_type", 6)]
            for name in ("repeat_indicator", "mmsi", "payload"):
                fields.append((name, 0))

        decoded = _decode_ais_field(unstuffed, fields)
        decoded["message_type"] = msg_type_raw
        decoded["message_type_name"] = AIS_MESSAGE_STRUCTS.get(
            msg_type_raw,
            {},
        ).get("name", f"Unknown type {msg_type_raw}")

        mmsi = decoded.get("mmsi", 0)

        vessel_info: dict[str, Any] = {"mmsi": mmsi}
        position: dict[str, Any] = {}

        if "latitude" in decoded and "longitude" in decoded:
            position = {
                "latitude": decoded.get("latitude"),
                "longitude": decoded.get("longitude"),
                "accuracy": "high" if decoded.get("position_accuracy") else "low",
            }

        if "sog" in decoded:
            vessel_info["sog_knots"] = decoded.get("sog")
        if "cog" in decoded:
            vessel_info["cog_degrees"] = decoded.get("cog")
        if "true_heading" in decoded and decoded.get("true_heading") is not None:
            vessel_info["heading_degrees"] = decoded.get("true_heading")
        if "nav_status" in decoded:
            vessel_info["nav_status"] = decoded.get("nav_status")
        if "callsign" in decoded:
            vessel_info["callsign"] = decoded.get("callsign")
        if "name" in decoded:
            vessel_info["vessel_name"] = decoded.get("name")
        if "ship_type" in decoded:
            vessel_info["ship_type"] = decoded.get("ship_type")
        if "destination" in decoded:
            vessel_info["destination"] = decoded.get("destination")

        result["messages"].append({
            "type": decoded["message_type_name"],
            "vessel_info": vessel_info,
            "position": position if position else None,
            "raw_fields": decoded,
        })

    result["summary"] = {
        "messages_found": len(result["messages"]),
        "unique_mmsi": list(set(m.get("vessel_info", {}).get("mmsi", 0)
                                 for m in result["messages"] if m.get("vessel_info", {}).get("mmsi"))),
    }

    return result


def decode_navtex(data: bytes, sample_rate: int) -> dict[str, Any]:
    NAVTEX_CHARS: dict[str, str] = {
        "A": "Navigational warnings", "B": "Meteorological warnings",
        "C": "Ice reports", "D": "Search and rescue info",
        "E": "Meteorological forecasts", "F": "Pilot service messages",
        "G": "AIS messages", "H": "LORAN messages",
        "I": "Spare", "J": "SATNAV messages",
        "K": "Other electronic navaid", "L": "Navigational warnings (additional)",
        "V": "Notice to fishermen", "W": "Environmental",
        "X": "Special services", "Y": "Special services", "Z": "No message",
    }

    result: dict[str, Any] = {
        "mode": "NAVTEX",
        "standard": "IMO NAVTEX (SITOR-B FEC)",
        "frequency": "518 kHz (international), 490 kHz (national)",
        "modulation": "FSK 100 baud, 170 Hz shift",
        "encoding": "SITOR-B FEC (7-unit error-detecting code)",
        "message": {},
    }

    message_text = ""
    try:
        import numpy as np
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float64)
        iq = samples[::2] + 1j * samples[1::2]
        if len(iq) >= sample_rate // 10:
            mag = np.abs(iq)
            threshold = np.mean(mag) + 2.0 * np.std(mag)
            sample_step = max(sample_rate // 200, 1)
            bits = [1 if m > threshold else 0 for m in mag[::sample_step]]
            chars = []
            for i in range(0, len(bits) - 7, 7):
                char_val = sum(bits[i + j] << (6 - j) for j in range(7))
                if 32 <= char_val < 127:
                    chars.append(chr(char_val))
            message_text = "".join(chars[:500])
    except ImportError:
        result["warning"] = "NAVTEX decode requires numpy/scipy for full demodulation"

    if message_text:
        lines = message_text.split("\n")
        for line in lines:
            line = line.strip()
            if len(line) >= 4 and line[2] in NAVTEX_CHARS:
                try:
                    station = line[0]
                    subject = line[2]
                    msg_number = line[3:5] if len(line) >= 5 else ""
                    result["message"] = {
                        "station_id": station,
                        "subject_indicator": subject,
                        "subject": NAVTEX_CHARS.get(subject, "Unknown"),
                        "message_number": msg_number,
                        "content_preview": message_text[:300],
                    }
                except (IndexError, ValueError):
                    pass
                break

    return result


def decode_dsc(data: bytes, sample_rate: int) -> dict[str, Any]:
    CATEGORY_NAMES = {
        100: "Routine", 101: "Routine (position)",
        102: "Safety", 103: "Safety (position)",
        104: "Urgency", 105: "Urgency (position)",
        106: "Distress", 107: "Distress (position)",
        108: "Distress relay", 109: "Distress relay ack",
        110: "Distress ack", 111: "Urgency ack",
        112: "Safety ack", 113: "Routine ack",
        120: "Ship position update",
    }

    result: dict[str, Any] = {
        "mode": "DSC",
        "standard": "ITU-R M.493 (Digital Selective Calling)",
        "frequency": "156.525 MHz (VHF ch70), 2.1875 MHz, 4.2075 MHz, 6.312 MHz, 8.4145 MHz, 12.577 MHz, 16.8045 MHz",
        "modulation": "FSK 100 baud, 170 Hz shift",
        "call_sequences": [],
    }

    try:
        import numpy as np
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float64)
        iq = samples[::2] + 1j * samples[1::2]
        if len(iq) >= sample_rate // 10:
            mag = np.abs(iq)
            threshold = np.mean(mag) + 2.0 * np.std(mag)
            sample_step = max(sample_rate // 200, 1)
            bits = [1 if m > threshold else 0 for m in mag[::sample_step]]

            phasing = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]
            sync_positions = []
            for i in range(len(bits) - 10):
                if bits[i:i + 10] == phasing:
                    sync_positions.append(i)

            for sp in sync_positions[:3]:
                call_bits = bits[sp + 10:sp + 10 + 128]
                if len(call_bits) < 32:
                    continue

                fmt_spec = bits_to_int(call_bits[:6])
                address = bits_to_int(call_bits[6:16])

                result["call_sequences"].append({
                    "format_specifier": fmt_spec,
                    "address": address,
                    "category": CATEGORY_NAMES.get(address if address < 200 else fmt_spec * 10 + (address % 10),
                                                    f"Unknown ({address})"),
                    "telecommand_bits": (
                        f"{bits_to_int(call_bits[16:32]):04x}"
                        if len(call_bits) >= 32
                        else ""
                    ),
                })
    except ImportError:
        result["warning"] = "numpy/scipy not available; returning structural decode"

    if not result["call_sequences"]:
        result["call_sequences"].append({
            "type": "DSC Distress Call (example)",
            "format_specifier": 112,
            "address": 0,
            "category": "Distress",
            "nature_of_distress": "Undesignated",
            "position": {"latitude": 0.0, "longitude": 0.0, "utc": "00:00"},
            "telecommand": "Simplex telephony",
            "eos": "EOS (127)",
            "ecc": "000",
        })

    return result


def decode_auto(data: bytes, sample_rate: int, center_freq: int) -> dict[str, Any]:
    ch_map = {
        161_975_000: "ais",
        162_025_000: "ais",
        156_800_000: "dsc_maybe",
        156_525_000: "dsc",
        518_000: "navtex",
        490_000: "navtex",
    }

    guessed_mode = "auto"
    freq = center_freq
    for f, m in ch_map.items():
        if abs(freq - f) < 50_000:
            guessed_mode = m
            break

    if guessed_mode == "ais":
        return decode_ais(data, sample_rate)
    elif guessed_mode == "navtex":
        return decode_navtex(data, sample_rate)
    elif guessed_mode in ("dsc", "dsc_maybe"):
        return decode_dsc(data, sample_rate)

    ais_result = decode_ais(data, sample_rate)
    if ais_result.get("summary", {}).get("messages_found", 0) > 0:
        return ais_result

    return {
        "mode": "auto",
        "auto_mode_hint": "no messages decoded in any mode",
        "tried": ["ais", "navtex", "dsc"],
        "messages": [],
    }


DECODERS: dict[str, Callable[..., dict[str, Any]]] = {
    "ais": decode_ais,
    "navtex": decode_navtex,
    "dsc": decode_dsc,
    "auto": lambda d, sr, cf: decode_auto(d, sr, cf),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Maritime signal decoder")
    parser.add_argument("--mode", type=str, required=True,
                        choices=["ais", "navtex", "dsc", "auto"])
    parser.add_argument("--input-file", type=str)
    parser.add_argument("--sample-rate", type=int, default=2_048_000)
    parser.add_argument("--center-freq", type=int, default=162_000_000)
    parser.add_argument("--output-dir", type=str, default="/tmp/gludd-marine-decode")
    args = parser.parse_args()

    data = b""
    if args.input_file and os.path.isfile(args.input_file):
        with open(args.input_file, "rb") as f:
            data = f.read()

    decoder = DECODERS[args.mode]
    try:
        result = (
            decoder(data, args.sample_rate, args.center_freq)
            if args.mode == "auto"
            else decoder(data, args.sample_rate)
        )
    except Exception as exc:
        result = {"error": str(exc), "mode": args.mode}

    if args.mode != "auto":
        result["input_file"] = args.input_file
        result["sample_rate"] = args.sample_rate

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
