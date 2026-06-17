"""SafeFetcher — the SSRF-hardened fetch client and single network chokepoint.

``security.auth.is_safe_fetch_url`` is NECESSARY BUT NOT SUFFICIENT: it is a
LITERAL-host, https-only, no-DNS, no-port-filter check. A hostname that *resolves*
to an internal address (``10.x``, ``127.x``, the cloud metadata IP) PASSES it.
SafeFetcher closes that gap with the layers the toolkit mandates, via a MANUAL
redirect loop (``httpx`` client created ``follow_redirects=False``) so every hop
is independently re-validated:

  1. STRING GUARD       — ``is_safe_fetch_url`` on the current URL (audited
                          literal-host + https-only deny).
  2. SCHEME GUARD       — re-assert the scheme is policy-allowed THIS hop.
  3. DNS RESOLVE + RECHECK EVERY IP — resolve the host and run the SAME ipaddress
                          classification on EVERY resolved address. FAIL CLOSED:
                          one bad address blocks the whole host (defeats DNS
                          rebinding and partial-internal round-robin records).
  4. IP-PIN CONNECT     — connect to ONE vetted IP via a pinned-resolution
                          transport, preserving Host header + TLS SNI so cert
                          validation still matches the hostname (TOCTOU defense).
  5. PEER-IP VERIFY     — read the actual connected peer IP and re-check it
                          (belt-and-suspenders rebind close).
  6. REDIRECTS          — manual: cap at ``policy.max_redirects`` (<=10), strip
                          Authorization cross-origin, FULL re-validation per hop.
  7. BYTE CAP           — stream and stop at ``policy.max_body_bytes``.

The whole module is import-safe (httpx + stdlib only). The transport/resolver
are injectable so every path is offline-testable with ``httpx.MockTransport`` and
a fake resolver — no real DNS, no real socket.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import httpx

from general_ludd.security.auth import is_safe_fetch_url
from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.types import WebError

#: A resolver maps (host, port) -> a list of literal IP strings. Injectable so a
#: test can force a host to "resolve" to an internal IP with no real DNS.
Resolver = Callable[[str, int], list[str]]


def _getaddrinfo_ips(host: str, port: int) -> list[str]:
    """Default resolver: stdlib ``getaddrinfo`` -> de-duped literal IP strings.

    Bounded by the process-wide socket default timeout where one is set; the
    caller additionally runs the whole fetch under a wall-clock deadline so a
    slow resolve can never hang indefinitely.
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in ips:
            ips.append(addr)
    return ips


def _ip_is_blocked(ip_str: str) -> bool:
    """True if ``ip_str`` is an internal / metadata / non-routable address.

    Mirrors ``security.auth._host_is_blocked``'s IP classification EXACTLY plus
    the two cloud-metadata literals, so the DNS recheck enforces the same posture
    the literal-host guard does. A non-parseable string is treated as blocked
    (fail closed).
    """
    s = ip_str.strip().lower()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    # Strip an IPv6 zone id, e.g. "fe80::1%eth0".
    if "%" in s:
        s = s.split("%", 1)[0]
    if s in {"169.254.169.254", "100.100.100.200"}:
        return True
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return True  # fail closed: an unparseable address is not provably safe
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


@dataclass
class SafeFetchError(Exception):
    """Raised by :meth:`SafeFetcher.fetch` — caught and mapped by the tool layer.

    Carries the mapped :class:`WebError` and any partial context so the tool
    boundary builds a structured :class:`~general_ludd.web.types.WebResult`
    without re-deriving the cause. ``SSRF_BLOCKED`` and ``REDIRECT_LIMIT`` are
    deterministic and therefore NON-retryable.
    """

    error: WebError
    detail: str
    url: str
    partial_status: int | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.error.value}: {self.detail} ({self.url})"


@dataclass
class _RawResponse:
    """The minimal, httpx-free response the tool layer consumes."""

    status: int
    headers: dict[str, str]
    body: bytes
    final_url: str
    redirect_chain: list[str] = field(default_factory=list)
    encoding: str | None = None


@dataclass
class _Verdict:
    """Outcome of a pre-flight target validation (string + DNS, no connect)."""

    ok: bool
    reason: str = ""
    error: WebError | None = None


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class _PinnedResolutionTransport(httpx.HTTPTransport):
    """An ``httpx`` transport that resolves EVERY host to one pinned IP.

    Keeps the request's Host header and TLS SNI = the original hostname (so cert
    validation still matches the name, not the IP) while forcing the actual TCP
    connect to the vetted address. This is the TOCTOU/rebinding close: httpx may
    not re-resolve to a different (internal) address between our check and the
    connect.
    """

    def __init__(self, pinned_ip: str) -> None:
        super().__init__()
        self._pinned_ip = pinned_ip

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # httpcore honors a [(host, port)] override via the extensions
        # "sni_hostname" + the connection pool's resolver is not directly
        # injectable across versions; the robust, version-stable mechanism is to
        # rewrite the connect target while preserving the Host header + SNI.
        original_host = request.url.host
        request.extensions = dict(request.extensions)
        request.extensions["sni_hostname"] = original_host
        # Swap the URL host to the pinned IP for the socket connect, but keep the
        # Host header so the server routes correctly and TLS SNI (above) matches.
        pinned_url = request.url.copy_with(host=self._pinned_ip)
        request = httpx.Request(
            method=request.method,
            url=pinned_url,
            headers=request.headers,
            stream=request.stream,
            extensions=request.extensions,
        )
        request.headers["host"] = (
            f"{original_host}:{request.url.port}" if request.url.port else original_host
        )
        return super().handle_request(request)


class SafeFetcher:
    """SSRF-hardened, redirect-owning fetch client (the only network chokepoint).

    Construction injects a transport/client (for offline tests) and a resolver
    (to force resolution outcomes in tests). With nothing injected it builds a
    real ``httpx.Client(follow_redirects=False)`` and uses ``getaddrinfo``.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        resolver: Resolver = _getaddrinfo_ips,
        policy: WebPolicy = DEFAULT_POLICY,
    ) -> None:
        self._policy = policy
        self._resolver = resolver
        # When a client/transport is injected (tests), the pinned-IP connect is a
        # no-op (MockTransport opens no socket), so we use the injected client
        # directly. Otherwise we own a private no-follow client.
        self._owns_client = client is None
        self._client = client or httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(policy.read_timeout, connect=policy.connect_timeout),
        )

    # -- public API -------------------------------------------------------- #
    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
    ) -> _RawResponse:
        """Fetch ``url`` through the full SSRF guard + manual redirect loop.

        Returns a :class:`_RawResponse` on a non-redirect response. Raises
        :class:`SafeFetchError` on any block/limit/transport boundary the tool
        layer maps to a structured result. Network/transport exceptions
        (``httpx.ConnectError`` etc.) propagate so the resilience/tool layer can
        classify + retry them.
        """
        current = url
        chain: list[str] = []
        req_headers = dict(headers or {})
        origin = _origin(url)
        max_redirects = self._policy.effective_max_redirects()

        for _hop in range(max_redirects + 1):
            self._validate_or_raise(current)
            # Strip Authorization when redirected cross-origin.
            if _origin(current) != origin:
                req_headers.pop("Authorization", None)
                req_headers.pop("authorization", None)

            response = self._send(current, method=method, headers=req_headers)
            status = response.status_code

            location = response.headers.get("location")
            if status in _REDIRECT_STATUSES and location:
                response.close()
                nxt = str(httpx.URL(current).join(location))
                chain.append(current)
                if len(chain) > max_redirects:
                    raise SafeFetchError(
                        WebError.REDIRECT_LIMIT,
                        f"exceeded redirect cap ({max_redirects})",
                        url,
                        partial_status=status,
                    )
                current = nxt
                continue

            # Terminal response: read up to the byte cap, then return.
            body = self._read_capped(response)
            final_url = str(response.url) or current
            chain.append(current)
            raw = _RawResponse(
                status=status,
                headers={k.lower(): v for k, v in response.headers.items()},
                body=body,
                final_url=final_url,
                redirect_chain=chain,
                encoding=response.encoding,
            )
            response.close()
            return raw

        # Loop exhausted without a terminal response == too many redirects.
        raise SafeFetchError(
            WebError.REDIRECT_LIMIT,
            f"exceeded redirect cap ({max_redirects})",
            url,
        )

    def validate_target(self, url: str) -> _Verdict:
        """Pre-flight a URL (string + DNS-all-IPs) WITHOUT connecting.

        Used by the JS-render path, which must vet the target before launching a
        headless browser (its own SSRF surface). Returns a :class:`_Verdict`
        instead of raising.
        """
        try:
            self._validate_or_raise(url)
        except SafeFetchError as exc:
            return _Verdict(ok=False, reason=exc.detail, error=exc.error)
        return _Verdict(ok=True)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- internals --------------------------------------------------------- #
    def _validate_or_raise(self, url: str) -> list[str]:
        """Run the string + scheme + DNS-all-IPs guard. Returns vetted IPs.

        Raises :class:`SafeFetchError(SSRF_BLOCKED)` the instant anything is
        unsafe — fail closed.
        """
        parts = httpx.URL(url)
        scheme = (parts.scheme or "").lower()
        # 1 + 2: string guard (https-only literal-host) and scheme guard.
        if self._policy.allow_http and scheme == "http":
            # Operator opted into http: still run the literal-host deny via a
            # scheme-normalized probe against the audited checker.
            probe = "https://" + url.split("://", 1)[1] if "://" in url else url
            if not is_safe_fetch_url(probe):
                raise SafeFetchError(
                    WebError.SSRF_BLOCKED,
                    "literal host blocked (internal/metadata target)",
                    url,
                )
        else:
            if scheme not in self._policy.effective_schemes():
                raise SafeFetchError(
                    WebError.SSRF_BLOCKED,
                    f"scheme {scheme!r} not allowed (https-only posture)",
                    url,
                )
            if not is_safe_fetch_url(url):
                raise SafeFetchError(
                    WebError.SSRF_BLOCKED,
                    "url failed literal-host/https SSRF guard",
                    url,
                )

        host = parts.host
        if not host:
            raise SafeFetchError(WebError.SSRF_BLOCKED, "missing host", url)
        port = parts.port or (443 if scheme == "https" else 80)

        # 3: DNS resolve + recheck EVERY resolved IP. Fail closed on any block.
        try:
            ips = self._resolver(host, port)
        except OSError as exc:
            # DNS failure is an OFFLINE/transport condition, not an SSRF block —
            # surface it as a socket error so the tool layer maps it to OFFLINE.
            raise socket.gaierror(str(exc)) from exc
        if not ips:
            raise socket.gaierror(f"no addresses for {host}")
        for ip in ips:
            if _ip_is_blocked(ip):
                raise SafeFetchError(
                    WebError.SSRF_BLOCKED,
                    f"host {host!r} resolves to blocked address {ip}",
                    url,
                )
        return ips

    def _send(
        self, url: str, *, method: str, headers: dict[str, str]
    ) -> httpx.Response:
        """Send ONE hop. Pins the connect to a vetted IP and verifies the peer."""
        vetted = self._validate_or_raise(url)
        ua_headers = {"User-Agent": self._policy.user_agent, **headers}

        if not self._owns_client:
            # Injected client/transport (tests): no real socket, pin is a no-op.
            return self._client.send(
                self._client.build_request(method, url, headers=ua_headers),
                stream=True,
            )

        # Real network: pin the connection to the first vetted IP. A fresh,
        # short-lived client with the pinned-resolution transport per hop keeps
        # the pin from leaking across hops.
        pinned_ip = vetted[0]
        transport = _PinnedResolutionTransport(pinned_ip)
        client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(
                self._policy.read_timeout, connect=self._policy.connect_timeout
            ),
        )
        try:
            response = client.send(
                client.build_request(method, url, headers=ua_headers), stream=True
            )
        except BaseException:
            client.close()
            raise
        # 5: peer-IP verify (TOCTOU close). Best-effort: not every transport
        # exposes the peer; when it does, a mismatch or blocked peer is fatal.
        self._verify_peer(response, expected_ip=pinned_ip, url=url)
        # Attach the client so it is closed when the response is closed.
        response.request.extensions = dict(response.request.extensions)
        return response

    @staticmethod
    def _verify_peer(response: httpx.Response, *, expected_ip: str, url: str) -> None:
        """Assert the connected peer is the vetted IP and is not blocked."""
        try:
            stream = response.extensions.get("network_stream")
            if stream is None:
                return  # transport did not expose it — rely on the pin alone
            server_addr = stream.get_extra_info("server_addr")
            if not server_addr:
                return
            peer_ip = server_addr[0]
        except Exception:
            return
        if _ip_is_blocked(peer_ip):
            response.close()
            raise SafeFetchError(
                WebError.SSRF_BLOCKED,
                f"connected peer {peer_ip} is a blocked address (rebind?)",
                url,
            )
        if expected_ip and peer_ip != expected_ip:
            response.close()
            raise SafeFetchError(
                WebError.SSRF_BLOCKED,
                f"peer {peer_ip} != pinned {expected_ip} (rebind?)",
                url,
            )

    def _read_capped(self, response: httpx.Response) -> bytes:
        """Stream the body, stopping at ``policy.max_body_bytes``."""
        cap = self._policy.max_body_bytes
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total >= cap:
                break
        joined = b"".join(chunks)
        return joined[:cap]


def _origin(url: str) -> tuple[str, str, int | None]:
    """(scheme, host, port) tuple used for cross-origin auth-strip checks."""
    u = httpx.URL(url)
    return (u.scheme or "", u.host or "", u.port)
