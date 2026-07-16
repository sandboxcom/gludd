"""
protocol_decoder -- Decode common digital protocols from baseband IQ samples.

Supports:
    - ADS-B  (1090 MHz, aviation surveillance, Extended Squitter)
    - AIS    (161.975 MHz, marine Automatic Identification System)
    - POCSAG (VHF/UHF paging, Post Office Code Standardization Advisory Group)
    - ACARS  (131.55 MHz, Aircraft Communications Addressing and Reporting System)

Each decoder accepts ``(data: bytes, sample_rate: int)`` where ``data`` is an
interleaved int16 IQ buffer (little-endian) and ``sample_rate`` is samples/sec.
The decoder demodulates a bit stream, locates the protocol sync/preamble, and
parses the protocol structure. When the structure is present it extracts the
typed metadata; when absent (short/empty/noise input) it returns a result with
``sync_found = False`` and zeroed metadata so callers always get a well-formed
dict.

Public API:
    SUPPORTED_PROTOCOLS        list[str]
    PROTOCOL_INFO              dict[str, dict[str, Any]]
    DECODERS                   dict[str, Callable[[bytes, int], dict]]
    list_protocols()           -> list[str]
    decode_protocol(data, protocol, sample_rate=2_048_000) -> dict
    decode_adsb(data, sample_rate) -> dict
    decode_ais(data, sample_rate) -> dict
    decode_pocsag(data, sample_rate) -> dict
    decode_acars(data, sample_rate) -> dict
"""

from __future__ import annotations

from typing import Any, Callable


SUPPORTED_PROTOCOLS: list[str] = ["adsb", "ais", "pocsag", "acars"]

PROTOCOL_INFO: dict[str, dict[str, Any]] = {
    "adsb": {
        "protocol": "ADS-B",
        "full_name": "Automatic Dependent Surveillance - Broadcast",
        "standard": "RTCA DO-260B / ICAO Annex 10 Volume III",
        "modulation": "PPM (Pulse Position Modulation, Manchester-coded)",
        "frequency_hz": 1_090_000_000,
        "bandwidth_hz": 2_000_000,
        "symbol_rate": 1_000_000,
        "message_length_bits": 112,
        "typical_use": "Aircraft broadcast position, velocity, identification; air-traffic surveillance",
    },
    "ais": {
        "protocol": "AIS",
        "full_name": "Automatic Identification System",
        "standard": "ITU-R M.1371-5 / IEC 61993-2",
        "modulation": "GMSK (Gaussian Minimum Shift Keying)",
        "frequency_hz": 161_975_000,
        "bandwidth_hz": 25_000,
        "symbol_rate": 9600,
        "message_length_bits": 168,
        "typical_use": "Marine vessel position, course, speed, identification; collision avoidance",
    },
    "pocsag": {
        "protocol": "POCSAG",
        "full_name": "Post Office Code Standardization Advisory Group",
        "standard": "CCIR Radio-Paging Code No.1 (Rec. 584) / POCSAG",
        "modulation": "2-FSK (\u00b14.5 kHz deviation)",
        "frequency_hz": 157_600_000,
        "bandwidth_hz": 25_000,
        "symbol_rate": 1200,
        "message_length_bits": 32,
        "typical_use": "Pager messaging (numeric, alpha, tone); VHF/UHF dispatch paging",
    },
    "acars": {
        "protocol": "ACARS",
        "full_name": "Aircraft Communications Addressing and Reporting System",
        "standard": "ARINC 618 / AEEC (Airlines Electronic Engineering Committee)",
        "modulation": "AM-MSK (AM subcarrier with Minimum Shift Keying)",
        "frequency_hz": 131_550_000,
        "bandwidth_hz": 25_000,
        "symbol_rate": 2400,
        "message_length_bits": 0,
        "typical_use": "Aircraft air-to-ground short messaging (OOOI events, weather, position, telemetry)",
    },
}


ADS_B_CALLSIGN_ALPHABET: dict[int, str] = {
    **{i: chr(ord("A") + i - 1) for i in range(1, 27)},
    32: " ",
    **{i: chr(ord("0") + i - 48) for i in range(48, 58)},
}


ADS_B_PREAMBLE_US: tuple[float, ...] = (0.0, 1.0, 3.5, 4.5)

ADS_B_LONG_BITS = 112
ADS_B_SHORT_BITS = 56

AIS_FLAG_BYTE = 0x7E
AIS_FLAG_BITS = [0, 1, 1, 1, 1, 1, 1, 0]

POCSAG_SYNC_WORD = 0x7CD215D8
POCSAG_SYNC_BITS = [(POCSAG_SYNC_WORD >> (31 - i)) & 1 for i in range(32)]

POCSAG_NUM_ALPHABET: dict[int, str] = {
    0: "0", 1: "1", 2: "2", 3: "3", 4: "4",
    5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
    10: " ", 11: "U", 12: "-", 13: ")", 14: "(", 15: " ",
}

ACARS_ALPHABET: dict[int, str] = {
    **{b: chr(b) if 32 <= b < 127 else "?" for b in range(256)},
    0x01: "<SOH>", 0x02: "<STX>", 0x03: "<ETX>",
    0x04: "<EOT>", 0x05: "<ENQ>", 0x06: "<ACK>",
    0x15: "<NAK>", 0x17: "<ETB>", 0x7F: "<DEL>",
    0x00: "<NUL>",
}


def _bits_to_int(bits: list[int]) -> int:
    val = 0
    for b in bits:
        val = (val << 1) | (b & 1)
    return val


def _bytes_to_bits(data: bytes) -> list[int]:
    out: list[int] = []
    for byte in data:
        for shift in range(7, -1, -1):
            out.append((byte >> shift) & 1)
    return out


def _bits_to_bytes(bits: list[int]) -> bytes:
    out = bytearray()
    for i in range(0, len(bits) - (len(bits) % 8), 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i + j]
        out.append(byte)
    return bytes(out)


def _hamming_weight(bits: list[int]) -> int:
    return sum(1 for b in bits if b)


def _correlate(bits: list[int], pattern: list[int], threshold: int | None = None) -> int:
    """Return offset of best correlation of ``pattern`` in ``bits`` (>=``threshold``), else -1."""
    if len(bits) < len(pattern):
        return -1
    if threshold is None:
        threshold = int(0.875 * len(pattern))
    best_pos = -1
    best_corr = threshold - 1
    for i in range(len(bits) - len(pattern) + 1):
        corr = sum(1 for j in range(len(pattern)) if bits[i + j] == pattern[j])
        if corr > best_corr:
            best_corr = corr
            best_pos = i
    return best_pos


def _demod_iq(data: bytes, sample_rate: int, bit_rate: int) -> list[int]:
    """Demodulate an on/off-keyed / FSK magnitude stream to bits.

    Uses numpy when available; otherwise falls back to a byte-threshold path so
    the decoder still runs in a minimal environment.
    """
    if not data:
        return []
    samples_per_bit = max(sample_rate // bit_rate, 1)
    try:
        import numpy as np

        raw = np.frombuffer(data, dtype=np.int16).astype(np.float64)
        iq = raw[::2] + 1j * raw[1::2] if len(raw) >= 2 else raw
        mag = np.abs(iq)
        if len(mag) < samples_per_bit:
            return []
        threshold = float(np.mean(mag) + 1.5 * np.std(mag))
        bits: list[int] = [
            1 if float(np.mean(mag[i:i + samples_per_bit])) > threshold else 0
            for i in range(0, len(mag) - samples_per_bit + 1, samples_per_bit)
        ]
        return bits
    except ImportError:
        step = max(samples_per_bit, 1)
        if len(data) < step:
            return []
        mean_val = sum(data[::2]) / max(len(data[::2]), 1)
        return [1 if data[i] > mean_val else 0 for i in range(0, len(data), step)]


def _manchester_decode(bits: list[int]) -> list[int]:
    """Manchester-decode (IEEE 802.3: high-low -> 1, low-high -> 0)."""
    out: list[int] = []
    for i in range(0, len(bits) - 1, 2):
        a, b = bits[i], bits[i + 1]
        if a == 1 and b == 0:
            out.append(1)
        elif a == 0 and b == 1:
            out.append(0)
    return out


def _nrzi_decode(bits: list[int]) -> list[int]:
    out: list[int] = []
    prev = 1
    for b in bits:
        out.append(1 if b == prev else 0)
        prev = b
    return out


def _hdlc_unstuff(bits: list[int]) -> list[int]:
    out: list[int] = []
    ones = 0
    for b in bits:
        if b == 1:
            ones += 1
            out.append(b)
            if ones == 5:
                ones = -1
        else:
            if ones != 5:
                out.append(b)
            ones = 0
    return out


def decode_adsb(data: bytes, sample_rate: int) -> dict[str, Any]:
    """Decode an ADS-B Extended Squitter (1090 MHz, PPM, 112-bit DF=17 frame)."""
    info = PROTOCOL_INFO["adsb"]
    bits = _demod_iq(data, sample_rate, info["symbol_rate"])
    if not bits:
        return _empty_result("ADS-B", info, {"downlink_format": None, "icao_address": None, "type_code": None, "callsign": None})

    decoded = _manchester_decode(bits)

    df = 0
    icao = 0
    type_code = 0
    callsign: str | None = None
    sync_found = False

    if len(decoded) >= ADS_B_LONG_BITS:
        df = _bits_to_int(decoded[0:5])
        if df in (17, 18):
            sync_found = True
            icao = _bits_to_int(decoded[8:32])
            type_code = _bits_to_int(decoded[32:37])
            if 1 <= type_code <= 4:
                callsign = _decode_callsign(decoded[40:88])
    elif len(decoded) >= ADS_B_SHORT_BITS:
        df = _bits_to_int(decoded[0:5])
        if df in (11, 16, 20, 21):
            sync_found = True
            icao = _bits_to_int(decoded[8:32])

    ber = 0.0
    if sync_found and len(decoded) >= ADS_B_LONG_BITS:
        ber = _ber_estimate(decoded[0:8], [1, 0, 1, 0, 0, 0, 0, 1])

    return {
        "mode": "ADS-B",
        "standard": info["standard"],
        "modulation": info["modulation"],
        "frequency_hz": info["frequency_hz"],
        "symbol_rate": info["symbol_rate"],
        "message_length_bits": ADS_B_LONG_BITS if sync_found else 0,
        "sync_found": sync_found,
        "downlink_format": df,
        "icao_address_hex": f"{icao:06X}" if icao else None,
        "type_code": type_code,
        "callsign": callsign,
        "payload_bits": len(decoded),
        "ber_estimate": round(ber, 4),
        "protocol_metadata": {
            "downlink_format": df,
            "icao_address": icao,
            "icao_address_hex": f"{icao:06X}" if icao else None,
            "type_code": type_code,
            "callsign": callsign,
            "capability": _bits_to_int(decoded[5:8]) if len(decoded) >= 8 else None,
            "extended_squitter": df == 17,
        },
    }


def _decode_callsign(char_bits: list[int]) -> str:
    out = ""
    for i in range(0, 48, 6):
        if i + 6 > len(char_bits):
            break
        val = _bits_to_int(char_bits[i:i + 6])
        out += ADS_B_CALLSIGN_ALPHABET.get(val, " ")
    return out.rstrip() or None  # type: ignore[return-value]


def decode_ais(data: bytes, sample_rate: int) -> dict[str, Any]:
    """Decode an AIS message (161.975 MHz, GMSK, HDLC-framed)."""
    info = PROTOCOL_INFO["ais"]
    bits = _demod_iq(data, sample_rate, info["symbol_rate"])
    if not bits:
        return _empty_result(
            "AIS", info,
            {"mmsi": None, "message_type": None, "navigation_status": None, "longitude": None, "latitude": None},
        )

    nrzi = _nrzi_decode(bits)
    flag_pos = _correlate(nrzi, AIS_FLAG_BITS, threshold=8)
    sync_found = flag_pos >= 0

    mmsi = 0
    msg_type = 0
    nav_status: int | None = None
    longitude: float | None = None
    latitude: float | None = None

    if sync_found:
        frame = _hdlc_unstuff(nrzi[flag_pos + 8:flag_pos + 8 + 200])
        if len(frame) >= 38:
            msg_type = _bits_to_int(frame[0:6])
            mmsi = _bits_to_int(frame[8:38])
            if msg_type in (1, 2, 3) and len(frame) >= 116:
                nav_status = _bits_to_int(frame[38:42])
                longitude = _twos_complement(frame[61:89], 28) / 600_000.0
                latitude = _twos_complement(frame[89:116], 27) / 600_000.0

    return {
        "mode": "AIS",
        "standard": info["standard"],
        "modulation": info["modulation"],
        "frequency_hz": info["frequency_hz"],
        "symbol_rate": info["symbol_rate"],
        "sync_found": sync_found,
        "flag_pos": flag_pos,
        "message_type": msg_type,
        "mmsi": mmsi,
        "ber_estimate": 0.0 if sync_found else 1.0,
        "payload_bits": len(bits),
        "protocol_metadata": {
            "mmsi": mmsi,
            "message_type": msg_type,
            "navigation_status": nav_status,
            "longitude": longitude,
            "latitude": latitude,
            "channel": "AIS-A" if sync_found else None,
            "hdlc_framed": True,
        },
    }


def _twos_complement(bits: list[int], width: int) -> int:
    val = _bits_to_int(bits[:width])
    if val & (1 << (width - 1)):
        val -= 1 << width
    return val


def decode_pocsag(data: bytes, sample_rate: int) -> dict[str, Any]:
    """Decode a POCSAG paging message (2-FSK, BCH(31,21) codewords in batches)."""
    info = PROTOCOL_INFO["pocsag"]
    bits = _demod_iq(data, sample_rate, info["symbol_rate"])
    if not bits:
        return _empty_result(
            "POCSAG", info,
            {"batch_count": 0, "address": None, "function": None, "message": None, "message_type": None},
        )

    sync_pos = _correlate(bits, POCSAG_SYNC_BITS, threshold=30)
    sync_found = sync_pos >= 0

    address = 0
    function = 0
    message_text: str | None = None
    message_type: str | None = None
    batch_count = 0

    cursor = sync_pos if sync_pos >= 0 else -1
    if cursor >= 0:
        while cursor >= 0 and cursor + 32 <= len(bits):
            sync_in_batch = _correlate(bits[cursor:cursor + 32], POCSAG_SYNC_BITS, threshold=30)
            if sync_in_batch >= 0:
                batch_count += 1
                cursor += 32
            codeword_start = cursor if sync_in_batch >= 0 else cursor
            if codeword_start + 32 > len(bits):
                break
            cw_bits = bits[codeword_start:codeword_start + 32]
            is_message = bool(cw_bits[0])
            if not is_message:
                address = _bits_to_int(cw_bits[1:19]) << 3
                function = _bits_to_int(cw_bits[19:21])
                message_type = "numeric"
                cursor = codeword_start + 32
                break
            else:
                nib = _bits_to_int(cw_bits[11:15])
                if message_text is None:
                    message_text = ""
                message_text += POCSAG_NUM_ALPHABET.get(nib, "?")
                cursor = codeword_start + 32

    if message_text is not None:
        message_text = message_text.strip() or None

    return {
        "mode": "POCSAG",
        "standard": info["standard"],
        "modulation": info["modulation"],
        "frequency_hz": info["frequency_hz"],
        "symbol_rate": info["symbol_rate"],
        "sync_found": sync_found,
        "sync_pos": sync_pos,
        "ber_estimate": 0.0 if sync_found else 1.0,
        "payload_bits": len(bits),
        "protocol_metadata": {
            "batch_count": batch_count,
            "address": address,
            "function": function,
            "message": message_text,
            "message_type": message_type,
            "bch_code": "BCH(31,21) + parity (32,31)",
            "baud_rates": [512, 1200, 2400],
        },
    }


def decode_acars(data: bytes, sample_rate: int) -> dict[str, Any]:
    """Decode an ACARS message (131.55 MHz, AM-MSK, SOH/STX/ETX framed)."""
    info = PROTOCOL_INFO["acars"]
    bits = _demod_iq(data, sample_rate, info["symbol_rate"])
    byte_stream = _bits_to_bytes(bits) if bits else data

    soh = byte_stream.find(b"\x01")
    sync_found = soh >= 0

    registration = ""
    label = ""
    mode = ""
    ack = ""
    block_id = ""
    message_text = ""

    if sync_found:
        body = byte_stream[soh + 1:]
        if body:
            mode = ACARS_ALPHABET.get(body[0], chr(body[0])) if body[0] < 128 else ""
            mode = body[0:1].decode("ascii", errors="replace")
        if len(body) >= 8:
            registration = body[1:8].decode("ascii", errors="replace")
        if len(body) >= 9:
            ack_char = body[8:9]
            ack = ack_char.decode("ascii", errors="replace")
        if len(body) >= 11:
            label = body[9:11].decode("ascii", errors="replace")
        if len(body) >= 12:
            block_id = body[11:12].decode("ascii", errors="replace")
        stx = body.find(b"\x02")
        etx = body.find(b"\x03")
        if 0 <= stx < etx:
            raw_msg = body[stx + 1:etx]
            message_text = "".join(
                ACARS_ALPHABET.get(b, chr(b) if b < 128 else "?") for b in raw_msg
            )

    return {
        "mode": "ACARS",
        "standard": info["standard"],
        "modulation": info["modulation"],
        "frequency_hz": info["frequency_hz"],
        "symbol_rate": info["symbol_rate"],
        "sync_found": sync_found,
        "soh_pos": soh,
        "ber_estimate": 0.0 if sync_found else 1.0,
        "payload_bits": len(bits),
        "protocol_metadata": {
            "registration": registration.strip(),
            "label": label,
            "mode": mode,
            "ack": ack,
            "block_id": block_id,
            "message": message_text.strip(),
            "framing": "SOH...STX...ETX with block-check sequence",
        },
    }


def _empty_result(mode: str, info: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": mode,
        "standard": info["standard"],
        "modulation": info["modulation"],
        "frequency_hz": info["frequency_hz"],
        "symbol_rate": info["symbol_rate"],
        "sync_found": False,
        "ber_estimate": 1.0,
        "payload_bits": 0,
        "protocol_metadata": meta,
    }


def _ber_estimate(received: list[int], expected: list[int]) -> float:
    if len(received) != len(expected) or not received:
        return 1.0
    errors = sum(1 for r, e in zip(received, expected) if r != e)
    return errors / len(received)


DECODERS: dict[str, Callable[[bytes, int], dict[str, Any]]] = {
    "adsb": decode_adsb,
    "ais": decode_ais,
    "pocsag": decode_pocsag,
    "acars": decode_acars,
}


def list_protocols() -> list[str]:
    """Return the names of all supported protocols."""
    return list(SUPPORTED_PROTOCOLS)


def decode_protocol(data: bytes, protocol: str, sample_rate: int = 2_048_000) -> dict[str, Any]:
    """Dispatch to the named protocol decoder; unknown protocols return an error dict."""
    if protocol not in DECODERS:
        return {
            "mode": "unknown",
            "error": f"unknown protocol: {protocol}; supported: {SUPPORTED_PROTOCOLS}",
            "supported": list(SUPPORTED_PROTOCOLS),
        }
    return DECODERS[protocol](data, sample_rate)
