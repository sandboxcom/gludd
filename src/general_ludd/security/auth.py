"""Shared auth, path-confinement, and SSRF primitives.

Centralizes the security checks that were previously duplicated (and in places
weaker) across the daemon, the worker, the skill fetcher, and the model/integrity
routers:

  * ``verify_psk``        — constant-time PSK comparison (hmac.compare_digest).
  * ``require_auth_env``  — read the GLUDD_REQUIRE_AUTH opt-in fail-closed flag.
  * ``is_path_within``    — realpath+commonpath jail; refuses absolute paths and
                            ``../`` escapes. Mirrors ExecutionEngine's guard.
  * ``is_safe_fetch_url`` — SSRF guard for remote skill fetches. https-only,
                            and blocks loopback / link-local / RFC-1918 targets
                            by a LITERAL host check (NO DNS resolution, so it is
                            import-safe and can never block on the network).

Hang-safety contract: NOTHING in this module performs blocking I/O — no socket
binds, no DNS lookups, no network calls, no sleeps. ``is_safe_fetch_url`` parses
the URL string and matches the literal host against deny patterns only.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
from urllib.parse import urlsplit


def verify_psk(presented: str, expected: str) -> bool:
    """Constant-time check that ``presented`` matches the configured ``expected``.

    Returns ``False`` for an empty presented token or an empty expected key.
    Uses :func:`hmac.compare_digest` so the comparison does not leak the secret
    via a timing side channel.
    """
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)


def require_auth_env(env: dict[str, str] | None = None) -> bool:
    """Return whether GLUDD_REQUIRE_AUTH requests a fail-closed posture."""
    source = env if env is not None else os.environ
    return source.get("GLUDD_REQUIRE_AUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_path_within(base: str, candidate: str) -> bool:
    """True iff ``candidate`` resolves to a path inside ``base``.

    ``candidate`` is joined onto ``base`` first, so a relative path is taken
    relative to the base while an ABSOLUTE candidate replaces the base entirely
    (the classic escape) — which this function then catches via ``commonpath``.
    Both paths are passed through ``realpath`` so symlink and ``../`` escapes are
    resolved before comparison. Pure string/filesystem-metadata work only; no
    network, no blocking.
    """
    try:
        base_real = os.path.realpath(base)
        full = os.path.realpath(os.path.join(base_real, candidate))
        common = os.path.commonpath([base_real, full])
    except (ValueError, OSError):
        # Mixed drives, embedded NULs, etc. -> treat as not contained.
        return False
    return common == base_real


def _host_is_blocked(host: str) -> bool:
    """LITERAL deny check for a URL host — no DNS, no network.

    Blocks empty hosts, loopback names, the cloud metadata endpoint, and any
    host that is *already* a private / loopback / link-local / reserved IP
    literal. Hostnames that are not IP literals are NOT resolved (that would be
    blocking DNS and is the documented hang risk); they pass the IP checks and
    are only caught by the explicit name blocklist below.
    """
    host = host.strip().lower()
    if not host:
        return True
    # Strip an IPv6 bracket wrapper, e.g. "[::1]" -> "::1".
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    # Explicit name blocklist (cloud metadata + loopback names).
    blocked_names = {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
    if host in blocked_names:
        return True
    # The AWS/GCP/Azure metadata service literal.
    if host in {"169.254.169.254", "100.100.100.200"}:
        return True
    # If the host is an IP literal, reject private / loopback / link-local /
    # reserved / multicast / unspecified ranges (covers RFC-1918, 127/8, ::1,
    # fc00::/7, fe80::/10, etc.). Non-literal hostnames raise ValueError and
    # fall through — we never resolve them.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_safe_fetch_url(url: str) -> bool:
    """SSRF guard for outbound skill fetches. https-only + literal host deny.

    Returns ``True`` only when the URL is well-formed, uses the ``https`` scheme,
    and its LITERAL host is not a loopback / link-local / RFC-1918 / metadata
    target. Performs NO DNS resolution and NO network I/O, so it is safe to call
    on any hot path and can never block.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme.lower() != "https":
        return False
    if not parts.hostname:
        return False
    return not _host_is_blocked(parts.hostname)


# git's "transport helper" prefixes and local-transport schemes that let a clone
# URL run an arbitrary external program or read a local path. ``git clone
# ext::<cmd>`` literally execs ``<cmd>``; ``file://`` and a bare local path read
# the filesystem; ``git::`` invokes the git-remote-git helper. None of these are
# a legitimate "clone a public repo" request, so they are refused outright.
_DANGEROUS_CLONE_PREFIXES = (
    "ext::",
    "git::",
    "fd::",
    "file://",
)
# SSH command-injection markers: a malicious ssh-style clone URL can smuggle
# ``-oProxyCommand=...`` (or any ``-o`` option) so that ``ssh`` runs an arbitrary
# command on connect. We refuse any ssh clone URL carrying one of these.
_SSH_INJECTION_MARKERS = (
    "-o",
    "proxycommand",
)


def is_safe_clone_url(url: str) -> bool:
    """RCE/SSRF guard for ``git clone`` URLs. Reuses :func:`_host_is_blocked`.

    Unlike :func:`is_safe_fetch_url` (https-only) a clone may legitimately use
    plain ``git://`` as well as ``https``/``http`` to a *public* host. This guard
    therefore allows exactly those, and refuses everything dangerous:

      * git transport-helper / local-transport forms (``ext::``, ``git::``,
        ``fd::``, ``file://``) and bare local filesystem paths — these can exec
        an external program or read local files.
      * ``ssh``/``scp``-style URLs that smuggle ``-o`` / ``ProxyCommand`` (ssh
        command injection).
      * ``http(s)`` / ``git`` URLs whose LITERAL host is a loopback /
        link-local / RFC-1918 / cloud-metadata target (SSRF), via the shared
        ``_host_is_blocked`` literal check (NO DNS, never blocks).

    Performs NO DNS resolution and NO network I/O.
    """
    if not url or not isinstance(url, str):
        return False
    stripped = url.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    # 1. Transport-helper / local-transport prefixes are never legitimate.
    for prefix in _DANGEROUS_CLONE_PREFIXES:
        if lowered.startswith(prefix):
            return False
    # 2. A leading '-' would be parsed by git as an option, not a URL.
    if stripped.startswith("-"):
        return False
    try:
        parts = urlsplit(stripped)
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    # 3. scp-style "user@host:path" (no scheme) — treat as ssh. urlsplit gives it
    #    no scheme and no netloc. Allow only when it carries no injection marker
    #    and looks like a real host:path (so a bare local path is NOT clonable).
    if scheme in {"ssh", ""} and not parts.netloc:
        after_at = stripped.split("@", 1)
        if "@" not in stripped or ":" not in after_at[1]:
            # Not an ssh host:path form (e.g. a bare local path) -> refuse.
            return False
        return all(marker not in lowered for marker in _SSH_INJECTION_MARKERS)
    if scheme == "ssh":
        if any(marker in lowered for marker in _SSH_INJECTION_MARKERS):
            return False
        host = parts.hostname
        if not host:
            return False
        return not _host_is_blocked(host)
    # 4. Network transports: only https / http / git, to a non-blocked host.
    if scheme not in {"https", "http", "git"}:
        return False
    if not parts.hostname:
        return False
    return not _host_is_blocked(parts.hostname)
