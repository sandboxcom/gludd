"""Canonical SSRF host/IP deny predicate — the SINGLE source of truth.

Every SSRF guard in the codebase (the skill fetcher's ``is_safe_fetch_url``, the
sanitizer's ``validate_fetch_url``, and the connector layer's
``is_safe_endpoint``) funnels its literal-host decision through the two
primitives here so the blocklists can never drift apart again:

  * :func:`_ip_addr_is_blocked` — classify an *already-parsed* IP literal as
    deny if it is private / loopback / link-local / reserved / multicast /
    unspecified (covers RFC-1918, 127/8, ::1, fc00::/7, fe80::/10, 169.254/16,
    etc.).
  * :func:`host_is_blocked` — the STRICTEST union of every blocklist that used
    to live in ``auth.py`` / ``sanitize.py`` / ``connectors/base.py``: empty
    hosts, the loopback names, the AWS/GCP/Azure/Alibaba metadata names AND
    their literal metadata IPs, plus any host that is *already* a blocked IP
    literal. An optional ``scheme_allowlist`` lets a caller fold the scheme
    decision into the same call where that is convenient.

Hang-safety contract: NOTHING here performs blocking I/O — no socket binds, no
DNS lookups, no network calls, no sleeps. Hostnames that are not IP literals are
NEVER resolved (that would be blocking DNS and the documented hang risk); they
are only caught by the explicit name blocklist below.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Collection
from urllib.parse import urlsplit

# --------------------------------------------------------------------------- #
# Canonical blocklists — the STRICTEST union of every former implementation.
# --------------------------------------------------------------------------- #
# Loopback / cloud-internal metadata host NAMES (no IP). Union of the
# auth.py blocked_names, sanitize.py _BLOCKED_HOSTNAMES, and connectors/base.py
# _BLOCKED_HOST_NAMES sets — so no call site can be weaker than the strictest.
BLOCKED_HOST_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",  # GCP metadata alias — was in per-connector blocklists
        "instance-data",
        "ip6-localhost",  # IPv6 loopback alias — falls through ip_address() parse
        "ip6-loopback",  # IPv6 loopback alias — falls through ip_address() parse
    }
)

# Cloud metadata service IP literals that callers must always deny by name even
# though the numeric IP checks below ALSO catch them (169.254.0.0/16 is
# link-local; 100.100.100.200 is Alibaba's metadata endpoint inside the carrier
# -grade-NAT 100.64.0.0/10 reserved range). Kept explicit for clarity/defense.
BLOCKED_METADATA_IPS = frozenset({"169.254.169.254", "100.100.100.200"})

# Default scheme allowlist when a caller folds scheme policy into host_is_blocked
# / is_url_blocked. Individual call sites override (auth = https-only).
DEFAULT_SCHEME_ALLOWLIST = frozenset({"http", "https"})


def _ip_addr_is_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """True iff an *already-parsed* IP literal is in a denied range.

    The full flag set: private / loopback / link-local / reserved / multicast /
    unspecified. This is the one and only numeric SSRF classifier; every guard
    delegates here so the flag set cannot drift.
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global  # catches TEST-NET/documentation ranges (192.0.2/24 etc.)
    )


def host_is_blocked(host: str) -> bool:
    """LITERAL deny check for a URL host — no DNS, no network.

    Returns ``True`` (deny) for an empty host, a loopback/metadata name in
    :data:`BLOCKED_HOST_NAMES`, a literal metadata IP in
    :data:`BLOCKED_METADATA_IPS`, or any host that is *already* a blocked IP
    literal (per :func:`_ip_addr_is_blocked`). Non-IP-literal hostnames are NOT
    resolved — they pass the IP checks and are caught only by the explicit name
    blocklist. Pure string work; never blocks.
    """
    if not host:
        return True
    host = host.strip().lower()
    if not host:
        return True
    # A NUL byte can truncate the host for a downstream resolver/HTTP client
    # ("localhost\x00.evil.com" may be read as "localhost"), so a NUL-bearing
    # host could smuggle a blocked target past the string blocklist. Deny it.
    if "\x00" in host:
        return True
    # Strip trailing FQDN dot(s) FIRST, before the IPv6 bracket unwrap. Order
    # matters: a bracketed host with a trailing dot ("[::1].") does NOT end in
    # "]", so the bracket check would be skipped and the dot-strip would leave
    # "[::1" — an unparseable host that slips past the IP blocklist. rstrip(".")
    # also removes MULTIPLE dots ("127.0.0.1.." -> "127.0.0.1") which a single
    # slice would miss. "localhost." / "127.0.0.1." resolve identically to the
    # undotted form, so without this the name/IP blocklists could be dot-bypassed.
    host = host.rstrip(".")
    if not host:
        return True
    # Strip an IPv6 bracket wrapper, e.g. "[::1]" -> "::1".
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    # RFC 6761 reserves the entire ".localhost" TLD for loopback, so ANY
    # subdomain (e.g. "foo.localhost", "api.svc.localhost") resolves to loopback
    # and must be denied, not just the bare "localhost" name in
    # BLOCKED_HOST_NAMES. This restores the ".localhost" suffix coverage the
    # prom_scrape connector had before it delegated to this canonical guard.
    if host == "localhost" or host.endswith(".localhost"):
        return True
    if host in BLOCKED_HOST_NAMES:
        return True
    if host in BLOCKED_METADATA_IPS:
        return True
    # If the host is an IP literal, classify it; a non-literal hostname raises
    # ValueError and falls through (we never resolve it).
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return _ip_addr_is_blocked(ip)


def is_url_blocked(
    url: str,
    scheme_allowlist: Collection[str] = DEFAULT_SCHEME_ALLOWLIST,
) -> bool:
    """True iff ``url`` should be denied for SSRF reasons.

    Denies a malformed URL, a scheme outside ``scheme_allowlist`` (compared
    case-insensitively), a missing host, or a host that :func:`host_is_blocked`
    rejects. This is the scheme-aware wrapper the public guards delegate to so
    the scheme policy and the host policy share one implementation; each caller
    passes its own ``scheme_allowlist`` (auth = ``{"https"}``, connectors =
    ``{"http", "https"}``). Performs NO DNS resolution and NO network I/O.
    """
    if not url or not isinstance(url, str):
        return True
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    allowed = {s.lower() for s in scheme_allowlist}
    if parts.scheme.lower() not in allowed:
        return True
    host = parts.hostname
    if not host:
        return True
    return host_is_blocked(host)
