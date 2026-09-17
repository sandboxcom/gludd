"""Provider-neutral identities shared by universal model consumers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum

_CATALOG_VERSION_RE = re.compile(r"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ModelCandidateProvider(StrEnum):
    """Stable provider identities available to model-capability consumers."""

    LOCAL_GGUF = "local_gguf"
    AZURE_FOUNDRY = "azure_foundry"
    AZURE_CONTAINER_APP = "azure_container_app"
    CATALOG_FREE_TIER = "catalog_free_tier"


def stable_identity_digest(value: dict[str, object]) -> str:
    """Return a canonical digest for non-secret model identity evidence."""
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class CatalogFreeTierCandidateIdentity:
    """Exact signed-catalog row admitted for an explicitly opted-in trial.

    The identity contains no endpoint or credential. The concrete provider
    transport remains behind Gludd's native gateway and its reviewed aliases.
    """

    platform: str
    model_id: str
    catalog_version: str
    catalog_payload_sha256: str

    def __post_init__(self) -> None:
        """Require canonical provider/model evidence from one exact catalog."""
        if not isinstance(self.platform, str) or _PLATFORM_RE.fullmatch(
            self.platform
        ) is None:
            raise ValueError("platform must be one canonical provider slug")
        if (
            not isinstance(self.model_id, str)
            or not self.model_id
            or self.model_id != self.model_id.strip()
            or len(self.model_id.encode("utf-8")) > 512
            or any(ord(character) < 0x20 for character in self.model_id)
        ):
            raise ValueError("model_id must be bounded canonical UTF-8 text")
        if not isinstance(
            self.catalog_version,
            str,
        ) or _CATALOG_VERSION_RE.fullmatch(self.catalog_version) is None:
            raise ValueError("catalog_version must be one canonical date version")
        if not isinstance(
            self.catalog_payload_sha256,
            str,
        ) or _DIGEST_RE.fullmatch(self.catalog_payload_sha256) is None:
            raise ValueError("catalog_payload_sha256 must be one lowercase digest")

    @property
    def provider(self) -> ModelCandidateProvider:
        """Return the scalable provider-neutral catalog category."""
        return ModelCandidateProvider.CATALOG_FREE_TIER

    @property
    def identity_digest(self) -> str:
        """Bind the model row to the exact authenticated catalog snapshot."""
        return stable_identity_digest(
            {
                "catalog_payload_sha256": self.catalog_payload_sha256,
                "catalog_version": self.catalog_version,
                "model_id": self.model_id,
                "platform": self.platform,
                "protocol": "gludd-catalog-free-tier-candidate-v1",
                "provider": self.provider.value,
            }
        )

    @property
    def evidence_identity_digest(self) -> str:
        """Keep learning scoped to the exact authenticated catalog row."""
        return self.identity_digest


__all__ = (
    "CatalogFreeTierCandidateIdentity",
    "ModelCandidateProvider",
    "stable_identity_digest",
)
