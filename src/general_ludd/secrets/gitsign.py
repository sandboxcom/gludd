"""Per-project gitsign configuration via project-namespaced OpenBao."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GitsignConfig:
    fulcio_url: str = "https://fulcio.sigstore.dev"
    rekor_url: str = "https://rekor.sigstore.dev"
    oidc_issuer: str = "https://oauth2.sigstore.dev/auth"
    key_ref: str = ""
    enabled: bool = False


def _scoped_path(project_id: str) -> str:
    return f"projects/{project_id}/gitsign/config"


def write_gitsign_config(
    mgr: Any,
    project_id: str,
    fulcio_url: str = "https://fulcio.sigstore.dev",
    rekor_url: str = "https://rekor.sigstore.dev",
    oidc_issuer: str = "https://oauth2.sigstore.dev/auth",
    key_ref: str = "",
    enabled: bool = True,
) -> None:
    mgr.write_secret(
        _scoped_path(project_id),
        {
            "fulcio_url": fulcio_url,
            "rekor_url": rekor_url,
            "oidc_issuer": oidc_issuer,
            "key_ref": key_ref,
            "enabled": enabled,
        },
    )


def read_gitsign_config(mgr: Any, project_id: str) -> GitsignConfig | None:
    data = mgr.read_secret(_scoped_path(project_id))
    if data is None:
        return None
    return GitsignConfig(
        fulcio_url=data.get("fulcio_url", "https://fulcio.sigstore.dev"),
        rekor_url=data.get("rekor_url", "https://rekor.sigstore.dev"),
        oidc_issuer=data.get("oidc_issuer", "https://oauth2.sigstore.dev/auth"),
        key_ref=data.get("key_ref", ""),
        enabled=data.get("enabled", False),
    )
