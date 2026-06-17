# DRY / Dead-Code / Duplication Audit — `src/general_ludd/`

Date: 2026-06-16
Scope: DRY violations, dead code, and copy-paste latent bugs in `src/general_ludd/`.
Method: read-only. Every finding below with concrete line numbers was **read firsthand**
from the named file. See "Enumeration limit" at the bottom for what could NOT be verified
and why.

## Enumeration limit (read this before trusting fleet-wide claims)

This harness exposed **no `Glob`/`Grep` tools** and the repo's make-only Bash policy
denies `grep`/`find`/`ls`; sub-agents (`Explore`) hit the same wall. As a result I could
**not** mechanically enumerate the full connector fleet. Findings are split into:

- **VERIFIED** — line numbers read directly from the file (base.py, datadog.py,
  prometheus.py, skills/fetcher.py, routers/skills.py).
- **INHERITED** — the prior audit's claim that "~32 connectors each re-implement their
  own SSRF guard / record-builder." I confirmed the *mechanism* in the two connectors I
  could read (datadog, prometheus); both reproduce the exact anti-pattern, so the
  fleet-wide generalization is credible but the per-file line clusters for the other
  ~30 connectors are NOT independently verified here. Re-run with `grep` to fill the table.

---

## Priority table

| # | Severity | Kind | Site(s) | Status | Fix |
|---|----------|------|---------|--------|-----|
| 1 | **HIGH (latent bug)** | Copy-paste drift | `skills/fetcher.py:129` ≡ `routers/skills.py:110` | VERIFIED | Extract `format_skill_md(skill)` |
| 2 | **HIGH (latent bug)** | Copy-paste drift | `skills/fetcher.py:118-130` vs `routers/skills.py:106-112` (install guard sequence) | VERIFIED | Extract `safe_install(skill, target_dir)` |
| 3 | **HIGH (large dup + drift)** | SSRF guard re-impl | `datadog.py:57-113`, `prometheus.py:49-111` ignore `base.is_safe_endpoint` | VERIFIED (2 of ~32) | Connectors call `base.is_safe_endpoint`; delete local guards |
| 4 | **HIGH (drift risk)** | SSRF policy divergence | `is_safe_endpoint` vs connector `_is_blocked_ip` disagree on `not ip.is_global` | VERIFIED | Pick one policy in `base`; see §3.1 |
| 5 | **MEDIUM (dup + drift)** | Record-builder re-impl | `datadog.py:162-172,241-256,305-316`; `prometheus.py:159-169,183-192` build the dict inline instead of `base.normalized_record()` | VERIFIED (2 of ~32) | Route every record through `base.normalized_record()` |
| 6 | **MEDIUM** | Duplicated helper | `_strip_brackets` / `_is_blocked_ip` / `_BLOCKED_HOSTNAMES` copied verbatim across connectors | VERIFIED (datadog≡prometheus) | Hoist into `base` |
| 7 | **LOW (cosmetic)** | Divergent transport Protocol | `datadog.HttpRequest` (L49) vs `prometheus.HttpGet` (L44) — two ad-hoc `Callable[...]` aliases, no shared Protocol | VERIFIED | Optional shared `HttpTransport` Protocol in `base` |
| 8 | INHERITED | Fleet SSRF/record dup | ~30 other connectors | NOT VERIFIED | Re-grep; see Enumeration limit |

---

## 1. HIGH — frontmatter string is copy-pasted (latent drift bug)

**VERIFIED.** Byte-identical f-string at two install paths:

- `skills/fetcher.py:129`
  ```python
  content = f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body}\n"
  skill_file.write_text(content)
  ```
- `routers/skills.py:110`
  ```python
  content = f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body}\n"
  with open(skill_file, "w") as f:
      f.write(content)
  ```

**Why it's a bug, not cosmetics:** the frontmatter schema is the contract the skill loader
parses back (`parse_skill_md`). The moment someone adds a field (e.g. `model:`, `allowed-tools:`)
or escapes a value at one site, the two installers emit *different* on-disk skill files
depending on whether the skill came via raw-URL (`fetcher`) or the GitHub admin route
(`routers/skills`). One path silently produces skills the loader treats differently. This is
exactly the class the prior audit already flagged.

**Fix:** one helper, both call it:
```python
# skills/format.py
def format_skill_md(skill: Skill) -> str:
    return f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body}\n"
```

## 2. HIGH — the whole install-guard sequence is duplicated (latent security drift)

**VERIFIED.** Beyond the f-string, both installers independently repeat the
*sanitize-name → is-path-within → write* guard chain:

- `skills/fetcher.py:118-130`: `_safe_skill_filename(skill.name)` → `is_path_within(target, f"{stem}.md")` → write.
- `routers/skills.py:106-112`: `_safe_skill_filename(skill.name)` → `is_path_within(target, f"{stem}.md")` → write.

**Why it's a bug:** this is the path-traversal / unsafe-filename defense. Two copies means a
hardening fix (or a new bypass discovered) at one site doesn't protect the other. Note they
already diverge in mechanics — `fetcher` uses `Path.write_text`, `routers/skills` uses raw
`open().write()`, and `fetcher` adds a separate `logger.warning` on refusal that the router
turns into an `HTTPException`. Divergence has already started.

**Fix:** a single `install_skill(skill, target_dir) -> Path | None` that owns sanitize +
within-check + frontmatter + write; both callers delegate. This also folds in finding #1.

## 3. HIGH — connectors re-implement the SSRF guard and ignore `base.is_safe_endpoint`

**VERIFIED for datadog + prometheus** (the two I could read); INHERITED for the rest.

`base.py:319` ships `is_safe_endpoint(url)` — a complete literal-host SSRF guard
(scheme allowlist, `_BLOCKED_HOST_NAMES`, full `ipaddress` classification). It is even
re-exported in `connectors/__init__.py:25,38`. Yet:

- `datadog.py` re-implements it: `_BLOCKED_HOSTNAMES` (L57-65), `_strip_brackets` (L68-72),
  `_is_blocked_ip` (L75-88), `_validate_site` (L91-113). **No import of `is_safe_endpoint`.**
- `prometheus.py` re-implements it: `_BLOCKED_HOSTNAMES` (L49-57), `_strip_brackets` (L62-66),
  `_is_blocked_ip` (L69-85), `_validate_base_url` (L88-111). **No import of `is_safe_endpoint`.**

The two connector copies of `_BLOCKED_HOSTNAMES` / `_strip_brackets` / `_is_blocked_ip` are
**verbatim identical** to each other.

**Risk if left:** four-plus copies of an SSRF guard is a security-critical DRY violation —
a CVE-class fix (e.g. a new metadata host, an IPv4-mapped-IPv6 bypass, NAT64 `64:ff9b::/96`)
must be applied in every copy or one connector stays exploitable. This is the single
highest-leverage consolidation in the connector layer.

**Fix:** connectors import and call `base.is_safe_endpoint(url)` (raising on `False`), and the
local `_BLOCKED_HOSTNAMES` / `_strip_brackets` / `_is_blocked_ip` / `_validate_*` are deleted.
If a connector needs the *normalized* URL too, add `base.validate_endpoint(url) -> str` that
calls `is_safe_endpoint` then returns `url.rstrip("/")`.

### 3.1 HIGH — the two guard implementations actually DISAGREE (drift already shipped)

**VERIFIED.** This is finding #4 and it is the reason #3 is urgent rather than cosmetic:

- `base.is_safe_endpoint` (base.py:361-368) blocks: `is_loopback`, `is_private`,
  `is_link_local`, `is_reserved`, `is_multicast`, `is_unspecified`. It **accepts** any
  non-resolving DNS name and does **not** test `not ip.is_global`.
- connector `_is_blocked_ip` (datadog.py:80-88, prometheus.py:77-85) blocks all of the above
  **plus `or not ip.is_global`**.

So the same literal IP can be **accepted by `base` and rejected by the connector** (or vice
versa for ranges that are non-global-but-not-in-base's-list, e.g. `100.64.0.0/10` CGNAT,
`192.0.0.0/24`, `198.18.0.0/15`). Today they silently enforce two different egress policies.
Consolidating (#3) also resolves this; pick `not ip.is_global` as the stricter, correct gate
and centralize it.

## 5. MEDIUM — record builders ignore `base.normalized_record()`

**VERIFIED for datadog + prometheus.**

`base.py:81` defines `normalized_record(...)` and `NormalizedRecord` (the 8-key TypedDict),
the whole point of which is that the `Observability` facade can rely on all eight keys. Yet
every connector record I read is hand-built as a literal dict:

- `datadog.py:162-172` (`_error_record`), `241-256` (logs), `305-316` (metrics).
- `prometheus.py:159-169` (`_error_record`), `183-192` (`_sample_record`).

**Risk if left:** (a) drift — datadog's metric `level_or_status` is `""`, prometheus's
error `value` is `0.0` while datadog's is `None`; these are exactly the inconsistencies the
shared builder exists to prevent. (b) if `NormalizedRecord` gains a 9th key, the facade
expects it but every inline dict omits it — a `KeyError` waiting in the merge/sort/correlate
path. (c) prometheus error records use `value: 0.0` where the contract's default is `None`,
which will pollute any metric aggregation that sums `value`.

**Fix:** replace each inline dict with `normalized_record(source=..., kind=..., ...)`. This
also makes the `value`/`level_or_status` defaults uniform for free.

## 6. MEDIUM — verbatim helper duplication (`_strip_brackets`, `_is_blocked_ip`)

**VERIFIED.** `datadog.py:68-88` and `prometheus.py:62-85` contain identical
`_strip_brackets` + `_is_blocked_ip` bodies (prometheus only adds a comment). Folded into the
#3 fix by hoisting into `base`.

## 7. LOW (cosmetic) — divergent injectable-transport aliases

**VERIFIED.** `datadog.py:49` `HttpRequest = Callable[..., "tuple[int, Any]"]` (method, url,
params, json, headers, timeout) vs `prometheus.py:44` `HttpGet = Callable[..., "tuple[int, Any]"]`
(url, params, headers). Both are stringly `Callable[...]` with no enforced signature, declared
per-connector. Low value: each is a one-liner and the signatures genuinely differ (POST+json
vs GET-only). A shared `HttpTransport` Protocol in `base` would document the contract but is
not load-bearing. List it; don't prioritize it.

---

## Dead code

**Could not be confirmed in this pass.** Identifying zero-call-site definitions requires a
cross-tree symbol grep, which the tooling blocked (see Enumeration limit). The prior audit's
known "entire connector layer is unwired" still holds structurally — confirmed indirectly:
`connectors/__init__.py:16-27` imports **only** `base`; no concrete connector
(`DatadogSource`, `PrometheusSource`, ...) is imported, registered, or re-exported anywhere in
the package init, so nothing wires them into a `SourceRegistry` at import time. That is the
known finding, not a new one.

**Candidate flagged but NOT verified:** `security/sanitize.py` `sanitize_job_id` was suspected
unused (a sub-agent saw only `sanitize_path` imported in `fetcher.py`). I could not grep the
full tree to confirm zero call sites — do not act on this without a real `grep -rn sanitize_job_id src/`.

---

## What to do first

1. **#3 + #3.1 + #6** (one change): connectors call `base.is_safe_endpoint`; delete local SSRF
   copies. Highest leverage — it's security-critical, it's the largest duplication, and the
   two copies already enforce *different* policies.
2. **#1 + #2** (one change): a single `install_skill()` owning sanitize + within-check +
   frontmatter + write. Closes the verified copy-paste-drift bug.
3. **#5**: route connector records through `normalized_record()`.
4. **#7**: optional, cosmetic.

## To complete this audit

Re-run with a working grep over `src/general_ludd/connectors/` for
`_is_blocked_ip|_BLOCKED_HOSTNAMES|_validate_|is_safe_endpoint|normalized_record|"level_or_status":`
to turn the INHERITED rows (the other ~30 connectors) into VERIFIED file:line clusters, and to
confirm/deny the `sanitize_job_id` dead-code candidate.
