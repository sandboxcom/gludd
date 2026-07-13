"""H.11: three independent protected-path deny-lists must not drift from canonical."""

from __future__ import annotations

from general_ludd.security.capability_lattice import (
    PROTECTED_FILE_STEMS,
    PROTECTED_PATH_SEGMENTS,
    PROTECTED_PATH_SUBSTRINGS,
)
from general_ludd.security.path_canonicalizer import CANONICAL_DENY_MARKERS
from general_ludd.self_update.applier import PROTECTED_PATH_MARKERS
from general_ludd.self_update.apply import (
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
    # /module_utils/capability_policy → remove leading slash only
    lowered.lstrip("/")
    return unm in canonical


def test_applier_protected_path_markers_are_subset_of_canonical():
    canonical = _canonical_lower()
    for marker in PROTECTED_PATH_MARKERS:
        assert marker.lower() in canonical, (
            f"applier.PROTECTED_PATH_MARKERS has {marker!r} missing from CANONICAL_DENY_MARKERS"
        )


def test_capability_lattice_protected_file_stems_are_subset_of_canonical():
    canonical = _canonical_lower()
    for marker in PROTECTED_FILE_STEMS:
        assert marker.lower() in canonical, (
            f"capability_lattice.PROTECTED_FILE_STEMS has {marker!r} missing from CANONICAL_DENY_MARKERS"
        )


def test_capability_lattice_protected_path_segments_are_subset_of_canonical():
    canonical = _canonical_lower()
    for marker in PROTECTED_PATH_SEGMENTS:
        assert marker.lower() in canonical, (
            f"capability_lattice.PROTECTED_PATH_SEGMENTS has {marker!r} missing from CANONICAL_DENY_MARKERS"
        )


def test_capability_lattice_protected_path_substrings_normalize_to_canonical():
    canonical = _canonical_lower()
    for marker in PROTECTED_PATH_SUBSTRINGS:
        assert _marker_matches_canonical(marker, canonical), (
            f"capability_lattice.PROTECTED_PATH_SUBSTRINGS has {marker!r} "
            f"missing from CANONICAL_DENY_MARKERS"
        )


def test_apply_hard_deny_substrings_normalize_to_canonical():
    canonical = _canonical_lower()
    for marker in _HARD_DENY_SUBSTRINGS:
        assert _marker_matches_canonical(marker, canonical), (
            f"apply._HARD_DENY_SUBSTRINGS has {marker!r} "
            f"missing from CANONICAL_DENY_MARKERS"
        )


def test_apply_hard_deny_segments_are_subset_of_canonical():
    canonical = _canonical_lower()
    for marker in _HARD_DENY_SEGMENTS:
        assert marker.lower() in canonical, (
            f"apply._HARD_DENY_SEGMENTS has {marker!r} missing from CANONICAL_DENY_MARKERS"
        )
