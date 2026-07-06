"""Pure, fail-soft parsers for push-side telemetry wire formats.

This is the receiver-side sibling of
:mod:`general_ludd.connectors.ingest_formats`. It adds parsers for the formats a
push-side endpoint must terminate that the connector layer did not cover:

* ``parse_otlp_logs`` / ``parse_otlp_metrics`` / ``parse_otlp_traces`` — the
  OTLP/HTTP **JSON** encoding (``application/json``) of the OpenTelemetry
  ``ExportLogsServiceRequest`` / ``ExportMetricsServiceRequest`` /
  ``ExportTraceServiceRequest`` envelopes. Protobuf (``application/x-protobuf``)
  is supported *only* when the optional ``opentelemetry-proto`` dependency is
  importable (guarded) — otherwise it fails soft, exactly like the msgpack path
  in :func:`~general_ludd.connectors.ingest_formats.parse_fluent_forward`.
* ``parse_syslog`` — RFC 3164 (BSD) and RFC 5424 syslog lines.

The fluent / beats / gelf formats are NOT re-implemented here — the router
imports and reuses :mod:`general_ludd.connectors.ingest_formats` for those.

Every parser obeys the same hard invariants as ``ingest_formats``:

* **pure** — no I/O, no global state; bytes/str in, ``list[dict]`` out;
* **fail-soft** — malformed input yields ``[]`` and NEVER raises;
* **size-capped** — a payload over :data:`MAX_PAYLOAD_BYTES` is rejected *before*
  any parse/allocate work; output is capped at :data:`MAX_EVENTS` records.

The normalized record shape matches ``ingest_formats`` so the same
:func:`general_ludd.connectors.normalize.normalize_join_keys` pass applies::

    {
        "ts": ..., "source": ..., "kind": "log"|"metric"|"trace",
        "level_or_status": ..., "message": ..., "value": ...,
        "labels": {...}, "raw": ...,
    }
"""

from __future__ import annotations

import json
import re
from typing import Any

# Reuse the connector layer's caps verbatim so the two ingest planes agree on a
# single payload/size policy (and a single place to tune it).
from general_ludd.connectors.ingest_formats import MAX_EVENTS, MAX_PAYLOAD_BYTES

__all__ = [
    "MAX_EVENTS",
    "MAX_PAYLOAD_BYTES",
    "parse_otlp_logs",
    "parse_otlp_metrics",
    "parse_otlp_traces",
    "parse_syslog",
]

# OTLP severityNumber (0..24) -> coarse name. Mirrors the OpenTelemetry logs spec
# severity bands; normalize_join_keys collapses these further into its canonical
# 5-level severity, so we only need a faithful coarse name here.
_OTLP_SEVERITY_BANDS: list[tuple[int, str]] = [
    (1, "trace"),
    (5, "debug"),
    (9, "info"),
    (13, "warn"),
    (17, "error"),
    (21, "fatal"),
]

# Syslog numeric severity (PRI % 8) -> name (RFC 5424 table 2).
_SYSLOG_SEVERITY: dict[int, str] = {
    0: "emergency",
    1: "alert",
    2: "critical",
    3: "error",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}


def _new_record(
    *,
    ts: object,
    source: str,
    kind: str,
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
        "kind": kind,
        "level_or_status": level_or_status,
        "message": message,
        "value": value,
        "labels": labels,
        "raw": raw,
    }


def _too_big(payload: bytes | str) -> bool:
    if isinstance(payload, str):
        # Measure the encoded size, not the character count.
        return len(payload.encode("utf-8", "ignore")) > MAX_PAYLOAD_BYTES
    return len(payload) > MAX_PAYLOAD_BYTES


def _decode_json(payload: bytes) -> Any:
    """Decode JSON bytes, fail-soft to ``None``."""
    try:
        return json.loads(payload)
    except (ValueError, TypeError):
        return None


def _otlp_severity_name(number: object) -> object:
    """Map an OTLP severityNumber to a coarse name; pass through anything else."""
    if not isinstance(number, int) or isinstance(number, bool):
        return None
    name: object = None
    for threshold, label in _OTLP_SEVERITY_BANDS:
        if number >= threshold:
            name = label
    return name


def _otlp_any_value(value: object) -> object:
    """Unwrap an OTLP ``AnyValue`` JSON object into a plain Python value.

    OTLP/JSON wraps every attribute value in a single-key type tag, e.g.
    ``{"stringValue": "x"}`` / ``{"intValue": "5"}`` / ``{"boolValue": true}``.
    Unknown / malformed shapes pass through unchanged (fail-soft).
    """
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "intValue" in value:
        raw = value["intValue"]
        try:
            return int(raw)
        except (ValueError, TypeError):
            return raw
    if "doubleValue" in value:
        raw = value["doubleValue"]
        try:
            return float(raw)
        except (ValueError, TypeError):
            return raw
    if "arrayValue" in value:
        arr = value.get("arrayValue")
        if isinstance(arr, dict) and isinstance(arr.get("values"), list):
            return [_otlp_any_value(v) for v in arr["values"]]
        return value
    return value


def _otlp_attributes(attrs: object) -> dict[str, object]:
    """Fold an OTLP ``attributes`` list (``[{key, value}, ...]``) into a dict."""
    out: dict[str, object] = {}
    if not isinstance(attrs, list):
        return out
    for attr in attrs:
        if not isinstance(attr, dict):
            continue
        key = attr.get("key")
        if not isinstance(key, str):
            continue
        out[key] = _otlp_any_value(attr.get("value"))
    return out


def _otlp_resource_labels(resource: object) -> dict[str, object]:
    """Pull resource-level attributes (service.name etc.) into a label dict."""
    if not isinstance(resource, dict):
        return {}
    labels = _otlp_attributes(resource.get("attributes"))
    # Surface the common service.name alias as ``service`` for join keys.
    if "service.name" in labels and "service" not in labels:
        labels["service"] = labels["service.name"]
    return labels


def _otlp_ts_seconds(nano: object) -> object:
    """Convert an OTLP unix-nano timestamp (often a JSON string) to float seconds."""
    if not isinstance(nano, (str, int)) or nano == "" or isinstance(nano, bool):
        return None
    try:
        return int(nano) / 1_000_000_000
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# OTLP logs                                                                    #
# --------------------------------------------------------------------------- #


def parse_otlp_logs(payload: bytes, *, content_type: str | None = None) -> list[dict[str, object]]:
    """Parse an OTLP/HTTP ``ExportLogsServiceRequest`` into normalized records.

    Accepts the OTLP/JSON encoding by default. When ``content_type`` indicates
    protobuf (``application/x-protobuf``), the binary body is decoded *only* if
    the optional ``opentelemetry-proto`` dependency is importable; otherwise the
    function fails soft (``[]``).

    Returns ``[]`` for any non-bytes, oversized, malformed, or empty payload.
    """
    doc = _otlp_load(payload, content_type, "logs")
    if doc is None:
        return []

    records: list[dict[str, object]] = []
    for rl in _as_list(doc.get("resourceLogs")):
        if not isinstance(rl, dict):
            continue
        res_labels = _otlp_resource_labels(rl.get("resource"))
        for sl in _as_list(rl.get("scopeLogs")) or _as_list(rl.get("instrumentationLibraryLogs")):
            if not isinstance(sl, dict):
                continue
            for rec in _as_list(sl.get("logRecords")):
                if len(records) >= MAX_EVENTS:
                    return records
                if not isinstance(rec, dict):
                    continue
                records.append(_otlp_log_record(rec, res_labels))
    return records


def _otlp_log_record(rec: dict[str, Any], res_labels: dict[str, object]) -> dict[str, object]:
    labels: dict[str, object] = dict(res_labels)
    labels.update(_otlp_attributes(rec.get("attributes")))
    trace_id = rec.get("traceId")
    if isinstance(trace_id, str) and trace_id:
        labels.setdefault("trace_id", trace_id)

    body = _otlp_any_value(rec.get("body"))
    message = body if isinstance(body, str) else ("" if body is None else str(body))
    severity = rec.get("severityText") or _otlp_severity_name(rec.get("severityNumber"))
    ts = _otlp_ts_seconds(rec.get("timeUnixNano") or rec.get("observedTimeUnixNano"))
    return _new_record(
        ts=ts,
        source=str(res_labels.get("service") or res_labels.get("service.name") or "otlp"),
        kind="log",
        level_or_status=severity,
        message=message,
        labels=labels,
        raw=rec,
    )


# --------------------------------------------------------------------------- #
# OTLP metrics                                                                 #
# --------------------------------------------------------------------------- #


def parse_otlp_metrics(payload: bytes, *, content_type: str | None = None) -> list[dict[str, object]]:
    """Parse an OTLP/HTTP ``ExportMetricsServiceRequest`` into normalized records.

    One record per numeric data point. Gauge / sum (counter) number points are
    covered; other point kinds are skipped (fail-soft). Protobuf is guarded as in
    :func:`parse_otlp_logs`.

    Returns ``[]`` for any non-bytes, oversized, malformed, or empty payload.
    """
    doc = _otlp_load(payload, content_type, "metrics")
    if doc is None:
        return []

    records: list[dict[str, object]] = []
    for rm in _as_list(doc.get("resourceMetrics")):
        if not isinstance(rm, dict):
            continue
        res_labels = _otlp_resource_labels(rm.get("resource"))
        for sm in _as_list(rm.get("scopeMetrics")) or _as_list(rm.get("instrumentationLibraryMetrics")):
            if not isinstance(sm, dict):
                continue
            for metric in _as_list(sm.get("metrics")):
                if not isinstance(metric, dict):
                    continue
                if not _otlp_metric_points(metric, res_labels, records):
                    return records
    return records


def _otlp_metric_points(
    metric: dict[str, Any],
    res_labels: dict[str, object],
    out: list[dict[str, object]],
) -> bool:
    """Append number data points of one metric. Returns False once MAX_EVENTS hit."""
    name = metric.get("name")
    name_s = name if isinstance(name, str) else "metric"
    for container_key in ("gauge", "sum"):
        container = metric.get(container_key)
        if not isinstance(container, dict):
            continue
        for point in _as_list(container.get("dataPoints")):
            if len(out) >= MAX_EVENTS:
                return False
            if not isinstance(point, dict):
                continue
            value = point.get("asDouble")
            if value is None:
                value = point.get("asInt")
                if isinstance(value, str):
                    try:
                        value = int(value)
                    except ValueError:
                        value = None
            labels: dict[str, object] = dict(res_labels)
            labels.update(_otlp_attributes(point.get("attributes")))
            labels["metric"] = name_s
            out.append(
                _new_record(
                    ts=_otlp_ts_seconds(point.get("timeUnixNano")),
                    source=str(res_labels.get("service") or "otlp"),
                    kind="metric",
                    level_or_status="info",
                    message=name_s,
                    labels=labels,
                    raw=point,
                    value=value if isinstance(value, (int, float)) else None,
                )
            )
    return True


# --------------------------------------------------------------------------- #
# OTLP traces                                                                  #
# --------------------------------------------------------------------------- #


def parse_otlp_traces(payload: bytes, *, content_type: str | None = None) -> list[dict[str, object]]:
    """Parse an OTLP/HTTP ``ExportTraceServiceRequest`` into normalized records.

    One record per span. ``traceId`` / ``spanId`` are surfaced into ``labels`` so
    spans correlate with logs on ``trace_id``. Protobuf is guarded as in
    :func:`parse_otlp_logs`.

    Returns ``[]`` for any non-bytes, oversized, malformed, or empty payload.
    """
    doc = _otlp_load(payload, content_type, "traces")
    if doc is None:
        return []

    records: list[dict[str, object]] = []
    for rs in _as_list(doc.get("resourceSpans")):
        if not isinstance(rs, dict):
            continue
        res_labels = _otlp_resource_labels(rs.get("resource"))
        for ss in _as_list(rs.get("scopeSpans")) or _as_list(rs.get("instrumentationLibrarySpans")):
            if not isinstance(ss, dict):
                continue
            for span in _as_list(ss.get("spans")):
                if len(records) >= MAX_EVENTS:
                    return records
                if not isinstance(span, dict):
                    continue
                records.append(_otlp_span_record(span, res_labels))
    return records


def _otlp_span_record(span: dict[str, Any], res_labels: dict[str, object]) -> dict[str, object]:
    labels: dict[str, object] = dict(res_labels)
    labels.update(_otlp_attributes(span.get("attributes")))
    trace_id = span.get("traceId")
    if isinstance(trace_id, str) and trace_id:
        labels["trace_id"] = trace_id
    span_id = span.get("spanId")
    if isinstance(span_id, str) and span_id:
        labels["span_id"] = span_id

    name = span.get("name")
    status = span.get("status")
    status_code = status.get("code") if isinstance(status, dict) else None
    return _new_record(
        ts=_otlp_ts_seconds(span.get("startTimeUnixNano")),
        source=str(res_labels.get("service") or "otlp"),
        kind="trace",
        level_or_status=status_code if status_code is not None else "info",
        message=name if isinstance(name, str) else "",
        labels=labels,
        raw=span,
    )


# --------------------------------------------------------------------------- #
# OTLP shared loader (JSON default, guarded protobuf)                          #
# --------------------------------------------------------------------------- #


def _otlp_load(payload: bytes, content_type: str | None, signal: str) -> dict[str, Any] | None:
    """Load an OTLP envelope dict from JSON, or guarded protobuf, fail-soft."""
    if not isinstance(payload, (bytes, bytearray)):
        return None
    payload = bytes(payload)
    if not payload or _too_big(payload):
        return None

    ct = (content_type or "").lower()
    if "protobuf" in ct or "x-protobuf" in ct:
        return _otlp_load_protobuf(payload, signal)

    doc = _decode_json(payload)
    return doc if isinstance(doc, dict) else None


def _otlp_load_protobuf(payload: bytes, signal: str) -> dict[str, Any] | None:
    """Decode OTLP protobuf -> the JSON-equivalent dict, only if the dep exists.

    Mirrors the guarded-optional-dependency pattern of the msgpack branch in
    ``ingest_formats``: if ``opentelemetry-proto`` is not installed, fail soft.
    """
    try:
        from google.protobuf.json_format import MessageToDict

        if signal == "logs":
            from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (  # type: ignore[import-not-found]  # opentelemetry-proto: optional, guarded by try/except
                ExportLogsServiceRequest as _Req,
            )
        elif signal == "metrics":
            from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (  # type: ignore[import-not-found]  # opentelemetry-proto: optional, guarded by try/except
                ExportMetricsServiceRequest as _Req,
            )
        else:
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (  # type: ignore[import-not-found]  # opentelemetry-proto: optional, guarded by try/except
                ExportTraceServiceRequest as _Req,
            )
    except ImportError:
        return None
    try:
        msg = _Req()
        msg.ParseFromString(payload)
        decoded = MessageToDict(msg, preserving_proto_field_name=False)
        return decoded if isinstance(decoded, dict) else None
    except Exception:  # any protobuf decode error must fail soft
        return None


def _as_list(value: object) -> list[Any]:
    """Return ``value`` if it is a list, else an empty list (fail-soft)."""
    return value if isinstance(value, list) else []


# --------------------------------------------------------------------------- #
# Syslog (RFC 3164 + RFC 5424)                                                #
# --------------------------------------------------------------------------- #

# RFC 5424: <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [SD] MSG
_RFC5424_RE = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<ver>\d{1,2}) "
    r"(?P<ts>\S+) (?P<host>\S+) (?P<app>\S+) (?P<procid>\S+) (?P<msgid>\S+) "
    r"(?P<rest>.*)$",
    re.DOTALL,
)

# RFC 3164: <PRI>Mmm dd hh:mm:ss HOSTNAME TAG: MSG
_RFC3164_RE = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<ts>[A-Z][a-z]{2} +\d{1,2} \d{2}:\d{2}:\d{2}) "
    r"(?P<host>\S+) (?P<rest>.*)$",
    re.DOTALL,
)


def parse_syslog(payload: bytes | str) -> list[dict[str, object]]:
    """Parse one or more newline-separated syslog lines into normalized records.

    Auto-detects RFC 5424 (the versioned ``<PRI>1 ...`` form) and falls back to
    RFC 3164 (the BSD ``<PRI>Mmm dd ...`` form). The PRI byte is split into a
    facility (``pri // 8``, surfaced as a label) and a severity (``pri % 8``,
    mapped to its name). A line that matches neither RFC is emitted as a best-
    effort record with the whole line as the message — never dropped, never
    raised.

    Returns ``[]`` for any non-bytes/str, oversized, or empty payload, and caps
    output at :data:`MAX_EVENTS` lines.
    """
    if not isinstance(payload, (bytes, bytearray, str)):
        return []
    if _too_big(payload):
        return []
    text = (
        bytes(payload).decode("utf-8", "replace")
        if isinstance(payload, (bytes, bytearray))
        else payload
    )
    if not text.strip():
        return []

    records: list[dict[str, object]] = []
    for line in text.splitlines():
        if len(records) >= MAX_EVENTS:
            break
        line = line.strip()
        if not line:
            continue
        records.append(_parse_syslog_line(line))
    return records


def _parse_syslog_line(line: str) -> dict[str, object]:
    m = _RFC5424_RE.match(line)
    if m is not None:
        return _syslog_record_5424(m, line)
    m = _RFC3164_RE.match(line)
    if m is not None:
        return _syslog_record_3164(m, line)
    # Unstructured line — keep it rather than drop it.
    return _new_record(
        ts=None,
        source="syslog",
        kind="log",
        level_or_status=None,
        message=line,
        labels={},
        raw=line,
    )


def _decode_pri(pri_text: str) -> tuple[object, object]:
    """Return ``(severity_name, facility_number)`` from a PRI string, fail-soft."""
    try:
        pri = int(pri_text)
    except (ValueError, TypeError):
        return None, None
    severity = _SYSLOG_SEVERITY.get(pri % 8, pri % 8)
    return severity, pri // 8


def _syslog_record_5424(m: re.Match[str], line: str) -> dict[str, object]:
    severity, facility = _decode_pri(m.group("pri"))
    host = m.group("host")
    app = m.group("app")
    labels: dict[str, object] = {"facility": facility, "app": app}
    if host not in (None, "-"):
        labels["host"] = host
    procid = m.group("procid")
    if procid not in (None, "-"):
        labels["procid"] = procid
    # Strip a leading structured-data block ``[...]`` / ``-`` from the message.
    rest = m.group("rest")
    message = _strip_structured_data(rest)
    return _new_record(
        ts=m.group("ts") if m.group("ts") != "-" else None,
        source=app if app not in (None, "-") else (host or "syslog"),
        kind="log",
        level_or_status=severity,
        message=message,
        labels=labels,
        raw=line,
    )


def _strip_structured_data(rest: str) -> str:
    """Drop a leading RFC5424 SD-ELEMENT (``[...]``) or NILVALUE (``-``)."""
    rest = rest.lstrip()
    if rest.startswith("-"):
        return rest[1:].lstrip()
    if rest.startswith("["):
        depth = 0
        for i, ch in enumerate(rest):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return rest[i + 1 :].lstrip()
    return rest


def _syslog_record_3164(m: re.Match[str], line: str) -> dict[str, object]:
    severity, facility = _decode_pri(m.group("pri"))
    host = m.group("host")
    rest = m.group("rest")
    labels: dict[str, object] = {"facility": facility}
    if host not in (None, "-"):
        labels["host"] = host
    # RFC 3164 TAG: optional ``tag[pid]:`` prefix on the message.
    tag = None
    tag_match = re.match(r"^(?P<tag>[^\s:\[]+)(?:\[\d+\])?:\s*(?P<msg>.*)$", rest, re.DOTALL)
    if tag_match is not None:
        tag = tag_match.group("tag")
        labels["tag"] = tag
        message = tag_match.group("msg")
    else:
        message = rest
    return _new_record(
        ts=m.group("ts"),
        source=tag or host or "syslog",
        kind="log",
        level_or_status=severity,
        message=message,
        labels=labels,
        raw=line,
    )
