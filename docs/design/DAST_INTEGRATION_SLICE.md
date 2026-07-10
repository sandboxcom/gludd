# DAST Integration Slice (design, 2026-07-10)

Status: **design-complete, not yet implemented.** Read-only survey + design task;
no source touched. Line numbers are current-tree at authoring time (HEAD after
`0e34db68`) — re-confirm with a Read before implementing, they drift.

## 1. What exists today (survey)

**SAST is wired; DAST is a placeholder name only.**

- `src/general_ludd/project_runner/profile.py:53` and `__init__.py:9-10` already
  list `dast` as an anticipated *logical check name* in the `project.yml`
  `commands` map docstring (alongside `test`/`lint`/`build`/`typecheck`/`sast`/
  `migrate`) — but **nothing resolves, runs, or parses a `dast` check**. `make grep
  Q=dast` finds only those two docstring mentions; `make grep Q=nuclei` / `Q=zap`
  / `Q=security_scan` return nothing in `src/`.
- `src/general_ludd/project_runner/findings.py:22-51` (`parse_findings`) parses
  exactly two tool JSON shapes: `_parse_semgrep` (54-66) and `_parse_bandit`
  (69-80), dispatched by `argv[0]` basename. Output shape is
  `list[str]` of `"SEVERITY file:line rule — message"`, consumed by
  `CheckResult.findings` (`runner.py:162`) and `summarize_findings` (83-89).
  This is the finding model DAST must reuse — no new data model needed.
- `src/general_ludd/project_runner/runner.py` (`ProjectCommandRunner.run`,
  219-328) is the sandboxed exec path every check (test/lint/sast/…) already
  goes through: `shlex`-parsed argv (no `shell=True`), `allowed_exec`
  allowlist + shell-metachar rejection (`profile.py:40,89-92`), a sanitized
  minimal child env (`runner.py:70-106`, secrets never inherited —
  `_SECRET_NAME_RE` at 64-67 hard-refuses any passthrough name that *looks*
  like a secret, **regardless of the target repo's own allowlist** — this
  precedent matters for §3), `start_new_session` + `killpg` on timeout
  (339-351), bounded stdout/stderr tail capture (109-146). `parse_findings` is
  invoked automatically at 305 for any check's stdout, keyed on tool basename.
- `src/general_ludd/quality/project_gate.py` (`run_project_gate`, 35-208)
  aggregates several named checks into a pass/fail verdict; `dast` can be
  added to a target's `checks=(...)` tuple with zero changes to this file —
  it already treats any undeclared-but-requested check as `MISSING` and any
  declared check generically.
- **Finding → todo ingestion pattern already exists** for GitHub issues:
  `src/general_ludd/git_automation/issue_ingestor.py` `GitHubIssueIngestor.poll_issues()`
  (32-74) returns `list[dict]` shaped
  `{"title","description","queue","priority","work_type","source"}`;
  `event_loop/loop.py:_phase_poll_issue_sources` (4262-4286) calls it on a
  tick-interval gate, then `await self._todo_repo.create(todo)` (4278) per
  item, fail-soft (`except Exception: logger.warning`, 4280-4281/4285-4286).
  `daemon.py:1622-1642` wires the ingestor from a trusted `UserConfig.issues`
  sub-config (`issues_cfg.polling_enabled` gate) — this is the template for
  DAST's config + wiring.
- `src/general_ludd/security/ssrf.py` is the canonical SSRF module:
  `host_is_blocked` (92-142, no-DNS literal check: empty/NUL host, the
  `.localhost` TLD, `BLOCKED_HOST_NAMES` 48-59, `BLOCKED_METADATA_IPS` 65,
  any already-blocked IP literal via `_ip_addr_is_blocked` 72-89 — which flags
  private/loopback/link-local/reserved/multicast/unspecified/non-global);
  `is_url_blocked` (145-170, scheme-aware wrapper); `resolved_host_is_blocked`
  (173-… , opt-in bounded-DNS variant, fail-closed on timeout/NXDOMAIN/empty).
- **No target-app deploy/serve endpoint discovery exists.** `make grep
  Q=deploy_endpoint` / `Q=health_url` / `Q=readiness_url` / `Q=serve_url` hit
  only the unrelated GPU-compute-instance deploy router
  (`routers/compute.py` `/admin/compute/deploy`, for launching model-inference
  hardware) and `infra/local_inference.py:219-246` (health-poll pattern for
  gludd's *own* local model server — reusable as a pattern, not as code). This
  is the genuine gap: gludd has no notion of "the app I just built is now
  reachable at URL X."

## 2. Why `host_is_blocked` is the WRONG primary gate here

Every existing SSRF call site uses `host_is_blocked`/`is_url_blocked` to stop
gludd from being tricked into **fetching an attacker-chosen internal URL**
(metrics scrape, webhook fetch, connector poll). DAST is the opposite shape:
the *expected, normal* target is almost always `http://127.0.0.1:<port>` or
`http://localhost:<port>` — the app gludd itself just built and served in the
same sandbox. `host_is_blocked` denies loopback unconditionally (ssrf.py:130-131,
138-142), so calling it unmodified on the DAST target would reject the
common case and only "protect" against a target that was never reachable
anyway. The real threats for DAST are narrower:

1. A malicious/compromised **target repo's `project.yml`** hardcodes the
   scan at an internal host unrelated to what was actually deployed (lateral
   SSRF via the untrusted-input path — the same class of threat
   `_SECRET_NAME_RE` defends against for env passthrough).
2. A discovered/templated target URL resolves to a **cloud metadata
   endpoint** (`169.254.169.254`, `metadata.google.internal`, …) instead of
   the intended local app — the one class of "internal host" that must be
   hard-denied even for a scan whose whole point is hitting local/internal
   addresses.

So the design below uses **allowlist-first, metadata-hard-deny-always**,
reusing ssrf.py's granular primitives rather than its top-level convenience
functions.

## 3. Design — target discovery + validation

New module: `src/general_ludd/project_runner/dast_target.py`.

```python
def resolve_dast_target(cfg: "DastSettings") -> str:
    """Return the validated scan target URL, or raise DastTargetError."""
```

Rules, in order:

1. **Fail-closed on missing allowlist.** `cfg.enabled=True` with an empty
   `cfg.target_allowlist` refuses to run (log + raise `DastTargetError`) — an
   operator must explicitly enumerate what may be scanned. This mirrors
   `ProjectProfile.allowed_exec` fail-closed-on-empty (`profile.py:56-58`).
2. **Allowlist match (primary, operator-trusted).** `cfg.target_allowlist` is
   a list of URL prefixes/globs (e.g. `["http://127.0.0.1:8080",
   "http://localhost:*", "https://staging.internal.example.com/*"]`),
   authored in gludd's **own** trusted config (`UserConfig.dast`, §6) — never
   in the target repo's `project.yml`. `cfg.static_target_url` (or a future
   auto-discovered URL, §7 stretch) must match at least one allowlist entry
   (`fnmatch`-style prefix/glob match) or is refused.
3. **Metadata hard-deny (secondary, non-overridable).** Independent of the
   allowlist match, always reject if the resolved host is in
   `general_ludd.security.ssrf.BLOCKED_HOST_NAMES` (specifically the
   metadata-name subset: `metadata`, `metadata.google.internal`,
   `metadata.goog`, `instance-data`) or the host/IP is in
   `BLOCKED_METADATA_IPS` (`169.254.169.254`, `100.100.100.200`) — import
   these two frozensets directly from `ssrf.py` rather than calling
   `host_is_blocked` (which would also reject the intended loopback case).
   An operator cannot allowlist their way past this — mirrors `_SECRET_NAME_RE`'s
   "the isolation guarantee must not depend on the untrusted/operator input"
   posture, just applied to a hard-coded deny list instead of a regex.
4. **Scheme restriction.** Only `http`/`https` (reuse `urlsplit` + a literal
   scheme check, same as `is_url_blocked` does — no need to import it, just
   mirror the two-line check to avoid pulling in the loopback-denying path).
5. Return the validated URL string. Never touches the network in this
   function (pure string/glob matching) — same hang-safety contract as
   `ssrf.py`'s non-resolving functions.

**Test plan (§8-A):** empty allowlist refused even when enabled; exact match
passes; glob/prefix match passes; non-matching URL refused; metadata IP/name
refused even when literally present in `target_allowlist` (adversarial
operator-typo case); non-http(s) scheme refused; malformed URL refused.

## 4. Design — scan execution + argv builder

**Threat carried forward from §2 item 1:** `project.yml` commands are
arbitrary strings from an untrusted target repo. If `dast: zap-baseline.py -t
https://internal-host ...` were allowed as a literal, static command (like
`test`/`lint`/`sast` are), the target repo could point the scanner anywhere,
bypassing §3 entirely. Fix: the `dast` command must be a **template**
containing a literal `{TARGET_URL}` placeholder token, and gludd's own
builder — never `ProjectProfile.resolve_argv` — performs the substitution
*after* §3 validation.

`project.yml` (target repo, untrusted) declares e.g.:

```yaml
commands:
  dast: zap-baseline.py -t {TARGET_URL} -J zap-report.json -m 5
allowed_exec: [zap-baseline.py]
```

New function in `project_runner/dast_target.py` (or a sibling `dast.py` —
either is fine, keep with the target-resolution code since they share the
threat model):

```python
_URL_LITERAL_RE = re.compile(r"https?://")  # mirrors profile._SHELL_META_RE style

def build_dast_argv(profile: ProjectProfile, target_url: str) -> list[str]:
    if not profile.has("dast"):
        raise ProjectProfileError("no 'dast' command in project.yml")
    raw = profile.commands["dast"]
    if "{TARGET_URL}" not in raw:
        raise ProjectProfileError("'dast' command must contain a {TARGET_URL} placeholder")
    without_placeholder = raw.replace("{TARGET_URL}", "")
    if _URL_LITERAL_RE.search(without_placeholder):
        raise ProjectProfileError(
            "'dast' command may not embed a literal http(s):// URL outside "
            "the {TARGET_URL} placeholder (target repo cannot self-authorize a scan target)"
        )
    substituted = raw.replace("{TARGET_URL}", target_url)
    # Re-run the SAME shell-metachar + allowed_exec checks profile.resolve_argv
    # applies, now over the substituted string, so a hostile discovered/allow-
    # listed URL still can't smuggle metacharacters post-substitution.
    return ProjectProfile(
        name=profile.name, commands={"dast": substituted},
        allowed_exec=profile.allowed_exec, env_passthrough=profile.env_passthrough,
    ).resolve_argv("dast")
```

Building a throwaway `ProjectProfile` for the substituted string is a cheap
way to re-run `_SHELL_META_RE` + `allowed_exec` checks (`profile.py:89-105`)
without duplicating that logic — `target_url` itself was already validated
scheme/host in §3, but this catches a pathological allowlist entry that
somehow contains shell metacharacters.

Execution then goes through the **existing** `ProjectCommandRunner.run`
(`runner.py:219`) unchanged — same sandbox, same env sanitization, same
timeout/kill semantics, same automatic `parse_findings` call at 305. A DAST
scan is timeout-prone (ZAP baseline default is a few minutes); pass an
explicit `timeout_s` (e.g. 600) rather than the 120s test-run bound engine.py
uses.

**Test plan (§8-B):** missing placeholder rejected; literal URL outside
placeholder rejected (the core anti-bait-and-switch case); successful
substitution produces the expected argv; not-in-`allowed_exec` still
rejected post-substitution; shell metachars in a pathological allowlist
entry still rejected.

## 5. Design — finding parsing (`findings.py` additions)

Add two tool parsers alongside `_parse_semgrep`/`_parse_bandit`
(`findings.py:54-80`):

- **ZAP baseline** (`argv[0]` basename `zap-baseline.py` or `zap`): JSON via
  `-J report.json` has shape `{"site": [{"alerts": [{"riskdesc": "High
  (Medium)", "name": ..., "desc": ..., "instances": [{"uri": ..., "method":
  ...}], ...}]}]}`. `_parse_zap_baseline(doc)` iterates
  `doc.get("site", [])` → `alerts` → `instances`, emits one finding per
  instance: `f"{sev} {uri} {name} — {desc[:200].strip()}"` where `sev` is the
  first word of `riskdesc` upper-cased (`"High (Medium)"` → `"HIGH"`).
- **nuclei**: JSONL, not a single JSON document (one object per line via
  `-jsonl -o out.jsonl`, or streamed to stdout with `-json`) — this breaks
  `parse_findings`'s current `text.startswith("{")` single-document
  assumption (`findings.py:34`). Add a **pre-dispatch branch**: if
  `tool.lower() == "nuclei"`, skip the single-`json.loads` path entirely and
  call `_parse_nuclei_jsonl(text)`, which splits on `\n`, `json.loads`s each
  non-empty line in its own `try/except` (one bad line never drops the rest),
  and emits `f"{sev} {matched-at} {template-id} — {name}"` where `sev` comes
  from `info.severity` upper-cased.
- Register both basenames in the `parse_findings` dispatch (`findings.py:43-50`),
  same fail-soft contract (`except Exception: return []`).

Both stay inside the existing `list[str]` finding shape — `CheckResult`,
`summarize_findings`, and every downstream consumer need zero changes.

**Test plan (§8-C):** `tests/unit/test_project_dast_findings.py` mirroring
`tests/unit/test_project_findings.py` structure — valid ZAP doc → correct
severity/uri/name extraction; empty `site`/`alerts`/`instances` → `[]`;
truncated/non-JSON → `[]`; nuclei JSONL with one malformed line among three
valid ones → the two valid findings still parsed; nuclei severity
case-folding.

## 6. Design — finding → todo ingestion + event-loop wiring

New module: `src/general_ludd/security/dast_ingestor.py`, structurally
mirroring `GitHubIssueIngestor` (`issue_ingestor.py:11-74`):

```python
class DastFindingIngestor:
    def __init__(self, *, project_name: str, todo_severity_threshold: str = "MEDIUM",
                 seen_ids: set[str] | None = None) -> None: ...

    def findings_to_todos(self, check_result: CheckResult, target_url: str) -> list[dict[str, Any]]:
        """Filter check_result.findings by severity >= threshold, dedup via a
        (rule-id, location) hash against self._seen_ids, and emit todo dicts:
        {"title": f"[DAST] {rule} — {loc}", "description": full finding line,
         "queue": "core", "priority": "high"|"medium", "work_type": "security_fix",
         "source": f"dast:{project_name}:{sha256(rule+loc)[:12]}"}
        """
```

Severity ordering `{"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}` (bandit's
existing severity vocabulary — ZAP/nuclei parsers normalize into the same
four buckets in §5). Findings below threshold still appear in the
`CheckResult`/gate report (§1's `run_project_gate`) for visibility, just
aren't auto-promoted to todos — avoids flooding the queue with informational
noise while still surfacing it to a human/gate reviewer.

**Event-loop wiring** — new phase in `event_loop/loop.py`, placed after
`_phase_poll_issue_sources` (4262-4286), same shape:

```python
async def _phase_run_dast_scan(self) -> None:
    if self._dast_ingestor is None or self._dast_runner is None:
        return
    self._dast_scan_tick_counter += 1
    if self._dast_scan_tick_counter < self._dast_scan_interval_ticks:
        return
    self._dast_scan_tick_counter = 0
    try:
        target_url = resolve_dast_target(self._dast_cfg)   # §3, raises DastTargetError
        argv = build_dast_argv(self._dast_profile, target_url)  # §4
        result = await asyncio.to_thread(self._dast_runner.run, "dast", timeout_s=600)
        new_todos = self._dast_ingestor.findings_to_todos(result, target_url)
        persisted = 0
        for todo in new_todos:
            if self._todo_repo is None:
                break
            try:
                await self._todo_repo.create(todo)
                persisted += 1
            except Exception as exc:
                logger.warning("Failed to persist DAST todo: %s", exc)
        if persisted:
            self._tick_metrics["dast_findings_todos"] = persisted
    except Exception as exc:
        logger.warning("DAST scan failed: %s", exc)
```

Fail-soft contract identical to `_phase_poll_issue_sources` — a scan failure
(tool missing, target unreachable, timeout) is logged and never raises past
the phase, matching the "no unseen events" + "never crash a tick" invariants
already enforced there. Route the subprocess-bound `runner.run` call through
`asyncio.to_thread` (or the `testrun_executor()` from Wave C's C-EVENTLOOP
item 12 if that lands first — same bounded-executor rationale: a slow ZAP
scan must not starve the event loop).

Insert `"run_dast_scan"` into `PHASE_ORDER` (same mechanical update C-SPD1
required for `flush_spend_ledger`: bump the 4 phase-count tests —
`test_obj04_event_loop.py`, `test_event_loop.py`, `test_audit_gaps_e2e.py`,
`test_event_loop_session_per_tick.py`).

**`daemon.py` wiring**, mirroring the issue-ingestor block at 1622-1642:

```python
dast_ingestor = None
dast_runner = None
if uc is not None:
    dast_cfg = getattr(uc, "dast", None)
    if dast_cfg is not None and getattr(dast_cfg, "enabled", False):
        from general_ludd.project_runner import ProjectCommandRunner, load_project_profile
        from general_ludd.security.dast_ingestor import DastFindingIngestor
        try:
            profile = load_project_profile(workspace_path)
            dast_runner = ProjectCommandRunner(workspace_path, profile)
            dast_ingestor = DastFindingIngestor(
                project_name=profile.name,
                todo_severity_threshold=getattr(dast_cfg, "todo_severity_threshold", "MEDIUM"),
                seen_ids=daemon_state.setdefault("dast_seen_ids", {}).setdefault(profile.name, set()),
            )
            app.state._dast_ingestor = dast_ingestor
            logger.info("DAST ingestor wired: scanning enabled for %s", profile.name)
        except ProjectProfileError as exc:
            logger.warning("DAST enabled but project.yml invalid/missing: %s", exc)
```

## 7. Config schema additions

New pydantic sub-config on `UserConfig` (wherever `issues: IssuesSettings`
lives today — same file/pattern), `dast: DastSettings`:

```python
class DastSettings(BaseModel):
    enabled: bool = False
    tool: Literal["zap", "nuclei"] = "zap"
    static_target_url: str | None = None          # Slice 1 (below)
    target_allowlist: list[str] = Field(default_factory=list)
    todo_severity_threshold: str = "MEDIUM"        # HIGH|MEDIUM|LOW|INFO
    scan_interval_ticks: int = 720                 # scans are expensive; poll rarer than issues
    scan_timeout_s: int = 600
```

`project.yml` schema addition (target repo, untrusted, unchanged
`ProjectProfile` model — just a new conventional key in `commands`):

```yaml
commands:
  dast: zap-baseline.py -t {TARGET_URL} -J zap-report.json -m 5
allowed_exec: [zap-baseline.py]
```

## 8. Staged rollout (avoid building target-discovery + scanning at once)

- **Slice 1 (ship first): operator-declared static target.** `static_target_url`
  is the only source `resolve_dast_target` accepts; no "serve the app and
  discover its port" machinery. This covers the common case (a long-lived
  staging/canary URL, or a docker-compose service gludd starts out-of-band of
  the event loop) and exercises the full §3-§6 pipeline safely.
- **Slice 2 (follow-on, NOT in this design's critical path): auto-discovery.**
  If a target's `project.yml` declares a `serve` check that backgrounds the
  built app, `resolve_dast_target` could poll a health endpoint
  (mirroring `infra/local_inference.py:219-246`'s health-poll pattern) to
  discover the live port before falling back to `static_target_url`. This
  needs a new `ProjectCommandRunner.run_background()` (start the process,
  return a handle instead of blocking on `proc.wait`) plus a teardown hook —
  real scope, deliberately deferred so Slice 1 isn't blocked on it.

## 9. Test plan summary (new files)

- `tests/unit/test_dast_target_resolution.py` — §3 (allowlist fail-closed,
  glob match, metadata hard-deny even inside an allowlist entry, scheme
  restriction, malformed URL).
- `tests/unit/test_dast_argv_builder.py` — §4 (missing placeholder, embedded
  literal URL outside placeholder, successful substitution, allowed_exec
  still enforced, metachar rejection post-substitution).
- `tests/unit/test_project_dast_findings.py` — §5 (ZAP + nuclei parsing,
  malformed-line skip, truncated/non-JSON → `[]`).
- `tests/unit/test_dast_finding_ingestor.py` — §6 (severity-threshold filter,
  dedup via seen_ids, todo dict shape/keys match `TodoRepository.create`
  expectations, `work_type="security_fix"`/`source` prefix `dast:`).
- `tests/unit/test_event_loop_dast_phase.py` — §6 (phase no-ops when
  `_dast_ingestor is None`; tick-interval gate; persists todos; scan/target
  errors caught and logged, never raised); update the 4 `PHASE_ORDER`-length
  tests listed above.
- `tests/e2e/test_dast_integration_e2e.py` — canned-JSON-fixture style (mirror
  `tests/security/test_sast.py`'s live-tool-but-deterministic-report split):
  either mock `ProjectCommandRunner.run` to return a `CheckResult` carrying a
  fixed ZAP/nuclei JSON payload as `stdout_tail` and assert the full
  parse→ingest→todo pipeline end-to-end without requiring the real binary in
  CI, or skip cleanly when the tool isn't installed (mirror `test-hooks-live`'s
  node-version skip pattern) for an optional live-tool variant.
