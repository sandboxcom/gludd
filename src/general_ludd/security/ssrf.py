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
import dataclasses
import ipaddress
import socket
from collections.abc import Collection
from urllib.parse import urlsplit


class SecuritySSRFError(ValueError):
    """Raised by :func:`resolve_and_pin` when a host is blocked by SSRF policy."""


# Compatibility alias retained for the established security API.
SSRFError = SecuritySSRFError


@dataclasses.dataclass(frozen=True)
class PinnedTarget:
    """A DNS-resolved, SSRF-vetted endpoint.

    *host*: the original hostname (for Host header / SNI).
    *ip*: the pinned IP address — connect directly to this.
    *port*: the port number.
    """

    host: str
    ip: str
    port: int

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
    if (
        host == "localhost"
        or host.endswith(".localhost")
        or host in BLOCKED_HOST_NAMES
        or host in BLOCKED_METADATA_IPS
        or _is_single_label_hostname(host)
        or _nonstandard_ip_blocked(host)
    ):
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
    if parts.scheme.lower() not in {s.lower() for s in scheme_allowlist}:
        return True
    host = parts.hostname
    return not host or host_is_blocked(host)


def resolve_and_pin(
    host: str,
    *,
    port: int = 443,
    timeout: float = 2.0,
) -> PinnedTarget:
    """Resolve a hostname and pin its vetted address against DNS rebinding.

    The canonical DNS-resolving SSRF guard. Use this when the caller has
    already accepted DNS-resolution as part of its threat model (e.g. internal
    services like Nomad or Cilium Hubble) AND needs a pinned IP to defeat DNS
    rebinding (connect via the returned IP, set the Host header + SNI to the
    original hostname).

    Steps (fail-closed throughout):
      1. :func:`host_is_blocked` quick literal check (no I/O).
      2. If ``host`` is a literal IP, returns :class:`PinnedTarget` directly
         (already vetted by step 1).
      3. DNS resolution via ``socket.getaddrinfo`` in a worker thread, bounded
         to ``timeout`` seconds.
      4. Every resolved address re-checked through :func:`_ip_addr_is_blocked`.

    Raises :class:`SSRFError` immediately on any block — timeout, NXDOMAIN,
    empty result, private/reserved IP, or the literal check.

    The pinned IP is the FIRST resolved public address. The original ``host``
    is preserved for callers to set the ``Host`` header and TLS SNI.
    """
    if host_is_blocked(host):
        raise SSRFError(f"host {host!r} is blocked by SSRF policy")

    normalized = host.strip().lower().rstrip(".")
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]

    try:
        ip = ipaddress.ip_address(normalized)
        return PinnedTarget(host=host, ip=str(ip), port=port)
    except ValueError:
        pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(socket.getaddrinfo, host, None)
        try:
            infos = future.result(timeout=timeout)
        except (OSError, concurrent.futures.TimeoutError):
            raise SSRFError(
                f"host {host!r} could not be resolved (denied for SSRF)"
            ) from None

    if not infos:
        raise SSRFError(
            f"host {host!r} resolved to no addresses (denied for SSRF)"
        ) from None

    pinned_ip: str | None = None
    for info in infos:
        addr = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise SSRFError(
                f"host {host!r} resolved to unparseable address {addr!r} (denied for SSRF)"
            ) from None
        if _ip_addr_is_blocked(ip):
            raise SSRFError(
                f"host {host!r} resolves to blocked address {addr!r} (denied for SSRF)"
            ) from None
        if pinned_ip is None:
            pinned_ip = str(ip)

    if pinned_ip is None:
        raise SSRFError(
            f"host {host!r} resolved to no usable addresses (denied for SSRF)"
        ) from None

    return PinnedTarget(host=host, ip=pinned_ip, port=port)


def resolved_host_is_blocked(host: str, *, timeout: float = 2.0) -> bool:
    """Convenience wrapper around :func:`resolve_and_pin`: ``True`` if blocked.

    Same DNS-resolving, fail-closed behavior — returns a boolean instead of a
    :class:`PinnedTarget`. For callers that only need the deny decision (Nomad,
    git_automation) and don't need the pinned IP for connection pinning.

    When to use this vs. :func:`resolve_and_pin`: reach for this when you only
    need the SSRF-gate decision; use :func:`resolve_and_pin` when you also need
    the vetted IP to pin the connection and defeat DNS rebinding.
    """
    try:
        resolve_and_pin(host, timeout=timeout)
    except SSRFError:
        return True
    return False
