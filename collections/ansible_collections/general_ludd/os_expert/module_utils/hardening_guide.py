#!/usr/bin/env python3
"""OS hardening recommendation engine for the NF.6 OS Expert collection.

Consumes the structured findings produced by the ``*_audit.py`` backends in
each ``os_expert`` role and emits prioritized, actionable hardening
recommendations. Each recommendation carries concrete remediation commands,
a verification step, change-risk classification, and standards references
(CIS Benchmarks, NIST SP 800-53, STIG).

Pipeline::

    audit JSON  -->  generate_guide(findings)  -->  HardeningGuide
                                                     |-> to_dict()   (JSON-serializable)
                                                     |-> format_markdown(...)  (human-readable report)

The knowledge base (``HARDENING_KB``) maps each finding ``id`` to a
remediation template. It is the single source of truth for "what to do"
about a given finding; the engine handles prioritization and presentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

VALID_CHANGE_RISKS = frozenset({"low", "medium", "high"})


@dataclass
class HardeningRecommendation:
    """A single actionable hardening step derived from one audit finding."""

    finding_id: str
    severity: str
    category: str
    title: str
    rationale: str
    commands: list[str]
    verification: str
    references: list[str] = field(default_factory=list)
    cis_controls: list[str] = field(default_factory=list)
    reboot_required: bool = False
    change_risk: str = "medium"


@dataclass
class HardeningGuide:
    """The prioritized output of :func:`generate_guide`."""

    recommendations: list[HardeningRecommendation]
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return {
            "summary": self.summary,
            "recommendations": [asdict(r) for r in self.recommendations],
        }


HARDENING_KB: dict[str, dict[str, Any]] = {
    # ---- Linux security ------------------------------------------------------
    "LSEC-SELINUX-001": {
        "title": "Enable SELinux in enforcing mode",
        "rationale": (
            "SELinux provides mandatory access control (MAC) that confines "
            "processes to least privilege. Running with it disabled leaves the "
            "system reliant on discretionary controls alone."
        ),
        "commands": [
            "sudo sed -i 's/^SELINUX=.*/SELINUX=enforcing/' /etc/selinux/config",
            "sudo setenforce 1",
        ],
        "verification": "getenforce   # expected: Enforcing",
        "references": ["CIS RHEL 9 1.6.1.1", "NIST SP 800-53 AC-3", "STIG RHEL-09-651015"],
        "cis_controls": ["CIS-RHEL9 1.6.1.1"],
        "reboot_required": True,
        "change_risk": "high",
    },
    "LSEC-SELINUX-002": {
        "title": "Switch SELinux from permissive to enforcing",
        "rationale": (
            "Permissive mode logs policy violations but does not block them, "
            "offering no real containment. Move to enforcing after validating "
            "applications tolerate the policy."
        ),
        "commands": [
            "sudo setenforce 1",
            "sudo sed -i 's/^SELINUX=.*/SELINUX=enforcing/' /etc/selinux/config",
        ],
        "verification": "getenforce   # expected: Enforcing",
        "references": ["CIS RHEL 9 1.6.1.2", "NIST SP 800-53 AC-3"],
        "cis_controls": ["CIS-RHEL9 1.6.1.2"],
        "reboot_required": False,
        "change_risk": "medium",
    },
    "LSEC-MAC-001": {
        "title": "Install and enable a mandatory access control framework",
        "rationale": (
            "Neither SELinux nor AppArmor is active. A MAC framework is a "
            "baseline defense-in-depth control for confining compromised "
            "services."
        ),
        "commands": [
            "sudo dnf install -y selinux-policy-targeted   # or: apparmor-utils",
            "sudo setenforce 1",
        ],
        "verification": "getenforce || sudo aa-status",
        "references": ["CIS RHEL 9 1.6.1", "NIST SP 800-53 AC-3"],
        "cis_controls": ["CIS-RHEL9 1.6.1"],
        "reboot_required": True,
        "change_risk": "high",
    },
    "LSEC-APPARMOR-001": {
        "title": "Move AppArmor complain-mode profiles to enforce",
        "rationale": (
            "Profiles in complain mode log violations but do not enforce. "
            "Review the audit log and switch profiles to enforce once "
            "applications are confirmed compatible."
        ),
        "commands": [
            "sudo aa-enforce /etc/apparmor.d/*",
        ],
        "verification": "sudo aa-status | grep complain   # expected: 0",
        "references": ["CIS Ubuntu 4.4", "NIST SP 800-53 AC-3"],
        "cis_controls": ["CIS-Ubuntu 4.4"],
        "reboot_required": False,
        "change_risk": "medium",
    },
    "LSEC-AUDITD-001": {
        "title": "Install and enable auditd for system-call auditing",
        "rationale": (
            "Without auditd there is no kernel-level record of security-relevant "
            "events, blunting incident response and attribution."
        ),
        "commands": [
            "sudo dnf install -y audit",
            "sudo systemctl enable --now auditd",
        ],
        "verification": "sudo systemctl is-active auditd   # expected: active",
        "references": ["CIS RHEL 9 4.1.1.1", "NIST SP 800-53 AU-2", "STIG RHEL-09-651010"],
        "cis_controls": ["CIS-RHEL9 4.1.1.1"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "LSEC-KERNEL-001": {
        "title": "Fully enable ASLR (randomize_va_space=2)",
        "rationale": (
            "Address Space Layout Randomization is a primary exploit "
            "mitigation. A value below 2 weakens it to legacy stack "
            "randomization only."
        ),
        "commands": [
            "sudo sysctl -w kernel.randomize_va_space=2",
            'echo "kernel.randomize_va_space = 2" | sudo tee /etc/sysctl.d/60-aslr.conf',
        ],
        "verification": "cat /proc/sys/kernel/randomize_va_space   # expected: 2",
        "references": ["CIS RHEL 9 1.4.2", "NIST SP 800-53 SC-30"],
        "cis_controls": ["CIS-RHEL9 1.4.2"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "LSEC-KERNEL-002": {
        "title": "Set kptr_restrict to hide kernel pointers",
        "rationale": (
            "Without kptr_restrict unprivileged users can read kernel pointer "
            "addresses, aiding kernel exploit development."
        ),
        "commands": [
            "sudo sysctl -w kernel.kptr_restrict=1",
            'echo "kernel.kptr_restrict = 1" | sudo tee /etc/sysctl.d/60-kptr.conf',
        ],
        "verification": "cat /proc/sys/kernel/kptr_restrict   # expected: 1",
        "references": ["CIS RHEL 9 1.4.3", "NIST SP 800-53 SC-30"],
        "cis_controls": ["CIS-RHEL9 1.4.3"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "LSEC-KERNEL-003": {
        "title": "Set dmesg_restrict to restrict kernel log access",
        "rationale": (
            "Unrestricted dmesg exposes kernel addresses and driver messages "
            "to unprivileged users, leaking information useful to attackers."
        ),
        "commands": [
            "sudo sysctl -w kernel.dmesg_restrict=1",
            'echo "kernel.dmesg_restrict = 1" | sudo tee /etc/sysctl.d/60-dmesg.conf',
        ],
        "verification": "cat /proc/sys/kernel/dmesg_restrict   # expected: 1",
        "references": ["CIS RHEL 9 1.4.4", "NIST SP 800-53 AC-3"],
        "cis_controls": ["CIS-RHEL9 1.4.4"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "LSEC-PAM-001": {
        "title": "Raise pam_pwquality minimum length to >= 8",
        "rationale": (
            "Short passwords are trivially brute-forced. CIS recommends a "
            "minimum length of at least 14; 8 is the floor."
        ),
        "commands": [
            "sudo sed -i 's/minlen=[0-9]*/minlen=14/' /etc/security/pwquality.conf",
        ],
        "verification": "grep minlen /etc/security/pwquality.conf",
        "references": ["CIS RHEL 9 6.2.1", "NIST SP 800-53 IA-5"],
        "cis_controls": ["CIS-RHEL9 6.2.1"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "LSEC-PAM-002": {
        "title": "Configure pam_faillock for account lockout",
        "rationale": (
            "Without account lockout an attacker can attempt unlimited password "
            "guesses online. CIS recommends lockout after 5 failures."
        ),
        "commands": [
            'sudo authselect enable-feature with-faillock',
            'echo "deny = 5" | sudo tee -a /etc/security/faillock.conf',
            'echo "unlock_time = 900" | sudo tee -a /etc/security/faillock.conf',
        ],
        "verification": "grep deny /etc/security/faillock.conf",
        "references": ["CIS RHEL 9 6.3.2", "NIST SP 800-53 AC-7"],
        "cis_controls": ["CIS-RHEL9 6.3.2"],
        "reboot_required": False,
        "change_risk": "medium",
    },
    "LSEC-PORTS-001": {
        "title": "Bind exposed services to internal interfaces only",
        "rationale": (
            "A service bound to 0.0.0.0 is reachable from every network the "
            "host touches. Database/cache ports (3306, 5432, 6379, etc.) must "
            "never face the public internet."
        ),
        "commands": [
            "# Edit the service config (bind-address / listen) to an internal IP,",
            "# then block the port at the firewall:",
            "sudo firewall-cmd --permanent --remove-port=<PORT>/tcp",
            "sudo firewall-cmd --reload",
        ],
        "verification": "ss -tlnp | grep <PORT>   # bind address should be internal",
        "references": ["CIS RHEL 9 3.5", "NIST SP 800-53 SC-7"],
        "cis_controls": ["CIS-RHEL9 3.5"],
        "reboot_required": False,
        "change_risk": "medium",
    },
    # ---- Windows security ----------------------------------------------------
    "WSEC-DEF-001": {
        "title": "Enable Windows Defender antivirus",
        "rationale": (
            "Disabling real-time AV removes the primary host-level malware "
            "defense and violates baseline endpoint protection requirements."
        ),
        "commands": [
            'powershell -Command "Set-MpPreference -DisableRealtimeMonitoring $false"',
        ],
        "verification": 'powershell -Command "Get-MpComputerStatus | Select AMServiceEnabled"',
        "references": ["CIS Windows 11 18.9.1", "NIST SP 800-53 SI-3"],
        "cis_controls": ["CIS-Win11 18.9.1"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "WSEC-DEF-002": {
        "title": "Enable Defender real-time protection",
        "rationale": (
            "Real-time protection scans files on access; disabling it lets "
            "malware land and execute unchallenged."
        ),
        "commands": [
            'powershell -Command "Set-MpPreference -DisableRealtimeMonitoring $false"',
        ],
        "verification": 'powershell -Command "(Get-MpComputerStatus).RealTimeProtectionEnabled"',
        "references": ["CIS Windows 11 18.9.2", "NIST SP 800-53 SI-3"],
        "cis_controls": ["CIS-Win11 18.9.2"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "WSEC-DEF-003": {
        "title": "Enable Defender behavior monitoring",
        "rationale": (
            "Behavior monitoring detects anomalous process activity that "
            "signature-based scanning would miss."
        ),
        "commands": [
            'powershell -Command "Set-MpPreference -DisableBehaviorMonitoring $false"',
        ],
        "verification": 'powershell -Command "(Get-MpPreference).DisableBehaviorMonitoring"',
        "references": ["CIS Windows 11 18.9.3", "NIST SP 800-53 SI-3"],
        "cis_controls": ["CIS-Win11 18.9.3"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "WSEC-DEF-004": {
        "title": "Enable Defender cloud-delivered protection (Block at First Sight)",
        "rationale": (
            "Cloud protection sends suspicious files to Microsoft for rapid "
            "analysis, closing the gap before a signature is published."
        ),
        "commands": [
            'powershell -Command "Set-MpPreference -MAPSReporting Advanced"',
            'powershell -Command "Set-MpPreference -SubmitSamplesConsent SendSafeSamples"',
        ],
        "verification": 'powershell -Command "(Get-MpPreference).MAPSReporting"',
        "references": ["CIS Windows 11 18.9.4", "NIST SP 800-53 SI-3"],
        "cis_controls": ["CIS-Win11 18.9.4"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "WSEC-DEF-005": {
        "title": "Investigate and remediate active Defender threats",
        "rationale": (
            "Active or quarantined threats indicate a compromise may be in "
            "progress. Quarantine, trace the infection vector, and rotate "
            "exposed credentials."
        ),
        "commands": [
            'powershell -Command "Get-MpThreat | Select ThreatName,SeverityID"',
            'powershell -Command "Remove-MpThreat"',
        ],
        "verification": 'powershell -Command "(Get-MpThreatDetection).Count"   # expected: 0',
        "references": ["CIS Windows 11 18.9", "NIST SP 800-53 IR-4", "NIST SP 800-61"],
        "cis_controls": ["CIS-Win11 18.9"],
        "reboot_required": False,
        "change_risk": "high",
    },
    "WSEC-FW-001": {
        "title": "Enable Windows Firewall on all profiles",
        "rationale": (
            "A disabled host firewall exposes every listening service to the "
            "network segment, violating the default-deny ingress posture."
        ),
        "commands": [
            'netsh advfirewall set allprofiles state on',
        ],
        "verification": 'netsh advfirewall show allprofiles state',
        "references": ["CIS Windows 11 9.1.1", "NIST SP 800-53 SC-7", "STIG WN10-00-000005"],
        "cis_controls": ["CIS-Win11 9.1.1"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "WSEC-FW-002": {
        "title": "Set firewall default inbound action to Block",
        "rationale": (
            "Allowing inbound connections by default defeats the purpose of a "
            "host firewall; the baseline posture must be default-deny."
        ),
        "commands": [
            'netsh advfirewall set allprofiles firewallpolicy blockinbound,allowoutbound',
        ],
        "verification": 'netsh advfirewall show allprofiles firewallpolicy',
        "references": ["CIS Windows 11 9.1.2", "NIST SP 800-53 SC-7"],
        "cis_controls": ["CIS-Win11 9.1.2"],
        "reboot_required": False,
        "change_risk": "medium",
    },
    "WSEC-AUDIT-001": {
        "title": "Enable Windows advanced audit policy subcategories",
        "rationale": (
            "With most audit categories set to 'No Auditing' there is no "
            "forensic record of logons, privilege use, or object access."
        ),
        "commands": [
            'auditpol /set /category:* /success:enable /failure:enable',
        ],
        "verification": 'auditpol /get /category:*',
        "references": ["CIS Windows 11 17.1.1", "NIST SP 800-53 AU-2", "STIG WN10-AU-000500"],
        "cis_controls": ["CIS-Win11 17.1.1"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "WSEC-PW-001": {
        "title": "Enable password complexity policy",
        "rationale": (
            "Without complexity requirements users choose predictable "
            "passwords that fall to dictionary attacks."
        ),
        "commands": [
            'net accounts /passwordreq:yes',
            'secedit /configure /cfg %windir%\\inf\\defltbase.inf /areas SECURITYPOLICY',
        ],
        "verification": 'net accounts   # Password complexity = Yes',
        "references": ["CIS Windows 11 1.1.1", "NIST SP 800-53 IA-5", "STIG WN10-00-000010"],
        "cis_controls": ["CIS-Win11 1.1.1"],
        "reboot_required": False,
        "change_risk": "medium",
    },
    "WSEC-PW-002": {
        "title": "Raise minimum password length to >= 8 (recommend 14)",
        "rationale": (
            "Short minimums permit weak passwords. NIST 800-63B and CIS "
            "recommend at least 14 characters; 8 is the absolute floor."
        ),
        "commands": [
            'net accounts /minpwlen:14',
        ],
        "verification": 'net accounts   # Minimum password length',
        "references": ["CIS Windows 11 1.1.2", "NIST SP 800-53 IA-5"],
        "cis_controls": ["CIS-Win11 1.1.2"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "WSEC-LOCKOUT-001": {
        "title": "Enable account lockout policy",
        "rationale": (
            "Without lockout an attacker can brute-force passwords online "
            "without rate limiting."
        ),
        "commands": [
            'net accounts /lockoutthreshold:5 /lockoutduration:30 /lockoutwindow:30',
        ],
        "verification": 'net accounts   # Lockout threshold',
        "references": ["CIS Windows 11 1.2.1", "NIST SP 800-53 AC-7", "STIG WN10-00-000015"],
        "cis_controls": ["CIS-Win11 1.2.1"],
        "reboot_required": False,
        "change_risk": "low",
    },
    "WSEC-PATCH-001": {
        "title": "Install recent security updates",
        "rationale": (
            "No hotfixes detected indicates the host may be far behind on "
            "patching, leaving known vulnerabilities open."
        ),
        "commands": [
            'powershell -Command "Install-Module PSWindowsUpdate -Force; Get-WindowsUpdate -Install -AcceptAll"',
        ],
        "verification": 'wmic qfe list brief /format:table',
        "references": ["CIS Windows 11 1.11", "NIST SP 800-53 SI-2", "STIG WN10-00-000038"],
        "cis_controls": ["CIS-Win11 1.11"],
        "reboot_required": True,
        "change_risk": "medium",
    },
    "WSEC-PATCH-002": {
        "title": "Install security-classified hotfixes",
        "rationale": (
            "The patch history contains no security-specific updates; review "
            "WSUS/SCCM posture and ensure security rollups are approved."
        ),
        "commands": [
            'powershell -Command "Get-WindowsUpdate -Category Security -Install -AcceptAll"',
        ],
        "verification": 'wmic qfe where "description like \'%Security%\'" get HotFixID',
        "references": ["CIS Windows 11 1.11", "NIST SP 800-53 SI-2"],
        "cis_controls": ["CIS-Win11 1.11"],
        "reboot_required": True,
        "change_risk": "medium",
    },
}


def _build_recommendation(finding: dict[str, Any], template: dict[str, Any]) -> HardeningRecommendation:
    risk = template.get("change_risk", "medium")
    if risk not in VALID_CHANGE_RISKS:
        risk = "medium"
    return HardeningRecommendation(
        finding_id=finding.get("id", ""),
        severity=finding.get("severity", "medium"),
        category=finding.get("category", "general"),
        title=template["title"],
        rationale=template["rationale"],
        commands=list(template["commands"]),
        verification=template.get("verification", ""),
        references=list(template.get("references", [])),
        cis_controls=list(template.get("cis_controls", [])),
        reboot_required=bool(template.get("reboot_required", False)),
        change_risk=risk,
    )


def generate_guide(findings: list[dict[str, Any]]) -> HardeningGuide:
    """Translate audit findings into a prioritized hardening guide.

    Parameters
    ----------
    findings:
        List of finding dicts as emitted by ``assess_findings()`` in the
        ``*_audit.py`` modules. Each must carry an ``id``; entries lacking
        an ``id`` are skipped.

    Returns
    -------
    HardeningGuide
        Recommendations sorted critical -> low, with an aggregate summary.
        Findings without a KB entry are recorded in
        ``summary["unmapped_finding_ids"]`` so coverage gaps are visible.
    """
    recommendations: list[HardeningRecommendation] = []
    unmapped: list[str] = []

    for finding in findings:
        finding_id = finding.get("id")
        if not finding_id:
            continue
        template = HARDENING_KB.get(finding_id)
        if template is None:
            unmapped.append(finding_id)
            continue
        recommendations.append(_build_recommendation(finding, template))

    recommendations.sort(
        key=lambda r: (SEVERITY_RANK.get(r.severity, 99), r.finding_id)
    )

    by_severity: dict[str, int] = {}
    for rec in recommendations:
        by_severity[rec.severity] = by_severity.get(rec.severity, 0) + 1

    summary: dict[str, Any] = {
        "total_findings": len(findings),
        "matched_recommendations": len(recommendations),
        "unmapped_finding_ids": unmapped,
        "by_severity": by_severity,
        "requires_reboot": any(rec.reboot_required for rec in recommendations),
    }

    return HardeningGuide(recommendations=recommendations, summary=summary)


def format_markdown(guide: HardeningGuide) -> str:
    """Render a :class:`HardeningGuide` as a human-readable Markdown report."""
    lines: list[str] = ["# OS Hardening Recommendations", ""]

    if not guide.recommendations:
        lines.append("No hardening actions required — all audited controls passed.")
        lines.append("")
        return "\n".join(lines)

    summary = guide.summary
    lines.append(
        f"**Findings:** {summary.get('matched_recommendations', 0)} actionable / "
        f"{summary.get('total_findings', 0)} total"
    )
    reboot = summary.get("requires_reboot")
    if reboot:
        lines.append("**Note:** one or more changes require a reboot to take effect.")
    lines.append("")

    by_sev = summary.get("by_severity", {})
    if by_sev:
        sev_parts = [f"{sev}: {count}" for sev, count in sorted(
            by_sev.items(), key=lambda item: SEVERITY_RANK.get(item[0], 99)
        )]
        lines.append("| Severity | Count |")
        lines.append("|----------|-------|")
        for sev, count in sorted(by_sev.items(), key=lambda item: SEVERITY_RANK.get(item[0], 99)):
            lines.append(f"| {sev} | {count} |")
        lines.append("")

    for rec in guide.recommendations:
        lines.append(f"## [{rec.severity.upper()}] {rec.title}")
        lines.append("")
        lines.append(f"**Finding:** `{rec.finding_id}` — {rec.category}")
        lines.append("")
        lines.append(f"{rec.rationale}")
        lines.append("")
        lines.append("**Remediation commands:**")
        lines.append("```")
        lines.extend(rec.commands)
        lines.append("```")
        lines.append("")
        lines.append(f"**Verify:** `{rec.verification}`")
        if rec.cis_controls:
            lines.append(f"**CIS Benchmark:** {', '.join(rec.cis_controls)}")
        if rec.change_risk != "low":
            lines.append(f"**Change risk:** {rec.change_risk} — validate in a non-production window.")
        if rec.references:
            lines.append(f"**References:** {', '.join(rec.references)}")
        lines.append("")

    unmapped = summary.get("unmapped_finding_ids", [])
    if unmapped:
        lines.append("## Unmapped findings")
        lines.append("")
        lines.append(
            "The following finding IDs have no hardening recipe yet and need "
            "manual review:"
        )
        lines.append("")
        for fid in unmapped:
            lines.append(f"- `{fid}`")
        lines.append("")

    return "\n".join(lines)


__all__ = [
    "HARDENING_KB",
    "SEVERITY_RANK",
    "VALID_CHANGE_RISKS",
    "HardeningGuide",
    "HardeningRecommendation",
    "format_markdown",
    "generate_guide",
]
