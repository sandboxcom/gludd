"""CHEM-019 provenance — every value traceable to source, method, code, artifact.

Implements CHEM-019 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §2 and the
provenance clause of §12 (Observability). Every factual value the chemistry
expert emits must be reconstructable end-to-end::

    value → source → method → conditions → code → raw artifact

* **source**: locator + citation + access date for the original record
* **method**: the procedure (experimental, predicted, computed) that produced it
* **conditions**: temperature, pressure, solvent, instrument, software version, ...
* **code**: the repository + commit + module that turned the artifact into the value
* **raw artifact**: content-addressed URI + digest of the underlying observation

The :func:`build_chain` function walks a result dict (single value or nested
records) and produces one :class:`ProvenanceChain` per ``provenance`` block it
finds. :func:`verify_chain` checks the five required links and flags orphan
artifacts (raw artifacts whose ``orphan`` flag is set — e.g. the underlying
file was deleted from object storage).
"""

from __future__ import annotations

import uuid
from typing import Any

SCHEMA_VERSION = "1.0"
PROVENANCE_VERSION = "chemistry-provenance@0.1.0"

REQUIRED_LINKS = ("source", "method", "conditions", "code", "raw_artifact")


def _new_id() -> str:
    return str(uuid.uuid4())


class ProvenanceChain:
    """A typed view over a single value's end-to-end provenance record.

    The chain is the canonical five-link form (source, method, conditions,
    code, raw_artifact). Construction is a thin projection over a dict — the
    underlying dict is the wire format that crosses API boundaries.
    """

    __slots__ = ("chain_id", "code", "conditions", "method", "raw_artifact", "source")

    def __init__(self, record: dict[str, Any]) -> None:
        if not isinstance(record, dict):
            raise TypeError("provenance record must be a dict")
        self.source: dict[str, Any] = dict(record.get("source") or {})
        self.method: str = str(record.get("method") or "")
        self.conditions: dict[str, Any] = dict(record.get("conditions") or {})
        self.code: dict[str, Any] = dict(record.get("code") or {})
        self.raw_artifact: dict[str, Any] = dict(record.get("raw_artifact") or {})
        self.chain_id: str = _new_id()

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "source": dict(self.source),
            "method": self.method,
            "conditions": dict(self.conditions),
            "code": dict(self.code),
            "raw_artifact": dict(self.raw_artifact),
            "schema_version": SCHEMA_VERSION,
            "canonicalizer": PROVENANCE_VERSION,
        }

    def __repr__(self) -> str:
        locator = self.source.get("locator", "?")
        return f"ProvenanceChain(method={self.method!r}, source={locator!r})"

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key) from None


def build_chain(result: Any) -> Any:
    """Build a :class:`ProvenanceChain` (or list of them) from a result.

    Three input shapes are accepted:

    * **Single value with a top-level ``provenance`` dict** — returns one
      :class:`ProvenanceChain`.
    * **A ``provenance`` dict directly** (no wrapping value) — returns one
      :class:`ProvenanceChain`.
    * **A nested dict of records each carrying a ``provenance`` block** —
      returns a list of :class:`ProvenanceChain`, one per nested record.

    Anything without a ``provenance`` block returns an empty list so callers
    can iterate uniformly.
    """
    if isinstance(result, ProvenanceChain):
        return result
    if not isinstance(result, dict):
        return []
    if "provenance" in result and isinstance(result["provenance"], dict):
        return ProvenanceChain(result["provenance"])
    if all(link in result for link in REQUIRED_LINKS):
        return ProvenanceChain(result)
    chains: list[ProvenanceChain] = []
    for value in result.values():
        if isinstance(value, dict) and isinstance(value.get("provenance"), dict):
            chains.append(ProvenanceChain(value["provenance"]))
    return chains


def verify_chain(chain: Any) -> dict[str, Any]:
    """Verify the completeness of a provenance chain.

    Returns a report dict::

        {
          "complete": bool,
          "missing": [str, ...],   # required links not present
          "orphan_artifacts": [str, ...],
          "chain_id": str | None,
        }

    A chain is complete iff all five required links are non-empty AND no raw
    artifact is flagged as orphan.
    """
    if isinstance(chain, ProvenanceChain):
        record = chain.to_dict()
        chain_id: str | None = chain.chain_id
    elif isinstance(chain, dict):
        record = chain
        chain_id = chain.get("chain_id")
    else:
        return {
            "complete": False,
            "missing": list(REQUIRED_LINKS),
            "orphan_artifacts": [],
            "chain_id": None,
            "reason": "chain must be a ProvenanceChain or dict",
        }

    missing: list[str] = []
    for link in REQUIRED_LINKS:
        value = record.get(link)
        if value is None:
            missing.append(link)
            continue
        if (isinstance(value, (dict, list)) and not value) or (isinstance(value, str) and not value.strip()):
            missing.append(link)

    orphan_artifacts: list[str] = []
    raw = record.get("raw_artifact")
    if isinstance(raw, dict) and raw.get("orphan"):
        uri = raw.get("uri") or raw.get("digest") or "<unknown>"
        orphan_artifacts.append(str(uri))
        missing.append(f"raw_artifact_orphan:{uri}")

    return {
        "complete": not missing and not orphan_artifacts,
        "missing": missing,
        "orphan_artifacts": orphan_artifacts,
        "chain_id": chain_id,
        "schema_version": SCHEMA_VERSION,
    }


__all__ = [
    "PROVENANCE_VERSION",
    "REQUIRED_LINKS",
    "SCHEMA_VERSION",
    "ProvenanceChain",
    "build_chain",
    "verify_chain",
]
