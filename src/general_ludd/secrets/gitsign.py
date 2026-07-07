"""Per-project gitsign configuration via project-namespaced OpenBao."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from general_ludd.secrets.manager import SecretsManager

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class GitsignConfig:
    fulcio_url: str = "https://fulcio.sigstore.dev"
    rekor_url: str = "https://rekor.sigstore.dev"
    oidc_issuer: str = "https://oauth2.sigstore.dev/auth"
    key_ref: str = ""
    enabled: bool = False


def _scoped_path(project_id: str) -> str:
    if not _SEGMENT_RE.match(project_id):
        raise ValueError(
            f"invalid project_id {project_id!r}: must match ^[A-Za-z0-9_-]+$"
        )
    return f"projects/{project_id}/gitsign/config"


def write_gitsign_config(
    mgr: SecretsManager,
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


def read_gitsign_config(mgr: SecretsManager, project_id: str) -> GitsignConfig | None:
    data = mgr.read_secret(_scoped_path(project_id))
    if data is None:
        return None
    return GitsignConfig(
        fulcio_url=cast(str, data.get("fulcio_url", "https://fulcio.sigstore.dev")),
        rekor_url=cast(str, data.get("rekor_url", "https://rekor.sigstore.dev")),
        oidc_issuer=cast(str, data.get("oidc_issuer", "https://oauth2.sigstore.dev/auth")),
        key_ref=cast(str, data.get("key_ref", "")),
        enabled=cast(bool, data.get("enabled", False)),
    )
