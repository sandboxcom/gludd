"""Regression policy for adjudicated Python dependency advisories."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
SECURITY = (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")


def test_ansible_core_uses_stable_fixed_release() -> None:
    assert '"ansible-core>=2.19.11,<2.20; python_version < \'3.12\'"' in PYPROJECT
    assert '"ansible-core>=2.21.2,<2.22; python_version >= \'3.12\'"' in PYPROJECT
    assert "--ignore-vuln PYSEC-2026-3458" not in MAKEFILE


def test_pip_uses_fixed_doubly_encoded_url_release() -> None:
    assert PYPROJECT.count('"pip>=26.2"') == 2
    assert '"pip>=26.1.2"' not in PYPROJECT
    assert "PYSEC-2026-3721" in SECURITY
    assert "--ignore-vuln PYSEC-2026-3721" not in MAKEFILE


def test_cryptography_pkcs7_vex_is_enforced() -> None:
    advisory = "PYSEC-2026-3552"
    assert f"--ignore-vuln {advisory}" in MAKEFILE
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
    assert "--ignore-vuln CVE-2025-69872" in MAKEFILE
    assert "security.safe_diskcache" in SECURITY
    assert "msgpack-v1" in SECURITY
