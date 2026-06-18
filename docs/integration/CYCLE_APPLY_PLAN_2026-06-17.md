# Remaining-Cycles Apply Plan — exact edits (2026-06-17)

State: batch-2/3a/3b committed (`f0dc262`,`a2f0346`,`5218549`). Cycle A part 1
(gated_commit/gated_merge port) gating via `make ship` (bwa7f9typ). Apply the rest
one `make ship` per cycle, in this order. All anchors verified against current code.
NOTE: subagent fleet rate-limited until 9am ET — apply inline on the main thread.

## Cycle A part 2 — ansible gated op + role (after part 1 lands)
- Extend `collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_git.py`:
  add `gated_commit` + `gated_merge` to `op` choices; add `gate_cmd` (list, required_if
  for those ops), `source`/`target`; handler calls
  `GitAutomation(path).gated_commit(files=[], message, gate_cmd)` /
  `.gated_merge(source, target, gate_cmd)`; on `not result.success` →
  `module.fail_json(**error_result(f"gate failed rc={result.gate_returncode}: {result.message}"))`;
  on success `module.exit_json(**ok_result({"commit_sha": result.commit_sha, "gate_returncode": result.gate_returncode}, changed=True))`.
  **USE result.commit_sha (NOT result.sha).** Fail-closed; no stub validation.
- New role `roles/validate_and_push_gated_commit/{tasks/main.yml,defaults/main.yml}`
  (gate_cmd default ['make','gate']; enable_push default false; fail-closed via module).

## Cycle 3 — is_path_within → is_join_within (4 files, atomic)  [VERIFIED SAFE]
- `security/auth.py`: rename `def is_path_within(base, candidate)` → `def is_join_within(...)`;
  add directly below: `# Back-compat alias\nis_path_within = is_join_within`.
- `security/__init__.py`: import tuple add `is_join_within` (keep `is_path_within`); `__all__` add `"is_join_within"`.
- `skills/fetcher.py`: import line `is_path_within`→`is_join_within`; call site (`if not is_path_within(str(target), f"{stem}.md"):`) → `is_join_within`.
- New test `tests/unit/test_security_auth_rename.py`: is_join_within callable; `is_path_within is is_join_within`; join_within(tmp,"sub")=True, (tmp,"../x")=False; exported from `general_ludd.security`.
- Alias is the SAME function object (no arg-order change). sanitize.py's separate is_path_within(candidate,root) is untouched.

## Cycle 4 — cassandra/clickhouse SSRF guard
- `connectors/cassandra_stats.py` _build_default_executor: before `url = self._jmx_url`/token read, insert
  `from general_ludd.connectors.base import is_safe_endpoint` then `if not is_safe_endpoint(self._jmx_url): self._driver_error="unsafe endpoint"; return None`.
  **Import is `general_ludd.connectors.base` (NOT bare `connectors.base`).**
- `connectors/clickhouse_stats.py` _build_default_executor: same guard on `self._url` before password read.
- New test `tests/unit/test_connector_ssrf_guard.py`: localhost jmx_url/url → _build_default_executor None + _driver_error set; injected executor → _get_executor returns it (bypass). Constructors: `(config=, executor=)`.

## Batch 4 — confirmed-real fixes (ship in 3 cycles: 4A isolated, 4B SSRF/redirect, 4C budget/ceiling)
Real file paths (verified — earlier guesses were wrong):
- **sanitize NUL**: `security/sanitize.py` sanitize_path — after `if not cleaned: return None` add `if "\x00" in cleaned:\n        return None`. Test test_sanitize_path_nul.py.
- **cosign chmod**: `secrets/cosign.py` generate_cosign_key (L89-90) — after writing cosign.key add `os.chmod(os.path.join(output_dir, "cosign.key"), 0o600)`. Test (importorskip cryptography) asserts mode 0o600.
- **compute SSRF**: `routers/compute.py` admin_register_compute_endpoint — after `url=req.get("url","")` add `from general_ludd.connectors.base import is_safe_endpoint` + `if not is_safe_endpoint(url): raise HTTPException(status_code=400, detail="Invalid endpoint URL: must be a safe HTTP(S) URL")`. Test uses `detail.lower()` in assertion.
- **healthz/readyz**: `daemon.py` (L1070-1128) — `psk = getattr(app.state,"_psk","")`; gate `reason`+`budget` behind `if psk:` in /healthz; gate `reason` behind psk in /readyz degraded. Keep status/no_auth/require_auth/auth_degraded/budget_exhausted public. test_daemon.py::test_healthz_endpoint stays green (only asserts no_auth/auth_degraded).
- **hooks SSRF+redirect**: `events/hooks.py` _fire_webhook (L154-170) — guard `config.url` via `is_safe_fetch_url` (import from general_ludd.security) before POST + add `follow_redirects=False` to httpx.post.
- **pagerduty/opsgenie redirect**: `connectors/{pagerduty.py:110,opsgenie.py:126}` _DefaultTransport.get — add `follow_redirects=False` to httpx.get.
- **monday redirect**: `issue_sources/monday.py` _default_transport — replace urlopen with a build_opener(_NoRedirectHandler) opener (raises HTTPError on redirect). Real class MondayIssueSource; transport-injection test.
- **per-job budget**: `models/job_invocation.py` add `budget_remaining: float=float("inf")` param + pass to call_model; `event_loop/loop.py` (~L944) compute `budget_remaining = max(0.0, status["daily_limit"]-status["daily_spend"])` from self._budget_guard.get_status() (guard None→inf) and pass; worker/app.py thread it. (get_status returns daily_limit+daily_spend — verified.) call_model returns content; NOT check_all_limits.
- **dispatcher ceiling**: `agents/dispatcher.py` __init__ add `global_task_limit: int=100` + `self._global_semaphore=asyncio.Semaphore(global_task_limit)`; wrap dispatch_one body in `async with self._global_semaphore:` (outside per-agent sem). Test: global_task_limit=2, 20 tasks slow executor, peak<=2; asyncio_mode=auto so no decorator; AgentConfig(name,description,type=AgentType.PRIMARY,max_concurrent=5) from agents.types.
- **prometheus hardening**: `connectors/prometheus.py` add `import math`; in _sample_record after `value=float(raw_value)` add `if not math.isfinite(value): value=0.0`; in _normalize cap at MAX_RESULT_SIZE=10000 (+1 error record). Record value key is "value"; data record level_or_status=="".
- **base_url property**: `issue_sources/base.py` __init__ store `self._base_url`; add `@property base_url` getter + setter that VALIDATES BEFORE ASSIGN (guard candidate, only set if passes → preserves old value on rejection). IssueSource is concrete. ValueError msg: `refusing internal base_url host: ...`.

## Also-confirmed backlog (lower priority, real)
- signing.py cosign private key world-readable (same chmod pattern). tool_adapter permission delegation (verify dispatch re-validates). benchmark recorder unguarded write. issue_sources base.py base_url (above).
