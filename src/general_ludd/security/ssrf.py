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
  * :func:`resolved_host_is_blocked` — an explicit, OPT-IN exception to the
    hang-safety contract below: a bounded (default 2s) DNS resolution of a
    non-literal hostname, with every resolved address re-checked through
    :func:`_ip_addr_is_blocked`. It exists for the small set of connectors
    that already accepted DNS-resolution risk before this module existed
    (Nomad, Cilium Hubble) — see its docstring for exactly when to reach for
    it instead of the no-DNS :func:`host_is_blocked`.

Hang-safety contract: NOTHING in this module performs blocking I/O EXCEPT the
one explicitly-named, opt-in :func:`resolved_host_is_blocked` — no socket
binds, no DNS lookups, no network calls, no sleeps anywhere else. Hostnames
that are not IP literals are NEVER resolved by :func:`host_is_blocked` /
:func:`is_url_blocked` (that would be blocking DNS and the documented hang
risk); they are only caught by the explicit name blocklist below.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import socket
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
        "metadata.azure.com",
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


_SINGLE_LABEL_BLOCKED: frozenset[str] = frozenset()


def _is_single_label_hostname(host: str) -> bool:
    """True iff ``host`` is a single-label (dot-less, non-IP) hostname.

    Single-label hostnames cannot be public FQDNs — they always resolve within
    the local network or the search domain, so allowing them is an SSRF risk.
    IP literals (both IPv4 and IPv6) are excluded by the ``:`` or ``.`` in
    their string form; this function only fires for names like ``vault``,
    ``grafana``, ``prometheus``.
    """
    if "." in host or ":" in host:
        return False
    if host in _SINGLE_LABEL_BLOCKED:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return True


def _nonstandard_ip_blocked(host: str) -> bool:
    """True if host, as a non-standard IP literal encoding, maps to a blocked IP.

    Handles three SSRF bypass encodings that ``ipaddress.ip_address`` rejects
    but many HTTP clients (curl, wget, Go net/http, browsers) accept:

    - Decimal integer IPv4: ``2130706433`` \u2192 ``127.0.0.1``
    - Octal dotted-quad:    ``0177.0.0.1`` \u2192 ``127.0.0.1``
    - Hex dotted-quad:      ``0x7f.0.0.1`` \u2192 ``127.0.0.1``
    - Mixed encodings:      ``0177.0x1f.0.1`` \u2192 ``127.31.0.1``

    For dotted-quad forms with non-standard octets, BOTH curl-style (leading
    ``0`` = octal, ``0x`` = hex) and decimal-style (ignore leading zeros)
    interpretations are checked; if EITHER yields a blocked IP the host is
    denied.
    """
    # -- decimal integer IPv4: "2130706433" → 127.0.0.1 -----------------------
    if host.isdigit() and len(host) <= 10:
        ip_int = int(host)
        if ip_int < (1 << 32):
            try:
                if _ip_addr_is_blocked(ipaddress.IPv4Address(ip_int)):
                    return True
            except ipaddress.AddressValueError:
                pass

    # -- octal / hex / mixed dotted-quad --------------------------------------
    if "." in host:
        parts = host.split(".")
        if len(parts) == 4 and all(parts):
            # Curl-style: leading 0x=hex, leading 0=octal, else decimal.
            try:
                octets: list[int] = []
                for p in parts:
                    if p.startswith(("0x", "0X")):
                        octets.append(int(p, 16))
                    elif p.startswith("0") and len(p) > 1 and set(p) <= set("01234567"):
                        octets.append(int(p, 8))
                    else:
                        octets.append(int(p, 10))
                    if not (0 <= octets[-1] <= 255):
                        raise ValueError
                if _ip_addr_is_blocked(ipaddress.IPv4Address(bytes(octets))):
                    return True
            except (ValueError, OverflowError):
                pass

            # Decimal-style: all octets as decimal (python requests / modern go).
            try:
                octets_dec = [int(p, 10) for p in parts]
                if all(0 <= o <= 255 for o in octets_dec) and _ip_addr_is_blocked(
                    ipaddress.IPv4Address(bytes(octets_dec))
                ):
                    return True
            except (ValueError, OverflowError):
                pass

    return False


def host_is_blocked(host: str) -> bool:
    """LITERAL deny check for a URL host — no DNS, no network.

    Returns ``True`` (deny) for an empty host, a loopback/metadata name in
    :data:`BLOCKED_HOST_NAMES`, a literal metadata IP in
    :data:`BLOCKED_METADATA_IPS`, any single-label (dot-less, non-IP) hostname,
    or any host that is *already* a blocked IP literal (per
    :func:`_ip_addr_is_blocked`). Non-IP-literal multi-label hostnames are NOT
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
    if _is_single_label_hostname(host):
        return True
    if _nonstandard_ip_blocked(host):
        return True
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


def resolved_host_is_blocked(host: str, *, timeout: float = 2.0) -> bool:
    """DNS-RESOLVING deny check — OPT-IN, bounded, for connectors that already
    resolve names.

    Unlike :func:`host_is_blocked` (pure literal check, never touches the
    network), this function:

      1. Applies :func:`host_is_blocked` first (covers a literal IP, a
         loopback/metadata name, and the ``.localhost`` TLD with no I/O).
      2. If ``host`` is not an IP literal and was not already denied, resolves
         it via ``socket.getaddrinfo`` bounded to ``timeout`` seconds (run in a
         worker thread so a hung resolver cannot block the caller past the
         deadline) and re-checks EVERY returned address through
         :func:`_ip_addr_is_blocked`.
      3. FAILS CLOSED: a resolution timeout, ``OSError`` (e.g. NXDOMAIN), or an
         empty result set is treated as a deny — an unresolvable host must
         never be silently waved through (that could mask an internal or
         DNS-rebinding target).

    When to use this vs. :func:`host_is_blocked`: reach for this ONLY when the
    connector's operator has already accepted DNS-resolution as part of that
    connector's threat model (e.g. an internal service, such as a Nomad agent
    or a Cilium Hubble relay, that is legitimately reached by an internal DNS
    name rather than a literal IP). Do NOT use this in a shared/general fetch
    path — the whole point of :func:`host_is_blocked` / :func:`is_url_blocked`
    is that they can NEVER hang on a slow or hostile resolver; this function
    can still take up to ``timeout`` seconds per call.
    """
    if host_is_blocked(host):
        return True

    normalized = host.strip().lower().rstrip(".")
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        ipaddress.ip_address(normalized)
        return False  # literal IP: host_is_blocked above already vetted it.
    except ValueError:
        pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(socket.getaddrinfo, host, None)
        try:
            infos = future.result(timeout=timeout)
        except (OSError, concurrent.futures.TimeoutError):
            # Fail closed: unresolvable (or too slow to resolve) -> deny. The
            # background thread may still be blocked on the syscall; letting
            # it finish in the background (rather than trying to kill it) is
            # the standard way to bound an unkillable blocking call.
            return True

    if not infos:
        return True
    for info in infos:
        addr = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return True
        if _ip_addr_is_blocked(ip):
            return True
    return False
