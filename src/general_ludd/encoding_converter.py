"""Encoding converter — detect, decode, and convert between text encodings.

UTF-8, UTF-16 (LE/BE), UTF-32 (LE/BE), ISO-8859-1, CP-1252, BOM detection,
roundtrip, and invalid-byte handling.
"""

from __future__ import annotations

from collections.abc import Sequence

BOM_SIGNATURES: dict[str, bytes] = {
    "utf-32-le": b"\xff\xfe\x00\x00",
    "utf-32-be": b"\x00\x00\xfe\xff",
    "utf-8": b"\xef\xbb\xbf",
    "utf-16-le": b"\xff\xfe",
    "utf-16-be": b"\xfe\xff",
}

_BOM_LENGTHS: dict[str, int] = {k: len(v) for k, v in BOM_SIGNATURES.items()}


def _normalize_encoding(encoding: str) -> str:
    mapping = {
        "latin-1": "iso-8859-1",
        "latin1": "iso-8859-1",
        "cp-1252": "cp1252",
        "windows-1252": "cp1252",
    }
    return mapping.get(encoding.lower().replace("_", "-"), encoding.lower().replace("_", "-"))


def convert(
    data: str | bytes,
    from_encoding: str,
    to_encoding: str,
) -> str | bytes:
    if isinstance(data, str):
        raw = data.encode(_normalize_encoding(from_encoding))
    else:
        raw = data
        if from_encoding == to_encoding and isinstance(data, bytes):
            text = raw.decode(_normalize_encoding(from_encoding))
            if to_encoding == "utf-8":
                return text
            return text.encode(_normalize_encoding(to_encoding)).decode(_normalize_encoding(to_encoding))
    text = raw.decode(_normalize_encoding(from_encoding))
    if _normalize_encoding(to_encoding) == "utf-8":
        return text
    return text.encode(_normalize_encoding(to_encoding))


def detect_bom(data: bytes) -> str | None:
    if not data:
        return None
    for encoding in sorted(BOM_SIGNATURES, key=lambda e: _BOM_LENGTHS[e], reverse=True):
        sig = BOM_SIGNATURES[encoding]
        if data[: len(sig)] == sig:
            return encoding
    return None


def decode_with_bom(data: bytes) -> str:
    bom_encoding = detect_bom(data)
    if bom_encoding is None:
        return data.decode("utf-8")
    enc = "utf-8" if bom_encoding == "utf-8" else bom_encoding
    offset = len(BOM_SIGNATURES[bom_encoding])
    return data[offset:].decode(enc)


def decode_all(data: bytes, encodings: Sequence[str] | None = None) -> str:
    if encodings is None:
        encodings = ["utf-8", "utf-16-le", "iso-8859-1", "cp1252", "utf-32-le"]
    for enc in encodings:
        try:
            return data.decode(_normalize_encoding(enc))
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"Could not decode with any encoding: {list(encodings)}")


def guess_encoding(data: bytes) -> str | None:
    bom_enc = detect_bom(data)
    if bom_enc is not None:
        return bom_enc
    try:
        data.decode("ascii")
        return "ascii"
    except UnicodeDecodeError:
        pass
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    zero_even = len(data) >= 2 and all(data[i] == 0 for i in range(1, min(len(data), 128), 2))
    if zero_even:
        return "utf-16-be"
    zero_odd = len(data) >= 2 and all(data[i] == 0 for i in range(0, min(len(data), 128), 2))
    if zero_odd:
        return "utf-16-le"
    return None


def roundtrip(text: str, encoding: str) -> str:
    enc = _normalize_encoding(encoding)
    raw = text.encode(enc)
    decoded = raw.decode(enc)
    return decoded
