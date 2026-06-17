"""Security sanitization utilities for path and input validation."""

from __future__ import annotations

import ipaddress
import os
import re
from urllib.parse import urlparse

_PATH_TRAVERSAL = re.compile(r"(?:\.\./|\.\.\\)")
_ABSOLUTE_PATH = re.compile(r"^/|^[A-Za-z]:\\")
_JOB_ID_PATTERN = re.compile(r"^[A-Z0-9_\-]+$")
# A skill/file basename must be a single safe path component: no separators, no
# parent refs, no NUL. Attacker-controlled frontmatter ``name:`` is written to
# ``<target>/<name>.md`` so a "../" or "/" there escapes the skills directory.
_SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._\- ]+$")


def sanitize_path(path: str) -> str | None:
    cleaned = path.strip()
    if not cleaned:
        return None
    # F-1 (fail-closed traversal check): the _PATH_TRAVERSAL regex only matches
    # "../" / "..\" (a parent ref FOLLOWED BY a separator), so a bare ".." or a
    # trailing "foo/.." component slipped through and the raw string was
    # returned (fail-open). Split on BOTH separators and reject if ANY segment
    # is exactly "..", in addition to the existing checks.
    for segment in re.split(r"[\\/]+", cleaned):
        if segment == "..":
            return None
    if _PATH_TRAVERSAL.search(cleaned):
        return None
    if _ABSOLUTE_PATH.match(cleaned):
        return None
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def sanitize_job_id(job_id: str) -> str | None:
    if not job_id:
        return None
    if "/" in job_id or "\\" in job_id:
        return None
    if _PATH_TRAVERSAL.search(job_id):
        return None
    if not _JOB_ID_PATTERN.match(job_id):
        return None
    return job_id


def sanitize_skill_name(name: str) -> str | None:
    """Validate an attacker-controlled skill/file basename.

    Returns the cleaned name, or ``None`` when it contains a path separator, a
    parent reference, a NUL byte, or any character outside a conservative safe
    set. The caller writes ``<target>/<name>.md`` so a "../" or "/" here would
    escape the skills directory (path traversal via frontmatter).
    """
    if not name:
        return None
    cleaned = name.strip()
    if not cleaned:
        return None
    if "/" in cleaned or "\\" in cleaned or "\x00" in cleaned:
        return None
    if cleaned in {".", ".."} or _PATH_TRAVERSAL.search(cleaned):
        return None
    # Reject names containing consecutive dots (e.g. ".hidden..traverse") that
    # could be crafted to appear benign while forming a traversal sequence after
    # normalization or joining with another path segment.
    if ".." in cleaned:
        return None
    if not _SAFE_NAME_PATTERN.match(cleaned):
        return None
    return cleaned


def confine_path(candidate: str, root: str) -> str | None:
    """Resolve ``candidate`` and confirm it stays within ``root``.

    Returns the real (symlink-resolved, absolute) path when it is ``root`` or a
    descendant of ``root``; otherwise ``None``. Both sides are passed through
    ``os.path.realpath`` so symlink and ``..`` escapes are defeated. An empty
    candidate or root returns ``None`` (fail closed).
    """
    if not candidate or not root:
        return None
    real_root = os.path.realpath(root)
    real_candidate = os.path.realpath(candidate)
    try:
        if (
            os.path.commonpath([real_root, real_candidate]) != real_root
        ):
            return None
    except ValueError:
        # Different drives / mixed absolute-relative — not relative to root.
        return None
    return real_candidate


def confine_path_multi(candidate: str, roots: list[str]) -> str | None:
    """Confine ``candidate`` to ANY of ``roots`` (realpath + descendant check).

    Returns the real path when ``candidate`` resolves inside at least one of the
    allowlisted ``roots``; otherwise ``None``. Used by the admin code/integrity
    endpoints, whose legitimate inputs can live in the repo/workspace OR the
    system temp dir OR the user config/data dirs.
    """
    for root in roots:
        confined = confine_path(candidate, root)
        if confined is not None:
            return confined
    return None


def is_path_within(candidate: str, root: str) -> bool:
    """Return True iff ``candidate`` resolves inside ``root`` (realpath-safe).

    Both paths are resolved via :func:`os.path.realpath` so symlinks and
    ``..`` components cannot escape the jail. Returns ``False`` on any error
    (mixed drives, embedded NULs) — fail closed.
    """
    confined = confine_path(candidate, root)
    return confined is not None


def is_safe_fetch_url(url: str) -> bool:
    """SSRF guard for outbound skill fetches. https-only + literal host deny.

    Returns ``True`` only when the URL is well-formed, uses the ``https`` scheme,
    and its LITERAL host is not a loopback / link-local / RFC-1918 / metadata
    target. Performs NO DNS resolution and NO network I/O.

    Delegates to :func:`general_ludd.security.auth.is_safe_fetch_url`.
    """
    from general_ludd.security.auth import is_safe_fetch_url as _auth_is_safe

    return _auth_is_safe(url)


def workspace_roots(*extra: str | None) -> list[str]:
    """Allowlisted filesystem roots for admin path-confined endpoints.

    Includes the current working directory (the repo/workspace), the system
    temp dir, and any ``extra`` roots (e.g. the daemon's config_dir or the
    integrity default scan dirs) that the caller passes. Empty/None entries are
    dropped. Each is realpath-resolved so the confinement check is symlink-safe.
    """
    import tempfile

    candidates: list[str | None] = [os.getcwd(), tempfile.gettempdir(), *extra]
    roots: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if not c:
            continue
        real = os.path.realpath(c)
        if real not in seen:
            seen.add(real)
            roots.append(real)
    return roots


# Hosts that must never be reachable via an arbitrary-URL fetch (SSRF). The
# RFC-1918 / link-local / loopback ranges are checked numerically below; these
# are the literal names that resolve to them or to cloud metadata services.
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
}


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_fetch_url(url: str) -> str | None:
    """Allowlist an arbitrary outbound fetch URL against SSRF.

    Returns the URL unchanged when it is safe to fetch, else ``None``. Rejects:

    * non-``https`` schemes (no ``http``, ``file``, ``gopher``, ``ftp`` …);
    * a missing hostname;
    * loopback/private/link-local/reserved/multicast literal IPs (v4 and v6),
      including ``169.254.169.254`` (cloud metadata);
    * the literal hostnames ``localhost`` and the GCP metadata host.

    Hostnames that are not IP literals are allowed through (DNS is not resolved
    here); callers MUST also disable redirect following so a public host cannot
    302 into a private one.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").strip()
    if not host:
        return None
    if host.lower() in _BLOCKED_HOSTNAMES:
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP literal — a DNS name. Allow (cannot resolve safely here).
        return url
    if _ip_is_blocked(ip):
        return None
    return url
