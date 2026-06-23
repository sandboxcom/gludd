# Detecting Individual Test Failures During an In-Progress GitHub Actions Run

**Research date:** 2026-06-21
**Question:** What is the cleanest, most reliable way to see the *first failing test within ~1–2 min of it failing*, while a 30–40 min CI run is still in progress?

**TL;DR**
- The REST **logs endpoint returns 404 mid-run** — there is no public live log API (no SSE, no WebSocket).
- `gh run watch` polls **status only** (15s default); it shows step pass/fail progression, never log content.
- **Check-run annotations** (from `::error::` workflow commands) are the only structured signal that appears *during* a running job. They populate per-step/per-emit as the runner forwards them — **mid-run, not only at completion** — but exact flush granularity is undocumented; treat as "appears within seconds-to-a-step, not guaranteed per-line."
- **Recommended:** shard the test job (matrix, `fail-fast`) **+** emit per-test `::error::` annotations via `pytest-github-actions-annotate-failures` **+** poll the jobs list and check-run annotations every **30s**. Add `workflow_job` webhooks if a public receiver is available, with polling as the reconciliation fallback.

---

## 1. Live job logs mid-run

### `GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs`

**Returns 404 while the job is `in_progress`.** Confirmed behavior, not a bug.

- Both the job-level (`/actions/jobs/{job_id}/logs`) and run-level (`/actions/runs/{run_id}/attempts/{n}/logs`) endpoints return **404 Not Found** until the job/run reaches a terminal state (success/failure/cancelled).
- Once complete, the endpoint returns a **302 redirect** to a signed CDN (Azure Blob) URL valid for ~1 minute — follow it immediately (`curl -L`, or let `gh api` follow the redirect).
- **Behavior changed in 2024.** Previously the endpoint returned partial logs (runner setup, early step output) during a run; that no longer works — it now 404s until done (community Discussion #154834, #75518).

### `gh run view --log`

Hits the same REST logs endpoint. For a running job it **fails** (404). `--log-failed` likewise only works post-completion. It does not block/wait for completion — it queries once and surfaces whatever the API returns.

### `gh run watch`

**Polls job/step status; does NOT stream logs.** Default interval 15s (`--interval`). It shows which step is executing and per-step pass/fail, but emits **no log content**. The gh maintainers stated explicitly (cli/cli#3484, discussion #11893): *"There's no API support for streaming workflow run logs."* You can chain `gh run watch && gh run view --log`, but the log view only happens after completion.

### Is there a live-streaming endpoint?

**Not publicly.** The **web UI** does stream live logs (GA April 2024: "streaming logs with backscroll" — ~1000 buffered lines + live tail). That uses GitHub's **internal** frontend transport (SSE/WebSocket/long-poll, undocumented), fed by the runner streaming logs to GitHub's backend in real time. **None of this is exposed via the public API.** actions/runner Discussion #917 confirms there's no supported way to tap the live runner log stream.

| Method | Works mid-run? | What you get |
|---|---|---|
| `GET /actions/jobs/{job_id}/logs` | **No — 404** | 302 → signed CDN URL (completed only) |
| `GET /actions/runs/{run_id}/attempts/{n}/logs` | **No — 404** | Same |
| `gh run view --log` | **No — fails** | Same 404 endpoint |
| `gh run watch` | Yes, **status only** | Step pass/fail progression (15s poll), no log text |
| Web UI | **Yes — live tail** | Internal API, not public |
| Public SSE/WebSocket | **Does not exist** | — |

---

## 2. Annotations mid-run

### Verdict: **Yes — annotations appear during an `in_progress` run** (but flush granularity is undocumented)

Two distinct mechanisms:

**Checks API (direct callers / GitHub Apps).** `PATCH /repos/{o}/{r}/check-runs/{id}` with `status: "in_progress"` and `output.annotations` **appends** up to 50 annotations per call, immediately visible in the Checks tab and PR "Files changed" tab. `GET /repos/{o}/{r}/check-runs/{id}/annotations` returns whatever has been flushed so far — it does **not** wait for completion.

**`::error::` workflow commands (Actions runner).** When a step writes `::error file=...,line=...,title=...::msg` to stdout/stderr, the **runner** translates it into a check-run annotation. Reports diverge on exact timing:

- **Optimistic (well-supported):** The runner forwards `::error::` commands to the Checks API as they stream, so annotations appear in the Checks UI **while the job is still running**, within seconds of each emit. This is how `pytest-github-actions-annotate-failures` surfaces per-test failures mid-run.
- **Cautious:** GitHub does not officially document *line-by-line* flush. Some community observation is that annotations from a step become reliably queryable **after that step finishes**, not necessarily mid-step. The full set is only *guaranteed* present once the job is `completed`.

**Honest synthesis:** Annotations are the only structured failure signal available before job completion, and they do populate mid-run — but design for "appears within a step / a few seconds of the emit," not a hard real-time per-line guarantee. Poll the annotations endpoint repeatedly rather than assuming one fetch is complete.

**Caveats:**
- You must know the `check_run_id` (discover via `GET /repos/{o}/{r}/commits/{sha}/check-runs`).
- Composite actions may fail to forward `::error::` as annotations (runner issue #1742), though the text still appears in raw logs.
- JUnit-XML post-processing actions (`mikepenz/action-junit-report`, `EnricoMi/publish-unit-test-result-action`) only annotate in a **post-step after pytest exits** → strictly slower (60–180s after the run) than inline `::error::`.

---

## 3. Rate limits

- **Primary (authenticated):** **5,000 requests/hour** (PAT/OAuth/App-installation token). Unauthenticated: 60/hr (tightened May 2025). Large orgs via App tokens can reach up to 15,000/hr.
- **Secondary / abuse limits (in addition to primary):**
  - ≤ **900 points/minute** (most REST endpoints = 1 point).
  - ≤ **100 concurrent requests** (shared REST + GraphQL).
  - Mutating requests (POST/PATCH/...): ≥ **1 second between** calls recommended.
  - Violations return **403 or 429** with a `Retry-After` header.
- **Headers to watch:** `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `X-RateLimit-Used`, `X-RateLimit-Resource`, plus `Retry-After` on 403/429.
- **Conditional GETs** (`ETag`/`If-None-Match`) returning **304 do NOT count** against the primary limit — use them for cheap polling.
- No special separate quota for the Actions endpoints; they draw from the `core` bucket.

### Safe polling interval

Budget: 5,000/hr ≈ 83/min ≈ 1.38/sec.

| Interval | Polls / 30 min | % of hourly budget |
|---|---|---|
| 10s | 180 | ~3.6% (still safe) |
| **30s** | **60** | **~1.2% (recommended)** |
| 60s | 30 | ~0.6% |

**Recommendation: 30s.** Detects within ~half a minute, negligible budget impact even with 2 endpoints/poll (jobs + annotations = ~120 req/run). Use ETags. Back off to 60s if `X-RateLimit-Remaining` drops below ~500. Don't poll faster than 10s.

---

## 4. Recommendation

**Goal:** first failing test visible within ~1–2 min of it failing, reliably.

### Comparison

| Approach | Detection latency | Per-test detail | Reliability / failure modes |
|---|---|---|---|
| **(a) Shard jobs + poll job conclusion** | poll interval + teardown (~30–90s) | Job/shard-level only | High — `conclusion` is atomic, stable API. But teardown (artifact upload, cleanup) delays the signal; no test names without log/XML parse |
| **(b) Poll check-run annotations (`::error::`)** | ~15–60s per failing test | Per file+line+test | High — appears mid-run, structured API. Needs `check_run_id`; composite-action gap; flush granularity undocumented |
| **(b′) JUnit-XML post-step annotations** | 60–180s after pytest exits | Per test name | High but **slow** — runs only after the job's test step finishes |
| **(c) Poll live logs** | n/a mid-run | Per test (fragile) | **Low / unusable mid-run** — endpoint 404s until completion; even post-hoc, parsing log text is brittle and buffering breaks "real-time" |
| **(d) `workflow_job` / `check_run` webhooks** | ~5–10s normally; **up to ~40 min during GitHub incidents** | Job-level (then fetch annotations) | Medium — fastest path, but needs public HTTPS receiver, retry/dedup, and a polling fallback |

### Why (c) is out

The live-log path is structurally impossible: the REST logs endpoint **404s during the run** and there is no public stream. Log parsing is also format-fragile. Eliminate it.

### Recommended architecture

1. **Shard the test suite** via a matrix strategy (approach a). With `fail-fast: true` (default), the first shard to fail cancels the rest — limiting blast radius and making the failing shard finish (and surface its signal) sooner. Use `fail-fast: false` only if you need every shard's full failure set.
2. **Emit per-test annotations** with **`pytest-github-actions-annotate-failures`** (zero-config when `GITHUB_ACTIONS=true`; emits `::error file,line,title::` per failure). This gives file+line+test-name visibility **mid-job**, before the job concludes — the core of approach (b).
3. **Poll every 30s**: `GET /actions/runs/{run_id}/jobs` (shard status/conclusion) **and** `GET /check-runs/{id}/annotations` (per-test failures). Use ETags; watch `X-RateLimit-Remaining`.
4. **Optional fast path:** subscribe to **`workflow_job`** webhooks (preferred over `check_run`: 1:1 with Actions jobs, fires once on completion). Single-digit-second notification normally. **Keep the 30s poller as the reconciliation fallback** — the July 2024 incident showed webhook delays up to ~40 min, so webhooks alone are not safe.

**Net latency:** per-test `::error::` annotations typically surface within ~15–60s of the test failing; with a 30s poll you observe them well inside the 1–2 min target. Shard-level `conclusion` is the durable backstop; webhooks (if available) shave polling lag to seconds.

**If webhooks aren't feasible:** approach (b) alone — `pytest-github-actions-annotate-failures` + 30s annotation poll — meets the <2 min goal.

---

## Sources

**Live logs / streaming**
- Discussion #154834 — API no longer returns logs in real-time: https://github.com/orgs/community/discussions/154834
- Discussion #75518 — REST 404 while run is still running: https://github.com/orgs/community/discussions/75518
- cli/cli#3484 — log streaming for `gh run watch`: https://github.com/cli/cli/issues/3484
- cli/cli discussion #11893 — make `gh run watch` display logs: https://github.com/cli/cli/discussions/11893
- Discussion #89879 — streaming logs with backscroll (web UI): https://github.com/orgs/community/discussions/89879
- GitHub Changelog, April 2024 — Actions UI improvements: https://github.blog/changelog/2024-04-30-github-actions-ui-improvements/
- actions/runner discussion #917 — streaming runner logs elsewhere: https://github.com/actions/runner/discussions/917
- REST: workflow jobs: https://docs.github.com/en/rest/actions/workflow-jobs
- gh CLI: https://cli.github.com/manual/gh_run_watch , https://cli.github.com/manual/gh_run_view

**Annotations**
- REST: check runs: https://docs.github.com/en/rest/checks/runs
- Workflow commands: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands
- Introducing check runs and annotations: https://github.blog/news-insights/product-news/introducing-check-runs-and-annotations/
- Creating GitHub Checks (Ken Muse): https://www.kenmuse.com/blog/creating-github-checks/
- pytest-github-actions-annotate-failures: https://github.com/pytest-dev/pytest-github-actions-annotate-failures
- Composite-action annotation gap (runner #1742): https://github.com/actions/runner/issues/1742

**Rate limits**
- Rate limits for the REST API: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- Rate limits for GitHub Apps: https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/rate-limits-for-github-apps
- Updated unauthenticated rate limits (May 2025): https://github.blog/changelog/2025-05-08-updated-rate-limits-for-unauthenticated-requests/

**Failure-detection strategies / webhooks**
- Webhook events and payloads: https://docs.github.com/en/webhooks/webhook-events-and-payloads
- Mergify — real GitHub webhook latency: https://mergify.com/blog/what-github-webhook-latency-actually-looks-like/
- GitHub Availability Report, July 2024 (webhook delays): https://github.blog/news-insights/company-news/github-availability-report-july-2024/
- Matrix strategy / sharding: https://github.com/orgs/community/discussions/176052 , https://runs-on.com/github-actions/the-matrix-strategy/
