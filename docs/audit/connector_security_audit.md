# Connector Security Audit — observability/ingest layer

> Adversarial, read-only review of `src/general_ludd/connectors/*` (SSRF guards,
> secret leakage, injection, DoS/resource, auth). Grounded in code actually read.
> Date: 2026-06-16. Reviewer: security analysis pass (read-only, no execution).

## 0. Scope & coverage honesty (read this first)

This environment exposes **no file-listing / glob / grep tool** and the audit was
run under a hard **no-Bash / no-subagent** constraint, so I could not enumerate
the full connector directory mechanically. Files were located by reading the
known anchors (`base.py`, `normalize.py`, `ingest_formats.py`, `__init__.py`) and
then by name-probing. **The receiver HTTP endpoint / ingest *router* that calls
`ingest_formats.py` could not be located by name and was NOT reviewed** — that is
the single biggest coverage gap and should be re-run with grep available (search
for `parse_gelf` / `parse_fluent_forward` / `MAX_PAYLOAD_BYTES` callers).

**Connectors actually read (11) + contract files (3):**

| File | Read | Notes |
|------|:----:|-------|
| `connectors/base.py` (`is_safe_endpoint`, `Observability`) | ✅ | the *shared* guard + fan-out facade |
| `connectors/normalize.py` | ✅ | cross-source normalization; secret-safe by design |
| `connectors/ingest_formats.py` | ✅ | push-side wire-format parsers |
| `connectors/prometheus.py` | ✅ | rolls own guard (stricter: `not is_global`) |
| `connectors/datadog.py` | ✅ | rolls own guard (stricter: `not is_global`) |
| `connectors/elasticsearch.py` | ✅ | rolls own guard |
| `connectors/grafana_loki.py` | ✅ | rolls own guard |
| `connectors/signoz.py` | ✅ | rolls own guard |
| `connectors/splunk.py` | ✅ | rolls own guard; `repr(exc)` in health |
| `connectors/github_actions.py` | ✅ | **raw `repo` path interpolation** |
| `connectors/jenkins.py` | ✅ | **cleanest**: `quote()` + default-deny host |
| `connectors/azure_monitor.py` | ✅ | **strictest guard**: rejects single-label hosts |

**NOT reviewed (could not enumerate):** the remaining ~20 connectors the task
references (cloud/CI/log/trace/metric families implied by
`normalize.AUTH_FAMILY_PREFIXES`: AWS/CloudWatch, GCP/Stackdriver, GitLab CI,
Kibana/OpenSearch, Tempo/Mimir/Jaeger, PagerDuty, New Relic, syslog/SNMP, …) and
the receiver router. Findings below are **patterns**: where a pattern is a
*contract* (e.g. the facade leaks `{exc}` for *every* source), it applies to all;
where it is per-connector, it is scoped to the file named.

---

## 1. Prioritized findings

Severity = exploitability × impact in the threat model where **connector config
(base_url, repo, workspace_id) is operator-supplied** but **spec / query bodies
and backend responses are attacker-influenceable**, and **record contents flow
to logs/UI/storage**.

| # | Sev | File:line | Class | Finding (one line) |
|---|-----|-----------|-------|--------------------|
| F1 | **Medium** | `base.py:319` (def) vs all connectors | SSRF / dead guard | `is_safe_endpoint` is exported but **no connector calls it**; every connector rolls its own divergent guard → inconsistent policy, drift risk |
| F2 | **Medium** | `github_actions.py:159,226` | Injection (URL path) | `self.repo` / `run_id` interpolated **raw** into the URL path (`f".../repos/{self.repo}/actions/runs"`), only validated for containing `/` |
| F3 | **Low/Med** | facade `base.py:217-229`; `prometheus.py:225`, `datadog.py:223,278`, `elasticsearch.py:239`, `splunk.py:250`, others | Secret/info leakage | exception text (`f"...{exc}"`, `repr(exc)`) and the raw exception object are placed into record `message`/`raw`/`error`; if a transport embeds URL+token/credentials in its exception, it leaks into records → logs/UI/storage |
| F4 | **Low** | all rolled guards except `azure_monitor.py:89` | SSRF reachability | single-label internal hostnames (`http://vault/`, `http://gitlab-internal/`, `http://grafana/`) **pass** every guard except Azure's; literal-host policy is intentional but the bar is inconsistent |
| F5 | **Low** | `prometheus.py:84`, `datadog.py:87` add `or not ip.is_global`; `elasticsearch/splunk/loki/signoz/jenkins/github` do **not** | SSRF policy divergence | two connectors block all non-global IPs (incl. some public-reserved); the rest allow anything not loopback/private/link-local/reserved/multicast — same config, different verdict |
| F6 | **Low** | `elasticsearch.py:43-50` | SSRF (minor) | `_BLOCKED_HOSTNAMES` omits `localhost.localdomain` / `ip6-localhost` that siblings block (relies on `localhost` literal + IP ranges) |
| F7 | Hardening | `ingest_formats.py:208` | DoS (bound ordering) | `parse_beats_lumberjack` checks `len(frames) > MAX_EVENTS` but `frames` is already a fully-decoded in-memory list (no byte cap here — the byte cap lives in the unreviewed *caller*) |
| F8 | Hardening | `splunk.py:295`, `azure_monitor.py:205`, `elasticsearch.py:295-298` | Resilience contract drift | these `query()`/transport paths **raise** (`RuntimeError`/`ConnectionError`) instead of returning an error record; safe *only* because the `Observability.find` facade catches — direct callers get an exception (and F3 leakage) |
| F9 | Hardening | `github_actions.py:99`, `jenkins.py:115` (`_default_http_get` via `urllib.urlopen`) | SSRF (transport-time) | the literal-host guard runs at construction; the **real** `urllib` transport follows HTTP redirects by default and re-resolves DNS at call time — a 302 to `http://169.254.169.254/…` or DNS-rebind is **not** re-checked (mitigated in practice because tests inject mocks, but the shipped default transport is live) |

Everything below expands each with trigger, fix, and a regression-test spec.

---

## 2. Confirmed findings (detail)

### F1 — `is_safe_endpoint` is dead; guards are copy-pasted and divergent (Medium)

**Where:** `base.py:319` defines `is_safe_endpoint` and `__init__.py:25,38`
re-exports it. Grep for callers was not possible here, but **every connector I
read implements its own** `_validate_base_url` / `_assert_safe_base_url` /
`_reject_if_internal` and none imports `is_safe_endpoint`.

**Why it matters:** the shared guard is the documented SSRF control, yet the real
enforcement is N independent copies (`prometheus`, `datadog`, `elasticsearch`,
`grafana_loki`, `signoz`, `splunk`, `github_actions`, `jenkins`, `azure_monitor`
each have their own). They already disagree (F4/F5/F6). A future connector can
ship with a weaker or absent check and nothing flags it. This is the root cause of
F4–F6.

**Trigger:** add a connector that forgets the guard, or relies on
`is_safe_endpoint` (which is *weaker* than the rolled guards — it does **not**
block single-label names and `base._BLOCKED_HOST_NAMES` lacks `metadata.goog`,
`metadata.azure.com`, `instance-data`, `ip6-localhost` that several connectors
block). Either way an internal/metadata host becomes reachable.

**Minimal fix:** make one canonical guard the single source of truth. Promote the
*strictest* rolled implementation (azure_monitor's, which adds the single-label
rejection) into `base.is_safe_endpoint` / a `base.assert_safe_base_url(url)` that
raises, then have every connector call it at construction. Delete the per-file
copies. Add the missing metadata names to `base._BLOCKED_HOST_NAMES`.

**Regression test (`tests/unit/test_connector_ssrf_uniform.py`):**
- Parametrize over every connector class; for a battery of hostile `base_url`s
  (`http://127.0.0.1`, `http://169.254.169.254`, `http://10.0.0.1`,
  `http://[::1]`, `http://metadata.google.internal`, `http://vault`,
  `http://192.168.1.1:9090`) assert **every** connector raises at construction.
- `test_no_connector_rolls_its_own_guard`: assert each connector module calls the
  shared `base` guard (AST check: no local `_validate_base_url` with `ipaddress`).
- `test_is_safe_endpoint_blocks_single_label`: `is_safe_endpoint("http://vault")`
  is `False` after the fix.

### F2 — GitHub Actions interpolates `repo`/`run_id` raw into the URL path (Low/Med)

**Where:** `github_actions.py:159` `f"{self.base_url}/repos/{self.repo}/actions/runs"`
and `:226` `f"{self.base_url}/repos/{self.repo}/actions/runs/{run_id}/jobs"`.
`repo` is validated only by `:137` (`"/" in str(repo)`).

**Contrast:** `jenkins.py:164` does it correctly:
`f"{self.base_url}/job/{quote(self.job, safe='')}/api/json?..."`.

**Trigger:** operator (or a templated/issue-derived) `repo` value like
`owner/name/../../orgs/secret-org` or `owner/name?x=` rewrites the request path /
adds query params; `run_id` (drill-down, possibly response/spec-derived) is
likewise unescaped. Threat is bounded because `base_url` host is SSRF-checked, so
this is a *path-traversal within the configured backend*, not a host pivot — but
it can reach unintended GitHub API paths with the connector's token.

**Minimal fix:** `urllib.parse.quote(self.repo, safe="/")` (keep the single owner/
name slash, escape everything else) and `quote(str(run_id), safe="")`; validate
`repo` against `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`.

**Regression test (`tests/unit/test_github_actions_path_safety.py`):**
- `test_repo_with_traversal_rejected`: `repo="o/n/../../x"` → `ValueError`.
- `test_repo_is_url_encoded`: a mock transport captures the URL; assert no raw
  `..`, no injected `?`/`#`, and the path is exactly `/repos/o/n/actions/runs`.
- `test_run_id_encoded`: `fetch_failed_logs("1/../2")` does not escape the path.

### F3 — Exception text + raw exception leak into records (Low/Med, but pervasive)

**Where (contract-level, every source):** `base.py:225`
`message=f"query failed: {exc}"` and `:227` `raw=exc` — the facade wraps **any**
source exception into a record that flows to logs/UI/storage.
**Per-connector echoes:** `prometheus.py:225` `f"transport error: {exc}"`,
`datadog.py:223,278`, `elasticsearch.py:239` `f"{type(exc).__name__}: {exc}"`,
`grafana_loki.py:243`/`signoz.py:297` `str(exc)`, `splunk.py:250`
`result["error"] = repr(exc)`, `azure_monitor.py:280` `f"{type}: {exc}"`.

**Trigger:** a transport whose exception message embeds the request URL (with a
token in a query param) or auth material — common with `requests`/`httpx`
connection errors that include the URL, and with custom transports that
`raise RuntimeError(f"... {url} ...")`. The token then lands in a normalized
record's `message`/`error`, and in `raw` the **live exception object** can carry
the request (`exc.request.url`, headers) for later `repr()`/serialization.

**Why not High:** none of the *reviewed* connectors put a token into a URL query
param (all use headers), and exception strings here are transport-level. The risk
is (a) custom/3rd-party transports, (b) `raw=exc` retaining objects that downstream
JSON/`repr` serialization can dig secrets out of.

**Minimal fix:** never store the raw exception in `raw`; store
`raw={"error": type(exc).__name__}` and a **scrubbed** message
(`message="transport error"`, with `type(exc).__name__` only, no `str(exc)`), or
run `str(exc)` through a redaction pass (strip anything matching configured token
env values / `Authorization`-like substrings / `?...=` query strings). Apply at
the facade (`base.py`) so it covers all sources.

**Regression test (`tests/unit/test_record_no_secret_leak.py`):**
- A fake source raises `RuntimeError("connect to https://h/api?api_key=SEKRET")`;
  run `Observability.find`; assert no record `message`/`raw` (recursively
  stringified) contains `SEKRET` or `api_key=`.
- `test_raw_is_not_live_exception`: assert `record["raw"]` is not an `Exception`.

**2026-08-01 implementation update:** the canonical connector exception sanitizer
now emits only the exception type in both records and logs; it never attaches
`exc_info`, exception text, URLs, paths, or credentials. The Kubernetes connector
uses that sanitizer for health, configuration, and transport failures while
returning stable, non-secret messages. Regression coverage is in
`tests/unit/test_h20_connector_exc_leak.py`,
`tests/unit/test_connector_kubernetes.py`, and
`tests/unit/test_connector_kubernetes_no_leak.py`.

This closes a failure mode seen in long-lived operator reports: Requests users
have posted tracebacks containing complete authenticated endpoint paths in
[issue #4246](https://github.com/psf/requests/issues/4246) and full request URLs
plus local source paths in
[issue #5801](https://github.com/psf/requests/issues/5801). It also follows the
[OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html),
which requires tokens, passwords, connection strings, keys, and potentially file
paths or internal network names to be removed, masked, or sanitized before log
storage.

### F4 — Single-label internal hostnames pass every guard but Azure's (Low)

**Where:** `azure_monitor.py:89` rejects dot-less, colon-less hosts:
`if "." not in host_l and ":" not in host_l: raise`. No other reviewed connector
does. `prometheus/datadog/elasticsearch/grafana_loki/signoz/splunk/jenkins/
github_actions` all `return False` ("allow") for any non-IP, non-blocklisted name.

**Trigger:** `base_url="http://grafana/"`, `http://vault:8200/`,
`http://prometheus.monitoring/` (k8s service DNS) — internal service names with
no dot — are accepted; at call time the resolver maps them to in-cluster private
IPs. The literal-IP block never fires because the host is a *name*. This is partly
*by design* (documented literal-host policy that refuses to resolve DNS), but
Azure's connector proves a cheap tightening the others skipped.

**Minimal fix:** fold Azure's single-label rejection into the shared guard (F1).
Optionally also reject hosts ending in known internal TLDs (`.internal`, `.local`,
`.cluster.local`, `.svc`).

**Regression test:** covered by F1's `test_is_safe_endpoint_blocks_single_label`
plus a `.svc.cluster.local` case.

### F5 — `is_global` divergence across guards (Low)

**Where:** `prometheus.py:84` and `datadog.py:87` end with `or not ip.is_global`;
`elasticsearch.py:70-84`, `splunk.py:91-103`, `grafana_loki.py:83-90`,
`signoz.py:99-106`, `jenkins.py:94-101`, `github_actions.py:72-79` do **not**.

**Trigger:** an IP that is public-but-not-`is_global` (e.g. some reserved/benchmark
ranges) is blocked by Prometheus/Datadog and allowed by the others — same operator
config, two verdicts. Low impact (these ranges are rarely a useful SSRF target),
but it is concrete policy inconsistency and a maintenance hazard.

**Minimal fix:** pick one policy in the shared guard (F1). `not is_global` is the
stricter, recommended choice.

### F6 — Elasticsearch blocklist is narrower than siblings (Low)

**Where:** `elasticsearch.py:43-50` lists only `localhost`, `metadata`,
`metadata.google.internal`, `metadata.goog` — missing `localhost.localdomain`,
`ip6-localhost` that `splunk`/`grafana_loki`/`signoz`/`jenkins` include.
Loopback IPs are still caught by the range check, so the gap is the *named*
loopback aliases only.

**Fix:** shared guard (F1) eliminates this.

---

## 3. Hardening / contract notes (not bugs as shipped)

### F7 — Beats parser has no byte cap of its own (hardening)

`ingest_formats.parse_beats_lumberjack` (`:208`) only caps **event count**
(`len(frames) > MAX_EVENTS`). Unlike `parse_fluent_forward`/`parse_gelf` (which
take raw `bytes` and enforce `MAX_PAYLOAD_BYTES` at `:126`/`:289`), this one takes
an already-decoded `list[dict]`, so the **byte ceiling is the caller's job**. If
the unreviewed receiver decodes Lumberjack frames into this list *without* a size
guard first, a hostile window could allocate large memory before the event-count
check. **Action:** verify the receiver caps bytes before calling this; add a test
once the caller is found. The other two parsers are well-bounded (size cap first,
`MAX_EVENTS` break in the loop, bounded GELF reassembly with `seq_count`/
duplicate-seq checks at `:353-369`).

### F8 — Two `query()` resilience contracts coexist (hardening)

`base.Source.query` docstring says "return a list of normalized-record dicts" and
most connectors honor "never raise" (return an error record). But
`splunk.query:295` raises `RuntimeError`, `azure_monitor._post_kql:205` raises
`RuntimeError`, and `elasticsearch.query:295-298` raises `ConnectionError`. This is
**safe only because `Observability.find` wraps every source in try/except**
(`base.py:215`). Any **direct** caller of `connector.query(...)` (e.g. a router
that bypasses the facade — unverified, the router was not located) gets an
exception, and via F3 a potentially secret-bearing one. **Action:** standardize on
"query never raises, returns an error record," or document that all callers must
go through the facade, and confirm the router does.

### F9 — Live default transports re-resolve DNS / follow redirects (hardening)

`github_actions._default_http_get:99` and `jenkins._default_http_get:115` use
`urllib.request.urlopen`, and `elasticsearch/azure_monitor` default to `httpx`.
The SSRF guard validates the **literal host at construction**; the live transport
then (a) resolves DNS at call time (DNS-rebind window — the guard explicitly does
not resolve, so a name that passed can map to `169.254.169.254`), and (b) `urllib`
**follows redirects by default**, so a backend 302 to an internal URL is fetched
unchecked. The connectors document this as accepted residual risk ("the connector
layer owns egress policy"), and tests inject mocks so the live path is rarely
hit — but the shipped default is exploitable if pointed at a hostile backend.
**Action (defense-in-depth):** in the default transports, disable redirect
following (or re-run the host guard on each redirect target), and consider a
pinned-resolver / connect-time IP re-check. Out of scope for "no DNS" connectors
by design, so filed as hardening, not a bug.

---

## 4. Modules that are clean (as read)

- **`normalize.py`** — genuinely secret-safe: `bundle_credentials`/`_iter_env_names`
  collect only `*_env` **names**, never dereference. Every public fn is total
  (`isinstance` guards, no raise). No injection surface (pure dict folding). No
  finding.
- **`ingest_formats.py`** parsers — fail-soft, byte-capped (fluent/gelf),
  event-capped, bounded GELF reassembly. Only the Beats caller-dependency note
  (F7). No raise, no eval, no `__import__` of attacker data (msgpack import is
  guarded). Clean.
- **`jenkins.py`** — the reference implementation: `quote(job, safe='')`,
  default-deny on empty host, Basic-auth from env only, never logs the token,
  `query` returns `[]` on non-2xx (no raise, no leak). Cleanest connector read.
- **`base.Observability`** correlation/fan-out logic — no injection, resilient.
  Its only issue is F3 (`raw=exc` / `{exc}` in the error record).
- **Auth handling across all reviewed connectors** — uniformly correct: tokens are
  read from `os.environ[<name_env>]` **at call time**, placed in **headers**
  (never URL query params), never stored on the instance in a way that logs, and
  the docstrings' "never logged" claim holds *except* via the F3 exception path.

---

## 5. Recommended priority order

1. **F1** (unify the SSRF guard — fixes F4/F5/F6 at once and stops future drift).
2. **F3** (scrub exception text + drop `raw=exc` at the facade — one change, all
   sources).
3. **F2** (`quote()` the GitHub `repo`/`run_id`; validate `repo` shape).
4. **Locate and review the receiver/ingest router** (the real coverage gap):
   confirm a payload-byte cap precedes `parse_beats_lumberjack` (F7), confirm
   auth on the push endpoint, and confirm it calls connectors via the facade (F8).
5. F5/F6/F9 hardening as part of the F1 unification.

## 6. What still needs grep/Bash to close out

- Enumerate and review the **~20 connectors not read** (esp. anything doing SQL,
  `subprocess`, `shell=True`, or putting a community string / password into a URL
  — none seen in the 11 read, but the AWS/SNMP/syslog families are exactly where
  that risk lives and were not reachable here).
- Find callers of `is_safe_endpoint` to confirm F1 (I assert "no callers" from the
  11 read + the rolled-guard pattern; grep would make it definitive).
- Find the **receiver router** to resolve F7/F8 and check endpoint auth + body cap.
</content>
</invoke>
