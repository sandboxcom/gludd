"""Pure, fail-soft parsers for push-side log-shipping wire formats.

The gludd receiver endpoint accepts bytes pushed by common log shippers and must
turn them into normalized records without trusting the sender. Each parser here is:

* **pure** — no I/O, no global state; bytes/decoded-events in, list of dicts out;
* **self-contained** — emits plain dicts (no model/base imports), so the receiver
  layer can map them onto whatever record type it likes;
* **fail-soft** — malformed input yields ``[]`` and *never* raises;
* **payload-size bounded** — oversized input is rejected up front, so a hostile or
  runaway sender cannot make a parser allocate without limit.

The normalized record shape (one dict per log line) is::

    {
        "ts":              <timestamp as sent: int | float | str>,
        "source":          <origin/tag/host string>,
        "kind":            "log",
        "level_or_status": <severity name|number|string>,
        "message":         <human-readable message string>,
        "value":           <numeric value if any, else None>,
        "labels":          <dict[str, object] of dimensional fields>,
        "raw":             <the original decoded record>,
    }

Three formats are covered:

``parse_fluent_forward``  Fluent Forward protocol, JSON mode — the
    ``[tag, [[time, record], ...]]`` array form. msgpack (binary) mode is detected
    and decoded *only* if ``msgpack`` is importable (guarded), else it fails soft.

``parse_beats_lumberjack``  Elastic Beats / Lumberjack v2 window — given the JSON
    events already decoded from the compressed window frames. Beat/host metadata is
    surfaced into ``labels`` (``host``, ``beat``).

``parse_gelf``  Graylog GELF (JSON). Chunked GELF (magic ``0x1e 0x0f``) is detected
    and reassembled *only* when every chunk of a single message is supplied in the
    one payload; a partial set fails soft. ``level`` is mapped to its syslog name.
"""

from __future__ import annotations

import json
import struct
from typing import Any

__all__ = [
    "MAX_EVENTS",
    "MAX_PAYLOAD_BYTES",
    "parse_beats_lumberjack",
    "parse_fluent_forward",
    "parse_gelf",
]

# Hard upper bound on a single pushed payload. Anything larger is rejected before
# any parse/allocate work — a fail-soft DoS guard, not a correctness limit.
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024  # 8 MiB

# Cap on the number of events/records produced from one payload, so a payload that
# is small-but-deeply-nested (or a huge decoded event list) cannot explode output.
MAX_EVENTS = 100_000

# Syslog severity number -> name (GELF reuses syslog levels 0..7).
_SYSLOG_LEVELS: dict[int, str] = {
    0: "emergency",
    1: "alert",
    2: "critical",
    3: "error",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}

# GELF chunked-message magic header bytes.
_GELF_CHUNK_MAGIC = b"\x1e\x0f"
# magic(2) + message_id(8) + sequence_number(1) + sequence_count(1) = 12-byte header
_GELF_CHUNK_HEADER_LEN = 12


def _new_record(
    *,
    ts: object,
    source: str,
    level_or_status: object,
    message: str,
    labels: dict[str, object],
    raw: object,
    value: object = None,
) -> dict[str, object]:
    """Build a normalized record dict with the canonical key set."""
    return {
        "ts": ts,
        "source": source,
        "kind": "log",
        "level_or_status": level_or_status,
        "message": message,
        "value": value,
        "labels": labels,
        "raw": raw,
    }


def _too_big(payload: bytes) -> bool:
    return len(payload) > MAX_PAYLOAD_BYTES


# --------------------------------------------------------------------------- #
# Fluent Forward                                                              #
# --------------------------------------------------------------------------- #


def parse_fluent_forward(payload: bytes) -> list[dict[str, object]]:
    """Parse a Fluent Forward message into normalized log records.

    Accepts the JSON-mode *Forward* array form ``[tag, [[time, record], ...]]``.
    If the payload is msgpack-encoded (binary mode), it is decoded only when the
    optional ``msgpack`` dependency is importable; otherwise the function fails
    soft and returns ``[]``.

    Returns ``[]`` for any non-bytes, oversized, malformed, or empty payload.
    """
    if not isinstance(payload, (bytes, bytearray)):
        return []
    payload = bytes(payload)
    if _too_big(payload):
        return []

    decoded = _decode_fluent_payload(payload)
    if decoded is None:
        return []

    # Expect: [tag, entries] (a 3rd element "option" map is allowed and ignored).
    if not isinstance(decoded, list) or len(decoded) < 2:
        return []
    tag, entries = decoded[0], decoded[1]
    if not isinstance(tag, str) or not isinstance(entries, list):
        return []

    records: list[dict[str, object]] = []
    for entry in entries:
        if len(records) >= MAX_EVENTS:
            break
        # Each entry is a [time, record] pair.
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        ts, rec = entry[0], entry[1]
        if not isinstance(rec, dict):
            continue

        labels: dict[str, object] = {"tag": tag}
        for k, v in rec.items():
            if k == "message":
                continue
            labels[str(k)] = v
        message = rec.get("message", "")
        records.append(
            _new_record(
                ts=ts,
                source=tag,
                level_or_status=rec.get("level"),
                message=message if isinstance(message, str) else str(message),
                labels=labels,
                raw=rec,
            )
        )
    return records


def _decode_fluent_payload(payload: bytes) -> Any:
    """Decode a Fluent payload as JSON, falling back to guarded msgpack.

    Returns the decoded object, or ``None`` if it cannot be decoded by any
    available codec.
    """
    try:
        return json.loads(payload)
    except (ValueError, TypeError):
        pass
    # Binary (msgpack) mode — only attempt if the optional dep is present.
    try:
        import importlib

        msgpack = importlib.import_module("msgpack")  # msgpack: required dep but ships no py.typed marker
    except ImportError:
        return None
    try:
        return msgpack.unpackb(payload, raw=False)
    except Exception:  # any msgpack decode error must fail soft
        return None


# --------------------------------------------------------------------------- #
# Beats / Lumberjack v2                                                       #
# --------------------------------------------------------------------------- #


def parse_beats_lumberjack(frames: list[dict[str, object]]) -> list[dict[str, object]]:
    """Parse already-decoded Lumberjack v2 JSON events into normalized records.

    ``frames`` is the list of JSON event documents extracted from a Lumberjack
    *window* (the caller is responsible for decompressing/decoding the wire frames
    into JSON, since that is transport, not format, concern). Beat and host
    metadata are surfaced into ``labels`` (``host``, ``beat``).

    Returns ``[]`` for a non-list, empty, or over-large event set.
    """
    if not isinstance(frames, list):
        return []
    if not frames or len(frames) > MAX_EVENTS:
        return []

    records: list[dict[str, object]] = []
    for event in frames:
        if not isinstance(event, dict):
            continue

        labels: dict[str, object] = {}
        host = _beats_host(event)
        if host is not None:
            labels["host"] = host
        beat = _beats_agent_type(event)
        if beat is not None:
            labels["beat"] = beat

        records.append(
            _new_record(
                ts=event.get("@timestamp") or event.get("timestamp"),
                source="beats",
                level_or_status=_beats_level(event),
                message=_beats_message(event),
                labels=labels,
                raw=event,
            )
        )
    return records


def _beats_host(event: dict[str, object]) -> object:
    host = event.get("host")
    if isinstance(host, dict):
        return host.get("name") or host.get("hostname")
    return host


def _beats_agent_type(event: dict[str, object]) -> object:
    agent = event.get("agent")
    if isinstance(agent, dict):
        return agent.get("type")
    return event.get("beat")


def _beats_level(event: dict[str, object]) -> object:
    log = event.get("log")
    if isinstance(log, dict) and "level" in log:
        return log["level"]
    return event.get("level")


def _beats_message(event: dict[str, object]) -> str:
    msg = event.get("message")
    if isinstance(msg, str):
        return msg
    event_field = event.get("event")
    if isinstance(event_field, dict):
        original = event_field.get("original")
        if isinstance(original, str):
            return original
    return msg if isinstance(msg, str) else ("" if msg is None else str(msg))


# --------------------------------------------------------------------------- #
# GELF                                                                        #
# --------------------------------------------------------------------------- #


def parse_gelf(payload: bytes) -> list[dict[str, object]]:
    """Parse a Graylog GELF payload into a normalized log record.

    Handles plain GELF JSON and chunked GELF (magic ``0x1e 0x0f``). For chunked
    input, all chunks of the message must be present in this one payload; a
    partial set fails soft (``[]``). ``level`` (a syslog severity number) is mapped
    to its name where known, and additional ``_field`` keys become labels.

    Returns ``[]`` for any non-bytes, oversized, malformed, or non-object payload,
    or when ``short_message`` is absent.
    """
    if not isinstance(payload, (bytes, bytearray)):
        return []
    payload = bytes(payload)
    if _too_big(payload):
        return []

    if payload[:2] == _GELF_CHUNK_MAGIC:
        assembled = _reassemble_gelf_chunks(payload)
        if assembled is None:
            return []
        payload = assembled

    try:
        doc = json.loads(payload)
    except (ValueError, TypeError):
        return []
    if not isinstance(doc, dict):
        return []

    short_message = doc.get("short_message")
    if not isinstance(short_message, str):
        return []

    host = doc.get("host")
    labels: dict[str, object] = {}
    if host is not None:
        labels["host"] = host
    # GELF additional fields are prefixed with an underscore; strip it for labels.
    for k, v in doc.items():
        if isinstance(k, str) and k.startswith("_"):
            labels[k[1:]] = v

    raw_level = doc.get("level", 1)  # GELF default severity is 1 (alert)
    level: object = _SYSLOG_LEVELS.get(raw_level, raw_level) if isinstance(raw_level, int) else raw_level

    return [
        _new_record(
            ts=doc.get("timestamp"),
            source=host if isinstance(host, str) else "gelf",
            level_or_status=level,
            message=short_message,
            labels=labels,
            raw=doc,
        )
    ]


def _reassemble_gelf_chunks(payload: bytes) -> bytes | None:
    """Reassemble a sequence of concatenated GELF chunks into the original bytes.

    Each chunk is ``magic(2) | message_id(8) | seq_no(1) | seq_count(1) | data``.
    Returns the concatenated data only when every chunk of the (single) message id
    is present and the chunk stream is well-formed; otherwise ``None``.
    """
    chunks: dict[int, bytes] = {}
    expected_count: int | None = None
    message_id: bytes | None = None
    offset = 0
    total = len(payload)

    while offset < total:
        if payload[offset : offset + 2] != _GELF_CHUNK_MAGIC:
            return None
        if offset + _GELF_CHUNK_HEADER_LEN > total:
            return None
        mid = payload[offset + 2 : offset + 10]
        seq_no, seq_count = struct.unpack_from(">BB", payload, offset + 10)
        if seq_count == 0 or seq_no >= seq_count:
            return None

        if message_id is None:
            message_id = mid
            expected_count = seq_count
        elif mid != message_id or seq_count != expected_count:
            # We only assemble a single message; mixed ids/counts fail soft.
            return None

        data_start = offset + _GELF_CHUNK_HEADER_LEN
        # Find where the next chunk begins (next magic), else end of payload.
        next_magic = payload.find(_GELF_CHUNK_MAGIC, data_start)
        data_end = next_magic if next_magic != -1 else total
        if seq_no in chunks:
            return None
        chunks[seq_no] = payload[data_start:data_end]
        offset = data_end

    if expected_count is None or len(chunks) != expected_count:
        return None
    return b"".join(chunks[i] for i in range(expected_count))
