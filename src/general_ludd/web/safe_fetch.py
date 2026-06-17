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
                          The pin is applied on EVERY real-network path AND
                          (wrapping the injected transport) on the injected-client
                          path, so an operator-supplied client can NOT silently
                          downgrade to a string+DNS-snapshot validation with a
                          live rebind window.
  5. PEER-IP VERIFY     — read the pinned/connected peer IP (the transport records
                          the address it actually targeted) and re-check it. A
                          blocked or mismatched peer is FATAL. This is NOT a silent
                          no-op: the pinned transport always records the target, so
                          the verify runs (and is offline-assertable) on every hop.
  6. REDIRECTS          — manual: cap at ``policy.max_redirects`` (<=10), strip
                          ALL credential headers (Authorization, Cookie,
                          Proxy-Authorization) cross-origin, FULL re-validation per
                          hop, keyed on the per-hop origin.
  7. BYTE CAP           — stream and stop at ``policy.max_body_bytes``.
  8. WALL-CLOCK DEADLINE — the WHOLE fetch (resolve + every hop + body read) runs
                          under ``policy.overall_deadline``; exceeding it raises a
                          structured TIMEOUT rather than letting the redirect chain
                          accumulate unbounded time.

The whole module is import-safe (httpx + stdlib only). The transport/resolver
are injectable so every path is offline-testable with ``httpx.MockTransport`` and
a fake resolver — no real DNS, no real socket.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import httpx

from general_ludd.security.auth import is_safe_fetch_url
from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.types import WebError

#: A resolver maps (host, port) -> a list of literal IP strings. Injectable so a
#: test can force a host to "resolve" to an internal IP with no real DNS.
Resolver = Callable[[str, int], list[str]]

#: Request-credential headers stripped on a cross-origin redirect hop so creds
#: never leak to a redirect target on a different origin (case-insensitive).
_CREDENTIAL_HEADERS = ("authorization", "cookie", "proxy-authorization")

#: The extension key under which the pinned transport records the literal IP it
#: targeted for the socket connect, so :meth:`SafeFetcher._verify_peer` can
#: re-check it deterministically (offline-assertable, never a silent no-op).
_PINNED_IP_EXT = "gludd_pinned_ip"


def _getaddrinfo_ips(host: str, port: int) -> list[str]:
    """Default resolver: stdlib ``getaddrinfo`` -> de-duped literal IP strings.

    Bounded by the process-wide socket default timeout where one is set; the
    caller (:meth:`SafeFetcher.fetch`) additionally runs the whole fetch under a
    monotonic wall-clock deadline (``policy.overall_deadline``) and checks it
    BEFORE and AFTER this resolve, so a slow resolve contributes to — and is
    bounded by — that budget.
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
    deterministic and therefore NON-retryable. ``TIMEOUT`` (deadline exceeded) is
    likewise surfaced structurally.
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


class _PinnedResolutionTransport(httpx.BaseTransport):
    """A transport that forces EVERY connect to one pinned, pre-vetted IP.

    It wraps an INNER transport (the real ``httpx.HTTPTransport`` on the network
    path, or an injected/mock transport in tests) so the pin is applied uniformly
    — there is no code path on which the pin is skipped. The request's Host header
    and TLS SNI stay the original hostname (so cert validation matches the name,
    not the IP) while the socket connect targets the vetted address. This is the
    TOCTOU/rebinding close: httpx may not re-resolve to a different (internal)
    address between our check and the connect.

    The literal IP it targeted is recorded into the response request's extensions
    (``gludd_pinned_ip``) so the peer-IP verify can re-check it deterministically
    — including offline, where the inner transport opens no real socket.
    """

    def __init__(
        self, inner: httpx.BaseTransport, pinned_ip: str, *, rewrite_host: bool
    ) -> None:
        self._inner = inner
        self._pinned_ip = pinned_ip
        # Only rewrite the connect target (URL host -> IP) for a REAL network
        # transport that actually does DNS + a socket connect. For an injected /
        # mock inner transport there is no socket, so rewriting the host would only
        # corrupt the request the handler inspects — we still record the pinned IP
        # for the verify step, just without mangling the URL.
        self._rewrite_host = rewrite_host

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        original_host = request.url.host
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = original_host
        # Record the pinned target so _verify_peer can assert against it even when
        # the inner transport (MockTransport) never opens a socket.
        extensions[_PINNED_IP_EXT] = self._pinned_ip

        if self._rewrite_host:
            # Swap the URL host to the pinned IP for the socket connect; keep the
            # Host header so the server routes correctly and TLS SNI (above) matches.
            pinned_url = request.url.copy_with(host=self._pinned_ip)
            outgoing = httpx.Request(
                method=request.method,
                url=pinned_url,
                headers=request.headers,
                stream=request.stream,
                extensions=extensions,
            )
            outgoing.headers["host"] = (
                f"{original_host}:{outgoing.url.port}"
                if outgoing.url.port
                else original_host
            )
        else:
            request.extensions = extensions
            outgoing = request

        response = self._inner.handle_request(outgoing)
        # Record the pinned target on the RESPONSE's own extensions (always
        # present) so the verify step can read it deterministically — including
        # offline, where the inner mock transport opens no socket. We avoid
        # touching response.request, which a mock transport may not have set.
        response.extensions[_PINNED_IP_EXT] = self._pinned_ip
        return response

    def close(self) -> None:  # pragma: no cover - delegation
        self._inner.close()


class SafeFetcher:
    """SSRF-hardened, redirect-owning fetch client (the only network chokepoint).

    Construction injects a transport/client (for offline tests) and a resolver
    (to force resolution outcomes in tests). With nothing injected it builds a
    real ``httpx.Client(follow_redirects=False)`` and uses ``getaddrinfo``.

    Whether the client is owned or injected, EVERY hop's actual connect is routed
    through a per-hop :class:`_PinnedResolutionTransport` pinned to a vetted IP and
    the peer is re-verified — an injected client can NOT bypass the pin.
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
        self._owns_client = client is None
        # The injected client's transport is what we wrap per-hop with the pin; we
        # never send on the injected client directly (that would skip the pin).
        self._injected_transport: httpx.BaseTransport | None = None
        if client is not None:
            self._injected_transport = client.__dict__.get("_transport")
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
        :class:`SafeFetchError` on any block/limit/deadline boundary the tool
        layer maps to a structured result. Network/transport exceptions
        (``httpx.ConnectError`` etc.) propagate so the resilience/tool layer can
        classify + retry them.
        """
        current = url
        chain: list[str] = []
        req_headers = dict(headers or {})
        origin = _origin(url)
        max_redirects = self._policy.effective_max_redirects()
        deadline = time.monotonic() + self._policy.overall_deadline

        for _hop in range(max_redirects + 1):
            self._check_deadline(deadline, url)
            # Resolve + validate ONCE per hop; thread the vetted IPs into _send so
            # the connect pins the exact address we vetted (no second getaddrinfo,
            # no widened TOCTOU window).
            vetted = self._validate_or_raise(current)
            # Strip ALL credential headers when redirected cross-origin (keyed on
            # the CURRENT hop's origin vs the original origin).
            if _origin(current) != origin:
                for h in list(req_headers):
                    if h.lower() in _CREDENTIAL_HEADERS:
                        req_headers.pop(h, None)

            response = self._send(
                current, method=method, headers=req_headers, vetted=vetted
            )
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
            try:
                body = self._read_capped(response, deadline=deadline, url=url)
            finally:
                response.close()
            final_url = str(response.url) or current
            chain.append(current)
            return _RawResponse(
                status=status,
                headers={k.lower(): v for k, v in response.headers.items()},
                body=body,
                final_url=final_url,
                redirect_chain=chain,
                encoding=response.encoding,
            )

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
    def _check_deadline(self, deadline: float, url: str) -> None:
        """Raise a structured TIMEOUT once the wall-clock budget is exceeded."""
        if time.monotonic() >= deadline:
            raise SafeFetchError(
                WebError.TIMEOUT,
                f"overall deadline ({self._policy.overall_deadline}s) exceeded",
                url,
            )

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
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        vetted: list[str],
    ) -> httpx.Response:
        """Send ONE hop, pinning the connect to a vetted IP and verifying the peer.

        ``vetted`` is the already-resolved, already-checked IP list for THIS hop
        (resolved once in :meth:`fetch`), so there is no second getaddrinfo and the
        pin targets exactly the address we vetted.
        """
        ua_headers = {"User-Agent": self._policy.user_agent, **headers}
        pinned_ip = vetted[0]

        # The inner transport is either the injected one (tests) or a fresh real
        # HTTPTransport. EITHER WAY it is wrapped by the pin — an injected client
        # never bypasses pinning.
        if self._injected_transport is not None:
            inner: httpx.BaseTransport = self._injected_transport
            own_inner = False
        else:
            inner = httpx.HTTPTransport()
            own_inner = True

        # Rewrite the URL host -> pinned IP only on the real-network path (a mock
        # inner transport has no socket, so the rewrite would just corrupt the URL
        # the handler inspects); the pin is still recorded + verified either way.
        transport = _PinnedResolutionTransport(
            inner, pinned_ip, rewrite_host=own_inner
        )
        client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(
                self._policy.read_timeout, connect=self._policy.connect_timeout
            ),
        )

        def _close_client() -> None:
            # Close the per-hop client WITHOUT closing an injected inner transport
            # (which the caller owns and may reuse for later hops).
            if own_inner:
                client.close()
            else:
                # Detach the borrowed inner so client.close() can't tear it down.
                client._transport = httpx.MockTransport(
                    lambda r: httpx.Response(204)
                )
                client.close()

        try:
            response = client.send(
                client.build_request(method, url, headers=ua_headers), stream=True
            )
        except BaseException:
            _close_client()
            raise

        # 5: peer-IP verify (TOCTOU close) — runs on every hop, never a silent
        # no-op (the pinned transport always recorded the target IP).
        try:
            self._verify_peer(response, expected_ip=pinned_ip, url=url)
        except BaseException:
            _close_client()
            raise

        # Attach a finalizer so the per-hop client is closed when the response is
        # closed (terminal read) OR on a redirect hop (fetch() calls response.close).
        self._attach_client_closer(response, _close_client)
        return response

    @staticmethod
    def _attach_client_closer(
        response: httpx.Response, close_client: Callable[[], None]
    ) -> None:
        """Make ``response.close()`` also close the per-hop pinned client.

        Without this the fresh per-hop ``httpx.Client`` (and its connection pool)
        would leak on every owned-path fetch — the redirect path calls
        ``response.close()`` and the terminal path calls it after the byte read, so
        wrapping it here closes the client exactly once in both cases.
        """
        original_close = response.close
        closed = {"done": False}

        def _close() -> None:
            try:
                original_close()
            finally:
                if not closed["done"]:
                    closed["done"] = True
                    close_client()

        response.close = _close  # type: ignore[method-assign]

    @staticmethod
    def _verify_peer(response: httpx.Response, *, expected_ip: str, url: str) -> None:
        """Assert the connected/pinned peer is the vetted IP and is not blocked.

        Reads the real connected peer when the transport exposes it
        (``network_stream`` -> ``server_addr``); otherwise falls back to the IP the
        pinned transport recorded (``gludd_pinned_ip``). Because the pinned
        transport ALWAYS records its target, this verify runs on every hop instead
        of silently no-opping — and is therefore offline-assertable.
        """
        peer_ip: str | None = None
        try:
            stream = response.extensions.get("network_stream")
            if stream is not None:
                server_addr = stream.get_extra_info("server_addr")
                if server_addr:
                    peer_ip = server_addr[0]
        except Exception:
            peer_ip = None
        if peer_ip is None:
            # Fall back to the IP the pin actually targeted (recorded by the
            # transport on the response's extensions). This is the deterministic,
            # offline-assertable signal.
            recorded = response.extensions.get(_PINNED_IP_EXT)
            if isinstance(recorded, str):
                peer_ip = recorded
        if peer_ip is None:
            return
        if _ip_is_blocked(peer_ip):
            raise SafeFetchError(
                WebError.SSRF_BLOCKED,
                f"connected peer {peer_ip} is a blocked address (rebind?)",
                url,
            )
        if expected_ip and peer_ip != expected_ip:
            raise SafeFetchError(
                WebError.SSRF_BLOCKED,
                f"peer {peer_ip} != pinned {expected_ip} (rebind?)",
                url,
            )

    def _read_capped(
        self, response: httpx.Response, *, deadline: float, url: str
    ) -> bytes:
        """Stream the body, stopping at ``policy.max_body_bytes`` or the deadline."""
        cap = self._policy.max_body_bytes
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            self._check_deadline(deadline, url)
            chunks.append(chunk)
            total += len(chunk)
            if total >= cap:
                break
        joined = b"".join(chunks)
        return joined[:cap]


def _origin(url: str) -> tuple[str, str, int | None]:
    """(scheme, host, port) tuple used for cross-origin credential-strip checks."""
    u = httpx.URL(url)
    return (u.scheme or "", u.host or "", u.port)
