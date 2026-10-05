"""Fail-closed loading for configured self-improvement evidence."""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path

from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_MAX_CONFIGURED_EVIDENCE_BYTES = 67_108_864


def configured_capability_evidence_store(
    self_improve_config: Mapping[str, object] | None,
) -> CapabilityEvidenceStore | None:
    """Load the exact runtime store selected by the Azure planning phase."""
    if self_improve_config is None:
        return None
    if not isinstance(self_improve_config, Mapping):
        raise ValueError("self_improve configuration must be a mapping")
    raw = self_improve_config.get("capability_evidence")
    if raw is None:
        return None
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"schema_version", "path"}
        or raw.get("schema_version") != 1
    ):
        raise ValueError("capability evidence configuration is invalid")
    configured_path = raw.get("path")
    if not isinstance(configured_path, str) or not configured_path:
        raise ValueError("capability evidence configuration is invalid")
    path = Path(configured_path)
    if not path.is_absolute():
        raise ValueError("capability evidence path must be absolute")
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_CONFIGURED_EVIDENCE_BYTES
        ):
            raise ValueError
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or any(
            not isinstance(record, Mapping) for record in payload
        ):
            raise ValueError
        canonical = path.resolve(strict=True)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError("capability evidence store is invalid") from None
    return CapabilityEvidenceStore(str(canonical))


__all__ = ["configured_capability_evidence_store"]
