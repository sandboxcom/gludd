"""DAST (Dynamic Application Security Testing) driver using ZAP baseline.

Runs an active or passive scan against a target application — either a
pre-existing URL (``target_url``) or one started on-demand via
``start_command`` + ``port`` — and parses the resulting JSON findings
through a severity threshold gate.

The driver is fail-closed when used as a completion gate: scanner failures,
missing/malformed/oversized reports, and threshold findings all produce a
failed :class:`DastResult`. ZAP's documented warning exit (2) remains usable
when its structured report has no finding at or above the configured threshold.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from general_ludd.project_runner.profile import DastConfig, ProjectProfile
from general_ludd.project_runner.runner import _build_env

logger = logging.getLogger(__name__)

_SEVERITY_ORDER: dict[str, int] = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

_ALLOW_ANY_EXEC_ENV = "GLUDD_PROJECT_ALLOW_ANY_EXEC"
_MAX_REPORT_BYTES = 8 * 1024 * 1024
_PROXY_ENV_NAMES = frozenset({"http_proxy", "https_proxy", "all_proxy"})
_SCANNER_SLOT = threading.Lock()

# ── dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class DastFinding:
    """One normalized finding parsed from a ZAP baseline JSON report."""

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
    """Outcome and evidence from one bounded ZAP baseline invocation."""

    passed: bool
    skipped: bool = False
    reason: str | None = None
    findings: list[DastFinding] = field(default_factory=list)
    exit_code: int | None = None
    duration_s: float = 0.0
    warnings: bool = False


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
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("DAST target URL scheme must be http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("DAST target URL must not contain userinfo")
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
    """Parse valid ZAP baseline JSON into de-duplicated normalized findings."""
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
    return any(_SEVERITY_ORDER.get(f.severity, 0) >= threshold for f in findings)


# ── public aliases (tests import these names) ─────────────────────────────────

is_loopback = _is_loopback
is_blocked_target = _is_blocked_target
severity_threshold_exceeded = _severity_exceeds


def _scan_namespace(profile: ProjectProfile, workspace: Path) -> str:
    """Return a stable, filesystem-safe namespace for one target project."""
    project = re.sub(r"[^a-zA-Z0-9_.-]+", "-", profile.name).strip("-.")
    project = project[:48] or "target"
    workspace_id = hashlib.sha256(str(workspace).encode()).hexdigest()[:8]
    return f"{project}-{workspace_id}"


def _proxy_free_env(profile: ProjectProfile | None = None) -> dict[str, str]:
    passthrough = () if profile is None else profile.env_passthrough
    env = _build_env(passthrough)
    for key in list(env):
        if key.lower() in _PROXY_ENV_NAMES:
            env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def _read_zap_report(path: Path) -> tuple[list[DastFinding] | None, str | None]:
    """Read one bounded ZAP JSON report and distinguish malformed from clean."""
    try:
        size = path.stat().st_size
    except OSError:
        # ``read_text`` remains authoritative. This also keeps virtual/mock
        # files usable while a real absent report fails on the read below.
        size = 0
    if size > _MAX_REPORT_BYTES:
        return None, "DAST report exceeds the 8 MiB limit"
    try:
        json_output = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"DAST report is absent or unreadable: {type(exc).__name__}"
    if len(json_output.encode("utf-8")) > _MAX_REPORT_BYTES:
        return None, "DAST report exceeds the 8 MiB limit"
    if not json_output.strip():
        return None, "DAST report is absent or empty"
    try:
        document = json.loads(json_output)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, "DAST report is malformed JSON"
    if not isinstance(document, dict) or not isinstance(document.get("site"), list):
        return None, "DAST report is malformed: expected a top-level site list"
    return parse_zap_baseline(json_output), None


# ── main lifecycle ────────────────────────────────────────────────────────────


def run_dast_scan(
    config: DastConfig,
    profile: ProjectProfile,
    workspace: str | Path,
) -> DastResult:
    """Run one fail-closed, bounded ZAP baseline scan for a project profile."""
    workspace_path = Path(workspace).resolve()
    namespace = _scan_namespace(profile, workspace_path)

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

    if (
        config.start_command is not None
        and profile.dast is config
        and not allow_any
    ):
        start_exe = os.path.basename(shlex.split(config.start_command)[0])
        if start_exe not in profile.allowed_exec:
            return DastResult(
                passed=False,
                skipped=True,
                reason=f"DAST target executable {start_exe!r} not in allowed_exec "
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
    if not _SCANNER_SLOT.acquire(blocking=False):
        return DastResult(
            passed=False,
            skipped=True,
            reason="another DAST scanner already owns the single scanner slot",
        )

    app_proc: subprocess.Popen[str] | None = None
    json_path: str | None = None
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

        # —— 5. Build one structured scanner argv ———————————————
        # The typed profile restricts ``tool`` to zap-baseline.py. Target and
        # report arguments are appended exactly once by this driver, never
        # accepted as a free-form project command.
        raw_command = [config.tool]

        # —— 6. Run scanner —————————————————————————————
        with tempfile.NamedTemporaryFile(
            suffix=".json", prefix="zap-baseline-", delete=False
        ) as json_tmp:
            json_path = json_tmp.name

        scanner_cmd = [*list(raw_command), "-t", scanner_target_url, "-J", json_path]

        start_s = time.monotonic()
        try:
            proc = subprocess.run(
                scanner_cmd,
                capture_output=True,
                text=True,
                timeout=config.max_duration_s,
                cwd=str(workspace_path),
                env={
                    **_proxy_free_env(profile),
                    "GLUDD_PROCESS_NAMESPACE": f"gludd-dast-scanner-{namespace}",
                },
                start_new_session=True,
            )
        except subprocess.TimeoutExpired:
            return DastResult(
                passed=False,
                skipped=True,
                reason=f"DAST scanner timed out after {config.max_duration_s}s",
            )
        except OSError as exc:
            return DastResult(
                passed=False,
                reason=f"DAST scanner launch failed: {type(exc).__name__}",
            )
        scanner_duration = time.monotonic() - start_s
        logger.info(
            "DAST scanner %r finished in %.1fs (exit %s)",
            config.tool,
            scanner_duration,
            proc.returncode,
        )

        # —— 7. Parse findings ——————————————————————————
        findings, report_error = _read_zap_report(Path(json_path))
        if report_error is not None or findings is None:
            return DastResult(
                passed=False,
                reason=report_error or "DAST report validation failed",
                exit_code=proc.returncode,
                duration_s=scanner_duration,
                warnings=proc.returncode == 2,
            )

        # —— 8. Severity gate ———————————————————————————
        warnings = proc.returncode == 2
        if proc.returncode not in {0, 2}:
            return DastResult(
                passed=False,
                reason=f"DAST scanner failed with exit code {proc.returncode}",
                findings=findings,
                exit_code=proc.returncode,
                duration_s=scanner_duration,
            )
        threshold_exceeded = _severity_exceeds(findings, config.fail_on)
        return DastResult(
            passed=not threshold_exceeded,
            reason=(
                f"DAST findings met {config.fail_on} threshold"
                if threshold_exceeded
                else None
            ),
            findings=findings,
            exit_code=proc.returncode,
            duration_s=scanner_duration,
            warnings=warnings,
        )

    finally:
        # —— 9. Teardown ————————————————————————————————
        if json_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(json_path)
        if app_proc is not None:
            _kill_app(app_proc)
        _SCANNER_SLOT.release()


# ── internal helpers ──────────────────────────────────────────────────────────


def _start_app(
    port: int,
    start_command: str,
) -> subprocess.Popen[str]:
    logger.info("starting target app on port %d: %s", port, start_command)
    argv = shlex.split(start_command)
    if not argv:
        raise ValueError("start_command must not be empty")
    proc = subprocess.Popen(
        argv,
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
    with httpx.Client(timeout=2, follow_redirects=False, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(health_url)
                response.raise_for_status()
                logger.info("health check passed for %s", health_url)
                return True
            except (OSError, httpx.HTTPError):
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
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
