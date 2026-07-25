"""Security sanitization utilities for path and input validation."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from general_ludd.security.ssrf import _ip_addr_is_blocked, host_is_blocked
from general_ludd.security.ssrf import is_url_blocked as _is_url_blocked

_CREDENTIAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"https?://[^@\s]+:[^@\s]+@", re.IGNORECASE), "https://[REDACTED_CREDS_IN_URL]@"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"x-api-key\s*[:=]\s*\S+", re.IGNORECASE), "[REDACTED_X_API_KEY]"),
    (re.compile(r"(?:api[_-]?key|apikey|api_key)\s*[:=]\s*\S+", re.IGNORECASE), "[REDACTED_API_KEY]"),
    (re.compile(r"(?:Authorization|auth)\s*[:=]\s*Bearer\s+\S+", re.IGNORECASE), "[REDACTED_BEARER_TOKEN]"),
    (re.compile(r"(?:Authorization|auth)\s*[:=]\s*Basic\s+\S+", re.IGNORECASE), "[REDACTED_BASIC_AUTH]"),
    (re.compile(r"(?:token|secret|password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE), "[REDACTED_CREDENTIAL]"),
]

# H.22: redact internal/blocked URLs and hostnames from error messages so SSRF
# rejections never disclose the target address to callers or logs.
_INTERNAL_HOST_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # IPv4 loopback (127.0.0.0/8)
    (re.compile(r"\b127\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[REDACTED_LOOPBACK_IP]"),
    # IPv6 loopback
    (re.compile(r"\[::1\]"), "[REDACTED_LOOPBACK_IP]"),
    # IPv6 loopback uncompressed
    (re.compile(r"\b(::1|0:0:0:0:0:0:0:1)\b"), "[REDACTED_LOOPBACK_IP]"),
    # Cloud metadata IPs
    (re.compile(r"\b169\.254\.169\.254\b"), "[REDACTED_METADATA_IP]"),
    (re.compile(r"\b100\.100\.100\.200\b"), "[REDACTED_METADATA_IP]"),
    # RFC-1918 private ranges
    (re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[REDACTED_PRIVATE_IP]"),
    (re.compile(r"\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"), "[REDACTED_PRIVATE_IP]"),
    (re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), "[REDACTED_PRIVATE_IP]"),
    # Link-local
    (re.compile(r"\b169\.254\.(?!169\.254\b)\d{1,3}\.\d{1,3}\b"), "[REDACTED_LINK_LOCAL_IP]"),
    # Known internal hostnames (SSRF targets)
    (re.compile(r"\blocalhost\.localdomain\b", re.IGNORECASE), "[REDACTED_INTERNAL_HOST]"),
    (re.compile(r"\bmetadata\.google\.internal\b", re.IGNORECASE), "[REDACTED_INTERNAL_HOST]"),
    (re.compile(r"\binstance-data(\.\S+)?\b", re.IGNORECASE), "[REDACTED_INTERNAL_HOST]"),
    # literal localhost (word-boundary to avoid false positives in e.g. "localhostname")
    (re.compile(r"\blocalhost\b", re.IGNORECASE), "[REDACTED_INTERNAL_HOST]"),
    # localhost with port
    (re.compile(r"\blocalhost:\d+\b", re.IGNORECASE), "[REDACTED_INTERNAL_HOST]"),
]


def sanitize_error_message(text: str) -> str:
    """Redact credential-bearing text and internal URLs from exception messages.

    Strips API keys, Bearer tokens, Basic auth tokens, passwords, URLs with
    embedded credentials, OpenAI-style API keys (``sk-...``), and header-style
    API keys from the given string. Also redacts loopback/private/internal
    IP addresses and known SSRF-target hostnames so error messages never
    disclose internal network details.

    Returns the sanitized string with each match replaced by a
    ``[REDACTED_*]`` token.

    Safe to call on any string including empty strings and strings without
    credentials; those pass through unchanged.
    """
    if not text:
        return text
    result = text
    for pattern, replacement in _CREDENTIAL_PATTERNS:
        result = pattern.sub(replacement, result)
    for pattern, replacement in _INTERNAL_HOST_PATTERNS:
        result = pattern.sub(replacement, result)
    return result

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
    descendant of ``root``; otherwise ``None``. A RELATIVE ``candidate`` is
    joined onto ``root`` first (so e.g. a bare ``"name.md"`` is taken relative to
    the confinement root, not the process CWD); an ABSOLUTE candidate is resolved
    as-is and then caught by the descendant check if it escapes. Both sides are
    passed through ``os.path.realpath`` so symlink and ``..`` escapes are
    defeated. An empty candidate or root returns ``None`` (fail closed).
    """
    if not candidate or not root:
        return None
    if "\x00" in candidate or "\x00" in root:
        return None
    real_root = os.path.realpath(root)
    # Join a relative candidate onto the root so a bare basename is confined
    # relative to root rather than the process CWD; os.path.join leaves an
    # absolute candidate unchanged (it replaces real_root), which the descendant
    # check below then rejects if it escapes — the classic absolute-path escape.
    real_candidate = os.path.realpath(os.path.join(real_root, candidate))
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

    Delegates to the canonical :func:`general_ludd.security.ssrf.is_url_blocked`
    with an https-only scheme allowlist — the same primitive
    :func:`general_ludd.security.auth.is_safe_fetch_url` uses, so the two can
    never drift.
    """
    if not url or not isinstance(url, str):
        return False
    return not _is_url_blocked(url, scheme_allowlist={"https"})


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


# Backwards-compatible alias: the canonical numeric SSRF classifier now lives in
# ``general_ludd.security.ssrf``. Kept importable under the historical name so
# any caller of ``sanitize._ip_is_blocked`` keeps working.
_ip_is_blocked = _ip_addr_is_blocked


def validate_fetch_url(url: str) -> str | None:
    """Allowlist an arbitrary outbound fetch URL against SSRF.

    Returns the URL unchanged when it is safe to fetch, else ``None``. Rejects:

    * non-``https`` schemes (no ``http``, ``file``, ``gopher``, ``ftp`` …);
    * a missing hostname;
    * loopback/private/link-local/reserved/multicast literal IPs (v4 and v6),
      including the cloud metadata IPs ``169.254.169.254`` and
      ``100.100.100.200``;
    * the literal metadata/loopback hostnames (``localhost``,
      ``localhost.localdomain``, ``metadata``, ``metadata.google.internal``,
      ``instance-data``).

    The host decision is delegated to the canonical
    :func:`general_ludd.security.ssrf.host_is_blocked`, so this guard now blocks
    the SAME hostile set as every other entrypoint (previously it missed
    ``localhost.localdomain`` / ``instance-data`` / ``100.100.100.200``).

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
    if host_is_blocked(host):
        return None
    return url
