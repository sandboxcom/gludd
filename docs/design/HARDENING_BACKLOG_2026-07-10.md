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

### H-SELFMOD-DENYLIST (MED-HIGH, CONFIRMED) — policy docs missing from self-mod deny-lists

**Defect:** `AGENTS.md`/`CLAUDE.md`/`TASKS.md` are in **neither** self-modification
protected-path deny-list: `security/capability_lattice.py:41-75`
(`PROTECTED_FILE_STEMS`/`PROTECTED_FILE_SUBSTRINGS`/`PROTECTED_PATH_SEGMENTS`) and
`self_update/applier.py:41-64` (`PROTECTED_PATH_MARKERS`) both omit them, though
AGENTS.md is the repo's stated policy authority. `self_update/apply.py` hard-denies
`settings.json` but not these. Reachable: CONFIG-tier auto-apply
(`auto_apply_config=True`) of a markdown rewrite needs no approval token, and
pipelines like `planning/debt_applier.py:137-141` merge LLM-supplied paths into
`plan.target_files`.

**Fix:** add `AGENTS.md`/`CLAUDE.md`/`TASKS.md` (+ `.md` policy docs generally) to
both deny-lists (stem + exact-name match).

**Tests:** `is_protected_path("AGENTS.md")`/`("CLAUDE.md")` == `True`; an
`apply_plan` targeting them is refused.

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
