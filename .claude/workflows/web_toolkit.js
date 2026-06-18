export const meta = {
  name: 'web-surf-scrape-crawl-toolkit',
  description: 'Build gludd web toolkit: SSRF-hardened raw fetch (+headers), HTML parse, multi-page search-gather, optional JS render, captcha-detect, and a polite crawler — all with timeout/retry/offline fallback',
  whenToUse: 'When building or revising the gludd web fetch/scrape/search/crawl toolkit.',
  phases: [
    { title: 'Discover', detail: 'map existing fetch tooling, tool exposure, resilience primitives, offline test pattern' },
    { title: 'Design', detail: '3 independent toolkit designs, judged' },
    { title: 'Build', detail: 'implement the cohesive web toolkit + tests in a worktree, commit via commit-bootstrap' },
    { title: 'Verify', detail: 'adversarial review: SSRF/redirect, offline/timeout fallback, crawler politeness/safety' },
    { title: 'Fix', detail: 'patch any blocking issue on the same branch' },
  ],
}

const FACTS = [
  'DEPS: core has httpx>=0.28 (async, streaming, redirects, timeouts) and requests>=2.32. Stdlib free: html.parser, urllib.robotparser, ipaddress, socket. Core also has tenacity (retry/backoff), diskcache (cache), pydantic, structlog. NOT present: beautifulsoup4, lxml, selectolax, trafilatura, playwright, selenium, tldextract, any search SDK. RULE: v1 must add NO new HARD dependency (or commit-bootstrap collection-check fails on an uninstalled import). Use httpx + stdlib html.parser + urllib.robotparser + tenacity. Put Playwright (JS render) and any rich-extraction lib behind an OPTIONAL [project.optional-dependencies].web extra and LAZY-import them inside the function with a try/except ImportError -> structured "renderer_unavailable" fallback, so the module imports fine with nothing extra installed.',
  'RENDER BACKENDS (explicit user requirement): the dynamic/JS-render path must support BOTH (a) FULLY HEADLESS local sessions (Playwright or Selenium headless=True, lazy-imported via the [web] extra) AND (b) connecting to a REMOTE / already-running browser via a webdriver / remote-debug interface — Chrome DevTools Protocol over a remote-debugging URL (Playwright connect_over_cdp / a ws|http endpoint, e.g. http://host:9222/json/version) OR a Selenium WebDriver/Grid (Remote) endpoint. The render entrypoint takes a config: mode in {headless_local, remote_cdp, webdriver_remote} plus an endpoint URL for the two remote modes (and optional viewport/user-agent/timeout). CRITICAL: SSRF-GUARD the remote-debug / grid endpoint (is_safe_fetch_url + getaddrinfo IP recheck) BEFORE connecting — a remote-debug URL is itself an SSRF vector (it often points at localhost:9222). All browser backends LAZY-imported with a structured renderer_unavailable fallback; explicit connect timeout + structured error on failure (never hang/raise). Default mode is headless_local; the remote modes are opt-in by config. Return the rendered HTML/text/screenshot-path through the same structured result shape as the static fetch.',
  'SSRF (CRITICAL): security/auth.py is_safe_fetch_url(url)->bool is LITERAL-HOST ONLY (no DNS), HTTPS-only, blocks localhost/metadata names + 169.254.169.254 + 100.100.100.200 + private/loopback/link-local/reserved/multicast IP literals; NO port filtering. A hostname that RESOLVES to an internal IP PASSES it. So the fetch/crawl layer MUST add a hardened guard: (a) call is_safe_fetch_url on the url string first; (b) socket.getaddrinfo-resolve the host and re-run the IP block-check on EVERY resolved address before connecting; (c) pin/verify the connected peer IP (TOCTOU); (d) re-run the FULL check (string + DNS) on EVERY redirect hop; (e) hard redirect cap (<=10); (f) HTTPS-only on every hop. connectors/base.py has is_safe_endpoint for connector endpoints (separate).',
  'SAFETY/ETHICS: respect robots.txt (urllib.robotparser) by default (overridable per-policy), rate-limit per host (token bucket), cap pages+depth, same-registrable-domain confinement for the crawler, identify via a configurable User-Agent. CAPTCHA: DETECT (403/429 + known captcha/bot-block body markers) and FALL BACK gracefully with a clear structured signal + an optional pluggable solver hook the OPERATOR can wire to a licensed service. Do NOT implement a built-in protection bypass.',
  'FALLBACK/TIMEOUT (explicit user requirement): every network call has an explicit timeout; wrap in tenacity retry with backoff for transient errors (timeout/connect/5xx/429) but NOT for 4xx-auth; on exhaustion or offline, return a STRUCTURED result object (ok=false, error, partial data) — never raise unhandled, never hang. A circuit-breaker per host so a dead host trips fast instead of retrying forever.',
  'COMMIT: make-only Bash repo. Commit the feature branch with the command  make commit-bootstrap MSG=...  (sanctioned NO-GATE commit: ruff/secrets/conflict/collection only). NEVER run make ship/gate/full test. Use Edit/Write for files. Do NOT spawn sub-agents.',
].join('\n\n')

const SRC = '/Users/shawnwilson/gludd/src/general_ludd'

const DISCO = {
  type: 'object', additionalProperties: false,
  required: ['existing_fetch','tool_exposure','resilience','offline_test_pattern'],
  properties: {
    existing_fetch: { type: 'string', description: 'Every existing outbound-HTTP primitive (file:func, sync/async, httpx/urllib) + any HTML parsing/search/crawl already present.' },
    tool_exposure: { type: 'string', description: 'How the model/agent calls a tool (MCP registry? skills? tool_loop dispatch?) — exactly where a new web tool should plug in to be callable, with the file/registration pattern.' },
    resilience: { type: 'string', description: 'Reusable retry/backoff (tenacity usage in repo?) + any CircuitBreaker class (import path + signature) to wrap network calls.' },
    offline_test_pattern: { type: 'string', description: 'The established way to test HTTP offline (httpx MockTransport / respx / transport injection) + whether a no-network marker exists; how a tool should degrade when offline.' },
  },
}

phase('Discover')
const disc = (await parallel([
  function () {
    return agent(
      'gludd repo. Read tool + Glob ONLY. Map (1) existing outbound-HTTP primitives across ' + SRC + ' (skills/fetcher.py, connectors using httpx, anything doing GET/POST) and any HTML-parse/search/crawl that already exists; and (2) tool_exposure: how the model invokes a tool today (mcp registry, skills, tool_loop) and exactly where/how to register a new web tool so the model can call it. Fill existing_fetch + tool_exposure precisely (file:path). Leave resilience/offline_test_pattern brief.',
      { label: 'discover:tooling', phase: 'Discover', schema: DISCO }
    )
  },
  function () {
    return agent(
      'gludd repo. Read tool + Glob ONLY. Map (1) resilience: how tenacity is used in the repo + any CircuitBreaker/RetryPolicy class (import path + signature) a web layer can reuse to wrap network calls in retry+timeout+breaker; and (2) offline_test_pattern: the established pattern to test HTTP without network (httpx.MockTransport / respx / fake-transport injection used by connectors), any no-network marker/conftest, and the convention for graceful-offline degradation. Fill resilience + offline_test_pattern precisely. Leave existing_fetch/tool_exposure brief.',
      { label: 'discover:resilience', phase: 'Discover', schema: DISCO }
    )
  },
])).filter(Boolean)
const discText = disc.map(function (d, i) { return '[discover ' + i + ']\n' + JSON.stringify(d, null, 2) }).join('\n\n')
log('Discover done.')

const DESIGN = {
  type: 'object', additionalProperties: false,
  required: ['approach','module_layout','ssrf_client','components','fallback_design','captcha_and_crawler','optional_extras','risks'],
  properties: {
    approach: { type: 'string' },
    module_layout: { type: 'string', description: 'Files/package layout under src/general_ludd/web/ and where the tool registers for model use.' },
    ssrf_client: { type: 'string', description: 'The hardened fetch client: is_safe_fetch_url + DNS-resolve-and-recheck-every-IP + per-redirect-hop recheck + redirect cap + HTTPS-only-each-hop + peer-IP verify. Concrete shape using httpx (custom transport/resolver or manual redirect loop).' },
    components: { type: 'string', description: 'fetch_raw (status/headers/body/final_url/elapsed), fetch_parsed (stdlib html.parser -> title/text/links/meta), search_gather (pluggable provider -> top N -> fetch+aggregate). Signatures + structured return types.' },
    fallback_design: { type: 'string', description: 'Timeout per call + tenacity retry (retryable vs not) + per-host circuit breaker + structured ok/error result on offline/exhaustion (never raise/hang). How tested offline (MockTransport).' },
    captcha_and_crawler: { type: 'string', description: 'captcha/bot-block detection + pluggable solver hook (no built-in bypass); crawler BFS with robots.txt (urllib.robotparser), per-host token-bucket rate limit, depth+page caps, same-domain confinement, URL normalize+dedup, SSRF guard per hop.' },
    optional_extras: { type: 'string', description: 'JS render via Playwright + rich extraction behind a [project.optional-dependencies].web extra, lazy-imported with renderer_unavailable fallback so base import + collection never break.' },
    risks: { type: 'array', items: { type: 'string' } },
  },
}

phase('Design')
const angles = [
  'MVP-first: smallest correct SSRF-safe fetch + parse + search-gather using only httpx + stdlib',
  'safety-first: the hardened SSRF client, robots/rate-limit/caps crawler, captcha-detect+fallback',
  'ergonomics-first: clean tool API the model can call + offline/timeout structured results + optional JS extra',
]
const designs = (await parallel(angles.map(function (angle, i) {
  return function () {
    return agent(
      'gludd repo. Design a cohesive WEB TOOLKIT for gludd: raw fetch (status/headers/body), parsed page, multi-page search-gather, optional JS render, captcha-detect, and a polite crawler — all SSRF-hardened with timeout/retry/offline fallback. Design lens: ' + angle + '\n\nGROUND-TRUTH FACTS (obey them):\n' + FACTS + '\n\nDISCOVERY:\n' + discText + '\n\nHard requirements: v1 adds NO new hard dep (httpx + stdlib html.parser + urllib.robotparser + tenacity); the SSRF-hardened client (DNS recheck + per-hop + redirect cap) is foundational; every network call has timeout + retry + per-host breaker + STRUCTURED offline fallback (never raise/hang); robots + rate-limit + caps + same-domain for the crawler; captcha = detect + fallback + pluggable solver hook (no bypass); JS render + rich extraction behind a lazy optional web extra. Produce concrete specs in the schema fields.',
      { label: 'design:' + i, phase: 'Design', schema: DESIGN, effort: 'high' }
    )
  }
}))).filter(Boolean)

const chosen = await agent(
  'You are the design judge for a gludd web toolkit. Pick the STRONGEST design and graft the best from the others into one final spec. Favor: a correct hardened SSRF client (the security keystone), genuine offline/timeout structured fallback, a polite robots/rate-limited crawler, and zero-new-hard-dep v1 with optional JS behind a lazy extra. captcha = detect+fallback+hook, never a bypass.\n\nFACTS:\n' + FACTS + '\n\nCANDIDATES:\n' + designs.map(function (d, i) { return '=== design ' + i + ' ===\n' + JSON.stringify(d, null, 2) }).join('\n\n') + '\n\nReturn the FINAL chosen design (same schema fields), paste-ready.',
  { label: 'design:judge', phase: 'Design', schema: DESIGN, effort: 'high' }
)
log('Design chosen.')

phase('Build')
const built = await agent(
  'gludd repo, make-only Bash. Implement this cohesive WEB TOOLKIT end-to-end and COMMIT it. Use Edit/Write; commit with the command  make commit-bootstrap MSG=...  ONLY (NEVER make ship/gate/full test). Do NOT spawn sub-agents.\n\nFACTS:\n' + FACTS + '\n\nFINAL DESIGN:\n' + JSON.stringify(chosen, null, 2) + '\n\nDeliver under src/general_ludd/web/ a package with: an SSRF-hardened fetch client (is_safe_fetch_url + getaddrinfo resolve + re-check every IP + manual redirect loop re-checking each hop + redirect cap + HTTPS-only each hop + peer-IP verify), fetch_raw (returns ok/status/headers/final_url/body/elapsed), fetch_parsed (stdlib html.parser -> title/text/links/meta; NO new dep), search_gather (pluggable SearchProvider interface -> fetch top N -> aggregate; offline-safe), a polite Crawler (BFS, urllib.robotparser robots, per-host token-bucket rate limit, depth+page caps, same-domain confinement, URL normalize+dedup, SSRF guard per hop), captcha/bot-block detection + a pluggable solver-hook interface (NO bypass), and an OPTIONAL js-render backend (Playwright) that is LAZY-imported with a renderer_unavailable structured fallback. EVERY network call: explicit timeout + tenacity retry (retryable transient only) + per-host circuit breaker + structured ok/error result on offline/exhaustion (never raise/hang). Add a [project.optional-dependencies].web extra (playwright) in pyproject.toml WITHOUT importing it at module top. Wire the toolkit so the model can call it (per discovery tool_exposure). Add comprehensive tests using httpx.MockTransport / fake-resolver so ALL paths (incl SSRF-block on DNS-to-internal, redirect-to-internal, offline timeout->structured error, robots-disallow, rate-limit, captcha-detect) run OFFLINE with no real network. Then git-add and run  make commit-bootstrap MSG=feat: web toolkit - SSRF-hardened fetch/parse/search-gather + polite crawler + captcha-detect + optional JS render, with timeout/retry/offline fallback. Return branch, SHA, file list, and the full text of the SSRF client + crawler for review.',
  { label: 'build:toolkit', phase: 'Build', isolation: 'worktree', effort: 'high' }
)

phase('Verify')
const VERDICT = {
  type: 'object', additionalProperties: false,
  required: ['dimension','sound','issues','severity'],
  properties: {
    dimension: { type: 'string' },
    sound: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' } },
    severity: { type: 'string', enum: ['none','low','medium','high','blocking'] },
  },
}
const lenses = [
  'SSRF: can ANY fetch/crawl hop reach an internal IP? Verify DNS-resolve-and-recheck-every-IP, per-redirect-hop recheck, redirect cap, HTTPS-only-each-hop, peer-IP verify. A hostname resolving to 169.254.169.254 or a redirect to http://10.0.0.1 MUST be blocked. Default sound=false unless the code clearly does all of this.',
  'OFFLINE/TIMEOUT FALLBACK: does every network call have an explicit timeout and return a STRUCTURED error (never raise/hang) when offline or after retry exhaustion? Is retry limited to transient errors (not 4xx-auth, not infinite)? Is the breaker real? Are tests truly offline (MockTransport, no real socket)?',
  'CRAWLER POLITENESS + DEP SAFETY: robots.txt respected, per-host rate limit, depth+page caps, same-domain confinement, URL dedup? AND does the module import with ZERO new deps installed (Playwright/parser lazy-imported, not top-level) so collection-check passed? Any captcha BYPASS (should be detect+fallback only)?',
]
const builtText = (typeof built === 'string') ? built : JSON.stringify(built)
const verdicts = (await parallel(lenses.map(function (lens, i) {
  return function () {
    return agent(
      'Adversarially review this just-built gludd web toolkit. Lens: ' + lens + '\n\nFACTS:\n' + FACTS + '\n\nBUILD RESULT (branch + key files):\n' + builtText + '\n\nBe skeptical — default sound=false if the evidence does not clearly show the property holds. List concrete issues + the fix. severity blocking = an SSRF hole, a hang/raise on offline, an unbounded crawler, a top-level optional import that breaks collection, or a captcha bypass.',
      { label: 'verify:' + i, phase: 'Verify', schema: VERDICT, effort: 'high' }
    )
  }
}))).filter(Boolean)

// "Make the fix more than partial": address EVERY real issue (blocking/high/medium/low),
// not just blocking, and require the fix agent to FULLY resolve them — no punting/skipping.
const toFix = verdicts.filter(function (v) { return v && !v.sound && v.severity !== 'none' && (v.issues || []).length })
let fixResult = null
if (toFix.length) {
  phase('Fix')
  const allIssues = toFix.map(function (b) { return '- [' + b.dimension + ' / ' + b.severity + '] ' + (b.issues || []).join('; ') }).join('\n')
  fixResult = await agent(
    'gludd repo, make-only Bash. The web toolkit has the issues below from adversarial review. Check out the build feature branch (info below) and FULLY resolve EVERY listed issue — do NOT skip, defer, or partially address any of them; if an issue is non-trivial, still fix it properly and add a test proving the fix. When done, re-commit with the command  make commit-bootstrap MSG=fix: web toolkit - fully resolve SSRF/offline/crawler review. Use Edit/Write; NO make ship/gate; NO sub-agents.\n\nBUILD:\n' + builtText + '\n\nALL ISSUES TO FULLY RESOLVE:\n' + allIssues + '\n\nReturn the updated commit SHA, and for EACH issue above state exactly how it was resolved (or, only if genuinely impossible, why — but attempt all).',
    { label: 'fix:toolkit', phase: 'Fix', isolation: 'worktree', effort: 'high' }
  )
}
const blocking = toFix

return {
  chosen_approach: chosen ? chosen.approach : null,
  build: built,
  verdicts: verdicts,
  blocking_count: blocking.length,
  fix: fixResult,
}
