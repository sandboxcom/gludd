"""Crawler — polite BFS web crawler, every fetch THROUGH the SSRF client.

Safety/ethics built in: robots.txt (overridable), per-host token-bucket rate
limit, depth + page caps, same-registrable-domain confinement, URL normalize +
dedup, and SSRF re-validation on EVERY hop (because each queued URL is fetched
through :class:`SsrfSafeClient`, a link or redirect to an internal IP is blocked
at fetch time, not merely at enqueue).  Bounded; never hangs; per-page failures
are collected, not raised.
"""

from __future__ import annotations

import contextlib
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

from general_ludd.web.breaker import HostCircuitBreaker
from general_ludd.web.captcha import CaptchaSolver
from general_ludd.web.parse import fetch_parsed
from general_ludd.web.ratelimit import TokenBucket
from general_ludd.web.results import CrawlResult, ParsedPage, WebError
from general_ludd.web.robots import RobotsCache
from general_ludd.web.ssrf_client import DEFAULT_UA, SsrfSafeClient

#: PSL-lite: a small set of multi-label public suffixes so co.uk / com.au /
#: github.io style hosts get a sensible registrable domain WITHOUT tldextract.
#: Operator-extendable via CrawlPolicy.allowed_domains for accuracy.
_MULTI_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk", "ltd.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au",
        "co.jp", "or.jp", "ne.jp", "go.jp", "ac.jp",
        "co.nz", "org.nz", "govt.nz",
        "com.br", "com.cn", "com.mx", "co.in", "co.za", "co.kr",
        "github.io", "gitlab.io",
    }
)


def registrable_domain(host: str) -> str:
    """Return the registrable ('eTLD+1') domain for ``host`` (PSL-lite).

    Handles the common multi-label suffixes above; otherwise falls back to the
    last two labels.  Lowercased; an empty/IP host returns itself.

    KNOWN APPROXIMATION (documented v1 trade-off — no ``tldextract``/real PSL dep):
    ``_MULTI_SUFFIXES`` is a hand-curated subset, so two unrelated hosts under an
    UNLISTED multi-label public suffix (e.g. ``foo.s3.amazonaws.com`` vs
    ``bar.s3.amazonaws.com``, or ``*.something.gov.tr``) may be judged
    same-registrable-domain (and so crawled together), or a legitimate same-site
    host be judged off-domain.  Operators who need exact same-site confinement
    MUST set :attr:`CrawlPolicy.allowed_domains` (which overrides this heuristic
    entirely) and/or :attr:`CrawlPolicy.allow_subdomains`.
    """
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return ""
    # IP literals have no registrable domain — confine to the exact host.
    if h.replace(".", "").isdigit() or ":" in h:
        return h
    labels = h.split(".")
    if len(labels) <= 2:
        return h
    last_two = ".".join(labels[-2:])
    last_three = ".".join(labels[-3:])
    if last_two in _MULTI_SUFFIXES:
        # suffix is two labels -> registrable domain is last three.
        return ".".join(labels[-3:]) if len(labels) >= 3 else h
    if last_three in _MULTI_SUFFIXES:  # rare 3-label suffix
        return ".".join(labels[-4:]) if len(labels) >= 4 else h
    return last_two


def normalize_url(url: str, *, base: str = "") -> str | None:
    """Canonicalise a URL for dedup: lowercase scheme/host, strip default port,
    drop fragment, sort query, collapse a bare trailing slash.  Returns None for a
    non-http(s) URL.
    """
    from urllib.parse import parse_qsl, urlencode, urljoin

    absolute = urljoin(base, url) if base else url
    parts = urlsplit(absolute)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    port = parts.port
    default = 443 if scheme == "https" else 80
    netloc = host if (port is None or port == default) else f"{host}:{port}"
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


@dataclass
class CrawlPolicy:
    """Politeness + safety knobs for a crawl."""

    max_pages: int = 50
    max_depth: int = 3
    per_host_rps: float = 1.0
    respect_robots: bool = True
    user_agent: str = DEFAULT_UA
    allowed_domains: frozenset[str] | None = None
    allow_subdomains: bool = True
    solver: CaptchaSolver | None = None
    timeout: httpx.Timeout | None = None
    rate_limit_max_wait: float = 30.0
    rate_limit_sleep: bool = True


@dataclass
class _Stats:
    pages: int = 0
    depth_reached: int = 0
    hosts: set[str] = field(default_factory=set)
    blocked: int = 0
    robots_denied: int = 0
    off_domain: int = 0
    rate_limited: int = 0


class Crawler:
    """A bounded, polite BFS crawler.  Call :meth:`crawl` to run it."""

    def __init__(
        self,
        seed_urls: list[str],
        *,
        policy: CrawlPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        client: SsrfSafeClient | None = None,
        breaker: HostCircuitBreaker | None = None,
    ) -> None:
        self.seed_urls = list(seed_urls)
        self.policy = policy or CrawlPolicy()
        # ONE shared SsrfSafeClient (one breaker + pool) for the whole crawl.
        self._client = client or SsrfSafeClient(
            transport=transport,
            breaker=breaker,
            timeout=self.policy.timeout,
            user_agent=self.policy.user_agent,
        )
        self._robots = RobotsCache(self._client)
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket(self, host: str, url: str = "") -> TokenBucket:
        b = self._buckets.get(host)
        if b is None:
            rate = self.policy.per_host_rps
            # POLITENESS: honor a robots.txt Crawl-delay. A site asking for
            # "Crawl-delay: 10" must be hit no faster than 1/10 rps, so the
            # effective rate is min(configured rps, 1/crawl_delay). The bucket is
            # created ONCE per host (after robots is loaded), so the delay is read
            # here and baked into the host's rate for the whole crawl.
            if self.policy.respect_robots and url:
                with contextlib.suppress(Exception):
                    delay = self._robots.crawl_delay(url)
                    if delay and delay > 0:
                        rate = min(rate, 1.0 / float(delay))
            b = TokenBucket(rate=rate, capacity=max(1.0, rate))
            self._buckets[host] = b
        return b

    def _seed_domains(self) -> frozenset[str]:
        if self.policy.allowed_domains:
            return frozenset(d.lower() for d in self.policy.allowed_domains)
        doms: set[str] = set()
        for u in self.seed_urls:
            host = (urlsplit(u).hostname or "").lower()
            if host:
                doms.add(registrable_domain(host))
        return frozenset(doms)

    def _in_scope(self, url: str, allowed: frozenset[str]) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            return False
        dom = registrable_domain(host)
        if self.policy.allow_subdomains:
            return dom in allowed
        # Exact-host confinement: the host's registrable domain must match AND no
        # subdomain (host == registrable domain) when subdomains disallowed.
        return dom in allowed and host == dom

    def crawl(self) -> CrawlResult:
        """Run the BFS crawl and return a structured :class:`CrawlResult`."""
        allowed = self._seed_domains()
        stats = _Stats()
        pages: list[ParsedPage] = []
        visited: list[str] = []
        skipped: list[dict[str, str]] = []
        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque()

        for s in self.seed_urls:
            n = normalize_url(s)
            if n and n not in seen:
                seen.add(n)
                queue.append((n, 0))

        while queue and stats.pages < self.policy.max_pages:
            url, depth = queue.popleft()
            stats.depth_reached = max(stats.depth_reached, depth)

            if not self._in_scope(url, allowed):
                stats.off_domain += 1
                skipped.append({"url": url, "reason": "off_domain"})
                continue

            if self.policy.respect_robots and not self._robots.can_fetch(
                self.policy.user_agent, url
            ):
                stats.robots_denied += 1
                skipped.append({"url": url, "reason": "robots_denied"})
                continue

            host = (urlsplit(url).hostname or "").lower()
            stats.hosts.add(host)
            waited = self._bucket(host, url).take(
                max_wait=self.policy.rate_limit_max_wait,
                sleep=self.policy.rate_limit_sleep,
            )
            if waited < 0:
                stats.rate_limited += 1
                skipped.append({"url": url, "reason": "rate_limit_timeout"})
                continue

            page = fetch_parsed(url, client=self._client)
            visited.append(url)
            stats.pages += 1

            if not page.ok:
                if page.error == WebError.SSRF_BLOCKED:
                    stats.blocked += 1
                if page.error == WebError.CAPTCHA_DETECTED and self.policy.solver:
                    # Hand to the operator solver hook; never auto-bypass.
                    with contextlib.suppress(Exception):
                        self.policy.solver.solve(page.captcha, None)  # type: ignore[arg-type]
                skipped.append({
                    "url": url,
                    "reason": getattr(page.error, "value", page.error) or "fetch_failed",
                })
                continue

            pages.append(page)

            if depth < self.policy.max_depth:
                for link in page.links:
                    n = normalize_url(link, base=page.final_url or url)
                    if not n or n in seen:
                        continue
                    if not self._in_scope(n, allowed):
                        stats.off_domain += 1
                        continue
                    seen.add(n)
                    queue.append((n, depth + 1))

        return CrawlResult(
            ok=len(pages) > 0,
            error=None if pages else WebError.OFFLINE,
            pages=pages,
            visited=visited,
            skipped=skipped,
            stats={
                "pages": stats.pages,
                "depth_reached": stats.depth_reached,
                "hosts": sorted(stats.hosts),
                "blocked": stats.blocked,
                "robots_denied": stats.robots_denied,
                "off_domain": stats.off_domain,
                "rate_limited": stats.rate_limited,
            },
        )
