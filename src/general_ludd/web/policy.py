"""WebPolicy — the configuration knobs for the whole web toolkit.

A single frozen-ish pydantic model threaded through every layer: the SSRF
fetcher, the resilience wrapper, the crawler, robots handling and rate limiting.
Defaults are SAFE-BY-CONSTRUCTION: https-only, obey robots, modest caps, a hard
redirect ceiling of 10. Relaxations (``allow_http``, ``allow_render``) are
default-OFF and explicitly documented as loosening the security posture.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

#: Hard ceiling on redirect hops — the policy may set a LOWER ``max_redirects``
#: but never a higher one (clamped in :meth:`WebPolicy.effective_max_redirects`).
REDIRECT_HARD_CEILING = 10

#: A polite, identifying default User-Agent. Operators SHOULD override it with a
#: contact URL so site owners can reach them.
DEFAULT_USER_AGENT = (
    "gludd-web/1.0 (+https://github.com/sandboxcom/gludd; polite-bot)"
)


class WebPolicy(BaseModel):
    """Tunable safety/politeness/resilience policy for a web operation."""

    model_config = ConfigDict(frozen=True)

    # -- identity ---------------------------------------------------------- #
    user_agent: str = DEFAULT_USER_AGENT

    # -- robots ------------------------------------------------------------ #
    obey_robots: bool = True
    #: What to do when robots.txt itself cannot be fetched. The crawler defaults
    #: to ``fail_closed`` (skip when unsure); plain fetch tools default the
    #: crawler-independent path to ``fail_open`` (one URL, operator-initiated).
    robots_on_error: Literal["fail_open", "fail_closed"] = "fail_open"
    robots_ttl_seconds: float = 3600.0

    # -- redirects / schemes ----------------------------------------------- #
    max_redirects: int = REDIRECT_HARD_CEILING
    allowed_schemes: frozenset[str] = frozenset({"https"})
    #: Relaxes the https-only posture to permit ``http``. DEFAULT OFF. Turning
    #: this on weakens SSRF/transport security and should be a deliberate operator
    #: choice for a known-plaintext host.
    allow_http: bool = False

    # -- crawl scope ------------------------------------------------------- #
    max_pages: int = 25
    max_depth: int = 2
    allow_subdomains: bool = False
    #: Optional explicit allow-set of registrable domains for crawl confinement
    #: (overrides the stdlib registrable-domain heuristic when supplied).
    allowed_domains: frozenset[str] = frozenset()

    # -- rate limiting ----------------------------------------------------- #
    per_host_rps: float = 1.0
    per_host_burst: int = 2
    #: Upper clamp (seconds) on a robots ``Crawl-delay`` the crawler will honor. A
    #: hostile/misconfigured ``Crawl-delay: 86400`` would otherwise make the next
    #: same-host acquire sleep up to a day; we cap it so politeness can never be
    #: weaponized into a per-page hang. The overall_deadline also bounds the crawl.
    max_crawl_delay: float = 30.0

    # -- timeouts / size --------------------------------------------------- #
    connect_timeout: float = 10.0
    read_timeout: float = 20.0
    #: Whole-operation wall-clock budget (redirect chain + retries) in seconds.
    overall_deadline: float = 60.0
    max_body_bytes: int = 5_000_000

    # -- resilience -------------------------------------------------------- #
    max_attempts: int = 3
    breaker_failure_threshold: int = 3
    breaker_cooldown_seconds: float = 30.0
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 30.0

    # -- optional render --------------------------------------------------- #
    #: JS render is opt-in per policy. DEFAULT OFF — the default toolkit is the
    #: lightweight httpx+stdlib path; rendering pulls in the optional ``web`` extra.
    allow_render: bool = False
    render_timeout: float = 15.0

    def effective_schemes(self) -> frozenset[str]:
        """Resolve the allowed schemes, honoring the ``allow_http`` override."""
        if self.allow_http:
            return self.allowed_schemes | {"http"}
        return self.allowed_schemes

    def effective_max_redirects(self) -> int:
        """Clamp ``max_redirects`` to the hard ceiling (never above 10)."""
        return min(self.max_redirects, REDIRECT_HARD_CEILING)


#: A module-level default so call sites can take a policy without constructing one.
DEFAULT_POLICY = WebPolicy()
