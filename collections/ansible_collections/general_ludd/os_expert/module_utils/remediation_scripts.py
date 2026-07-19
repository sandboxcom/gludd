#!/usr/bin/env python3
"""Auto-remediation script generator for the NF.6 OS Expert collection.

Consumes a :class:`~module_utils.hardening_guide.HardeningGuide` and emits
ready-to-run shell scripts (bash for Linux findings, PowerShell for Windows
findings) that apply the recommended hardening commands end-to-end.

Pipeline::

    HardeningGuide  -->  generate_bash_script(g)      -->  GeneratedScript (bash)
                     -->  generate_powershell_script(g) -->  GeneratedScript (powershell)
                     -->  generate_scripts(g)           -->  list[GeneratedScript]
                                                           (auto-routed by platform)

Platform routing uses the finding-ID prefix: ``LSEC-*`` -> Linux/bash,
``WSEC-*`` -> Windows/PowerShell. Recommendations whose finding ID does not
match either prefix are dropped from script generation (and should be surfaced
by the caller via the guide's ``summary["unmapped_finding_ids"]``).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from module_utils.hardening_guide import HardeningGuide, HardeningRecommendation

LINUX_PREFIX = "LSEC"
WINDOWS_PREFIX = "WSEC"

_POWERSHELL_WRAPPER_RE = re.compile(r'^\s*powershell\s+(?:-Command|-c)\s+"(.*)"\s*$', re.IGNORECASE)


@dataclass
class GeneratedScript:
    """A single generated remediation script ready to write to disk."""

    content: str
    language: str
    finding_ids: list[str] = field(default_factory=list)
    recommendation_count: int = 0


def _platform_of(rec: HardeningRecommendation) -> str | None:
    fid = rec.finding_id or ""
    if fid.startswith(LINUX_PREFIX):
        return "linux"
    if fid.startswith(WINDOWS_PREFIX):
        return "windows"
    return None


def _partition_by_platform(
    guide: HardeningGuide,
) -> tuple[list[HardeningRecommendation], list[HardeningRecommendation]]:
    linux: list[HardeningRecommendation] = []
    windows: list[HardeningRecommendation] = []
    for rec in guide.recommendations:
        platform = _platform_of(rec)
        if platform == "linux":
            linux.append(rec)
        elif platform == "windows":
            windows.append(rec)
    return linux, windows


def _strip_powershell_wrapper(cmd: str) -> str:
    """Convert a ``powershell -Command "..."`` invocation to the inner PS code.

    Commands stored in the hardening KB are written to be runnable from
    ``cmd.exe`` / bash, so they wrap PowerShell cmdlets in a ``powershell -Command``
    invocation. Inside a ``.ps1`` script that wrapper is redundant and breaks
    native execution. Native commands (``netsh``, ``auditpol``, ``net``, ``wmic``)
    pass through unchanged.
    """
    match = _POWERSHELL_WRAPPER_RE.match(cmd)
    if match:
        return match.group(1)
    return cmd


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- bash generation --------------------------------------------------------


def _bash_header(
    recs: list[HardeningRecommendation], generated_at: str, reboot_required: bool
) -> list[str]:
    finding_ids = sorted({r.finding_id for r in recs if r.finding_id})
    refs = sorted({ref for r in recs for ref in r.references})
    lines: list[str] = [
        "#!/bin/bash",
        "#",
        "# OS Hardening Remediation Script (auto-generated)",
        f"# Generated: {generated_at}",
        f"# Findings: {', '.join(finding_ids) if finding_ids else '(none)'}",
        f"# Recommendations: {len(recs)}",
    ]
    if refs:
        lines.append(f"# References: {', '.join(refs)}")
    lines.append("#")
    lines.append(
        "# WARNING: Review each command against your environment before executing."
    )
    if reboot_required:
        lines.append("# NOTE: One or more recommendations require a reboot to take effect.")
    lines.append("#")
    lines.append("set -euo pipefail")
    lines.append("")
    return lines


def _bash_section(rec: HardeningRecommendation, include_verify: bool) -> list[str]:
    bar = "#" + "=" * 76
    lines: list[str] = [bar]
    lines.append(f"# [{rec.severity.upper()}] {rec.title}")
    lines.append(f"# Finding: {rec.finding_id}")
    if rec.change_risk and rec.change_risk != "low":
        lines.append(
            f"# Risk: {rec.change_risk} - validate in a non-production window."
        )
    lines.append(bar)
    lines.extend(rec.commands)
    lines.append("")
    if include_verify and rec.verification:
        lines.append("# Verify:")
        lines.append(rec.verification)
        lines.append("")
    return lines


def generate_bash_script(
    guide: HardeningGuide, *, include_verify: bool = True
) -> GeneratedScript:
    """Render the Linux recommendations in ``guide`` as a bash script.

    Non-Linux recommendations (finding IDs without the ``LSEC`` prefix) are
    skipped. An empty guide yields a minimal script with a clear "no actions"
    marker so the artifact is still a valid runnable shell file.
    """
    recs = [r for r in guide.recommendations if _platform_of(r) == "linux"]
    finding_ids = sorted({r.finding_id for r in recs if r.finding_id})
    reboot_required = any(r.reboot_required for r in recs)
    generated_at = _iso_timestamp()

    if not recs:
        content = "\n".join(
            [
                "#!/bin/bash",
                "#",
                f"# Generated: {generated_at}",
                "# No remediation actions required.",
                "#",
                "set -euo pipefail",
                'echo "No hardening actions to apply."',
                "",
            ]
        )
        return GeneratedScript(
            content=content,
            language="bash",
            finding_ids=[],
            recommendation_count=0,
        )

    lines = _bash_header(recs, generated_at, reboot_required)
    for rec in recs:
        lines.extend(_bash_section(rec, include_verify))

    if reboot_required:
        lines.append("# ----------------------------------------------------------------")
        lines.append("# Reboot required for one or more of the changes above.")
        lines.append("# Schedule a reboot when ready: shutdown -r now")
        lines.append("")

    return GeneratedScript(
        content="\n".join(lines),
        language="bash",
        finding_ids=finding_ids,
        recommendation_count=len(recs),
    )


# ---- powershell generation --------------------------------------------------


def _powershell_header(
    recs: list[HardeningRecommendation], generated_at: str, reboot_required: bool
) -> list[str]:
    finding_ids = sorted({r.finding_id for r in recs if r.finding_id})
    refs = sorted({ref for r in recs for ref in r.references})
    lines: list[str] = [
        "# OS Hardening Remediation Script (auto-generated)",
        f"# Generated: {generated_at}",
        f"# Findings: {', '.join(finding_ids) if finding_ids else '(none)'}",
        f"# Recommendations: {len(recs)}",
    ]
    if refs:
        lines.append(f"# References: {', '.join(refs)}")
    lines.append("#")
    lines.append(
        "# WARNING: Review each command against your environment before executing."
    )
    if reboot_required:
        lines.append("# NOTE: One or more recommendations require a reboot to take effect.")
    lines.append("#")
    lines.append('$ErrorActionPreference = "Stop"')
    lines.append("")
    return lines


def _powershell_section(rec: HardeningRecommendation, include_verify: bool) -> list[str]:
    bar = "#" + "=" * 76
    lines: list[str] = [bar]
    lines.append(f"# [{rec.severity.upper()}] {rec.title}")
    lines.append(f"# Finding: {rec.finding_id}")
    if rec.change_risk and rec.change_risk != "low":
        lines.append(
            f"# Risk: {rec.change_risk} - validate in a non-production window."
        )
    lines.append(bar)
    for raw in rec.commands:
        lines.append(_strip_powershell_wrapper(raw))
    lines.append("")
    if include_verify and rec.verification:
        lines.append("# Verify:")
        lines.append(_strip_powershell_wrapper(rec.verification))
        lines.append("")
    return lines


def generate_powershell_script(
    guide: HardeningGuide, *, include_verify: bool = True
) -> GeneratedScript:
    """Render the Windows recommendations in ``guide`` as a PowerShell script.

    Non-Windows recommendations (finding IDs without the ``WSEC`` prefix) are
    skipped. ``powershell -Command "..."`` wrappers are stripped so the emitted
    cmdlets run natively inside the ``.ps1`` file.
    """
    recs = [r for r in guide.recommendations if _platform_of(r) == "windows"]
    finding_ids = sorted({r.finding_id for r in recs if r.finding_id})
    reboot_required = any(r.reboot_required for r in recs)
    generated_at = _iso_timestamp()

    if not recs:
        content = "\n".join(
            [
                "# OS Hardening Remediation Script (auto-generated)",
                f"# Generated: {generated_at}",
                "# No remediation actions required.",
                "#",
                '$ErrorActionPreference = "Stop"',
                'Write-Output "No hardening actions to apply."',
                "",
            ]
        )
        return GeneratedScript(
            content=content,
            language="powershell",
            finding_ids=[],
            recommendation_count=0,
        )

    lines = _powershell_header(recs, generated_at, reboot_required)
    for rec in recs:
        lines.extend(_powershell_section(rec, include_verify))

    if reboot_required:
        lines.append("# ----------------------------------------------------------------")
        lines.append("# Reboot required for one or more of the changes above.")
        lines.append("# Schedule a reboot when ready: Restart-Computer")
        lines.append("")

    return GeneratedScript(
        content="\n".join(lines),
        language="powershell",
        finding_ids=finding_ids,
        recommendation_count=len(recs),
    )


# ---- auto-routing -----------------------------------------------------------


def generate_scripts(
    guide: HardeningGuide, *, include_verify: bool = True
) -> list[GeneratedScript]:
    """Auto-route recommendations by platform and emit one script per platform.

    Returns at most two scripts (bash + powershell). Empty platforms produce
    no script. An entirely empty guide returns ``[]``.
    """
    linux_recs, windows_recs = _partition_by_platform(guide)
    scripts: list[GeneratedScript] = []
    if linux_recs:
        scripts.append(generate_bash_script(guide, include_verify=include_verify))
    if windows_recs:
        scripts.append(generate_powershell_script(guide, include_verify=include_verify))
    return scripts


__all__ = [
    "LINUX_PREFIX",
    "WINDOWS_PREFIX",
    "GeneratedScript",
    "generate_bash_script",
    "generate_powershell_script",
    "generate_scripts",
]
