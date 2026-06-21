# Overload Retry Coverage Audit
_Date: 2026-06-18_

Audit of both the **orchestrator dispatch layer** (how the Claude harness re-dispatches
subagents killed by server overload) and the **gludd product layer** (how the
`ModelGateway` retries LLM calls killed by 529/overload status codes).

The user directive: on `API Error: Server is temporarily limiting requests (not your
usage limit)` (and 529/overload/503/429), BOTH layers must KEEP RETRYING / re-dispatching
until the prompt gets through. This is a transient overload, NOT a usage limit.

---

## 1. Coverage Table

| Layer | Handles overload? | Evidence (file:line) | Gap? |
|---|---|---|---|
| **gludd — classifier** | Y | `timeout_detector.py:56` — `_RETRYABLE_SERVER_CODES = frozenset({500, 502, 503, 529})`; classifier maps 529 HTTP status to `TimeoutKind.PROVIDER_ERROR` at `_classify_http_error` line 133 | None — 529 is classified correctly |
| **gludd — retry policy cap** | Partial | `TimeoutRetryPolicy.__init__` defaults `max_retries=3`, `failover_after_retries=3` (lines 297-308); `call_model_with_retry` uses `stop_after_attempt(failover_after)=3` (gateway.py:507). Only 3 attempts before walking fallbacks. No separate high cap for overload. | Gap: 3 is low for a server overload scenario that may persist for minutes. No PROVIDER_ERROR-specific cap. |
| **gludd — watchdog / redispatch** | Gap | `scripts/agent_watchdog.py` — classifies stalled agents by file-mtime + content markers (ACTIVE / DONE / LIKELY_STALLED). No detection of the overload message text, no separate redispatch path with overload-specific high cap. | Gap: watchdog has no overload-awareness; it treats an overload-killed agent the same as any stall. |
| **AGENTS.md — dispatch policy** | Partial | `AGENTS.md` lines 341-385, "Agent At-Rest / Re-Dispatch Policy" table row: `"killed by transient API error (529/429/503)" → "Re-dispatch after backoff (exponential if it repeats)."` | Partial: no explicit high/separate cap for overload vs normal failed-agent 3-cap; does not quote the exact error string; does not explicitly distinguish overload from a true usage-quota limit; watchdog's role in detection not described. |
| **gludd — resilience/ module** | N/A | `src/general_ludd/resilience/` does NOT exist. "RetryPolicy / CircuitBreaker" referred to by the task = `TimeoutRetryPolicy` + `ModelHealthTracker` in `timeout_detector.py`. | No gap in naming — just terminology. |

### What branch `aaa92d93` still needs to cover

Branch `aaa92d93`'s stated commit is adding/strengthening overload handling on top of
the current state at `6063e51`. Based on this audit, the current state at `6063e51` has:

- [x] 529 correctly mapped to `PROVIDER_ERROR` (retryable)
- [x] PROVIDER_ERROR retried with exponential backoff (up to 3 attempts)
- [x] Fallback chain walked after primary exhaustion
- [ ] **MUST ADD**: a higher per-profile retry cap for `PROVIDER_ERROR` / `RATE_LIMITED`
  specifically — the current 3-attempt default is too aggressive for an overload that
  lasts 1-5 minutes. Recommended: `overload_max_retries=10` (or configurable) for
  `TimeoutKind.PROVIDER_ERROR`, with `max_backoff_seconds` extended (e.g. 120s vs 60s).
- [ ] **MUST ADD**: `agent_watchdog.py` should detect the overload message text in the
  output tail and classify those agents as `OVERLOAD_KILLED` (a sub-state of
  `LIKELY_STALLED_INCOMPLETE`) so the orchestrator can distinguish "overload-killed →
  retry-until-through with no cap decrement" from "genuine stall → 3-cap retry."
- [ ] **MUST ADD** (or document clearly): how the orchestrator invokes the watchdog.
  Currently `agent_watchdog.py` is a CLI script with no automatic invocation path.
  Without the orchestrator calling it after each completion notification, stalled agents
  are never detected.

---

## 2. Ready-to-paste AGENTS.md paragraph

Paste this immediately after the existing `killed by transient API error (529/429/503)` row
in the **"Agent At-Rest / Re-Dispatch Policy"** table — as a new sub-section titled
**"Overload errors: high-cap re-dispatch until through"**:

---

### Overload errors: high-cap re-dispatch until through

**`API Error: Server is temporarily limiting requests (not your usage limit)`** (and HTTP
429, 503, 529) is a **server-side overload signal, not a usage or quota failure.** The
work did NOT complete; it must be re-dispatched until it gets through.

**Orchestrator rule — re-dispatch until through, separate cap:**

1. When a subagent's completion notification contains the overload string (or a 429/503/529
   status line), **do not count the attempt against the normal failed-agent 3-cap.** Overload
   retries use a separate high cap (≥ 10 attempts, or no cap while the backoff stays finite).
2. Re-dispatch the SAME work (same prompt) with **exponential backoff starting at 15 s**:
   attempt 1 → 15 s, attempt 2 → 30 s, attempt 3 → 60 s, then cap at 120 s per retry.
3. **Never abandon overload-killed work** after a few attempts. Keep re-dispatching until
   either (a) the attempt succeeds, or (b) the error shifts to a different kind (auth
   failure / true usage exhaustion → stop immediately).
4. **Distinguish overload from a true quota limit.** A true usage limit says "you have
   exceeded your usage limit" or returns a 402. That is NOT retryable — stop and notify.
   The overload message explicitly says "(not your usage limit)" — that phrase is the
   signal to keep retrying.

**Detection engine:** `scripts/agent_watchdog.py` is the tool that classifies stalled
agents (ACTIVE / LIKELY_STALLED_INCOMPLETE / DONE). After every completion notification
in a multi-agent session, run `make watchdog-check` (or equivalent) to scan for
overload-killed agents before deciding whether to close out the work. An overload-killed
agent whose tail contains the overload message should be re-queued, not counted as failed.

**Product layer (gludd `ModelGateway`):** `call_model_with_retry` in
`src/general_ludd/models/gateway.py` uses `TimeoutRetryPolicy` from
`src/general_ludd/models/timeout_detector.py`. HTTP 529 is mapped to `PROVIDER_ERROR`
(a retryable kind) via `_RETRYABLE_SERVER_CODES` in `timeout_detector.py:56`. The
policy retries with exponential backoff (base 1 s, max 60 s). Current default cap is 3
before fallback — branch `aaa92d93` must raise this for `PROVIDER_ERROR`/`RATE_LIMITED`
to ≥ 10 and extend `max_backoff_seconds` to 120 s so a sustained overload does not
exhaust the retry budget in under 3 minutes.

---

## 3. Remaining gaps requiring follow-up build

### Gap G1 — gludd product: low retry cap for overload (PROVIDER_ERROR)
**File:** `src/general_ludd/models/timeout_detector.py`, `TimeoutRetryPolicy.__init__`
**Current:** `max_retries=3`, `failover_after_retries=3`, `max_backoff_seconds=60.0`
**Required:** introduce a kind-specific cap so `PROVIDER_ERROR` and `RATE_LIMITED`
retry up to 10 times (configurable via `overload_max_retries` kwarg) with `max_backoff_seconds`
raised to 120 s. `gateway.py:call_model_with_retry` should pass `overload_max_retries`
through to the policy when the exception kind is overload-class.
**Branch:** `aaa92d93` must cover this.

### Gap G2 — agent_watchdog: no overload message detection
**File:** `scripts/agent_watchdog.py`
**Current:** `classify_tail` detects DONE vs stalled by text markers; has no knowledge
of the overload error string.
**Required:** add `_OVERLOAD_MARKERS` list (e.g. `"temporarily limiting requests"`,
`"overloaded"`, `"529"` in context of an error line) to `classify_tail`; return a new
sub-state or reason string `"overload-killed"` so the orchestrator can read it from
`--list-stalled` output and distinguish overload from a genuine stall. This enables
the orchestrator to apply the high-cap rule from §2 automatically.

### Gap G3 — watchdog invocation: no automatic path
**Current:** `agent_watchdog.py` is a standalone CLI script. The orchestrator has no
Make target or hook that calls it after completion notifications.
**Required:** add `make watchdog-check` that runs
`python scripts/agent_watchdog.py --list-stalled` against the tasks dir, prints any
overload-killed agents, and exits non-zero if any remain. The orchestrator should call
`make watchdog-check` after each multi-agent batch completes, before treating the batch
as done.

### Gap G4 — AGENTS.md: exact error string not quoted (existing text)
**Current:** the table row mentions `(529/429/503)` but does not quote the exact harness
error string.
**Required:** the paragraph in §2 of this document (the ready-to-paste text) adds the
exact string `"API Error: Server is temporarily limiting requests (not your usage limit)"`
as the detection key, and explicitly contrasts it with the quota-exhausted message.
**Action:** paste §2 into AGENTS.md post-ship (not during this read-only audit session).
