"""Local-file observability connectors (``kind='logs'``).

Two self-contained connector classes that read log-shaped data off the local
filesystem and normalize it into the canonical observability record shape:

* :class:`JsonlLogSource`  — line-delimited JSON logs.
* :class:`SyslogGrepSource` — plain RFC3164-ish syslog text, scanned with a
  Python regex (it never shells out to an external process; the "grep" is
  implemented purely in Python).

Normalized record shape (every record is a plain ``dict``)::

    {
        "ts":              str,            # best-effort timestamp (may be "")
        "source":          str,            # the connector's name
        "kind":            "logs",
        "level_or_status": str,            # log level / severity (may be "")
        "message":         str,            # the human-readable message
        "value":           float | None,   # numeric value, if any
        "labels":          dict[str, Any], # remaining structured fields
        "raw":             Any,            # the original parsed object / line
    }

Security invariant
------------------
Every path a connector touches MUST resolve (via :func:`os.path.realpath`) to a
location *inside* the configured allowed ``root``. A path that escapes via
``..``, an absolute path outside the root, or a symlink pointing outside the
root is refused at construction time with :class:`ValueError`. This keeps the
connector from reading arbitrary host files.

These classes are deliberately standalone: they do not import the package
``__init__`` or any sibling connector module.

"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any

from general_ludd.connectors.exc_sanitizer import sanitize_exc_for_health

# Fields consumed into canonical slots, by canonical name -> candidate keys.
_TS_KEYS = ("ts", "time", "@timestamp")
_LEVEL_KEYS = ("level", "severity")
_MESSAGE_KEYS = ("message", "msg")

# RFC3164-ish prefix:  "Jun 15 10:00:00 host process[pid]: message"
_SYSLOG_RE = re.compile(
    r"^(?P<month>[A-Z][a-z]{2})\s+"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+"
    r"(?P<process>[^\s\[:]+)(?:\[(?P<pid>\d+)\])?:\s?"
    r"(?P<message>.*)$"
)

_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def _confine(path: str, root: str) -> str:
    """Return the realpath of ``path``, asserting it lives inside ``root``.

    Raises :class:`ValueError` if the resolved path escapes the allowed root
    (via ``..``, an absolute path outside root, or a symlink target outside
    root). Symlinks are resolved by :func:`os.path.realpath` before the check.
    """
    real_root = os.path.realpath(root)
    real_path = os.path.realpath(path)
    # Boundary prefix: ensure exactly one trailing separator so that a root of
    # "/" (already a separator) does not become "//" and reject everything.
    boundary = real_root if real_root.endswith(os.sep) else real_root + os.sep
    # Compare on path boundaries: real_path must equal real_root or sit under it.
    if real_path != real_root and not real_path.startswith(boundary):
        raise ValueError(
            f"path {path!r} resolves to {real_path!r}, which is outside the "
            f"allowed root {real_root!r}"
        )
    return real_path


def _to_float(value: Any) -> float | None:
    """Best-effort numeric coercion; returns None when not a clean number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _consume_first(
    obj: dict[str, Any], keys: tuple[str, ...], consumed: set[str]
) -> str:
    """Return and mark the first present canonical-field candidate."""
    for key in keys:
        if key in obj:
            consumed.add(key)
            return str(obj[key])
    return ""


class JsonlLogSource:
    """Read line-delimited JSON logs and normalize them.

    Config keys:
        path:  a single JSONL file path (mutually usable with ``paths``).
        paths: a list of JSONL file paths.
        root:  the allowed base directory; every path is confined to it.
    """

    KIND = "logs"

    def __init__(self, config: dict[str, Any]) -> None:
        """Configure confined JSONL paths and the observable source name."""
        root = config.get("root")
        if not root:
            raise ValueError("JsonlLogSource requires a 'root' (allowed base dir)")
        self._root = str(root)

        raw_paths: list[str] = []
        if config.get("paths"):
            raw_paths.extend(str(p) for p in config["paths"])
        if config.get("path"):
            raw_paths.append(str(config["path"]))
        if not raw_paths:
            raise ValueError("JsonlLogSource requires 'path' or 'paths'")

        # Confine every path at construction time (fail-closed).
        self._paths: list[str] = [_confine(p, self._root) for p in raw_paths]
        self.name = str(config.get("name") or "jsonl_log_source")
        self.last_malformed_count = 0

    def health(self) -> dict[str, Any]:
        """Report reachability of configured files. Never raises."""
        try:
            missing = [p for p in self._paths if not os.path.isfile(p)]
            healthy = not missing
            return {
                "healthy": healthy,
                "name": self.name,
                "kind": self.KIND,
                "paths": list(self._paths),
                "missing": missing,
            }
        except Exception as exc:  # pragma: no cover - defensive: health never raises
            return {"healthy": False, "name": self.name, "kind": self.KIND, "error": sanitize_exc_for_health(exc)}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Read, normalize, and filter records.

        Spec keys (all optional): ``pattern`` (regex over message), ``level``,
        ``start``, ``end`` (inclusive ISO/string compare over ts), ``limit``.
        """
        pattern = spec.get("pattern")
        regex = re.compile(pattern) if pattern else None
        level = spec.get("level")
        level_norm = str(level).upper() if level is not None else None
        start = spec.get("start")
        end = spec.get("end")
        limit = spec.get("limit")

        records: list[dict[str, Any]] = []
        malformed = 0

        for path in self._paths:
            try:
                with open(path, encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except (ValueError, TypeError):
                            malformed += 1
                            continue
                        if not isinstance(obj, dict):
                            malformed += 1
                            continue
                        records.append(self._normalize(obj))
            except FileNotFoundError:
                continue

        self.last_malformed_count = malformed

        filtered: list[dict[str, Any]] = []
        for rec in records:
            if regex is not None and not regex.search(rec["message"]):
                continue
            if level_norm is not None and rec["level_or_status"].upper() != level_norm:
                continue
            if start is not None and (rec["ts"] == "" or rec["ts"] < start):
                continue
            if end is not None and (rec["ts"] == "" or rec["ts"] > end):
                continue
            filtered.append(rec)
            if isinstance(limit, int) and limit >= 0 and len(filtered) >= limit:
                break

        return filtered

    def _normalize(self, obj: dict[str, Any]) -> dict[str, Any]:
        consumed: set[str] = set()
        ts = _consume_first(obj, _TS_KEYS, consumed)
        level = _consume_first(obj, _LEVEL_KEYS, consumed)
        message = _consume_first(obj, _MESSAGE_KEYS, consumed)
        labels = {k: v for k, v in obj.items() if k not in consumed}
        value = _to_float(obj.get("value"))

        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": level,
            "message": message,
            "value": value,
            "labels": labels,
            "raw": obj,
        }


class SyslogGrepSource:
    """Scan a plain syslog text file with a Python regex (never ``grep``).

    Config keys:
        path: the syslog file (default ``/var/log/syslog``).
        root: the allowed base directory; the path is confined to it.
    """

    KIND = "logs"

    def __init__(self, config: dict[str, Any]) -> None:
        """Configure one confined syslog path and observable source name."""
        root = config.get("root")
        if not root:
            raise ValueError("SyslogGrepSource requires a 'root' (allowed base dir)")
        self._root = str(root)
        path = str(config.get("path") or "/var/log/syslog")
        self._path = _confine(path, self._root)
        self.name = str(config.get("name") or "syslog_grep_source")

    def health(self) -> dict[str, Any]:
        """Report reachability of the syslog file. Never raises."""
        try:
            present = os.path.isfile(self._path)
            return {
                "healthy": present,
                "name": self.name,
                "kind": self.KIND,
                "path": self._path,
            }
        except Exception as exc:  # pragma: no cover - defensive: health never raises
            return {"healthy": False, "name": self.name, "kind": self.KIND, "error": sanitize_exc_for_health(exc)}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Scan the file with a regex.

        Spec keys: ``pattern`` (regex, required to match anything meaningfully),
        ``limit``, ``since`` (inclusive lower bound compared against parsed ts).
        """
        pattern = spec.get("pattern")
        regex = re.compile(pattern) if pattern else re.compile(r".*")
        limit = spec.get("limit")
        since = spec.get("since")

        results: list[dict[str, Any]] = []
        try:
            with open(self._path, encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.rstrip("\n")
                    if not line:
                        continue
                    if not regex.search(line):
                        continue
                    rec = self._normalize(line)
                    if since is not None and (rec["ts"] == "" or rec["ts"] < since):
                        continue
                    results.append(rec)
                    if isinstance(limit, int) and limit >= 0 and len(results) >= limit:
                        break
        except FileNotFoundError:
            return []

        return results

    def _normalize(self, line: str) -> dict[str, Any]:
        match = _SYSLOG_RE.match(line)
        if match is None:
            return {
                "ts": "",
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "",
                "message": line,
                "value": None,
                "labels": {"host": "", "process": ""},
                "raw": line,
            }

        host = match.group("host")
        process = match.group("process")
        message = match.group("message")
        ts = self._parse_ts(match.group("month"), match.group("day"), match.group("time"))

        labels: dict[str, Any] = {"host": host, "process": process}
        pid = match.group("pid")
        if pid is not None:
            labels["pid"] = pid

        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "",
            "message": message,
            "value": None,
            "labels": labels,
            "raw": line,
        }

    @staticmethod
    def _parse_ts(month: str, day: str, time_str: str) -> str:
        """Best-effort ISO timestamp using the current year (syslog omits it)."""
        month_num = _MONTHS.get(month)
        if month_num is None:
            return ""
        try:
            hour, minute, second = (int(part) for part in time_str.split(":"))
            year = _dt.date.today().year
            stamp = _dt.datetime(year, month_num, int(day), hour, minute, second)
        except (ValueError, TypeError):
            return ""
        return stamp.isoformat()
