"""/proc + /sys host metrics connector.

Reads kernel-exported metric files under ``/proc`` and ``/sys`` through an
injected file reader and normalizes them into flat metric records. All reads
are path-confined: any path that, once normalized, does not live under
``/proc`` or ``/sys`` is refused before the reader is ever called. This blocks
``..`` traversal and absolute-path escapes (e.g. ``/etc/shadow``).

Self-contained: imports nothing from sibling connector modules and defines its
own reader protocol so it can be unit-tested with a canned, injected reader
(no real ``/proc`` access required).
"""

from __future__ import annotations

import os
import re
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class FileReader(Protocol):
    """Injectable file reader. Returns the text contents of a confined path."""

    def __call__(self, path: str) -> str: ...


# Roots the connector is permitted to read from. Anything else is refused.
_ALLOWED_ROOTS: tuple[str, ...] = ("/proc", "/sys")

# Named selectors -> the file they read. ``spec['select']`` chooses which.
_SELECTORS: dict[str, str] = {
    "stat": "/proc/stat",
    "meminfo": "/proc/meminfo",
    "loadavg": "/proc/loadavg",
    "pressure_cpu": "/proc/pressure/cpu",
    "pressure_io": "/proc/pressure/io",
    "pressure_memory": "/proc/pressure/memory",
    "net_dev": "/proc/net/dev",
    "diskstats": "/proc/diskstats",
}


def _under_root(candidate: str) -> bool:
    """True iff ``candidate`` (an absolute path) sits under an allowed root."""
    return any(
        candidate == root or candidate.startswith(root + os.sep)
        for root in _ALLOWED_ROOTS
    )


def _is_confined(path: str) -> bool:
    """True iff ``path`` resolves to a location under an allowed root.

    Both the lexical normalization (``os.path.normpath``) AND the symlink-
    resolved real path (``os.path.realpath``) must land under an allowed root.
    Requiring both defeats a magic-symlink escape such as
    ``/proc/1/root/etc/shadow`` whose normpath stays under ``/proc`` but whose
    realpath escapes to ``/etc/shadow``.
    """
    norm = os.path.normpath(path)
    if not os.path.isabs(norm):
        return False
    if not _under_root(norm):
        return False
    real = os.path.realpath(norm)
    return _under_root(real)


class ProcSysSource:
    """Collect host metrics by reading confined ``/proc`` and ``/sys`` files."""

    KIND = "metrics"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        reader: FileReader | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "proc_sys"))
        self._reader: FileReader | None = reader

    # -- confined read ----------------------------------------------------

    def _read(self, path: str) -> str:
        if not _is_confined(path):
            raise ValueError(f"path outside /proc,/sys is refused: {path}")
        if self._reader is None:
            raise RuntimeError("no reader injected for ProcSysSource")
        # Hand the reader the fully resolved path so it reads exactly what was
        # confinement-checked (no TOCTOU gap, no re-resolution surprise).
        return self._reader(os.path.realpath(os.path.normpath(path)))

    @staticmethod
    def _resolve_select(spec: dict[str, Any]) -> tuple[str, str]:
        """Return ``(selector_name, confined_path)`` from the spec.

        A spec may name a known selector (``select='meminfo'``) or pass an
        explicit ``path`` (still subject to confinement).
        """
        select = spec.get("select")
        if isinstance(select, str) and select in _SELECTORS:
            return select, _SELECTORS[select]
        path = spec.get("path")
        if isinstance(path, str) and path:
            return path, path
        raise ValueError(f"unknown proc_sys selector: {select!r}")

    # -- health -----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return ``{'ok': bool, 'detail': str}``; never raises."""
        try:
            content = self._read("/proc/loadavg")
            if not content.strip():
                return {"ok": False, "detail": "/proc/loadavg was empty"}
            return {"ok": True, "detail": "/proc readable"}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    # -- query ------------------------------------------------------------

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Read the selected file and return normalized metric records."""
        select, path = self._resolve_select(spec)
        content = self._read(path)
        if select == "loadavg":
            return self._parse_loadavg(select, path, content)
        if select == "stat":
            return self._parse_stat(select, path, content)
        if select == "net_dev":
            return self._parse_net_dev(select, path, content)
        if select == "diskstats":
            return self._parse_diskstats(select, path, content)
        return self._parse_keyed(select, path, content)

    # -- record helper ----------------------------------------------------

    def _record(
        self,
        select: str,
        path: str,
        message: str,
        value: float | int | None,
        labels: dict[str, str],
        raw: str,
    ) -> dict[str, Any]:
        return {
            "ts": None,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "ok",
            "message": message,
            "value": value,
            "labels": {"path": path, "select": select, **labels},
            "raw": raw,
        }

    # -- parsers ----------------------------------------------------------

    @staticmethod
    def _num(token: str) -> float | int | None:
        token = token.strip()
        if token == "":
            return None
        try:
            if re.fullmatch(r"[+-]?\d+", token):
                return int(token)
            return float(token)
        except ValueError:
            return None

    def _parse_keyed(self, select: str, path: str, content: str) -> list[dict[str, Any]]:
        """``key: value`` style files such as /proc/meminfo and pressure files."""
        records: list[dict[str, Any]] = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                key, _, rest = line.partition(":")
                key = key.strip()
                fields = rest.split()
            else:
                # No colon: either a bare-value file (e.g. a single /sys metric)
                # or a space-separated kv line (e.g. pressure ``some avg10=...``).
                parts = line.split()
                key = parts[0] if parts else line
                # If the leading token is itself numeric the whole line is a
                # bare value (e.g. ``42``); use the file basename as the key.
                if self._num(key) is not None and "=" not in line:
                    key = path.rsplit("/", 1)[-1]
                    fields = parts
                else:
                    fields = parts[1:]
            labels: dict[str, str] = {}
            value: float | int | None = None
            numeric_fields = [f for f in fields if "=" not in f]
            if numeric_fields:
                value = self._num(numeric_fields[0])
                if len(numeric_fields) > 1:
                    labels["unit"] = numeric_fields[1]
            # pressure files emit ``some avg10=0.00 avg60=...`` kv pairs
            for tok in fields:
                if "=" in tok:
                    k, _, v = tok.partition("=")
                    labels[k] = v
            records.append(self._record(select, path, key, value, labels, line))
        return records

    def _parse_loadavg(self, select: str, path: str, content: str) -> list[dict[str, Any]]:
        fields = content.split()
        names = ("load1", "load5", "load15")
        records: list[dict[str, Any]] = []
        for name, token in zip(names, fields, strict=False):
            records.append(self._record(select, path, name, self._num(token), {}, content.strip()))
        return records

    def _parse_stat(self, select: str, path: str, content: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for line in content.splitlines():
            parts = line.split()
            if not parts:
                continue
            head = parts[0]
            if head.startswith("cpu"):
                cpu_fields = ("user", "nice", "system", "idle", "iowait", "irq", "softirq")
                for fname, token in zip(cpu_fields, parts[1:], strict=False):
                    records.append(
                        self._record(
                            select, path, f"cpu.{fname}", self._num(token), {"cpu": head}, line
                        )
                    )
            else:
                value = self._num(parts[1]) if len(parts) > 1 else None
                records.append(self._record(select, path, head, value, {}, line))
        return records

    def _parse_net_dev(self, select: str, path: str, content: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        rx = ("rx_bytes", "rx_packets", "rx_errs", "rx_drop")
        tx = ("tx_bytes", "tx_packets", "tx_errs", "tx_drop")
        for line in content.splitlines():
            if ":" not in line:
                continue
            iface, _, rest = line.partition(":")
            iface = iface.strip()
            fields = rest.split()
            for fname, token in zip(rx, fields[:4], strict=False):
                records.append(
                    self._record(select, path, f"{fname}", self._num(token), {"iface": iface}, line)
                )
            for fname, token in zip(tx, fields[8:12], strict=False):
                records.append(
                    self._record(select, path, f"{fname}", self._num(token), {"iface": iface}, line)
                )
        return records

    def _parse_diskstats(self, select: str, path: str, content: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        cols = ("reads", "reads_merged", "sectors_read", "ms_reading",
                "writes", "writes_merged", "sectors_written", "ms_writing")
        for line in content.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            device = parts[2]
            for fname, token in zip(cols, parts[3:11], strict=False):
                records.append(
                    self._record(select, path, fname, self._num(token), {"device": device}, line)
                )
        return records
