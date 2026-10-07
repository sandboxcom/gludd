"""Regression policy for adjudicated Python dependency advisories."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
SECURITY = (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
PROFILE_CONFIG = tomllib.loads(
    (ROOT / "config" / "dependency_profiles.toml").read_text(encoding="utf-8")
)


def _profile_project(name: str) -> str:
    project = cast(str, PROFILE_CONFIG["profiles"][name]["project"])
    return (ROOT / project / "pyproject.toml").read_text(encoding="utf-8")


def test_ansible_core_uses_stable_fixed_release() -> None:
    for profile in ("ansible-controller", "dev-ansible"):
        project = _profile_project(profile)
        assert '"ansible-core>=2.19.11,<2.20; python_version < \'3.12\'"' in project
        assert '"ansible-core>=2.21.2,<2.22; python_version >= \'3.12\'"' in project
    assert "PYSEC-2026-3458" not in PROFILE_CONFIG["audit-ignore"]


def test_pip_uses_fixed_doubly_encoded_url_release() -> None:
    project = _profile_project("dev-build")
    assert project.count('"pip>=26.2"') == 1
    assert '"pip>=26.1.2"' not in project
    assert "PYSEC-2026-3721" in SECURITY
    assert "PYSEC-2026-3721" not in PROFILE_CONFIG["audit-ignore"]


def test_cryptography_pkcs7_vex_is_enforced() -> None:
    advisory = "PYSEC-2026-3552"
    assert advisory in PROFILE_CONFIG["audit-ignore"]
    assert advisory in SECURITY

    vulnerable_apis = (
        "pkcs7_decrypt_der",
        "pkcs7_decrypt_pem",
        "pkcs7_decrypt_smime",
    )
    offenders: list[str] = []
    for path in (ROOT / "src" / "general_ludd").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(api in text for api in vulnerable_apis):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_diskcache_vex_names_the_safe_serializer() -> None:
    assert "CVE-2025-69872" in PROFILE_CONFIG["audit-ignore"]
    assert "security.safe_diskcache" in SECURITY
    assert "msgpack-v1" in SECURITY
