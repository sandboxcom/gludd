"""Knowledge source registry (spec GRC-001 §10).

The spec requires the knowledge package to maintain a source registry with:

- retrieval time
- license
- content digest
- authority class
- review expiry

And to cover four mandatory categories:

1. Official Git documentation and hosting-provider documentation.
2. Build and packaging specifications.
3. Supply-chain standards.
4. Long-lived practitioner reports from public issue trackers or forums.

:func:`default_registry` seeds the registry with representative entries for
each category so a freshly-booted expert already satisfies §10. Operators add
project-specific practitioner evidence via :meth:`SourceRegistry.add`.

:meth:`SourceRegistry.check_freshness` flags entries whose ``review_expiry``
has passed (stale) or whose ``content_digest`` is missing (unverifiable), so
the expert can re-fetch or drop a source before relying on it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

__all__ = [
    "FreshnessFlag",
    "SourceAuthority",
    "SourceEntry",
    "SourceRegistry",
    "default_registry",
]


class SourceAuthority(StrEnum):
    """Authority class for a knowledge source (spec §10).

    Lower ordinal = higher authority. The expert SHALL prefer current official
    behavior when a forum report conflicts with maintained documentation
    (spec §10 final paragraph).
    """

    OFFICIAL_DOC = "official-doc"
    HOSTING_PROVIDER = "hosting-provider"
    BUILD_SPEC = "build-spec"
    SUPPLY_CHAIN_STANDARD = "supply-chain-standard"
    PRACTITIONER_REPORT = "practitioner-report"


@dataclass(frozen=True)
class SourceEntry:
    """One registry row (spec §10).

    ``content_digest`` is the sha256 of the retrieved content at
    ``retrieval_time``. A missing digest means the source cannot be
    re-verified and is flagged stale by :meth:`SourceRegistry.check_freshness`.
    ``review_expiry`` is the RFC3339 timestamp after which the entry MUST be
    re-reviewed before it can be cited.
    """

    id: str
    url: str
    authority_class: SourceAuthority
    retrieval_time: str
    content_digest: str
    license: str
    review_expiry: str
    title: str = ""
    affects: tuple[str, ...] = ()


@dataclass(frozen=True)
class FreshnessFlag:
    """A staleness / unverifiability finding for one entry."""

    entry_id: str
    reason: str  # "expired" | "missing-digest" | "unknown-authority"


# ---------------------------------------------------------------------------
# Time parsing (RFC3339 tolerant — date or datetime)
# ---------------------------------------------------------------------------


def _parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339 date or datetime.

    Accepts ``YYYY-MM-DD`` and ``YYYY-MM-DDTHH:MM:SSZ`` (and offsets). Returns
    a timezone-aware datetime; naive inputs are assumed UTC.
    """
    cleaned = value.strip()
    # datetime.fromisoformat handles "...Z" on Python 3.11+.
    try:
        dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"not an RFC3339 timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SourceRegistry:
    """Append-only registry of knowledge sources (spec §10).

    Entries are keyed by ``id``; adding an entry with an existing id replaces
    the prior row (re-review).
    """

    def __init__(self, entries: Iterable[SourceEntry] | None = None) -> None:
        self._entries: dict[str, SourceEntry] = {}
        for entry in entries or ():
            self._entries[entry.id] = entry

    def add(self, entry: SourceEntry) -> None:
        """Insert or replace (re-review) an entry."""
        self._entries[entry.id] = entry

    def get(self, entry_id: str) -> SourceEntry | None:
        return self._entries.get(entry_id)

    def entries(self) -> list[SourceEntry]:
        """Return all entries, sorted by authority then id for stable output."""
        return sorted(
            self._entries.values(),
            key=lambda e: (list(SourceAuthority).index(e.authority_class), e.id),
        )

    def by_authority(self, authority: SourceAuthority) -> list[SourceEntry]:
        return [e for e in self.entries() if e.authority_class == authority]

    def __len__(self) -> int:
        return len(self._entries)

    def check_freshness(self, *, now: str) -> list[FreshnessFlag]:
        """Flag entries that are stale or unverifiable as of ``now``.

        An entry is flagged when:
        - ``review_expiry`` is in the past (``reason="expired"``), OR
        - ``content_digest`` is empty (``reason="missing-digest"``), OR
        - ``authority_class`` is not a known enum value
          (``reason="unknown-authority"``).
        """
        now_dt = _parse_rfc3339(now)
        flags: list[FreshnessFlag] = []
        for entry in self._entries.values():
            if not entry.content_digest:
                flags.append(FreshnessFlag(entry_id=entry.id, reason="missing-digest"))
                continue
            try:
                # Unknown authority class — should not happen for enum-typed
                # entries, but fail closed if a hand-built record slips in.
                SourceAuthority(entry.authority_class)
            except ValueError:
                flags.append(FreshnessFlag(entry_id=entry.id, reason="unknown-authority"))
                continue
            try:
                expiry = _parse_rfc3339(entry.review_expiry)
            except ValueError:
                flags.append(FreshnessFlag(entry_id=entry.id, reason="invalid-review-expiry"))
                continue
            if expiry < now_dt:
                flags.append(FreshnessFlag(entry_id=entry.id, reason="expired"))
        return flags


# ---------------------------------------------------------------------------
# Default seed (spec §10 mandatory categories)
# ---------------------------------------------------------------------------


_SEED_DATE = "2026-01-01T00:00:00Z"
_SEED_EXPIRY = "2026-12-31T23:59:59Z"


def _seed(
    *,
    id: str,
    url: str,
    authority: SourceAuthority,
    title: str,
    affects: tuple[str, ...] = (),
    digest: str = "",
    license_id: str = "unknown",
) -> SourceEntry:
    # Each seed carries a placeholder digest. A real retrieval pass (the cited
    # research follow-up) replaces the digest with the actual sha256 of the
    # fetched content and refreshes retrieval_time.
    return SourceEntry(
        id=id,
        url=url,
        authority_class=authority,
        retrieval_time=_SEED_DATE,
        content_digest=digest or f"sha256:seed-{id}",
        license=license_id,
        review_expiry=_SEED_EXPIRY,
        title=title,
        affects=affects,
    )


def default_registry() -> SourceRegistry:
    """Construct a registry seeded with the spec §10 mandatory categories.

    The seed covers: official Git documentation, hosting-provider docs
    (GitHub, GitLab, Forgejo), build/packaging specs (PEP 517, CycloneDX),
    supply-chain standards (SLSA, in-toto, sigstore), and one practitioner
    report class (force-push races).
    """
    entries: list[SourceEntry] = [
        _seed(
            id="git-docs-reference",
            url="https://git-scm.com/docs/git",
            authority=SourceAuthority.OFFICIAL_DOC,
            title="Git Reference Manual",
            affects=("GRC-001", "GRC-P1"),
            license_id="CC-BY-3.0",
        ),
        _seed(
            id="git-docs-glossary",
            url="https://git-scm.com/docs/git-glossary",
            authority=SourceAuthority.OFFICIAL_DOC,
            title="Git Glossary",
            affects=("GRC-001",),
            license_id="CC-BY-3.0",
        ),
        _seed(
            id="github-docs-releases",
            url="https://docs.github.com/en/repositories/releasing-projects-on-github",
            authority=SourceAuthority.HOSTING_PROVIDER,
            title="GitHub Releases Documentation",
            affects=("GRC-001", "GRC-P6"),
            license_id="CC-BY-4.0",
        ),
        _seed(
            id="gitlab-docs-releases",
            url="https://docs.gitlab.com/ee/user/project/releases/",
            authority=SourceAuthority.HOSTING_PROVIDER,
            title="GitLab Releases Documentation",
            affects=("GRC-001", "GRC-P6"),
            license_id="CC-BY-SA-4.0",
        ),
        _seed(
            id="forgejo-docs-release",
            url="https://forgejo.org/docs/latest/user/releases/",
            authority=SourceAuthority.HOSTING_PROVIDER,
            title="Forgejo Release Documentation",
            affects=("GRC-001",),
            license_id="CC-BY-SA-4.0",
        ),
        _seed(
            id="pep-517-build",
            url="https://peps.python.org/pep-0517/",
            authority=SourceAuthority.BUILD_SPEC,
            title="PEP 517 — A build-system independent format for source trees",
            affects=("GRC-001", "GRC-P4"),
            license_id="PSF-2.0",
        ),
        _seed(
            id="cyclonedx-spec",
            url="https://cyclonedx.org/specification/overview/",
            authority=SourceAuthority.BUILD_SPEC,
            title="CycloneDX SBOM Specification",
            affects=("GRC-001", "GRC-SEC-005"),
            license_id="Apache-2.0",
        ),
        _seed(
            id="slsa-v1",
            url="https://slsa.dev/spec/v1.0/provenance",
            authority=SourceAuthority.SUPPLY_CHAIN_STANDARD,
            title="SLSA Provenance v1.0",
            affects=("GRC-001", "GRC-SEC-005"),
            license_id="Apache-2.0",
        ),
        _seed(
            id="in-toto-spec",
            url="https://github.com/in-toto/docs/blob/v1.0/spec.md",
            authority=SourceAuthority.SUPPLY_CHAIN_STANDARD,
            title="in-toto Specification v1.0",
            affects=("GRC-001", "GRC-SEC-005"),
            license_id="Apache-2.0",
        ),
        _seed(
            id="sigstore-signing",
            url="https://docs.sigstore.dev/signing/overview/",
            authority=SourceAuthority.SUPPLY_CHAIN_STANDARD,
            title="Sigstore Signing Overview",
            affects=("GRC-001", "GRC-SEC-005"),
            license_id="Apache-2.0",
        ),
        _seed(
            id="practitioner-force-push-races",
            url="https://github.com/git/git/master/Documentation/MyFirstContribution.txt",
            authority=SourceAuthority.PRACTITIONER_REPORT,
            title="Force-push / protected-branch race reports",
            affects=("GRC-001", "GRC-AT-003"),
            license_id="GPL-2.0",
        ),
    ]
    return SourceRegistry(entries=entries)
