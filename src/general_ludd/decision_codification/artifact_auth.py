"""Bounded payload construction for decision-artifact authentication."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sha256:([0-9a-f]{64})$")
_PROJECT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)
_GENERATION_STATE_PURPOSE: Final[str] = "shared-generation-state/v1"


class ArtifactPayloadError(ValueError):
    """Reject an invalid authentication payload before it reaches the HMAC store."""


def observability_hmac_payload(
    project_id: str,
    policy_digest: str,
    receipt_digest: str,
) -> dict[str, str]:
    """Build one validated exact-scope observability payload."""
    if (
        not isinstance(project_id, str)
        or _PROJECT_PATTERN.fullmatch(project_id) is None
        or not isinstance(policy_digest, str)
        or _DIGEST_PATTERN.fullmatch(policy_digest) is None
        or not isinstance(receipt_digest, str)
        or _DIGEST_PATTERN.fullmatch(receipt_digest) is None
    ):
        raise ArtifactPayloadError
    return {
        "purpose": "decision-observability-status/v1",
        "project_id": project_id,
        "policy_digest": policy_digest,
        "receipt_digest": receipt_digest,
    }


def generation_state_hmac_payload(
    state: Mapping[str, object],
) -> dict[str, object]:
    """Build one bounded shared-generation-state authentication payload."""
    if not isinstance(state, dict):
        raise ArtifactPayloadError
    return {"purpose": _GENERATION_STATE_PURPOSE, "state": state}
