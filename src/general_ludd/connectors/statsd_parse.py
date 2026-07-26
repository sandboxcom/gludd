"""StatsdParseSource — pure parser for the StatsD line protocol (KIND='metrics').

Self-contained: no imports from sibling connectors, no package base class,
NO network. This is the *parse half* of a future StatsD receiver; given a list
of already-received lines (``spec['lines']``) it normalizes each into a record.

StatsD line grammar (DogStatsD-compatible tags supported):

    metric:value|type[|@sample_rate][|#tag1:v1,tag2,tag3:v3]

Supported ``type`` codes: ``c`` (counter), ``g`` (gauge), ``ms`` (timer),
``h`` (histogram), ``s`` (set). Gauges may carry a sign delta (``+5`` / ``-3``);
the leading sign is preserved in ``labels['delta']`` and the numeric value keeps
its sign.

Each line becomes a normalized record:

    {
      'ts': None,                 # parse-only: no receive time available here
      'source': str,              # connector name
      'kind': 'metrics',
      'level_or_status': str,     # the StatsD type code ('c','g','ms','h','s')
      'message': str,             # the metric name
      'value': float|None,        # parsed numeric value (None for a set member)
      'labels': dict,             # parsed #tags (+ optional sample_rate/delta) +
                                  #   'set_value' for set members
      'raw': str,                 # the original line
    }
"""

from __future__ import annotations

from typing import Any

from general_ludd.connectors.normalize import sanitize_metric_value

_VALID_TYPES = {"c", "g", "ms", "h", "s"}
_NAME_PREFIXES = frozenset({"counters", "gauges", "histograms", "sets", "timers"})


def _strip_name(name: str) -> str:
    """Remove the conventional ``stats.<type>.`` envelope from a metric.

    Some callers used the original parser's small functional API, which stripped
    this transport prefix before emitting a record.  The class parser keeps names
    untouched; this helper preserves that legacy behavior without changing the
    canonical :class:`StatsdParseSource` contract.
    """
    parts = name.split(".")
    if len(parts) > 2 and parts[0] == "stats" and parts[1] in _NAME_PREFIXES:
        return ".".join(parts[2:])
    return name


def _dispatch(line: str) -> list[dict[str, Any]]:
    """Compatibility wrapper for parsing one StatsD line.

    The historical functional parser returned an empty list for malformed input,
    and preserved the sample-rate token as text.  Keep that behavior while
    delegating all validation and normalization to ``StatsdParseSource``.
    """
    try:
        record = StatsdParseSource().parse_line(str(line))
    except StatsdParseError:
        return []

    record["message"] = _strip_name(str(record["message"]))
    type_names = {"c": "counter", "g": "gauge", "ms": "timer", "h": "histogram", "s": "set"}
    record["labels"]["metric_type"] = type_names[str(record["level_or_status"])]
    for field in str(line).split("|")[2:]:
        if field.startswith("@"):
            record["labels"]["sample_rate"] = field[1:]
            break
    return [record]


class StatsdParseError(ValueError):
    """Raised for a malformed StatsD line when strict parsing is requested."""


class StatsdParseSource:
    """Pure StatsD line-protocol parser (KIND='metrics')."""

    KIND = "metrics"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.name = str(cfg.get("name", "statsd_parse"))
        # When strict, a malformed line raises; otherwise it is skipped unless
        # query is asked to keep errors (spec['keep_errors']).
        self._strict = bool(cfg.get("strict", False))

    def health(self) -> dict[str, Any]:
        """A pure parser is always healthy. Never raises."""
        return {"ok": True, "detail": "pure parser (no transport)"}

    # -- parsing --------------------------------------------------------------
    @staticmethod
    def _parse_tags(segment: str) -> dict[str, str]:
        """Parse a ``#k:v,flag,k2:v2`` tag segment into a dict."""
        tags: dict[str, str] = {}
        body = segment[1:] if segment.startswith("#") else segment
        for part in body.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                k, _, v = part.partition(":")
                tags[k] = v
            else:
                # value-less tag (a flag); record as present.
                tags[part] = ""
        return tags

    def parse_line(self, line: str) -> dict[str, Any]:
        """Parse one StatsD line into a normalized record. Raises on malformed."""
        raw = line.rstrip("\n")
        text = raw.strip()
        if not text:
            raise StatsdParseError("empty line")

        name_part, sep, rest = text.partition(":")
        if not sep:
            raise StatsdParseError(f"missing ':' value separator: {raw!r}")
        if not name_part:
            raise StatsdParseError(f"empty metric name: {raw!r}")

        fields = rest.split("|")
        value_str = fields[0]
        if len(fields) < 2 or not fields[1]:
            raise StatsdParseError(f"missing metric type: {raw!r}")
        mtype = fields[1]
        if mtype not in _VALID_TYPES:
            raise StatsdParseError(f"unknown metric type {mtype!r}: {raw!r}")

        labels: dict[str, Any] = {}
        for extra in fields[2:]:
            if extra.startswith("@"):
                rate_txt = extra[1:]
                try:
                    labels["sample_rate"] = float(rate_txt)
                except ValueError as exc:
                    raise StatsdParseError(f"bad sample rate {extra!r}: {raw!r}") from exc
            elif extra.startswith("#"):
                labels.update(self._parse_tags(extra))

        value: float | None
        if mtype == "s":
            # Set members carry an opaque (often non-numeric) value.
            value = None
            labels["set_value"] = value_str
        else:
            if value_str[:1] in ("+", "-"):
                labels["delta"] = value_str[0]
            value = sanitize_metric_value(value_str)
            if value is None:
                raise StatsdParseError(f"bad numeric value {value_str!r}: {raw!r}")

        return {
            "ts": None,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": mtype,
            "message": name_part,
            "value": value,
            "labels": labels,
            "raw": raw,
        }

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize ``spec['lines']`` (a list of StatsD lines) into records.

        spec keys:
          lines        (required) iterable of StatsD protocol lines.
          keep_errors  (optional) if True, malformed lines yield an error record
                       instead of being raised/skipped.
          limit        (optional) max records returned.
        """
        spec = spec or {}
        lines = spec.get("lines")
        if lines is None:
            raise ValueError("query spec requires 'lines'")

        keep_errors = bool(spec.get("keep_errors", False))
        limit = spec.get("limit")
        max_n = int(limit) if limit is not None else None

        out: list[dict[str, Any]] = []
        for line in lines:
            try:
                out.append(self.parse_line(str(line)))
            except StatsdParseError:
                if self._strict and not keep_errors:
                    raise
                if keep_errors:
                    out.append(
                        {
                            "ts": None,
                            "source": self.name,
                            "kind": self.KIND,
                            "level_or_status": "error",
                            "message": "parse_error",
                            "value": None,
                            "labels": {"reason": "malformed"},
                            "raw": str(line).rstrip("\n"),
                        }
                    )
                # else: silently skip malformed lines.
            if max_n is not None and len(out) >= max_n:
                break
        return out
