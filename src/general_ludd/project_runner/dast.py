"""DAST (Dynamic Application Security Testing) driver using ZAP baseline.

Runs an active or passive scan against a target application — either a
pre-existing URL (``target_url``) or one started on-demand via
``start_command`` + ``port`` — and parses the resulting JSON findings
through a severity threshold gate.

The module mirrors the SAST parsers in :mod:`general_ludd.project_runner.findings`
in its fail-soft philosophy: a non-zero scanner exit, a malformed JSON
report, or a health-check timeout never raises — the driver returns a
:class:`DastResult` with ``skipped=True`` or ``findings=[]``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from general_ludd.project_runner.profile import ProjectProfile

logger = logging.getLogger(__name__)

_SEVERITY_ORDER: dict[str, int] = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

_ALLOWED_FAIL_ON = frozenset({"HIGH", "MEDIUM", "LOW"})

_ALLOW_ANY_EXEC_ENV = "GLUDD_PROJECT_ALLOW_ANY_EXEC"

# ── dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class DastFinding:
    severity: str
    rule_id: str
    url: str
    method: str
    evidence: str
    solution: str
    cwe_id: str = ""
    riskcode: int = 0


@dataclass
class DastResult:
    passed: bool
    skipped: bool = False
    reason: str | None = None
    findings: list[DastFinding] = field(default_factory=list)


# ── config ────────────────────────────────────────────────────────────────────


class DastConfig(BaseModel):
    start_command: str | None = None
    target_url: str | None = None
    port: int | None = None
    health_path: str = Field(default="/")
    startup_timeout_s: int = Field(default=60, ge=1)
    tool: str = Field(default="zap-baseline.py")
    max_duration_s: int = Field(default=900, ge=1)
    fail_on: str = Field(default="HIGH")

    @model_validator(mode="after")
    def _validate_dast_config(self) -> DastConfig:
        if self.start_command is not None and self.target_url is not None:
            raise ValueError(
                "set exactly one of start_command or target_url, not both "
                f"(got start_command={self.start_command!r}, target_url={self.target_url!r})"
            )
        if self.start_command is None and self.target_url is None:
            raise ValueError(
                "set exactly one of start_command or target_url (got neither)"
            )
        if self.start_command is not None and self.port is None:
            raise ValueError(
                "port is required when start_command is set "
                f"(start_command={self.start_command!r})"
            )
        upper_fail = self.fail_on.strip().upper()
        if upper_fail not in _ALLOWED_FAIL_ON:
            raise ValueError(
                f"fail_on must be one of {sorted(_ALLOWED_FAIL_ON)} (got {self.fail_on!r})"
            )
        self.fail_on = upper_fail
        return self


# ── SSRF / target-URL validation ──────────────────────────────────────────────

_LOOPBACK4 = ipaddress.IPv4Network("127.0.0.0/8")
_LOOPBACK6 = ipaddress.IPv6Address("::1")
_PRIVATE_10 = ipaddress.IPv4Network("10.0.0.0/8")
_PRIVATE_172 = ipaddress.IPv4Network("172.16.0.0/12")
_PRIVATE_192 = ipaddress.IPv4Network("192.168.0.0/16")
_LINK_LOCAL = ipaddress.IPv4Network("169.254.0.0/16")
_METADATA = ipaddress.IPv4Address("169.254.169.254")


def _is_loopback(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() in {"localhost"} or _host_ends_with_localhost(host)
    if isinstance(addr, ipaddress.IPv4Address):
        return addr in _LOOPBACK4
    return addr == _LOOPBACK6


def _host_ends_with_localhost(host: str) -> bool:
    lower = host.lower()
    return lower == "localhost" or lower.endswith(".localhost")


def _is_blocked_target(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv4Address):
        if addr in _PRIVATE_10 or addr in _PRIVATE_172 or addr in _PRIVATE_192:
            return True
        if addr in _LINK_LOCAL:
            return True
        if addr == _METADATA:
            return True
    return False


def _validate_target_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip()
    if not host:
        raise ValueError(f"could not extract host from URL {url!r}")
    if _is_loopback(host):
        return
    if _is_blocked_target(host):
        raise ValueError(f"target URL host {host!r} is a blocked range for DAST scans")


# ── ZAP baseline JSON parser ──────────────────────────────────────────────────

_RISKCODE_MAP: dict[str, str] = {"0": "INFO", "1": "LOW", "2": "MEDIUM", "3": "HIGH"}


def parse_zap_baseline(json_text: str) -> list[DastFinding]:
    try:
        doc = json.loads(json_text)
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning("zap-baseline output is not valid JSON — returning no findings")
        return []

    if not isinstance(doc, dict):
        return []

    findings: list[DastFinding] = []
    seen: set[tuple[str, str, str]] = set()

    sites = doc.get("site", [])
    if not isinstance(sites, list):
        sites = []

    for site in sites:
        if not isinstance(site, dict):
            continue
        alerts = site.get("alerts", [])
        if not isinstance(alerts, list):
            continue
        for alert in alerts:
            if not isinstance(alert, dict):
                continue
            instances = alert.get("instances", [])
            if not isinstance(instances, list):
                continue
            for inst in instances:
                if not isinstance(inst, dict):
                    continue
                rule_id = str(alert.get("alert", ""))
                url_val = str(inst.get("uri", ""))
                method_val = str(inst.get("method", ""))
                evidence = str(inst.get("evidence", "")) or str(alert.get("desc", ""))
                solution = str(alert.get("solution", ""))
                cwe_id = str(alert.get("cweid", ""))
                riskcode_str = str(alert.get("riskcode", "0"))
                riskcode = _parse_riskcode_int(riskcode_str)
                severity = _RISKCODE_MAP.get(riskcode_str, "INFO")

                key = (rule_id, url_val, method_val)
                if key in seen:
                    continue
                seen.add(key)

                findings.append(
                    DastFinding(
                        severity=severity,
                        rule_id=rule_id,
                        url=url_val,
                        method=method_val,
                        evidence=evidence,
                        solution=solution,
                        cwe_id=cwe_id,
                        riskcode=riskcode,
                    )
                )

    return findings


def _parse_riskcode_int(raw: str) -> int:
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0


# ── severity threshold ────────────────────────────────────────────────────────


def _severity_exceeds(findings: list[DastFinding], fail_on: str) -> bool:
    threshold = _SEVERITY_ORDER.get(fail_on.upper(), 3)
    for f in findings:
        if _SEVERITY_ORDER.get(f.severity, 0) >= threshold:
            return True
    return False


# ── public aliases (tests import these names) ─────────────────────────────────

is_loopback = _is_loopback
is_blocked_target = _is_blocked_target
severity_threshold_exceeded = _severity_exceeds


# ── main lifecycle ────────────────────────────────────────────────────────────


def run_dast_scan(
    config: DastConfig,
    profile: ProjectProfile,
    workspace: str | Path,
) -> DastResult:
    workspace_path = Path(workspace).resolve()

    # —— 1. Validate target URL (if provided) —————————
    if config.target_url is not None:
        try:
            _validate_target_url(config.target_url)
        except ValueError as exc:
            return DastResult(
                passed=False,
                skipped=True,
                reason=f"target URL {config.target_url!r} failed validation: {exc}",
            )

    # —— 2. Check tool availability ————————————————————
    tool_path = shutil.which(config.tool)
    allow_any = (
        os.getenv(_ALLOW_ANY_EXEC_ENV, "").strip().lower()
        in {"1", "true", "yes", "on"}
    )

    if tool_path is None and not allow_any:
        return DastResult(
            passed=False,
            skipped=True,
            reason=f"DAST tool {config.tool!r} not found on PATH "
            f"and not in allowed_exec",
        )

    # —— 3. Check allowed_exec —————————————————————————
    if not allow_any:
        exe_basename = os.path.basename(config.tool)
        if exe_basename not in profile.allowed_exec:
            return DastResult(
                passed=False,
                skipped=True,
                reason=f"DAST tool {exe_basename!r} not in allowed_exec "
                f"{profile.allowed_exec}",
            )

    # —— determine scanner target URL ——————————————————
    if config.target_url is not None:
        scanner_target_url = config.target_url
    elif config.start_command is not None and config.port is not None:
        scanner_target_url = f"http://127.0.0.1:{config.port}"
    else:
        return DastResult(
            passed=False,
            skipped=True,
            reason="no target_url or start_command+port available",
        )

    # —— 4. Start app (if start_command) ———————————————
    app_proc: subprocess.Popen[str] | None = None
    try:
        if config.start_command is not None and config.port is not None:
            app_proc = _start_app(config.port, config.start_command)
            health_ok = _wait_health(config.port, config.health_path, config.startup_timeout_s)
            if not health_ok:
                _kill_app(app_proc)
                app_proc = None
                return DastResult(
                    passed=False,
                    skipped=True,
                    reason=(
                        f"health check timed out after "
                        f"{config.startup_timeout_s}s on "
                        f"http://127.0.0.1:{config.port}{config.health_path}"
                    ),
                )

        # —— 5. Resolve scanner argv ———————————————————
        try:
            raw_command = profile.resolve_argv(config.tool)
        except Exception:
            raw_command = [config.tool]

        # —— 6. Run scanner —————————————————————————————
        with tempfile.NamedTemporaryFile(
            suffix=".json", prefix="zap-baseline-", delete=False
        ) as json_tmp:
            json_path = json_tmp.name

        scanner_cmd = list(raw_command) + [
            "-t", scanner_target_url,
            "-J", json_path,
        ]

        start_s = time.monotonic()
        try:
            proc = subprocess.run(
                scanner_cmd,
                capture_output=True,
                text=True,
                timeout=config.max_duration_s,
                cwd=str(workspace_path),
            )
        except subprocess.TimeoutExpired:
            return DastResult(
                passed=False,
                skipped=True,
                reason=f"DAST scanner timed out after {config.max_duration_s}s",
            )
        scanner_duration = time.monotonic() - start_s
        logger.info(
            "DAST scanner %r finished in %.1fs (exit %s)",
            config.tool,
            scanner_duration,
            proc.returncode,
        )

        # —— 7. Parse findings ——————————————————————————
        json_output = ""
        try:
            json_output = Path(json_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning("could not read ZAP JSON output from %s", json_path)

        findings = parse_zap_baseline(json_output)

        try:
            os.unlink(json_path)
        except OSError:
            pass

        # —— 8. Severity gate ———————————————————————————
        passed = not _severity_exceeds(findings, config.fail_on)
        return DastResult(passed=passed, findings=findings)

    finally:
        # —— 9. Teardown ————————————————————————————————
        if app_proc is not None:
            _kill_app(app_proc)


# ── internal helpers ──────────────────────────────────────────────────────────


def _start_app(
    port: int,
    start_command: str,
) -> subprocess.Popen[str]:
    logger.info("starting target app on port %d: %s", port, start_command)
    proc = subprocess.Popen(
        start_command,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    return proc


def _wait_health(
    port: int,
    health_path: str,
    startup_timeout_s: int,
) -> bool:
    health_url = f"http://127.0.0.1:{port}{health_path}"
    deadline = time.monotonic() + startup_timeout_s
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(health_url, timeout=2)
            logger.info("health check passed for %s", health_url)
            return True
        except (OSError, Exception):
            time.sleep(1)
    logger.warning("health check timed out for %s after %ds", health_url, startup_timeout_s)
    return False


def _kill_app(proc: subprocess.Popen[str]) -> None:
    pid = proc.pid
    if pid is None:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
