"""PoliteCrawler — polite-by-construction BFS over the SSRF-hardened fetcher.

Politeness/safety properties, all enforced per hop:
  * ROBOTS: ``RobotsCache`` (fetched THROUGH SafeFetcher, so robots is itself
    SSRF-checked); disallowed URLs are SKIPPED (not errors). Crawler default for
    unreachable robots is FAIL-CLOSED.
  * RATE LIMIT: per-host token bucket; ``Crawl-delay`` feeds the bucket.
  * CAPS: ``max_pages`` and ``max_depth`` hard-stop the BFS.
  * SAME-REGISTRABLE-DOMAIN CONFINEMENT: stdlib heuristic (last two labels with a
    small embedded multi-part public-suffix set) — a POLITENESS/scope boundary,
    NOT the security boundary (the SSRF guard runs per hop regardless).
  * URL NORMALIZE + DEDUP: canonical key prevents revisits/cycles.
  * RESILIENT: a per-page failure (timeout/SSRF/offline/captcha) is captured into
    its GatheredPage and the crawl CONTINUES.
"""

from __future__ import annotations

import time
from collections import deque
from urllib.parse import urldefrag, urlsplit, urlunsplit

from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.ratelimit import HostRateLimiter
from general_ludd.web.resilience import WebResilience
from general_ludd.web.robots import RobotsCache
from general_ludd.web.safe_fetch import SafeFetcher
from general_ludd.web.tools import _to_gathered, fetch_parsed
from general_ludd.web.types import GatheredPage, WebError, WebResult

#: A small embedded multi-label public-suffix set for common cases (best-effort;
#: no tldextract dependency). Operators needing precision set policy.allowed_domains.
_MULTI_LABEL_SUFFIXES = frozenset(
    {
        "co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "net.au", "org.au",
        "co.jp", "co.nz", "co.za", "com.br", "github.io",
    }
)


def registrable_domain(host: str) -> str:
    """Best-effort registrable domain (last label(s)) without tldextract.

    Handles common multi-label suffixes (``co.uk``, ``github.io``, ...). Documented
    as approximate; it is a politeness/scope boundary, not security.
    """
    host = (host or "").strip().lower().rstrip(".")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    last_three = ".".join(labels[-3:])
    if last_two in _MULTI_LABEL_SUFFIXES and len(labels) >= 3:
        return last_three
    return last_two


def normalize_url(url: str) -> str:
    """Canonicalize for dedup: lowercase scheme/host, drop default :443/:80,
    drop fragment, sort nothing destructive but keep query, normalize path.
    """
    url, _frag = urldefrag(url)
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


class PoliteCrawler:
    """BFS crawler honoring robots, rate limits, caps and domain confinement."""

    def __init__(
        self,
        *,
        policy: WebPolicy = DEFAULT_POLICY,
        fetcher: SafeFetcher | None = None,
        resilience: WebResilience | None = None,
    ) -> None:
        # The crawler default for unreachable robots is fail-closed unless the
        # operator explicitly relaxed it on the policy.
        self._policy = policy
        self._fetcher = fetcher if fetcher is not None else SafeFetcher(policy=policy)
        self._resilience = resilience if resilience is not None else WebResilience(policy=policy)
        self._robots = RobotsCache(self._fetcher, policy)
        self._rate = HostRateLimiter(policy.per_host_rps, policy.per_host_burst)

    def _in_scope(self, url: str, seed_reg: str) -> bool:
        host = urlsplit(url).hostname or ""
        if self._policy.allowed_domains:
            return registrable_domain(host) in self._policy.allowed_domains
        if self._policy.allow_subdomains:
            return registrable_domain(host) == seed_reg
        return host.lower() == self._seed_host

    def crawl(self, seed_url: str) -> WebResult:
        """BFS from ``seed_url``; return an aggregate :class:`WebResult`."""
        seed_host = (urlsplit(seed_url).hostname or "").lower()
        self._seed_host = seed_host
        seed_reg = registrable_domain(seed_host)

        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(seed_url, 0)])
        seen.add(normalize_url(seed_url))

        results: list[GatheredPage] = []
        skipped_robots = 0
        blocked_hosts: set[str] = set()
        depth_reached = 0
        deadline_exceeded = False

        # Whole-crawl wall-clock budget: redirect chains, retries, rate-limit
        # spacing and (clamped) crawl-delays all draw from ONE deadline so a large
        # frontier can never run unbounded. Exceeding it stops the BFS cleanly with
        # whatever was gathered so far (recorded in meta).
        deadline = time.monotonic() + self._policy.overall_deadline

        while queue and len(results) < self._policy.max_pages:
            if time.monotonic() >= deadline:
                deadline_exceeded = True
                break
            url, depth = queue.popleft()
            depth_reached = max(depth_reached, depth)

            # Robots gate (fetched through SafeFetcher).
            if not self._robots.can_fetch(url):
                skipped_robots += 1
                results.append(
                    GatheredPage(
                        url=url, ok=False, error=WebError.ROBOTS_DISALLOWED,
                        detail="disallowed by robots.txt",
                    )
                )
                continue

            # Crawl-delay -> rate limiter, then acquire a token (per-host spacing).
            # CLAMP the delay to policy.max_crawl_delay so a hostile/misconfigured
            # robots ``Crawl-delay: 86400`` can't turn politeness into a ~day hang.
            delay = self._robots.crawl_delay(url)
            host = urlsplit(url).netloc
            if delay:
                self._rate.set_min_interval(
                    host, min(delay, self._policy.max_crawl_delay)
                )
            self._rate.acquire(host)

            page = fetch_parsed(
                url, policy=self._policy, fetcher=self._fetcher, resilience=self._resilience
            )
            gathered = _to_gathered(url, page)
            results.append(gathered)
            if page.error == WebError.SSRF_BLOCKED:
                blocked_hosts.add(host)

            # Enqueue in-scope, deduped links if we can still go deeper.
            if depth < self._policy.max_depth and page.parsed is not None:
                for link in page.parsed.links:
                    norm = normalize_url(link.href)
                    if norm in seen:
                        continue
                    if not self._in_scope(link.href, seed_reg):
                        continue
                    seen.add(norm)
                    queue.append((link.href, depth + 1))

        ok = any(g.ok for g in results)
        return WebResult(
            ok=ok,
            url=seed_url,
            results=results,
            meta={
                "pages_fetched": sum(1 for g in results if g.ok),
                "depth_reached": depth_reached,
                "skipped_robots": skipped_robots,
                "blocked_hosts": sorted(blocked_hosts),
                "seed_registrable_domain": seed_reg,
                "deadline_exceeded": deadline_exceeded,
            },
        )
