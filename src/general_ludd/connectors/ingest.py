"""Push-side ingest normalizer for the observability receiver (#82, receiver core).

This module is the *normalize* half of the push receiver: the daemon
``/api/ingest`` endpoint reads a raw request body + headers, calls
:func:`normalize`, and (post-commit) pushes the resulting records into a
:class:`~general_ludd.connectors.webhook_buffer.WebhookBufferSource` so they
appear alongside pulled sources via ``/api/observe``.

It is deliberately **self-contained** — it imports nothing from sibling
connectors or any connector base class. Every record it emits is a plain dict
matching the shared normalized-record schema::

    {
        "ts": float | None,          # epoch seconds the event occurred
        "source": str,               # logical source name (e.g. "ingest:otlp")
        "kind": str,                 # logs / traces / alerts / deploys / issues
        "level_or_status": str,      # log level or status ("firing"/"error"/...)
        "message": str,              # human-readable line
        "value": float | None,       # numeric payload (None for non-metrics)
        "labels": dict[str, Any],    # correlation keys (trace_id, host, ...)
        "raw": Any,                  # untouched source payload, for drill-down
    }

Supported formats (``fmt``):

* ``"otlp"``   — OTLP/JSON logs (``resourceLogs``) and spans (``resourceSpans``)
  -> ``logs`` / ``traces`` records; labels carry ``trace_id`` / ``span_id`` /
  ``service``.
* ``"webhook"`` — a generic JSON body; the shape is *sniffed* (GitHub event via
  the ``X-GitHub-Event`` header, Alertmanager ``alerts[]`` payload, or a generic
  fallback) -> ``alerts`` / ``deploys`` / ``issues`` / ``logs`` records.
* ``"syslog"``  — one or more RFC 5424 / RFC 3164 lines -> ``logs`` records;
  labels carry ``host`` / ``severity``.

Hard guarantees:

* **Fail-soft**: a malformed / unrecognized / oversized payload returns ``[]``.
  :func:`normalize` never raises.
* **Bounded**: payloads larger than :data:`MAX_PAYLOAD_BYTES` are dropped
  (return ``[]``) before any parsing work happens.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

# Upper bound on a single ingest payload. Anything larger is dropped wholesale
# (returns []) — the receiver must never buffer an unbounded request body.
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB

# Syslog numeric severity -> textual level (RFC 5424 §6.2.1).
_SYSLOG_SEVERITY_NAME = {
    0: "emerg",
    1: "alert",
    2: "crit",
    3: "error",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}

_PRI_RE = re.compile(r"^<(\d{1,3})>")


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #
def normalize(fmt: str, payload: bytes, headers: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a pushed ``payload`` of format ``fmt`` into records.

    Never raises. Returns ``[]`` for an unknown format, an oversized payload, or
    any payload the format-specific parser cannot make sense of.
    """
    if not isinstance(payload, (bytes, bytearray)):
        return []
    if len(payload) > MAX_PAYLOAD_BYTES:
        return []

    headers = headers or {}
    try:
        if fmt == "otlp":
            return _normalize_otlp(bytes(payload))
        if fmt == "webhook":
            return _normalize_webhook(bytes(payload), headers)
        if fmt == "syslog":
            return _normalize_syslog(bytes(payload))
    except Exception:
        # Fail-soft: any parser blowing up degrades to "no records", never a raise.
        return []
    return []


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _record(
    *,
    source: str,
    kind: str,
    message: str = "",
    ts: float | None = None,
    level_or_status: str = "info",
    value: float | None = None,
    labels: dict[str, Any] | None = None,
    raw: Any = None,
) -> dict[str, Any]:
    """Build a normalized-record dict with all eight keys present."""
    return {
        "ts": ts,
        "source": source,
        "kind": kind,
        "level_or_status": level_or_status,
        "message": message,
        "value": value,
        "labels": labels if labels is not None else {},
        "raw": raw,
    }


def _loads(payload: bytes) -> Any:
    """Parse JSON bytes, or return ``None`` on any decode/parse error."""
    try:
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _ns_to_seconds(value: Any) -> float | None:
    """Convert a UnixNano timestamp (int/str) to epoch seconds, or ``None``."""
    if value is None:
        return None
    try:
        return int(value) / 1_000_000_000
    except (TypeError, ValueError):
        return None


def _otlp_attr_value(value: Any) -> Any:
    """Unwrap an OTLP ``AnyValue`` dict into a plain python scalar."""
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "intValue", "doubleValue"):
        if key in value:
            return value[key]
    return value


def _otlp_attrs(attributes: Any) -> dict[str, Any]:
    """Flatten an OTLP ``[{key, value}]`` attribute list into a dict."""
    out: dict[str, Any] = {}
    if isinstance(attributes, list):
        for attr in attributes:
            if isinstance(attr, dict) and "key" in attr:
                out[str(attr["key"])] = _otlp_attr_value(attr.get("value"))
    return out


# --------------------------------------------------------------------------- #
# OTLP
# --------------------------------------------------------------------------- #
def _normalize_otlp(payload: bytes) -> list[dict[str, Any]]:
    data = _loads(payload)
    if not isinstance(data, dict):
        return []
    records: list[dict[str, Any]] = []
    records.extend(_otlp_logs(data.get("resourceLogs")))
    records.extend(_otlp_spans(data.get("resourceSpans")))
    return records


def _otlp_logs(resource_logs: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not isinstance(resource_logs, list):
        return records
    for rl in resource_logs:
        if not isinstance(rl, dict):
            continue
        resource = rl.get("resource") or {}
        res_attrs = _otlp_attrs(resource.get("attributes")) if isinstance(resource, dict) else {}
        service = res_attrs.get("service.name")
        for scope in rl.get("scopeLogs") or []:
            if not isinstance(scope, dict):
                continue
            for lr in scope.get("logRecords") or []:
                if not isinstance(lr, dict):
                    continue
                records.append(_otlp_log_record(lr, service))
    return records


def _otlp_log_record(lr: dict[str, Any], service: Any) -> dict[str, Any]:
    body = lr.get("body")
    message = str(_otlp_attr_value(body)) if body is not None else ""
    ts = _ns_to_seconds(lr.get("timeUnixNano") or lr.get("observedTimeUnixNano"))
    labels: dict[str, Any] = {}
    if lr.get("traceId"):
        labels["trace_id"] = lr["traceId"]
    if lr.get("spanId"):
        labels["span_id"] = lr["spanId"]
    if service is not None:
        labels["service"] = service
    level = str(lr.get("severityText") or "info")
    return _record(
        source="ingest:otlp",
        kind="logs",
        message=message,
        ts=ts,
        level_or_status=level,
        labels=labels,
        raw=lr,
    )


def _otlp_spans(resource_spans: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not isinstance(resource_spans, list):
        return records
    for rs in resource_spans:
        if not isinstance(rs, dict):
            continue
        resource = rs.get("resource") or {}
        res_attrs = _otlp_attrs(resource.get("attributes")) if isinstance(resource, dict) else {}
        service = res_attrs.get("service.name")
        for scope in rs.get("scopeSpans") or []:
            if not isinstance(scope, dict):
                continue
            for span in scope.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                records.append(_otlp_span_record(span, service))
    return records


def _otlp_span_record(span: dict[str, Any], service: Any) -> dict[str, Any]:
    ts = _ns_to_seconds(span.get("startTimeUnixNano"))
    labels: dict[str, Any] = {}
    if span.get("traceId"):
        labels["trace_id"] = span["traceId"]
    if span.get("spanId"):
        labels["span_id"] = span["spanId"]
    if span.get("parentSpanId"):
        labels["parent_span_id"] = span["parentSpanId"]
    if service is not None:
        labels["service"] = service
    return _record(
        source="ingest:otlp",
        kind="traces",
        message=str(span.get("name") or ""),
        ts=ts,
        level_or_status="",
        labels=labels,
        raw=span,
    )


# --------------------------------------------------------------------------- #
# Webhook
# --------------------------------------------------------------------------- #
def _header_get(headers: dict[str, Any], name: str) -> Any:
    """Case-insensitive header lookup."""
    target = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target:
            return value
    return None


def _normalize_webhook(payload: bytes, headers: dict[str, Any]) -> list[dict[str, Any]]:
    data = _loads(payload)
    if not isinstance(data, dict):
        return []

    gh_event = _header_get(headers, "X-GitHub-Event")
    if gh_event is not None:
        return [_github_record(str(gh_event), data)]

    if isinstance(data.get("alerts"), list):
        return _alertmanager_records(data["alerts"])

    # Generic JSON -> a single log record.
    message = str(data.get("message") or data.get("text") or "")
    return [
        _record(
            source="ingest:webhook",
            kind="logs",
            message=message,
            ts=time.time(),
            labels={},
            raw=data,
        )
    ]


# GitHub event name -> normalized record kind.
_GITHUB_KIND = {
    "deployment": "deploys",
    "deployment_status": "deploys",
    "push": "deploys",
    "release": "deploys",
    "issues": "issues",
    "issue_comment": "issues",
    "pull_request": "issues",
}


def _github_record(event: str, data: dict[str, Any]) -> dict[str, Any]:
    kind = _GITHUB_KIND.get(event, "logs")
    labels: dict[str, Any] = {"event": event}
    repo = data.get("repository")
    if isinstance(repo, dict) and repo.get("full_name"):
        labels["repo"] = repo["full_name"]
    if data.get("action"):
        labels["action"] = data["action"]

    message = ""
    deployment = data.get("deployment")
    if isinstance(deployment, dict):
        if deployment.get("sha"):
            labels["sha"] = deployment["sha"]
        if deployment.get("environment"):
            labels["environment"] = deployment["environment"]
        message = f"deployment -> {deployment.get('environment', '')}".strip()
    issue = data.get("issue")
    if isinstance(issue, dict):
        if issue.get("number") is not None:
            labels["issue"] = issue["number"]
        message = str(issue.get("title") or message)
    if not message:
        message = f"github:{event}"

    return _record(
        source="ingest:webhook",
        kind=kind,
        message=message,
        ts=time.time(),
        level_or_status=str(data.get("action") or "info"),
        labels=labels,
        raw=data,
    )


def _alertmanager_records(alerts: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        labels = dict(alert.get("labels") or {}) if isinstance(alert.get("labels"), dict) else {}
        annotations = alert.get("annotations") or {}
        summary = ""
        if isinstance(annotations, dict):
            summary = str(annotations.get("summary") or annotations.get("description") or "")
        status = str(alert.get("status") or "firing")
        message = summary or str(labels.get("alertname") or "alert")
        records.append(
            _record(
                source="ingest:webhook",
                kind="alerts",
                message=message,
                ts=_parse_iso8601(alert.get("startsAt")),
                level_or_status=status,
                labels=labels,
                raw=alert,
            )
        )
    return records


def _parse_iso8601(value: Any) -> float | None:
    """Parse an ISO-8601 'Z' timestamp to epoch seconds, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.timestamp()


# --------------------------------------------------------------------------- #
# Syslog
# --------------------------------------------------------------------------- #
def _normalize_syslog(payload: bytes) -> list[dict[str, Any]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return []
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rec = _syslog_line(line)
        if rec is not None:
            records.append(rec)
    return records


def _syslog_line(line: str) -> dict[str, Any] | None:
    """Parse one RFC 5424 / 3164 line. Returns ``None`` if there is no PRI."""
    match = _PRI_RE.match(line)
    if not match:
        return None
    pri = int(match.group(1))
    if pri > 191:  # PRI is facility*8 + severity; max valid is 23*8+7 = 191
        return None
    severity = pri % 8
    rest = line[match.end():].strip()

    host, message = _syslog_host_and_msg(rest)
    labels: dict[str, Any] = {"severity": severity, "severity_name": _SYSLOG_SEVERITY_NAME[severity]}
    if host:
        labels["host"] = host

    return _record(
        source="ingest:syslog",
        kind="logs",
        message=message,
        ts=None,
        level_or_status=_SYSLOG_SEVERITY_NAME[severity],
        labels=labels,
        raw=line,
    )


def _syslog_host_and_msg(rest: str) -> tuple[str, str]:
    """Extract (host, message) from the post-PRI remainder.

    Handles RFC 5424 (``1 TIMESTAMP HOST APP ...``) and RFC 3164
    (``MMM DD HH:MM:SS HOST TAG: msg``), plus a bare ``HOST msg`` fallback.
    """
    tokens = rest.split()
    if not tokens:
        return "", ""

    # RFC 5424: VERSION is a digit; HOSTNAME is the 3rd token (after timestamp).
    if tokens[0].isdigit() and len(tokens) >= 3:
        host = tokens[2]
        return host, " ".join(tokens[3:])

    # RFC 3164: "MMM DD HH:MM:SS HOST ..." — month name + day + clock + host.
    if (
        len(tokens) >= 5
        and len(tokens[0]) == 3
        and tokens[0].isalpha()
        and tokens[1].isdigit()
        and ":" in tokens[2]
    ):
        host = tokens[3]
        return host, " ".join(tokens[4:])

    # Fallback: first token is the host, the rest is the message.
    return tokens[0], " ".join(tokens[1:])
