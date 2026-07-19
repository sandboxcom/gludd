"""macOS security connector — csrutil, spctl, xprotect, tccutil wrappers.

Self-contained source: imports nothing from sibling connectors. All subprocess
calls use a LIST argv (never ``shell=True``) and an injectable runner so unit
tests never need the real binaries.

Security: all caller-supplied values are validated to reject leading-dash
(option-injection guard) and shell metacharacters before they touch argv.
"""

from __future__ import annotations

import contextlib
import subprocess
import time
from collections.abc import Callable
from typing import Any

Runner = Callable[[list[str]], tuple[int, str, str]]

_SHELL_METACHARS: frozenset[str] = frozenset(";&|`$<>(){}[]!*?#~\n\r\t \"'")

_DEFAULT_TIMEOUT = 30.0

_XPROTECT_PLIST = (
    "/System/Library/CoreServices/XProtect.bundle"
    "/Contents/Resources/XProtect.meta.plist"
)


def _validate_arg(value: str, field: str) -> str:
    """Validate a caller-supplied arg or raise ValueError.

    Rejects non-strings, empty values, a leading dash (option-injection
    guard), and any shell metacharacter / control character.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {type(value).__name__}")
    if value == "":
        raise ValueError(f"{field} must not be empty")
    if value.startswith("-"):
        raise ValueError(f"{field} must not start with '-': {value!r}")
    bad = sorted(set(value) & _SHELL_METACHARS)
    if bad:
        raise ValueError(f"{field} contains disallowed characters {bad!r}: {value!r}")
    return value


def _default_runner(argv: list[str]) -> tuple[int, str, str]:
    """Run argv as a discrete LIST, never ``shell=True``, always time-bound."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (124, "", f"timeout after {_DEFAULT_TIMEOUT}s")
    except (OSError, ValueError) as exc:
        return (127, "", str(exc))
    return (proc.returncode, proc.stdout, proc.stderr)


class MacOSSecuritySource:
    """Query macOS security subsystem state.

    Parameters
    ----------
    config:
        Arbitrary mapping; ``name`` key sets the log-record source name.
    runner:
        Optional injected command runner ``(argv) -> (rc, stdout, stderr)``.
        Defaults to a ``subprocess.run`` LIST-argv runner (never ``shell=True``).
    """

    KIND = "logs"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "macos_security"))
        self._runner: Runner = runner if runner is not None else _default_runner

    # -- health ---------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe ``csrutil status``; return ``{'ok': bool, 'detail': str}``.

        Never raises — all exceptions are caught.
        """
        try:
            rc, out, err = self._runner(["csrutil", "status"])
            if rc == 0:
                return {"ok": True, "detail": "csrutil responded"}
            detail = (err or out or "").strip() or f"exit code {rc}"
            return {"ok": False, "detail": detail}
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    # -- query ----------------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a security probe and return normalized records.

        ``spec['target']`` selects the probe:

        =================  ===========================================
        target              command run
        =================  ===========================================
        ``"csrutil"``       ``csrutil status``
        ``"sip"``           ``csrutil status`` (alias)
        ``"spctl"``         ``spctl --status``
        ``"gatekeeper"``    ``spctl --status`` (alias)
        ``"xprotect"``      ``stat`` on XProtect.meta.plist (mtime)
        ``"tcc"``           ``tccutil list [service]``
        =================  ===========================================

        Additional ``spec`` keys:
          ``service`` — for ``target="tcc"``, the TCC service to query
          (e.g. ``"Camera"``).  Omit to list all services.

        Returns an empty list on non-zero exit.
        """
        spec = spec or {}
        target = str(spec.get("target", "sip")).strip().lower()
        _validate_arg(target, field="target")

        if target in ("csrutil", "sip"):
            return self._run_csrutil_status()
        if target in ("spctl", "gatekeeper"):
            return self._run_spctl_status()
        if target == "xprotect":
            return self._run_xprotect()
        if target == "tcc":
            service = spec.get("service")
            if service is not None:
                service = _validate_arg(str(service), field="service")
                return self._run_tccutil(service)
            return self._run_tccutil_all()
        return []

    # -- csrutil --------------------------------------------------------------

    def _run_csrutil_status(self) -> list[dict[str, Any]]:
        argv = ["csrutil", "status"]
        rc, out, _err = self._runner(argv)
        if rc != 0:
            return []
        return self._normalize_csrutil(out)

    def _normalize_csrutil(self, stdout: str) -> list[dict[str, Any]]:
        ts = time.time()
        lines = (stdout or "").strip().splitlines()
        sip_status = "unknown"
        flags: dict[str, str] = {}

        for line in lines:
            stripped = line.strip()
            if not stripped or ":" not in stripped:
                continue
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().rstrip(".")
            if "system integrity protection status" in key.lower():
                sip_status = val.lower()
            else:
                flags[key] = val

        return [{
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": sip_status,
            "message": f"SIP: {sip_status}",
            "value": sip_status,
            "labels": dict(flags),
            "raw": {"command": "csrutil status", "stdout": stdout, "flags": dict(flags)},
        }]

    # -- spctl / gatekeeper ---------------------------------------------------

    def _run_spctl_status(self) -> list[dict[str, Any]]:
        argv = ["spctl", "--status"]
        rc, out, _err = self._runner(argv)
        if rc != 0:
            return []
        return self._normalize_spctl(out)

    def _normalize_spctl(self, stdout: str) -> list[dict[str, Any]]:
        ts = time.time()
        text = (stdout or "").strip()
        status = "enabled" if "enabled" in text.lower() else "disabled"

        return [{
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": status,
            "message": text,
            "value": text,
            "labels": {"gatekeeper": status},
            "raw": {"command": "spctl --status", "stdout": text},
        }]

    # -- xprotect -------------------------------------------------------------

    def _run_xprotect(self) -> list[dict[str, Any]]:
        argv = ["stat", "-f", "%m", _XPROTECT_PLIST]
        rc, out, _err = self._runner(argv)
        ts = time.time()

        if rc == 0:
            return self._normalize_xprotect_stat(out, ts)

        return self._run_xprotect_softwareupdate_fallback(ts)

    def _normalize_xprotect_stat(
        self, stdout: str, ts: float
    ) -> list[dict[str, Any]]:
        mtime_str = (stdout or "").strip()
        mtime_val: float | None = None
        with contextlib.suppress(ValueError, TypeError):
            mtime_val = float(mtime_str)

        return [{
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "present",
            "message": f"XProtect.meta.plist mtime={mtime_str}",
            "value": mtime_val,
            "labels": {"path": _XPROTECT_PLIST, "mtime": mtime_str},
            "raw": {
                "command": f"stat -f %m {_XPROTECT_PLIST}",
                "stat_stdout": stdout,
                "mtime": mtime_val,
            },
        }]

    def _run_xprotect_softwareupdate_fallback(
        self, ts: float
    ) -> list[dict[str, Any]]:
        argv = ["softwareupdate", "--history"]
        rc, out, _err = self._runner(argv)

        if rc != 0:
            return [{
                "ts": ts,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "unknown",
                "message": "XProtect status unavailable (stat + softwareupdate both failed)",
                "value": None,
                "labels": {},
                "raw": {
                    "command": "softwareupdate --history",
                    "stdout": out,
                },
            }]

        lines = (out or "").strip().splitlines()
        xprotect_lines = [
            line.strip() for line in lines if "xprotect" in line.lower()
        ]
        last_update = xprotect_lines[-1] if xprotect_lines else ""
        status = "ok" if xprotect_lines else "not_found"

        return [{
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": status,
            "message": last_update or "XProtect not found in softwareupdate history",
            "value": len(xprotect_lines),
            "labels": {"entries_found": len(xprotect_lines)},
            "raw": {
                "command": "softwareupdate --history",
                "xprotect_lines": xprotect_lines[:10],
            },
        }]

    # -- tccutil --------------------------------------------------------------

    def _run_tccutil_all(self) -> list[dict[str, Any]]:
        argv = ["tccutil", "list"]
        rc, out, _err = self._runner(argv)
        ts = time.time()

        if rc != 0:
            return [{
                "ts": ts,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "error",
                "message": "tccutil list failed",
                "value": None,
                "labels": {},
                "raw": {"command": "tccutil list", "stdout": out},
            }]

        return self._normalize_tccutil_output(out, ts, service="*")

    def _run_tccutil(self, service: str) -> list[dict[str, Any]]:
        argv = ["tccutil", "list", service]
        rc, out, _err = self._runner(argv)
        ts = time.time()

        if rc != 0:
            return [{
                "ts": ts,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "error",
                "message": f"tccutil list {service} failed (exit {rc})",
                "value": None,
                "labels": {"service": service},
                "raw": {
                    "command": f"tccutil list {service}",
                    "stdout": out,
                },
            }]

        return self._normalize_tccutil_output(out, ts, service=service)

    def _normalize_tccutil_output(
        self, stdout: str, ts: float, *, service: str
    ) -> list[dict[str, Any]]:
        lines = (stdout or "").strip().splitlines()
        records: list[dict[str, Any]] = []
        current_service = service

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.endswith(":") and not stripped.startswith((" ", "\t")):
                current_service = stripped.rstrip(":").strip()
                continue

            records.append({
                "ts": ts,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "granted",
                "message": stripped,
                "value": None,
                "labels": {"service": current_service, "entry": stripped},
                "raw": {
                    "command": f"tccutil list {service}",
                    "output_line": stripped,
                },
            })

        return records
