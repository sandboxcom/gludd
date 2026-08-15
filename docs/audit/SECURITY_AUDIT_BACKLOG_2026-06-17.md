# Security Audit Backlog — 2026-06-17

Broad read-only sweep of the gludd repo. All findings below are FUTURE/deferred work;
none were addressed in integration batch 2 or batch 3.

---

## Findings

### 1. Capability-gate gap in tool dispatch
- **File:** `execution/tool_loop.py:118`
- **Issue:** `call_tool()` receives a `server_id` but the transport layer does not verify the
  resolved tool is actually owned by that server. A crafted request could invoke a tool
  registered to a different server under a trusted server's identity.
- **Severity:** Med
- **Fix:** Assert `tool.server_id == resolved_server_id` before dispatching.

---

### 2. Mutable `base_url` bypasses URL guard
- **File:** `issue_sources/base.py:~593`
- **Issue:** `self.base_url` is a plain mutable attribute. A subclass (or monkey-patch) can
  overwrite it after `__init__` runs, silently bypassing `_guard_base_url` and opening SSRF
  or redirect-to-attacker-controlled-host vectors.
- **Severity:** Low (no current subclass mutates it)
- **Fix:** Re-run the guard inside `fetch()`, or convert `base_url` to a `@property` with a
  guarded setter.

---

### 3. Self-attesting release manifest (no signature chain)
- **File:** `runtime/release.py:~52`
- **Issue:** `CHECKSUMS.sha256` is read and trusted without any GPG signature or HMAC
  chain-of-custody verification. A compromised manifest file would pass undetected.
- **Severity:** Med
- **Fix:** GPG-verify or HMAC-verify the manifest before trusting its contents.

---

### 4. Server-Side Template Injection (SSTI) risk in variable render
- **File:** `dispatch/variable_store.py:70-79`
- **Issue:** `render()` compiles its `template` argument via Jinja2 `from_string()`.
  If any caller ever passes an untrusted template string (e.g., user-supplied), this is a
  full SSTI.
- **Severity:** Low (current callers pass trusted templates)
- **Fix:** Switch to `jinja2.sandbox.SandboxedEnvironment`, OR add an explicit comment
  documenting that callers MUST supply only trusted, developer-authored template strings.

---

### 5. Raw exception logged — may embed secret values
- **File:** `secrets/migration.py:63`
- **Issue:** `logger.warning("...%s", exc)` logs the full exception representation. Backend
  exception messages from secrets stores (Vault, AWS SSM, etc.) routinely embed the
  offending secret value or token in the error string.
- **Severity:** Med
- **Fix:** Log `type(exc).__name__` (and optionally a static context string) instead of `exc`.

---

### 6. PSK Authorization header missing on internal reload POSTs
- **File:** `reload/worker_broadcast.py:58-79`
- **Issue:** Daemon-to-worker `/admin/reload` and `/admin/models/sync` POST requests are
  sent without an `Authorization: Bearer <GLUDD_AUTH_PSK>` header, while the worker endpoint
  enforces PSK auth. Reloads silently 401 and the worker never applies them — auth/functional
  gap.
- **Severity:** Med
- **Fix:** Attach `Authorization: Bearer {GLUDD_AUTH_PSK}` to the outgoing `httpx` requests in
  `worker_broadcast.py`.

---

## Sweep Coverage

The broad read-only sweep also examined: MCP server/client layer, execution-engine and
worktree jails, scheduler, observability pipeline, ingest layer, database layer (alembic +
ORM), and config loader. No new security issues were identified in those areas — they are
considered sound as of this sweep.

---

*6 findings total. All deferred. Sweep date: 2026-06-17.*
