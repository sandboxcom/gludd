"""SyslogFileSource — path-confined syslog text-file log connector.

Self-contained: no imports from sibling connectors, no package base class.

Reads a syslog text file with a pure-Python regex parser supporting the two
common on-the-wire prefixes:

* RFC 3164 (BSD):  ``<34>Oct 11 22:14:15 host process[123]: message``
  (the priority ``<NN>`` is optional in many file-logged variants)
* RFC 5424:        ``<34>1 2026-06-16T22:14:15.003Z host process 123 ID - msg``

Each line becomes a normalized record:

    {
      'ts': str|None,            # raw timestamp text as it appeared
      'source': str,             # connector name
      'kind': 'logs',
      'level_or_status': str,    # severity name derived from PRI (or 'info')
      'message': str,            # the message body
      'value': None,
      'labels': {'host','process','facility','severity', ...},
      'raw': str,                # the original line
    }

Path confinement: ``__init__`` takes a ``root`` directory; every path passed to
``query`` is resolved and must stay inside ``root`` or it is refused
(``PathConfinementError``). Symlinks and ``..`` escapes are rejected because the
real path is compared against the real root.
"""

from __future__ import annotations

import logging
import os
import re

# Syslog severity (the low 3 bits of PRI) and facility (PRI >> 3) decode tables.
_SEVERITIES = (
    "emergency",
    "alert",
    "critical",
    "error",
    "warning",
    "notice",
    "info",
    "debug",
)
_FACILITIES = (
    "kern",
    "user",
    "mail",
    "daemon",
    "auth",
    "syslog",
    "lpr",
    "news",
    "uucp",
    "cron",
    "authpriv",
    "ftp",
    "ntp",
    "audit",
    "alert",
    "clock",
    "local0",
    "local1",
    "local2",
    "local3",
    "local4",
    "local5",
    "local6",
    "local7",
)

# RFC 5424: <PRI>VERSION TIMESTAMP HOST APP PROCID MSGID SD MSG
# SD is either a NILVALUE '-' or one-or-more '[...]' structured-data elements.
_RFC5424 = re.compile(
    r"^(?:<(?P<pri>\d{1,3})>)?(?P<ver>\d)\s+"
    r"(?P<ts>\S+)\s+"
    r"(?P<host>\S+)\s+"
    r"(?P<app>\S+)\s+"
    r"(?P<procid>\S+)\s+"
    r"(?P<msgid>\S+)\s+"
    r"(?P<sd>-|(?:\[[^\]]*\])+)"
    r"(?:\s+(?P<rest>.*))?$"
)

# RFC 3164 (BSD): <PRI?>MMM DD HH:MM:SS HOST TAG[PID]: MSG
_RFC3164 = re.compile(
    r"^(?:<(?P<pri>\d{1,3})>)?"
    r"(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+"
    r"(?P<tag>[^\s:\[]+)(?:\[(?P<pid>\d+)\])?:?\s*"
    r"(?P<msg>.*)$"
)


logger = logging.getLogger(__name__)


class PathConfinementError(ValueError):
    """Raised when a requested path escapes the configured root."""


def _decode_pri(pri: str | None) -> tuple[str, str]:
    """Return (facility, severity) names from a syslog PRI value."""
    if pri is None:
        return ("user", "info")
    try:
        value = int(pri)
    except (TypeError, ValueError):
        return ("user", "info")
    severity = _SEVERITIES[value & 0x7]
    fac_index = value >> 3
    facility = _FACILITIES[fac_index] if fac_index < len(_FACILITIES) else f"facility{fac_index}"
    return (facility, severity)


class SyslogFileSource:
    """Path-confined syslog text-file connector (KIND='logs')."""

    KIND = "logs"

    def __init__(self, config: dict[str, object]) -> None:
        cfg = config or {}
        root = cfg.get("root")
        if not root:
            raise ValueError("SyslogFileSource requires config['root'] (confinement dir)")
        # realpath resolves symlinks so confinement can't be tricked by a link.
        self._root = os.path.realpath(str(root))
        self.name = str(cfg.get("name", "syslog_file"))
        # default read cap; overridable per-query via spec['limit'].
        self._default_limit = int(str(cfg.get("limit", 1000)))
        self._encoding = str(cfg.get("encoding", "utf-8"))

    # -- confinement ----------------------------------------------------------
    def _resolve_confined(self, path: str) -> str:
        """Resolve ``path`` (relative to root if not absolute) and confine it."""
        candidate = path if os.path.isabs(path) else os.path.join(self._root, path)
        real = os.path.realpath(candidate)
        root = self._root
        if real != root and not real.startswith(root + os.sep):
            raise PathConfinementError(f"path {path!r} escapes root {self._root!r}")
        return real

    # -- contract -------------------------------------------------------------
    def health(self) -> dict[str, object]:
        """Report whether the confinement root is a usable directory. Never raises."""
        try:
            if not os.path.isdir(self._root):
                return {"ok": False, "detail": f"root not a directory: {self._root}"}
            if not os.access(self._root, os.R_OK):
                return {"ok": False, "detail": f"root not readable: {self._root}"}
            return {"ok": True, "detail": f"root ok: {self._root}"}
        except OSError:  # pragma: no cover - defensive
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}

    def _parse_line(self, line: str) -> dict[str, object]:
        stripped = line.rstrip("\n")
        m5 = _RFC5424.match(stripped)
        if m5 is not None:
            facility, severity = _decode_pri(m5.group("pri"))
            procid = m5.group("procid")
            labels = {
                "host": m5.group("host"),
                "process": m5.group("app"),
                "facility": facility,
                "severity": severity,
            }
            if procid not in (None, "-"):
                labels["pid"] = procid
            return {
                "ts": m5.group("ts"),
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": severity,
                "message": m5.group("rest") or "",
                "value": None,
                "labels": labels,
                "raw": stripped,
            }
        m3 = _RFC3164.match(stripped)
        if m3 is not None:
            facility, severity = _decode_pri(m3.group("pri"))
            labels = {
                "host": m3.group("host"),
                "process": m3.group("tag"),
                "facility": facility,
                "severity": severity,
            }
            pid = m3.group("pid")
            if pid:
                labels["pid"] = pid
            return {
                "ts": m3.group("ts"),
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": severity,
                "message": m3.group("msg"),
                "value": None,
                "labels": labels,
                "raw": stripped,
            }
        # Unparseable line: keep it as an info record so nothing is silently lost.
        return {
            "ts": None,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "info",
            "message": stripped,
            "value": None,
            "labels": {"host": None, "process": None, "facility": "user", "severity": "info"},
            "raw": stripped,
        }

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Parse a confined syslog file into normalized records.

        spec keys:
          path    (required) file under the configured root.
          pattern (optional) regex; only matching raw lines are returned.
          limit   (optional) max records (default config['limit']).
          since   (optional) substring; raw lines lexically < since are skipped
                  (cheap timestamp-prefix filter for already-ordered logs).
        """
        spec = spec or {}
        path = spec.get("path")
        if not path:
            raise ValueError("query spec requires 'path'")
        real = self._resolve_confined(str(path))

        limit = int(str(spec.get("limit", self._default_limit)))
        pattern = spec.get("pattern")
        compiled = re.compile(str(pattern)) if pattern else None
        since = spec.get("since")

        out: list[dict[str, object]] = []
        with open(real, encoding=self._encoding, errors="replace") as fh:
            for line in fh:
                raw = line.rstrip("\n")
                if not raw:
                    continue
                if since is not None and raw < str(since):
                    continue
                if compiled is not None and not compiled.search(raw):
                    continue
                out.append(self._parse_line(line))
                if len(out) >= limit:
                    break
        return out
