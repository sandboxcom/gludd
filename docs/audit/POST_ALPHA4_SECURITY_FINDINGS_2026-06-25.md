# Post-alpha.4 Security Findings — 2026-06-25

Consolidated, CONFIRMED security findings from this session's audits. **All entries
are POST-COMMIT backlog — none block the alpha.4 release.** Apply after ship.

Every source path below was verified to exist at authoring time. Line numbers are
approximate (`~`) and MUST be re-pinned against a fresh read at apply time.

## Priority table

| ID | Sev | Area | File | Summary |
|----|-----|------|------|---------|
| CI-1 | **CRITICAL** | Completion-integrity / daemon wiring | `daemon.py:893-908`, `worker/app.py:70`, `routers/models.py:124/431/571` | Production daemon cannot make live model calls — `_model_gateway` built with `provider_registry=None`; no registry fallback → first live call raises `ValueError: "No provider registry configured"`. |
| F-E | HIGH | Gateway / SSRF | `src/general_ludd/models/gateway.py` (~985) | Fail-open SSRF: bare `except (ValueError, ImportError)` swallows the SSRF-guard ValueError, masking the egress block. |
| F-F | HIGH | Gateway / budget | `src/general_ludd/models/gateway.py` (~915) | Budget bypass: bare `except Exception` in `_walk_fallbacks` swallows `BudgetExceededError`. |
| SU-A | HIGH | Self-update auth | `src/general_ludd/self_update/apply.py` (~186) | Approval truthiness: `bool(request.approval_token)` — any non-empty string approves. |
| SU-B | HIGH | Self-update auth | `src/general_ludd/self_update/apply.py` (`apply_plan`) | `plan.requires_approval` never read → SECURITY CONFIG VALUE_EDIT auto-applies. |
| compute-CIDR | HIGH | Infra exposure | `src/general_ludd/infra/compute.py:68` | `allowed_cidr` defaults to `0.0.0.0/0` → inference VM open to the internet. |
| connectors transport-drop | MEDIUM | Connectors | `src/general_ludd/connectors/*` | 8 connectors silently dropped from `list_sources()` (registry calls `factory(config)` single-arg). |
| applier-C | MEDIUM | Self-update TOCTOU | `src/general_ludd/self_update/applier.py` (~244/~284) | `_first_escape` resolves symlinks at check, write uses raw path. |
| applier-D | MEDIUM | Self-update fail-open | `src/general_ludd/self_update/applier.py` (~153) | `_first_protected` `.resolve()` failure falls back to lexical path. |
| daemon-bind | MEDIUM | Network exposure | `cli.py:250`, `tui/runner.py:91`, `tui/keybindings.py:1280` | Daemon host defaults to `0.0.0.0`. |
| connectors NaN | MEDIUM | Connectors | `src/general_ludd/connectors/*` | 5 incompatible NaN/Inf policies across metric connectors. |
| HF-B615 | LOW | Model download | `src/general_ludd/models/model_registry.py` (`download()`) | No pinned `revision=` (operator-only repo_id). |
| B310 urlopen | INFO | Connectors | `src/general_ludd/connectors/*` (17 sites) | Scheme-guarded via `security/ssrf.py`; annotation only. |
| B314/B405 XML | INFO | XML parse | (`ET.parse` of `coverage.xml`) | Trusted local input; annotate or use defusedxml. |
| slurm output | LOW | Infra | `src/general_ludd/infra/slurm.py` | `--output` path lacks newline rejection. |
| loop except-pass | LOW | Logging | `src/general_ludd/event_loop/loop.py` (~114/~121/~283) | Genuinely silent swallows; add log lines. |

---

## CRITICAL — functional / completion-integrity blocker

### CI-1 — production daemon cannot make live model calls (provider_registry=None)

`daemon.py:893-908` builds `app.state._model_gateway` with `provider_registry=None`.
`ModelGateway.call_model` / `_invoke_and_bill` (`gateway.py:441-444`) has **NO registry
fallback** → it raises `ValueError: "No provider registry configured"` on the first live
call. `routers/models.py admin_models_call` reuses that None-registry gateway: its rebuild
at ~430-439 is guarded by `if gateway is None`, so for a non-None (but unwired) gateway the
rebuild is **skipped**. The 3 `ProviderRegistry()` construction sites in `models.py`
(124 / 431 / 571) build **EMPTY** registries (`register_provider` is never called anywhere
in `src/`). `worker/app.py:70` has the identical defect.

**NET:** both the existing `/admin/models/call` (`gludd_model_call`) **and** the new
`/admin/models/workflow` raise on the first LIVE call in a real daemon.

The prior "live z.ai proven" evidence was `tests/e2e/test_zai_daemon_http.py`, which
pre-seeds a **wired** gateway (`_build_zai_gateway` calls `register_provider`) and **SKIPS
lifespan** — so it validated the `ModelGateway` class, not the daemon's runtime wiring.

**Impact:** the flagship "AI does jobs with models" capability is non-functional live; this
also blocks the new langchain / langgraph ansible feature.

**Fix:** register each profile's provider into a `ProviderRegistry` at daemon startup (and
in the worker) — e.g. add a `ProviderRegistry.from_profiles(profiles)` factory, used at
`daemon.py:899`, `worker/app.py:70`, and `models.py:124/431/571`. **Post-commit**
(`daemon.py` + `worker/app.py` are in the pending alpha.4 commit).

---

## HIGH

### F-E — gateway fail-open SSRF
`models/gateway.py` `_try_call_model` (~985): a bare
`except (ValueError, ImportError): return None` swallows the SSRF-guard `ValueError`
→ control falls open to the next fallback profile, masking the egress block.

**Fix:** add `class SSRFRejectionError(ValueError)`, raise it at the SSRF guard
(~461), and `except SSRFRejectionError: raise` **before** the bare catch. Mirrors the
existing D-24 `BudgetExceededError` re-raise pattern.

### F-F — gateway budget bypass
`models/gateway.py` `_walk_fallbacks` (~915): a bare `except Exception` swallows
`BudgetExceededError` → the harness spends past the per-profile budget ceiling on the
fallback path.

**Fix:** `except BudgetExceededError: raise` before the bare except.

### SU-A — self-update approval truthiness
`self_update/apply.py` (~186): `has_approval = bool(request.approval_token)` — any
non-empty string is treated as a valid approval.

**Fix:** use `hmac.compare_digest` against a configured secret (e.g. via
`security/auth.py verify_psk`); fail-closed when the secret is unset.

### SU-B — requires_approval unenforced
`self_update/apply.py apply_plan` never reads `plan.requires_approval` (set by
`classifier.py:283`) → a SECURITY-subsystem CONFIG `VALUE_EDIT` auto-applies with no
approval.

**Fix:** add an `if plan.requires_approval and not has_approval: return AWAITING_APPROVAL`
guard.

### compute-CIDR — open inference VM
`infra/compute.py:68`: `allowed_cidr` defaults to `0.0.0.0/0` → an unauthenticated
inference VM is reachable from the whole internet.

**Fix:** restrictive default + explicit opt-in for `0.0.0.0/0`.

---

## MEDIUM

### connectors transport-drop
8 connectors (`prometheus`, `datadog`, `thanos`, `nagios`, `nats`, `kafka_exporter`,
`rabbitmq`, `splunk_observability`) hard-require an injected transport, but the registry
calls `factory(config)` with a single arg → they are silently dropped from
`list_sources()`.

**Fix:** give each a default urllib transport, mirroring the
`gitlab_ci` / `github_actions` pattern.

### applier-C — TOCTOU
`self_update/applier.py` `_first_escape` (~244) resolves symlinks at check time, but
the write (~284) uses the raw path.

**Fix:** return and write the resolved paths; use `O_NOFOLLOW` in the writer.

### applier-D — fail-open on resolve error
`self_update/applier.py` `_first_protected` (~153): a `.resolve()` failure falls back
to the lexical path.

**Fix:** fail-closed (return the path on resolve error).

### daemon-bind
Daemon host defaults to `0.0.0.0` (`cli.py:250`, `tui/runner.py:91`,
`tui/keybindings.py:1280`).

**Fix:** default to `127.0.0.1` + opt-in via env/flag.

### connectors NaN divergence
5 incompatible NaN/Inf policies across the metric connectors.

**Fix:** a shared `sanitize_metric_value()` → finite float or `None`.

---

## LOW / INFORMATIONAL

### HF-B615 — unpinned HF revision
`models/model_registry.py download()` lacks a pinned `revision=` (operator-only
`repo_id`, so low severity).

**Fix:** thread an optional `revision` through + warn when unpinned.

### B310 — urlopen
All 17 connector `urlopen` sites are scheme-guarded via `security/ssrf.py`
`is_url_blocked`.

**Fix:** annotate `# nosec B310`; optionally push the guard down to the per-call
boundary.

### B314 / B405 — XML parse
All `ET.parse` targets are the trusted local `coverage.xml`.

**Fix:** annotate `# nosec`, or swap to `defusedxml`.

### slurm `output` directive
`infra/slurm.py` `--output` path lacks newline rejection (auth'd-admin surface, not
shell injection).

**Fix:** wire `output` into `_validate_submit_params`.

### loop.py except-pass logging
Add `logger.warning` / `logger.debug` at `event_loop/loop.py` ~114, ~121, ~283 — these
are genuinely silent swallows.

---

## ALREADY-FIXED / NOT-REPRODUCED

Recorded so they are not re-investigated:

- **SF1 slurm command injection** — SAFE: arg-list `sbatch`, no shell.
- **SF2 self-update auth** — COVERED by PSK middleware.
- **gateway empty-200 guard** — present.
- **daemon default_registry** — used.
- **health_tracker ordering** — correct.
- **local_inference `--wrap`** — validators reject all metacharacters.
