"""Canonical file-integrity-monitor (FIM) exclude pattern set.

A single source of truth for the regexes that drop compiled artefacts, VCS
internals, and on-disk databases from every integrity/change listing.  Previously
this list was copy-pasted across :mod:`general_ludd.cli`
(``_scan_local_integrity``), :mod:`general_ludd.cli_core_changes`
(``_excluded``), and :mod:`general_ludd.routers.integrity` — three literals that
could silently drift apart.  Import :data:`FIM_EXCLUDE_PATTERNS` instead.

Each entry is an ``re.search`` pattern applied to a path whose separators have
been normalised to ``/``.  Match semantics are unchanged from the historical
literal ``[r"\\.pyc$", r"__pycache__", r"\\.git/", r"\\.db$"]``.
"""

from __future__ import annotations

# NOTE: keep this identical to the historical literal so scan/exclusion
# behaviour is unchanged — this is a de-duplication, not a policy change.
FIM_EXCLUDE_PATTERNS: tuple[str, ...] = (
    r"\.pyc$",
    r"__pycache__",
    r"\.git/",
    r"\.db$",
)
