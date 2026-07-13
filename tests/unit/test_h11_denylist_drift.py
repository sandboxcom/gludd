"""H.11: deny-list subsets must not drift from the canonical :data:`CANONICAL_DENY_MARKERS`.

All three enforcement sites (capability_lattice, applier, apply) now import their
named subsets from :mod:`general_ludd.security.path_canonicalizer` — the single
source of truth.  This test verifies every named subset is a subset of the
canonical deny set.
"""

from __future__ import annotations

from general_ludd.security.path_canonicalizer import (
    CANONICAL_DENY_MARKERS,
    PROTECTED_FILE_STEMS,
    PROTECTED_PATH_MARKERS,
    PROTECTED_PATH_SEGMENTS,
    PROTECTED_PATH_SUBSTRINGS,
    _HARD_DENY_SEGMENTS,
    _HARD_DENY_SUBSTRINGS,
)


def _canonical_lower() -> frozenset[str]:
    return frozenset(m.lower() for m in CANONICAL_DENY_MARKERS)


def _marker_matches_canonical(marker: str, canonical: frozenset[str]) -> bool:
    """Check if marker (possibly path-anchored) maps to a canonical entry."""
    lowered = marker.lower()
    if lowered in canonical:
        return True
    stripped = lowered.strip("/")
    if stripped in canonical:
        return True
    if "/" not in lowered:
        return False
    # /module_utils/capability_policy — strip leading slash only
    no_leading = lowered.lstrip("/")
    return no_leading in canonical


def test_protected_path_markers_are_subset_of_canonical():
    canonical = _canonical_lower()
    for marker in PROTECTED_PATH_MARKERS:
        assert marker.lower() in canonical, (
            f"PROTECTED_PATH_MARKERS has {marker!r} missing from CANONICAL_DENY_MARKERS"
        )


def test_protected_file_stems_are_subset_of_canonical():
    canonical = _canonical_lower()
    for marker in PROTECTED_FILE_STEMS:
        assert marker.lower() in canonical, (
            f"PROTECTED_FILE_STEMS has {marker!r} missing from CANONICAL_DENY_MARKERS"
        )


def test_protected_path_segments_are_subset_of_canonical():
    canonical = _canonical_lower()
    for marker in PROTECTED_PATH_SEGMENTS:
        assert marker.lower() in canonical, (
            f"PROTECTED_PATH_SEGMENTS has {marker!r} missing from CANONICAL_DENY_MARKERS"
        )


def test_protected_path_substrings_normalize_to_canonical():
    canonical = _canonical_lower()
    for marker in PROTECTED_PATH_SUBSTRINGS:
        assert _marker_matches_canonical(marker, canonical), (
            f"PROTECTED_PATH_SUBSTRINGS has {marker!r} "
            f"missing from CANONICAL_DENY_MARKERS"
        )


def test_hard_deny_substrings_normalize_to_canonical():
    canonical = _canonical_lower()
    for marker in _HARD_DENY_SUBSTRINGS:
        assert _marker_matches_canonical(marker, canonical), (
            f"_HARD_DENY_SUBSTRINGS has {marker!r} "
            f"missing from CANONICAL_DENY_MARKERS"
        )


def test_hard_deny_segments_are_subset_of_canonical():
    canonical = _canonical_lower()
    for marker in _HARD_DENY_SEGMENTS:
        assert marker.lower() in canonical, (
            f"_HARD_DENY_SEGMENTS has {marker!r} missing from CANONICAL_DENY_MARKERS"
        )
