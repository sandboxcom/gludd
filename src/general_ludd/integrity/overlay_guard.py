"""Self-improvement overlay monitoring guard (safety warning).

When gludd's self-improvement loop is enabled, agent-authored changes land in
the config **overlay** directories:

  * the project-local ``.gludd/`` directory (``find_project_gludd_dir()``); and
  * the user overlay ``${XDG_CONFIG_HOME:-~/.config}/gludd``.

If those overlays are NOT inside the file-integrity monitor's ``watch_paths`` —
or an ``exclude_patterns`` regex skips their files — self-modifications go
UNTRACKED by FIM: the very changes we most want an audit trail for slip past the
tamper detector.  :func:`warn_if_overlay_unmonitored` emits a
``logging.warning`` for each such gap so operators notice the blind spot.

The scan sites (``cli._scan_local_integrity`` and
``routers/integrity.admin_integrity_scan``) call it with the resolved
``self_improve_enabled`` signal; when self-improve is off it is a no-op (no
false alarms).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path

from general_ludd.config.project_dir import find_project_gludd_dir

logger = logging.getLogger(__name__)

# Default self-improve interval. Mirrors daemon.py: the interval defaults to 10
# so the feature is ON out-of-the-box; interval 0 disables it. The authoritative
# runtime signal for "self-improve enabled" is therefore ``interval > 0``.
_DEFAULT_SELF_IMPROVE_INTERVAL = 10


def resolve_self_improve_enabled(
    si_cfg: Mapping[str, object] | None,
    *,
    default_interval: int = _DEFAULT_SELF_IMPROVE_INTERVAL,
) -> bool:
    """Return True when the self-improve loop is active for *si_cfg*.

    The authoritative runtime signal is the self-improve *interval*: a positive
    interval means the loop runs (``event_loop/loop.py::_phase_self_improve``),
    interval ``0`` disables it. An absent/empty config defaults to
    *default_interval* (ON out-of-the-box), matching daemon.py's wiring.
    """
    if not si_cfg:
        return default_interval > 0
    try:
        interval = int(str(si_cfg.get("interval", default_interval)))
    except (TypeError, ValueError):
        interval = default_interval
    return interval > 0


def _user_overlay_dir() -> Path:
    """Resolve the user overlay ``${XDG_CONFIG_HOME:-~/.config}/gludd``."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "gludd"


def resolve_overlay_dirs() -> list[str]:
    """Return the self-improve overlay directories that currently exist.

    A non-existent overlay cannot receive self-modifications yet, so it is
    omitted (nothing to warn about). Order: project ``.gludd/`` first, then the
    user overlay. Duplicates (same resolved path) are collapsed.
    """
    dirs: list[str] = []
    proj = find_project_gludd_dir()
    if proj is not None:
        dirs.append(str(proj))
    user = _user_overlay_dir()
    if user.is_dir():
        dirs.append(str(user))

    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        try:
            rp = str(Path(d).expanduser().resolve())
        except (OSError, RuntimeError):
            rp = d
        if rp not in seen:
            seen.add(rp)
            out.append(d)
    return out


def _is_within(child: str, parent: str) -> bool:
    """True when *child* is *parent* or a descendant of it (resolved paths)."""
    try:
        c = Path(child).expanduser().resolve()
        p = Path(parent).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return c == p or p in c.parents


def warn_if_overlay_unmonitored(
    watch_paths: list[str],
    exclude_patterns: list[str],
    self_improve_enabled: bool,
    *,
    overlay_dirs: list[str] | None = None,
) -> list[str]:
    """Warn when self-improve overlays fall outside FIM's effective scope.

    Returns (and logs via ``logging.warning``) one warning string per overlay
    that would go untracked when *self_improve_enabled* is True. Returns ``[]``
    and logs nothing when self-improve is disabled — no false alarms.

    An overlay is flagged when either:

      * it is not inside any ``watch_paths`` root (not monitored at all), or
      * an ``exclude_patterns`` regex ``re.search``-matches a representative
        file under it (``general-ludd.yml`` / ``general_ludd/x.py``), meaning
        the overlay's files would be skipped by the scan.

    Args:
        watch_paths: The directories the integrity scan will walk.
        exclude_patterns: Regex strings; a file whose path matches is skipped
            (``FileIntegrityScanner.scan`` matches these via ``re.search``).
        self_improve_enabled: The resolved runtime signal — see
            :func:`resolve_self_improve_enabled`.
        overlay_dirs: Explicit overlay directories to check (mainly for tests);
            defaults to :func:`resolve_overlay_dirs`.
    """
    if not self_improve_enabled:
        return []

    overlays = overlay_dirs if overlay_dirs is not None else resolve_overlay_dirs()
    if not overlays:
        return []

    roots = [w for w in watch_paths if w]
    compiled: list[re.Pattern[str]] = []
    for pat in exclude_patterns or []:
        try:
            compiled.append(re.compile(pat))
        except re.error:
            continue

    warnings: list[str] = []
    for overlay in overlays:
        try:
            resolved = str(Path(overlay).expanduser().resolve())
        except (OSError, RuntimeError):
            resolved = overlay

        if not any(_is_within(overlay, root) for root in roots):
            msg = (
                f"Self-improvement is enabled but the overlay directory "
                f"{overlay!r} is NOT within any file-integrity monitored path "
                f"(watch_paths); self-modifications written there will not be "
                f"tracked by FIM."
            )
            logger.warning(msg)
            warnings.append(msg)
            continue

        # Monitored — but an exclude pattern may still skip the overlay's files.
        representatives = [
            os.path.join(resolved, "general-ludd.yml"),
            os.path.join(resolved, "general_ludd", "x.py"),
        ]
        matched = next(
            (
                p.pattern
                for p in compiled
                for rep in representatives
                if p.search(rep)
            ),
            None,
        )
        if matched is not None:
            msg = (
                f"Self-improvement is enabled but the overlay directory "
                f"{overlay!r} is matched by an integrity exclude pattern "
                f"({matched!r}); self-modifications written there will be "
                f"skipped and not tracked by FIM."
            )
            logger.warning(msg)
            warnings.append(msg)

    return warnings
