"""Tests for auto-remediation script generation (module_utils/remediation_scripts.py).

TDD: written before the implementation. Covers bash/PowerShell script
generation from a HardeningGuide, platform auto-routing, header metadata,
verification step handling, reboot warnings, and edge cases.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
if str(COLLECTION_ROOT) not in sys.path:
    sys.path.insert(0, str(COLLECTION_ROOT))

from module_utils.hardening_guide import (  # noqa: E402
    HardeningGuide,
    HardeningRecommendation,
    generate_guide,
)
from module_utils.remediation_scripts import (  # noqa: E402
    GeneratedScript,
    generate_bash_script,
    generate_powershell_script,
    generate_scripts,
)


# ---- helpers ----------------------------------------------------------------


def _rec(
    finding_id: str,
    severity: str = "high",
    category: str = "general",
    commands: list[str] | None = None,
    verification: str = "echo ok",
    reboot_required: bool = False,
    change_risk: str = "low",
    title: str = "Sample rec",
) -> HardeningRecommendation:
    return HardeningRecommendation(
        finding_id=finding_id,
        severity=severity,
        category=category,
        title=title,
        rationale="because",
        commands=commands if commands is not None else ["echo apply"],
        verification=verification,
        references=["CIS x", "NIST y"],
        cis_controls=["CIS-x"],
        reboot_required=reboot_required,
        change_risk=change_risk,
    )


def _guide(*recs: HardeningRecommendation) -> HardeningGuide:
    return HardeningGuide(recommendations=list(recs), summary={"total_findings": len(recs)})


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
def linux_guide() -> HardeningGuide:
    return _guide(
        _rec(
            "LSEC-SELINUX-001",
            severity="critical",
            commands=[
                "sudo sed -i 's/^SELINUX=.*/SELINUX=enforcing/' /etc/selinux/config",
                "sudo setenforce 1",
            ],
            verification="getenforce   # expected: Enforcing",
            reboot_required=True,
            change_risk="high",
            title="Enable SELinux in enforcing mode",
        ),
        _rec(
            "LSEC-KERNEL-001",
            severity="high",
            commands=["sudo sysctl -w kernel.randomize_va_space=2"],
            verification="cat /proc/sys/kernel/randomize_va_space",
            title="Fully enable ASLR",
        ),
    )


@pytest.fixture
def windows_guide() -> HardeningGuide:
    return _guide(
        _rec(
            "WSEC-DEF-001",
            severity="critical",
            commands=['powershell -Command "Set-MpPreference -DisableRealtimeMonitoring $false"'],
            verification='powershell -Command "Get-MpComputerStatus"',
            title="Enable Windows Defender antivirus",
        ),
        _rec(
            "WSEC-FW-001",
            severity="high",
            commands=["netsh advfirewall set allprofiles state on"],
            verification="netsh advfirewall show allprofiles state",
            title="Enable Windows Firewall",
        ),
    )


@pytest.fixture
def mixed_guide(linux_guide: HardeningGuide, windows_guide: HardeningGuide) -> HardeningGuide:
    return HardeningGuide(
        recommendations=[*linux_guide.recommendations, *windows_guide.recommendations],
        summary={"total_findings": 4},
    )


# ---- bash script generation -------------------------------------------------


class TestBashScript:
    def test_has_bash_shebang(self, linux_guide):
        script = generate_bash_script(linux_guide)
        assert script.content.startswith("#!/bin/bash"), "bash script must start with shebang"

    def test_sets_strict_mode(self, linux_guide):
        script = generate_bash_script(linux_guide)
        assert "set -euo pipefail" in script.content, "bash script must use strict mode"

    def test_contains_every_command(self, linux_guide):
        script = generate_bash_script(linux_guide)
        assert "sudo setenforce 1" in script.content
        assert "sudo sysctl -w kernel.randomize_va_space=2" in script.content

    def test_includes_verification_by_default(self, linux_guide):
        script = generate_bash_script(linux_guide)
        assert "getenforce" in script.content
        assert "cat /proc/sys/kernel/randomize_va_space" in script.content

    def test_can_omit_verification(self, linux_guide):
        script = generate_bash_script(linux_guide, include_verify=False)
        assert "getenforce" not in script.content

    def test_header_records_finding_ids(self, linux_guide):
        script = generate_bash_script(linux_guide)
        assert "LSEC-SELINUX-001" in script.content
        assert "LSEC-KERNEL-001" in script.content

    def test_header_has_generated_timestamp(self, linux_guide):
        script = generate_bash_script(linux_guide)
        # ISO-8601 date prefix is sufficient
        assert "Generated:" in script.content

    def test_reboot_warning_emitted_when_required(self, linux_guide):
        script = generate_bash_script(linux_guide)
        assert "reboot" in script.content.lower()

    def test_no_reboot_warning_when_not_required(self):
        guide = _guide(_rec("LSEC-KERNEL-002", reboot_required=False))
        script = generate_bash_script(guide)
        assert "reboot required" not in script.content.lower()

    def test_change_risk_high_is_annotated(self, linux_guide):
        script = generate_bash_script(linux_guide)
        # The high-risk SELinux rec must surface its risk in the per-section header
        assert "high" in script.content.lower()

    def test_empty_guide_produces_minimal_script(self):
        guide = HardeningGuide(recommendations=[], summary={})
        script = generate_bash_script(guide)
        assert "#!/bin/bash" in script.content
        assert "no remediation" in script.content.lower() or "no actions" in script.content.lower()

    def test_language_field_set(self, linux_guide):
        script = generate_bash_script(linux_guide)
        assert script.language == "bash"

    def test_finding_ids_field_populated(self, linux_guide):
        script = generate_bash_script(linux_guide)
        assert set(script.finding_ids) == {"LSEC-SELINUX-001", "LSEC-KERNEL-001"}

    def test_recommendation_count_field(self, linux_guide):
        script = generate_bash_script(linux_guide)
        assert script.recommendation_count == 2


# ---- powershell script generation -------------------------------------------


class TestPowerShellScript:
    def test_has_comment_header_not_shebang(self, windows_guide):
        script = generate_powershell_script(windows_guide)
        # .ps1 files do not use a shebang; header is a comment block
        assert not script.content.startswith("#!"), "powershell must not start with shebang"
        assert script.content.lstrip().startswith("#"), "powershell must start with a comment header"

    def test_sets_error_action_preference(self, windows_guide):
        script = generate_powershell_script(windows_guide)
        assert "$ErrorActionPreference" in script.content
        assert "Stop" in script.content

    def test_strips_powershell_command_wrapper(self, windows_guide):
        """Inside a .ps1 the `powershell -Command "..."` wrapper must be removed."""
        script = generate_powershell_script(windows_guide)
        assert "Set-MpPreference -DisableRealtimeMonitoring $false" in script.content
        assert 'powershell -Command "Set-MpPreference' not in script.content

    def test_keeps_native_cmd_commands(self, windows_guide):
        script = generate_powershell_script(windows_guide)
        assert "netsh advfirewall set allprofiles state on" in script.content

    def test_contains_verification_by_default(self, windows_guide):
        script = generate_powershell_script(windows_guide)
        assert "Get-MpComputerStatus" in script.content

    def test_can_omit_verification(self, windows_guide):
        script = generate_powershell_script(windows_guide, include_verify=False)
        assert "Get-MpComputerStatus" not in script.content

    def test_header_records_finding_ids(self, windows_guide):
        script = generate_powershell_script(windows_guide)
        assert "WSEC-DEF-001" in script.content
        assert "WSEC-FW-001" in script.content

    def test_language_field_set(self, windows_guide):
        script = generate_powershell_script(windows_guide)
        assert script.language == "powershell"

    def test_empty_guide_produces_minimal_script(self):
        guide = HardeningGuide(recommendations=[], summary={})
        script = generate_powershell_script(guide)
        assert "$ErrorActionPreference" in script.content
        assert "no remediation" in script.content.lower() or "no actions" in script.content.lower()


# ---- auto-routing (generate_scripts) ----------------------------------------


class TestAutoRouting:
    def test_mixed_guide_emits_two_scripts(self, mixed_guide):
        scripts = generate_scripts(mixed_guide)
        langs = sorted(s.language for s in scripts)
        assert langs == ["bash", "powershell"]

    def test_linux_only_guide_emits_one_bash(self, linux_guide):
        scripts = generate_scripts(linux_guide)
        assert len(scripts) == 1
        assert scripts[0].language == "bash"

    def test_windows_only_guide_emits_one_powershell(self, windows_guide):
        scripts = generate_scripts(windows_guide)
        assert len(scripts) == 1
        assert scripts[0].language == "powershell"

    def test_empty_guide_emits_no_scripts(self):
        guide = HardeningGuide(recommendations=[], summary={})
        assert generate_scripts(guide) == []

    def test_finding_ids_partitioned_correctly(self, mixed_guide):
        scripts = generate_scripts(mixed_guide)
        bash = next(s for s in scripts if s.language == "bash")
        ps = next(s for s in scripts if s.language == "powershell")
        assert all(fid.startswith("LSEC") for fid in bash.finding_ids)
        assert all(fid.startswith("WSEC") for fid in ps.finding_ids)

    def test_no_empty_scripts_emitted(self, mixed_guide):
        """If a platform has zero findings, no empty script is produced for it."""
        scripts = generate_scripts(mixed_guide)
        for s in scripts:
            assert s.recommendation_count > 0


# ---- integration with generate_guide ----------------------------------------


class TestIntegrationWithGuide:
    def test_bash_script_from_real_guide(self):
        findings = [
            {"id": "LSEC-SELINUX-001", "severity": "high", "category": "selinux"},
            {"id": "LSEC-KERNEL-002", "severity": "medium", "category": "kernel"},
        ]
        guide = generate_guide(findings)
        script = generate_bash_script(guide)
        assert "setenforce" in script.content
        assert "kptr_restrict" in script.content
        assert script.recommendation_count == 2

    def test_powershell_script_from_real_guide(self):
        findings = [
            {"id": "WSEC-FW-001", "severity": "critical", "category": "firewall"},
            {"id": "WSEC-PW-002", "severity": "medium", "category": "password"},
        ]
        guide = generate_guide(findings)
        script = generate_powershell_script(guide)
        assert "advfirewall" in script.content
        assert "minpwlen" in script.content
