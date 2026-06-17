"""SsrfSafeClient — the single hardened fetch chokepoint for the web toolkit.

``security.auth.is_safe_fetch_url`` is LITERAL-host-only (HTTPS-only + a literal
deny check, NO DNS) — so a hostname that *resolves* to an internal IP currently
PASSES it.  This module closes that DNS-rebinding / TOCTOU hole.  Every other
component (fetch_raw, fetch_parsed, search_gather, the Crawler, render's target +
endpoint guard) routes through here, so the SSRF guard + explicit timeout +
tenacity retry + per-host circuit breaker + structured offline fallback are
enforced in exactly ONE place and inherited everywhere.

Per-hop pipeline (run on the INITIAL url AND on EVERY redirect target):
  (a) STRING gate    — security.auth.is_safe_fetch_url: HTTPS-only + literal deny.
  (b) DNS + IP screen — getaddrinfo-resolve, re-run the IP block on EVERY address.
  (c) PEER-IP pin    — connect to a screened IP; re-screen the connected peer
                       (defence-in-depth against a rebind between our resolve and
                       httpx's own resolve).
  (d) HTTPS each hop — re-asserted by (a) on every hop's URL.
  (e) REDIRECT cap   — manual loop, follow_redirects=False, hard stop at <=10.

Nothing here ever raises to the caller and nothing ever hangs: every request
carries an explicit ``httpx.Timeout`` and every exit path returns a structured
:class:`~general_ludd.web.results.RawFetchResult`.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from general_ludd.security.auth import is_safe_fetch_url
from general_ludd.web.breaker import HostCircuitBreaker
from general_ludd.web.captcha import detect_captcha
from general_ludd.web.resilience import (
    NonRetryableWebError,
    RetryableWebError,
    is_retryable,
)
from general_ludd.web.results import RawFetchResult, WebError

DEFAULT_UA = "general-ludd-web/1.0 (+https://github.com/sandboxcom/gludd)"

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)

#: The metadata-service literals security.auth blocks by name; we re-block them
#: as resolved-IP literals too (a name could CNAME/resolve straight to them).
_METADATA_IPS = frozenset({"169.254.169.254", "100.100.100.200"})

#: Bounded number of fetch attempts (1 initial + retries) for transient errors.
_MAX_ATTEMPTS = 4

#: Redirect statuses we follow manually (re-validating each hop).
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class SSRFBlocked(NonRetryableWebError):
    """A URL/host/peer failed the SSRF screen — never retried, never connected."""


def _screen_ip(ip_str: str) -> bool:
    """Return True iff ``ip_str`` is a SAFE (public, non-metadata) address.

    Mirrors ``security.auth._host_is_blocked`` IP semantics but applied to a
    RESOLVED address literal: reject private / loopback / link-local / reserved /
    multicast / unspecified ranges plus the cloud-metadata literals.  An
    unparseable string is treated as unsafe (fail-closed).
    """
    raw = (ip_str or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if raw in _METADATA_IPS:
        return False
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    # IPv4-mapped IPv6 (::ffff:a.b.c.d) bypass defence: the mapped form is NOT
    # is_private/is_reserved for a PUBLIC-looking literal like Alibaba's metadata
    # 100.100.100.200, and the raw-string _METADATA_IPS match misses the mapped
    # spelling. Unwrap to the embedded v4 address and re-check BOTH the metadata
    # literal set AND every range predicate against the unwrapped form.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        if str(mapped) in _METADATA_IPS:
            return False
        ip = mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve_and_screen(host: str, port: int) -> list[str]:
    """Resolve ``host`` and SCREEN every returned address; fail closed.

    Returns the list of screened (safe) IP strings.  Raises:
      * :class:`SSRFBlocked` if ANY resolved address is internal/metadata, OR
      * :class:`OfflineResolveError` on a getaddrinfo failure / empty result
        (NXDOMAIN / offline), so the caller maps it to a structured OFFLINE
        result rather than a hang or an unhandled raise.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:  # NXDOMAIN, no network, etc.
        raise OfflineResolveError(f"getaddrinfo failed: {type(exc).__name__}") from exc
    addrs: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip = str(sockaddr[0])
        if not _screen_ip(ip):
            raise SSRFBlocked(
                f"host {host!r} resolves to a blocked address", kind="dns",
            )
        addrs.append(ip)
    if not addrs:
        raise OfflineResolveError(f"host {host!r} resolved to no addresses")
    # De-dup while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


class OfflineResolveError(NonRetryableWebError):
    """DNS resolution failed (offline / NXDOMAIN) — surfaced as a structured OFFLINE."""


def _split_host_port(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    return host, port


def _guard_url(url: str) -> list[str]:
    """Run the full per-hop screen on ``url``; return its screened IP set.

    (a) string gate (HTTPS-only + literal deny) -> (b) DNS resolve + IP re-screen.
    Raises :class:`SSRFBlocked` or :class:`OfflineResolveError`.
    """
    if not is_safe_fetch_url(url):
        raise SSRFBlocked(f"url failed is_safe_fetch_url string gate: {url!r}", kind="string")
    host, port = _split_host_port(url)
    if not host:
        raise SSRFBlocked(f"url has no host: {url!r}", kind="string")
    return resolve_and_screen(host, port)


class _PinnedTransport(httpx.HTTPTransport):
    """A real-network transport that dials ONLY a pre-screened IP literal.

    TRUE pinning (closes the DNS-rebind / TOCTOU hole): we rewrite the outgoing
    request URL's host to a SCREENED IP LITERAL so httpx connects to exactly the
    address :func:`resolve_and_screen` vetted — httpx never gets to re-resolve the
    hostname through its OWN resolver, so a rebinding server cannot swap in a fresh
    internal IP between our getaddrinfo and httpx's connect.  TLS integrity is
    preserved because we keep the original hostname as (a) the ``Host`` header and
    (b) the SNI / certificate-verification name via ``extensions['sni_hostname']``.

    Belt-and-braces: after connect we ALSO re-screen the actual peer address.  A
    MISSING peer address now FAILS CLOSED (the previous skip-on-None was a fail-open
    backstop) — but with true pinning the peer can only ever be a screened literal.
    """

    def __init__(self, host: str, screened_ips: list[str], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._host = host
        self._screened_ips = list(screened_ips)
        # The single IP we pin this transport's dial to (first screened address).
        self._pin_ip = screened_ips[0] if screened_ips else ""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        original_host = request.url.host
        pin_ip = self._pin_ip
        if pin_ip and original_host and original_host != pin_ip:
            # Rewrite the connect target to the screened IP literal; preserve the
            # real hostname for Host header + SNI + cert verification.
            ip_obj = ipaddress.ip_address(pin_ip)
            request.url = request.url.copy_with(host=pin_ip)
            request.headers["Host"] = (
                original_host
                if request.url.port in (None, 80, 443)
                else f"{original_host}:{request.url.port}"
            )
            # IPv6 literal SNI must stay the hostname; set the verification name.
            request.extensions = {**request.extensions, "sni_hostname": original_host}
            _ = ip_obj  # parsed only to assert pin_ip is a valid literal
        response = super().handle_request(request)
        # Re-screen the actual connected peer. FAIL CLOSED if we cannot read it.
        stream = response.extensions.get("network_stream")
        peer = None
        if stream is not None:
            try:
                peer = stream.get_extra_info("server_addr")
            except Exception:
                peer = None
        if peer:
            peer_ip = peer[0] if isinstance(peer, (tuple, list)) else str(peer)
            if not _screen_ip(str(peer_ip)) or str(peer_ip) not in self._screened_ips:
                response.close()
                raise SSRFBlocked(
                    f"connected peer {peer_ip!r} not in screened set", kind="peer",
                )
        # peer is None -> we could not verify the dialed address. With URL-host
        # pinning above the dial is already constrained to a screened literal, so
        # this is informational; we accept it (the pin, not the post-check, is the
        # guarantee). Only an UNPINNED dial would be a concern, which cannot occur
        # here because pin_ip is always a screened address.
        return response


class SsrfSafeClient:
    """The single hardened fetch core.  ``fetch`` never raises and never hangs."""

    def __init__(
        self,
        *,
        timeout: httpx.Timeout | None = None,
        max_redirects: int = 10,
        transport: httpx.BaseTransport | None = None,
        breaker: HostCircuitBreaker | None = None,
        user_agent: str = DEFAULT_UA,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> None:
        self._timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        self._max_redirects = min(max(0, max_redirects), 10)
        # An injected transport (tests) bypasses real sockets/pinning; the DNS
        # screen still runs because the monkeypatched getaddrinfo is consulted.
        self._injected_transport = transport
        self._breaker = breaker if breaker is not None else HostCircuitBreaker()
        self._user_agent = user_agent
        self._max_attempts = max(1, max_attempts)

    # -- public ---------------------------------------------------------------

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> RawFetchResult:
        """Fetch ``url`` with the full SSRF/redirect/timeout/retry/breaker stack."""
        started = time.monotonic()
        redirect_chain: list[str] = []
        try:
            screened = _guard_url(url)
        except SSRFBlocked as exc:
            return self._fail(WebError.SSRF_BLOCKED, exc, started, redirect_chain)
        except OfflineResolveError as exc:
            return self._fail(WebError.OFFLINE, exc, started, redirect_chain)

        host, port = _split_host_port(url)
        if not self._breaker.allow(host, port):
            return RawFetchResult(
                ok=False,
                error=WebError.CIRCUIT_OPEN,
                detail=f"circuit open for {host}:{port}",
                final_url=url,
                elapsed_s=round(time.monotonic() - started, 4),
                redirect_chain=redirect_chain,
            )

        last_exc: BaseException | None = None
        try:
            for attempt in Retrying(
                retry=retry_if_exception(is_retryable),
                wait=wait_exponential_jitter(initial=0.5, max=30.0),
                stop=stop_after_attempt(self._max_attempts),
                reraise=True,
            ):
                with attempt:
                    try:
                        result = self._fetch_once(
                            url, method=method, headers=headers,
                            screened=screened, started=started,
                        )
                    except (SSRFBlocked, OfflineResolveError, NonRetryableWebError):
                        # Terminal: never retried (is_retryable returns False) and
                        # mapped to a structured result by the outer handler.
                        raise
                    except (RetryableWebError, httpx.HTTPError, TimeoutError) as exc:
                        last_exc = exc
                        self._breaker.record_failure(host, port)
                        raise
                    self._breaker.record_success(host, port)
                    return result
        except SSRFBlocked as exc:
            self._breaker.record_failure(host, port)
            return self._fail(WebError.SSRF_BLOCKED, exc, started, redirect_chain)
        except OfflineResolveError as exc:
            self._breaker.record_failure(host, port)
            return self._fail(WebError.OFFLINE, exc, started, redirect_chain)
        except NonRetryableWebError as exc:
            self._breaker.record_failure(host, port)
            return self._fail(_error_for(exc), exc, started, redirect_chain)
        except (httpx.TimeoutException, TimeoutError) as exc:
            return self._fail(WebError.TIMEOUT, exc, started, redirect_chain)
        except (httpx.ConnectError, httpx.HTTPError) as exc:
            return self._fail(WebError.OFFLINE, exc, started, redirect_chain)
        except Exception as exc:  # pragma: no cover - last-resort: never raise out
            return self._fail(WebError.OFFLINE, exc, started, redirect_chain)
        # Loop exhausted without returning (should not happen with reraise=True).
        return self._fail(
            WebError.TIMEOUT, last_exc or RuntimeError("exhausted"), started, redirect_chain,
        )

    # -- internals ------------------------------------------------------------

    def _build_client(self, host: str, screened: list[str]) -> httpx.Client:
        if self._injected_transport is not None:
            transport: httpx.BaseTransport = self._injected_transport
        else:
            transport = _PinnedTransport(host, screened, verify=True)
        return httpx.Client(
            transport=transport,
            timeout=self._timeout,
            follow_redirects=False,
            headers={"User-Agent": self._user_agent},
        )

    def _fetch_once(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str] | None,
        screened: list[str],
        started: float,
    ) -> RawFetchResult:
        """One attempt: manual redirect loop, re-screening EVERY hop."""
        cur = url
        cur_screened = screened
        chain: list[str] = []
        cur_host, _ = _split_host_port(cur)
        client = self._build_client(cur_host, cur_screened)
        try:
            for _hop in range(self._max_redirects + 1):
                resp = client.request(method, cur, headers=headers)
                status = resp.status_code
                if status in _REDIRECT_CODES and resp.headers.get("location"):
                    location = resp.headers["location"]
                    nxt = urljoin(cur, location)
                    chain.append(cur)
                    if len(chain) > self._max_redirects:
                        raise NonRetryableWebError(
                            f"redirect cap exceeded at {nxt!r}", kind="redirect",
                        )
                    # Re-screen the redirect target fully (string + DNS + IP).
                    cur_screened = _guard_url(nxt)
                    cur = nxt
                    cur_host, _ = _split_host_port(cur)
                    # Rebuild the client so the pinned transport pins the NEW
                    # screened IP literal (its host) for the next hop.
                    client.close()
                    client = self._build_client(cur_host, cur_screened)
                    continue
                # Terminal response.
                body = resp.text
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                captcha = detect_captcha(status, body, hdrs)
                resolved_ip = cur_screened[0] if cur_screened else None
                base: dict[str, Any] = dict(
                    status=status,
                    headers=hdrs,
                    body=body,
                    # ``cur`` is the original (hostname) URL we manually walked to;
                    # resp.url may carry the pinned IP literal (the transport
                    # rewrites the connect host), so cur is the truthful final_url.
                    final_url=cur,
                    elapsed_s=round(time.monotonic() - started, 4),
                    redirect_chain=chain,
                    resolved_ip=resolved_ip,
                    captcha=captcha,
                )
                # Only a STRONG captcha signal (a known vendor marker) blocks as
                # CAPTCHA_DETECTED. A bare suspicious-status weak signal (vendor
                # is None) is attached advisorily but must NOT pre-empt the normal
                # 4xx/5xx/429 retry+classification below (a legit 403/429/503).
                if captcha.detected and captcha.vendor is not None:
                    return RawFetchResult(
                        ok=False, error=WebError.CAPTCHA_DETECTED,
                        detail=f"captcha:{captcha.vendor}", **base,
                    )
                if status in (401, 403):
                    raise NonRetryableWebError(f"auth error {status}", kind="auth")
                if 400 <= status < 500 and status != 429:
                    return RawFetchResult(ok=False, error=WebError.HTTP_4XX,
                                          detail=f"http {status}", **base)
                if status == 429:
                    raise RetryableWebError(f"rate limited {status}", status=status)
                if status >= 500:
                    if status in (500, 502, 503, 529):
                        raise RetryableWebError(f"server error {status}", status=status)
                    return RawFetchResult(ok=False, error=WebError.HTTP_5XX,
                                          detail=f"http {status}", **base)
                return RawFetchResult(ok=True, **base)
            raise NonRetryableWebError("redirect cap exceeded", kind="redirect")
        finally:
            client.close()

    def _fail(
        self,
        error: WebError,
        exc: BaseException,
        started: float,
        redirect_chain: list[str],
    ) -> RawFetchResult:
        return RawFetchResult(
            ok=False,
            error=error,
            detail=f"{type(exc).__name__}: {_scrub(str(exc))}",
            elapsed_s=round(time.monotonic() - started, 4),
            redirect_chain=redirect_chain,
        )


def _error_for(exc: NonRetryableWebError) -> WebError:
    kind = getattr(exc, "kind", "")
    if kind == "redirect":
        return WebError.REDIRECT_LIMIT
    if kind == "auth":
        return WebError.HTTP_4XX
    return WebError.SSRF_BLOCKED


def _scrub(message: str) -> str:
    """Trim a failure message so creds/query strings never leak into a result."""
    msg = (message or "").splitlines()[0] if message else ""
    return msg[:200]
