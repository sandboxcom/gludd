# Web toolkit reconciliation

## Status and ancestry

S83.124 reconciles the durable behavior from four completed but divergent
histories: `06be3a1a`, `fb7d5bf1`, `46de4c8b`, and `2edfde3e`. The histories
implemented similar fetch, parse, search, crawl, render, and tool surfaces with
different private HTTP clients and dispatcher changes. Current development
already has the stronger shared outbound boundary in
`general_ludd.security.url_fetch`; this feature reuses it instead of copying
either stale client. It deliberately does not copy historical `Makefile`,
`pyproject.toml`, `uv.lock`, dynamic-dispatcher, or capability-lattice changes.

## Contract

Every public operation returns a frozen, JSON-serializable structured result.
Expected environmental failures do not escape the model-call boundary:

- `fetch_raw` performs retrieval through the current `FetchPolicy` and
  `secure_fetch` boundary. The toolkit itself cannot open sockets.
- `fetch_parsed` uses the standard-library `HTMLParser`, limits discovered
  links, excludes active and non-visible content, and keeps successful
  non-HTML responses as bounded text.
- `search_gather` requires an operator-injected provider. An unconfigured
  provider is distinguishable from a valid zero-hit result, and a failed hit
  does not discard successful hits.
- `crawl_site` is sequential breadth-first traversal with an overall deadline,
  same-host confinement, URL de-duplication, a page/depth/link ceiling,
  monotonic pacing, and robots evaluation fetched through the same outbound
  boundary. A missing robots file permits crawling; an unavailable or invalid
  robots response fails closed by default.
- `render_js` is disabled by default. The optional backend receives only HTML
  that was already fetched through the hardened boundary and has an explicitly
  offline interface. It never receives permission to navigate a browser to an
  untrusted URL. Missing backends and oversized output are structured results.
- The current synthetic MCP server advertises `web_fetch`,
  `web_fetch_parsed`, `web_search`, `web_crawl`, and `web_render`. The existing
  `web_retrieve` tool remains available for compatibility. Blocking work runs
  in a worker thread rather than on the MCP event loop.

The package remains importable without a search service or browser dependency.
Challenge detection is advisory and bounded; it reports a signal and never
attempts to solve, evade, or bypass a challenge.

## Security and fail-closed behavior

The maintained fetch boundary admits HTTPS only by default, normalizes the host
allowlist, resolves every redirect hop, rejects private and reserved addresses,
pins the selected public address in the transport, disables environment proxy
inheritance, strips sensitive headers across origins, and enforces one deadline
and byte ceiling. These properties address the redirect and DNS-rebinding
failure classes described by the
[OWASP SSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
and the
[urllib3 redirect-configuration advisory](https://github.com/urllib3/urllib3/security/advisories/GHSA-pq67-6m6q-mj2v).

Neither parser output nor search-provider output is trusted as authorization.
Every gathered or crawled URL returns to the same fetch boundary. Crawling is
confined before queuing and again validated at fetch time. User-supplied methods
are limited to retrieval operations. Renderer code cannot become a second
network path through the public interface. Model arguments may narrow policy
limits but cannot widen the configured ceilings.

## Practitioner evidence and mature primitives

The design prefers maintained platform primitives over bespoke protocol or
network parsing:

- A long-lived 2020 practitioner report describes
  [`RobotFileParser.read()` hanging](https://stackoverflow.com/questions/64036636/python-robotfileparser-hangs-in-read).
  The toolkit therefore fetches `/robots.txt` through the bounded shared client
  and passes text to `RobotFileParser.parse()`; it never calls the unbounded
  convenience reader.
- A 2010 crawler-operator discussion documents the operational cost of an
  [overly aggressive bot](https://stackoverflow.com/questions/2069491/too-aggressive-bot).
  Sequential breadth-first work, a descriptive user agent, robots handling,
  monotonic pacing, and hard page/depth limits are defaults rather than caller
  conventions.
- CPython's long-running malformed-document report
  [bpo-32876 / cpython#77057](https://github.com/python/cpython/issues/77057)
  supports tolerant partial extraction and structured parse degradation rather
  than treating arbitrary HTML as well-formed XML.
- Playwright documents that browser request routing does not intercept all
  [service-worker requests](https://github.com/microsoft/playwright/blob/main/docs/src/api/class-browsercontext.md),
  and practitioners continue to report the limitation in
  [playwright#37675](https://github.com/microsoft/playwright/issues/37675).
  A browser navigation backend would therefore not inherit the pinned fetch
  boundary. Rendering is offline-only until a backend can prove equivalent
  subresource isolation.
- The standard-library
  [`urllib.robotparser`](https://docs.python.org/3/library/urllib.robotparser.html),
  `html.parser`, `collections.deque`, and `time.monotonic` provide the protocol,
  parser, bounded queue, and clock primitives. Robots semantics follow
  [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html).

## Resource and observability bounds

Policy construction rejects non-finite, negative, ambiguous, or excessive
limits. Defaults cap a response at 1 MiB, redirects at three, DNS resolution at
two seconds, one fetch at fifteen seconds, one crawl at thirty seconds, crawl
depth at two, pages at ten, links per page at one hundred, search results at
ten, and rendered output at 1 MiB. Absolute construction ceilings prevent a
model from converting a request field into unbounded work. Crawl memory is
bounded by the page and per-page-link ceilings and it performs no fan-out.

Results expose stable error categories, final URL/status when available,
partial search/crawl successes, visited and skipped URLs, truncation, provider
state, robots state, and bounded counters. They do not expose transport objects,
stack traces, resolver internals, or secrets. This gives operators useful
signals without creating a second logging or persistence path.

## ZDD and rollback

The rollout is additive. Existing `web_retrieve`, external MCP transports, role
dispatch, and persisted data are unchanged. The default search and render paths
remain offline, so deploying code before providers does not create surprise
egress. Builtin registration is process-local and stateless; old and new calls
can drain during a rolling restart with no migration or shared cache handoff.

Rollback removes the five new builtin registrations and the additive web
package, then restarts workers. In-flight calls remain bounded by their existing
deadline, there is no database or filesystem state to reverse, and legacy
`web_retrieve` continues to serve callers. Operators can also disable rendering
or remove a provider independently without taking fetch/parse offline.

## Verification

Verification is failing-first and warnings-strict. It covers fetch exception and
HTTP mappings, parser tolerance and link bounds, provider absence versus zero
hits, partial gather behavior, robots denial and outage, crawl confinement and
caps, offline rendering, output limits, current MCP registration and dispatch,
plus the adjacent outbound-security and existing builtin suites. The final task
evidence records exact focused coverage, collection, lint, type, documentation,
and guarded-commit results.
