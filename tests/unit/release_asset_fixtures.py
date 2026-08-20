"""Canonical beta4 release-asset fixtures shared by release contract tests."""

from __future__ import annotations

from typing import Any


def complete_release_assets(version: str = "0.1.0") -> list[dict[str, Any]]:
    """Return the 30 assets that satisfy all 28 required release categories."""
    distribution_version = (
        version.replace("-alpha.", "a")
        .replace("-beta.", "b")
        .replace("-rc.", "rc")
    )
    return [
        {"name": f"gludd-{version}-linux-x86_64.tar.gz", "size": 5_000_000},
        {"name": f"gludd-{version}-linux-aarch64.tar.gz", "size": 4_800_000},
        {"name": f"gludd-{version}-macos-arm64.tar.gz", "size": 4_500_000},
        {"name": f"gludd-{version}-windows-x86_64.zip", "size": 5_200_000},
        {"name": f"gludd_{version}_amd64.deb", "size": 5_000_000},
        {"name": f"gludd-{version}-1.x86_64.rpm", "size": 5_100_000},
        {"name": f"gludd-{version}-macos-arm64.dmg", "size": 6_000_000},
        {"name": f"gludd-{version}-setup-x86_64.exe", "size": 5_500_000},
        {
            "name": f"general_ludd_agent-{distribution_version}-py3-none-any.whl",
            "size": 100_000,
        },
        {"name": f"general_ludd_agent-{distribution_version}.tar.gz", "size": 100_000},
        {"name": "general_ludd-agent-0.2.0.tar.gz", "size": 100_000},
        {"name": "general_ludd-language-0.1.0.tar.gz", "size": 100_000},
        {"name": "general_ludd-networking-0.2.0.tar.gz", "size": 100_000},
        {"name": f"gludd-collections-{version}.json", "size": 2_048},
        {"name": "ansible-ee-execution-environment.yml", "size": 2_048},
        {"name": "ansible-ee-requirements.yml", "size": 2_048},
        {"name": "ansible-ee-requirements.txt", "size": 2_048},
        {"name": "ansible-ee-bindep.txt", "size": 2_048},
        {"name": "ansible-ee-runtime-lock.json", "size": 2_048},
        {"name": "ansible-managed-host-python.lock.json", "size": 2_048},
        {
            "name": "ansible-collection-python-boundary-inventory.json",
            "size": 2_048,
        },
        {"name": f"gludd-ee-image-{version}.json", "size": 2_048},
        {"name": f"gludd-container-{version}.json", "size": 2_048},
        {"name": "SHA256SUMS", "size": 2_048},
        {"name": f"gludd-sbom-{version}.json", "size": 32_000},
        {"name": "install.sh", "size": 8_000},
        {"name": "LICENSE", "size": 11_000},
        {"name": "THIRD_PARTY_LICENSES.md", "size": 45_000},
        {"name": f"gludd-smoke-release-{version}.json", "size": 2_048},
        {"name": f"gludd-release-manifest-{version}.json", "size": 2_048},
    ]
