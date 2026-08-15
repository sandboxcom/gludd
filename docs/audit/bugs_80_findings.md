# Bug Audit #80 — run_history / issue_ingestor / skills.renderer (+ adjacent)

Analysis/doc-only. No code changed, no tests run (gate in flight). Every finding is
grounded in a `file:line` read during this audit. Fixes are sketches for a later
implementer — none are applied.

Modules read in full: `src/general_ludd/observability/run_history.py`,
`src/general_ludd/git_automation/issue_ingestor.py`,
`src/general_ludd/skills/renderer.py`, plus the adjacent
`skills/fetcher.py`, `skills/loader.py`, `skills/registry.py`, `skills/catalog.py`,
`skills/skill.py`, `routers/skills.py`, `routers/maintenance.py`,
`security/auth.py`, `security/sanitize.py`, and `execution/engine.py`.
Existing tests read: `tests/unit/test_run_history_coverage.py`,
`tests/unit/test_skills.py`, `tests/unit/test_v3_skills_fetcher.py`,
`tests/unit/test_w3_6_f_proofs.py`.

8 genuine bugs found (target ≥7). Ordered by severity.

---

## BUG-1 — SSTI / RCE: skill body rendered through a non-sandboxed Jinja2 Environment

- **Location:** `src/general_ludd/skills/renderer.py:56` (the `Environment(undefined=StrictUndefined, autoescape=False)` construction; render at line 59).
- **Defect class:** template injection (SSTI) → remote code execution.
- **Why it's reachable with attacker-controlled input:** `render_skill(body, ...)` is called by `execution/engine.py:48` (`_render_skill_body`) from `_build_system_prompt` (`engine.py:62`) with `job.skill_body`. Skill bodies are not authored only locally — `RemoteSkillFetcher.fetch` / `GitHubSkillSource.download_skill` (`skills/fetcher.py:84-93, 97-112`) pull a SKILL.md from a remote GitHub repo / raw URL and `parse_skill_md` (`skills/loader.py:41`) puts the post-frontmatter text verbatim into `Skill.body`. That body then becomes `job.skill_body`. A plain `jinja2.Environment` (not `SandboxedEnvironment`) evaluates arbitrary attribute access.
- **Failing scenario / trigger input:** install a skill whose body contains a classic Jinja2 SSTI payload, e.g.
  ```json
  {{ cycler.__init__.__globals__.os.popen('id').read() }}
  ```
  When a job using that skill is built, `render_skill` executes `os.popen('id')` in the daemon process. (`{{ "".__class__.__mro__[1].__subclasses__() }}` is the equivalent gadget if `cycler` is unavailable.)
- **Precise fix (sketch):** use Jinja2's sandbox.
  ```python
  from jinja2.sandbox import SandboxedEnvironment
  env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
  ```
  and catch `jinja2.exceptions.SecurityError` (alongside `UndefinedError`), re-raising as `SkillRenderError`. Optionally cap template size and disable `{% ... %}` statement blocks if only `{{ var }}` substitution is intended.
- **Regression test to add:** in a new `tests/unit/test_renderer_sandbox.py`:
  - Input: `render_skill("{{ cycler.__init__.__globals__ }}", {})` → assert it raises `SkillRenderError` (not a dict leak, not RCE).
  - Input: `render_skill("{{ ''.__class__.__mro__ }}", {})` → assert raises `SkillRenderError`.
  - Sanity: `render_skill("Hi {{ name }}", {"name": "x"}) == "Hi x"` still passes.

---

## BUG-2 — Frontmatter injection: raw `skill.name` / `skill.description` interpolated into YAML on install

- **Location:** `src/general_ludd/skills/fetcher.py:129` (`content = f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body}\n"`) and the duplicate at `src/general_ludd/routers/skills.py:110`.
- **Defect class:** injection (metadata/frontmatter injection via unescaped interpolation).
- **Why it's reachable:** `skill.name`/`skill.description` come from a *remote* SKILL.md's YAML frontmatter (`skills/loader.py:28-29`) — fully attacker-controlled. The install code sanitizes only the *filename stem* (`_safe_skill_filename`, `fetcher.py:118`) and the path-confinement check, but writes the raw `name`/`description` back into a new YAML frontmatter block. There is no escaping/quoting.
- **Failing scenario / trigger input:** a remote skill whose frontmatter sets
  ```yaml
  name: "harmless"
  description: |
    x
    model_profile: privileged-model
    tools: [shell, write]
    trigger_patterns: ["deploy"]
  ```
  After install, the written file's frontmatter contains injected `model_profile`, `tools`, and `trigger_patterns` keys. When `discover_skills` (`loader.py:55`) re-parses the installed file, those injected fields are honored — e.g. the attacker silently grants the skill new tools or a stronger model profile, or wires a trigger so it auto-activates. (A newline in `name` similarly corrupts the document.)
- **Precise fix (sketch):** serialize frontmatter with a YAML dumper instead of f-string interpolation:
  ```python
  import yaml
  fm = yaml.safe_dump({"name": skill.name, "description": skill.description},
                      default_flow_style=False, sort_keys=False)
  content = f"---\n{fm}---\n\n{skill.body}\n"
  ```
  Apply in both `fetcher.install` and `routers/skills.py`. (Better: factor a single `build_skill_md(skill)` helper so the two call sites can't drift — see BUG-8.)
- **Regression test to add:** in `tests/unit/test_skills_fetcher_install.py`:
  - Build a `Skill(name="ok", description="x\nmodel_profile: evil\ntools: [shell]")`, stub `fetch` to return it, call `install(url, tmpdir)`.
  - Re-parse the written file with `parse_skill_md`; assert `model_profile is None` and `tools == []` (injection did NOT take effect), and `description` round-trips as a single string.

---

## BUG-3 — Issue-ingestor dedup is a no-op in production: every poll re-emits all open issues

- **Location:** `src/general_ludd/routers/maintenance.py:55` (a fresh `GitHubIssueIngestor(...)` is constructed inside the request handler on every `/admin/issues/poll` call); the ineffective state lives at `src/general_ludd/git_automation/issue_ingestor.py:23` (`self._seen_ids: set[int] = set()`) and `:35-37`.
- **Defect class:** incorrect state transition / broken idempotency (dedup state is per-instance and per-instance lifetime is one request).
- **Why it's a bug:** the ingestor's whole dedup contract — proven by `tests/unit/test_w3_6_f_proofs.py:101-103` ("Second poll over the same issue is idempotent") — relies on `_seen_ids` persisting across `poll_issues()` calls. But the router instantiates a new ingestor every request, so `_seen_ids` is always empty at poll time. The unit test passes because it reuses one instance; production never does.
- **Failing scenario / trigger:** POST `/admin/issues/poll` twice for a repo with one open labeled issue. Expected (per the documented contract): second call returns `count: 0`. Actual: both calls return `count: 1` with the same issue → duplicate todos created downstream on every poll cycle.
- **Precise fix (sketch):** persist the ingestor (and thus `_seen_ids`) across requests — cache it on `app.state` keyed by `(owner, repo, label)`, mirroring `_get_catalog` in `routers/skills.py:17`:
  ```python
  key = (owner, repo, label)
  ingestor = app.state.__dict__.setdefault("_issue_ingestors", {}).get(key)
  if ingestor is None:
      ingestor = GitHubIssueIngestor(owner=owner, repo=repo, label=label)
      app.state._issue_ingestors[key] = ingestor
  ```
  (Durable dedup across daemon restarts requires persisting seen ids in the DB; out of scope for this bug, but worth a follow-up — see BUG-7 note.)
- **Regression test to add:** integration test against the real router/app:
  - Stub `GitHubIssueIngestor._fetch_labeled_issues` to always return one issue `{"id": 1, ...}`.
  - POST `/admin/issues/poll` twice with the same `{owner, repo}`; assert first response `count == 1`, second `count == 0`.

---

## BUG-4 — URL/parameter injection (and missing escaping) in the GitHub issues request

- **Location:** `src/general_ludd/git_automation/issue_ingestor.py:70-73` (the `url` f-string interpolates `self._owner`, `self._repo`, `self._label` directly into the request URL with no `urllib.parse.quote`).
- **Defect class:** injection (HTTP request / query-parameter injection) + unhandled-input class.
- **Why it's reachable:** `owner`, `repo`, and `label` flow straight from the `/admin/issues/poll` request payload (`routers/maintenance.py:56-58`, `str(payload.get(...))`). They are interpolated unescaped into both the path and the `labels=` query string.
- **Failing scenario / trigger input:** `{"owner": "o", "repo": "r", "label": "a&state=closed&foo"}` produces
  `...?labels=a&state=closed&foo&state=open&per_page=50`, smuggling extra query params (the duplicated `state` lets an attacker steer which issues are pulled). A `repo` of `"r/issues/1/comments?x="` (or containing `#`, spaces, `..`) rewrites the request target entirely. There's no `quote()` and no validation of owner/repo against GitHub's `[A-Za-z0-9._-]` charset.
- **Precise fix (sketch):**
  ```python
  from urllib.parse import quote
  owner = quote(self._owner, safe=""); repo = quote(self._repo, safe="")
  url = (f"https://api.github.com/repos/{owner}/{repo}/issues"
         f"?labels={quote(self._label, safe='')}&state=open&per_page=50")
  ```
  and/or validate owner/repo/label against `^[A-Za-z0-9._-]+$` in `__init__`, returning unconfigured on violation.
- **Regression test to add:** in `tests/unit/test_issue_ingestor_url.py`, monkeypatch `urlopen` to capture the request URL; construct `GitHubIssueIngestor(owner="o", repo="r", label="a&state=closed")`, call `_fetch_labeled_issues`; assert the captured URL contains `labels=a%26state%3Dclosed` and exactly one `state=open` (no smuggled `state=closed`).

---

## BUG-5 — `GitHubSkillSource.from_url` raises IndexError on short URLs (unhandled error → 500)

- **Location:** `src/general_ludd/skills/fetcher.py:47-49` (`parts = url.replace("https://github.com/", "").split("/")` then `owner = parts[0]; repo = parts[1]` with no length check).
- **Defect class:** unhandled error (IndexError on malformed input).
- **Why it's reachable:** `routers/skills.py:96` calls `GitHubSkillSource.from_url(f"https://github.com/{repo}")` where `repo` is request-supplied and only checked for truthiness (`routers/skills.py:94`). A `repo` with no `/` makes `parts` length 1, so `parts[1]` raises `IndexError`, which the route does not catch → an unhandled 500 instead of the intended 422.
- **Failing scenario / trigger input:** POST `/admin/skills/fetch-github` with `{"repo": "justowner", "path": "x"}` → `from_url("https://github.com/justowner")` → `IndexError: list index out of range`.
- **Precise fix (sketch):** make `from_url` defensive (return a clearly-invalid source or raise a typed `ValueError`):
  ```python
  parts = [p for p in url.replace("https://github.com/", "").split("/") if p]
  if len(parts) < 2:
      raise ValueError(f"GitHub URL must include owner/repo: {url!r}")
  owner, repo = parts[0], parts[1]
  ```
  and have `routers/skills.py` catch `ValueError` → `HTTPException(422)`. (`fetch_github_skill` at `fetcher.py:136-138` already guards `len(parts) < 2`; `from_url` should match.)
- **Regression test to add:** in `tests/unit/test_skills_fetcher_from_url.py`: assert `GitHubSkillSource.from_url("https://github.com/owner")` raises `ValueError` (not `IndexError`); and that the well-formed `"https://github.com/o/r/tree/dev/sub"` still yields `owner="o", repo="r", branch="dev", subdir="sub"`.

---

## BUG-6 — RunHistory accessors leak internal mutable state (data-dict aliasing)

- **Location:** `src/general_ludd/observability/run_history.py:29-32` (`record_event` stores the caller's `data` dict by reference), `:42` (`get_timeline` returns `list(...)` — a shallow copy whose inner event dicts are shared), and `:48-55` (`get_summary` appends those same shared dicts).
- **Defect class:** aliasing (shared mutable references across the API boundary).
- **Why it's a bug:** `record_event` does `self._timeline[job_id].append({"event_type": ..., "data": data})` — the stored `data` is the *same object* the caller passed. `get_timeline` only copies the outer list (`list(self._timeline.get(...))`); the dicts inside are the live internal objects. So a caller can mutate recorded history after the fact, and the existing test `test_get_timeline_returns_copy_not_internal_list` (`tests/unit/test_run_history_coverage.py:45-51`) only checks list-level isolation and misses this.
- **Failing scenario / trigger:**
  ```python
  rec = RunHistoryRecorder(); payload = {"model": "x"}
  rec.record_event("job-1", "call", payload)
  payload["model"] = "TAMPERED"          # mutate after recording
  assert rec.get_timeline("job-1")[0]["data"]["model"] == "x"   # FAILS — reads "TAMPERED"
  rec.get_timeline("job-1")[0]["data"]["model"] = "again"        # mutate returned copy
  assert rec.get_timeline("job-1")[0]["data"]["model"] == "x"   # FAILS again
  ```
- **Precise fix (sketch):** deep-copy on store (or on read). Cheapest correct fix is on store:
  ```python
  import copy
  self._timeline[job_id].append({"event_type": event_type, "data": copy.deepcopy(data)})
  ```
  and return copies from `get_timeline`/`get_summary` (`copy.deepcopy(...)`) so the recorder is a true immutable flight recorder.
- **Regression test to add:** extend `tests/unit/test_run_history_coverage.py`: record an event with a dict, mutate the source dict, assert the stored event is unchanged; then mutate a dict returned by `get_timeline` and assert the next `get_timeline` is unchanged.

---

## BUG-7 — `get_summary` matches todo ids by naive substring → false aggregation across todos

- **Location:** `src/general_ludd/observability/run_history.py:50` (`if todo_id in job_id:`).
- **Defect class:** incorrect state transition / logic error (substring vs. structured-key match).
- **Why it's a bug:** job ids embed a todo id (the test fixtures use the form `"TODO-42:job-a"`, `tests/unit/test_run_history_coverage.py:95`). `todo_id in job_id` is a raw substring test, so `get_summary("TODO-4")` collects every event of `TODO-42`, `TODO-40`, `TODO-411`, etc., and `get_summary("TODO-1")` collects `TODO-12`, `TODO-100`, `TODO-1:job` alike. The "summary" for one todo silently includes other todos' events — wrong counts in any UI/decision built on it. The existing test only exercises the happy 2-digit case (`TODO-42` vs `TODO-99`) and never probes a prefix collision, so the bug is uncovered.
- **Failing scenario / trigger:**
  ```python
  rec.record_event("TODO-1:job", "e1", {})
  rec.record_event("TODO-12:job", "e2", {})
  summary = rec.get_summary("TODO-1")
  assert summary["event_count"] == 1   # FAILS — returns 2 (TODO-12 matched as substring)
  ```
- **Precise fix (sketch):** match on a structured key, not a substring. If the convention is `"<todo_id>:<job>"`, compare the prefix segment:
  ```python
  if job_id == todo_id or job_id.split(":", 1)[0] == todo_id:
  ```
  (Better long-term: store the `todo_id` alongside each event at `record_event` time and group on it, removing the string-parsing entirely.)
- **Regression test to add:** in `tests/unit/test_run_history_coverage.py`: record events under `"TODO-1:a"` and `"TODO-12:b"`; assert `get_summary("TODO-1")["event_count"] == 1` and `get_summary("TODO-12")["event_count"] == 1`.

---

## BUG-8 — Install-frontmatter logic duplicated in two call sites (one already drifted)

- **Location:** `src/general_ludd/skills/fetcher.py:129` vs `src/general_ludd/routers/skills.py:110` — the *same* `f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body}\n"` string is built independently in two places.
- **Defect class:** duplication that defeats a fix (and the shared root cause of BUG-2). This is the lower-severity, correctness-adjacent item; included because it directly undermines remediation.
- **Why it matters (not style padding):** because the YAML-frontmatter build is copy-pasted, fixing BUG-2 in `fetcher.install` alone leaves `routers/skills.py` still vulnerable (and vice-versa). The two paths can — and here will — drift. The `_safe_skill_filename` / `is_path_within` guard is already correctly shared via import; the frontmatter serialization is the one piece that was inlined twice.
- **Precise fix (sketch):** extract one helper next to `_safe_skill_filename` in `fetcher.py`:
  ```python
  def build_installed_skill_md(skill: Skill) -> str:
      import yaml
      fm = yaml.safe_dump({"name": skill.name, "description": skill.description},
                          default_flow_style=False, sort_keys=False)
      return f"---\n{fm}---\n\n{skill.body}\n"
  ```
  and call it from both `fetcher.install` and `routers/skills.py`. This single-sources the BUG-2 fix.
- **Regression test to add:** a unit test asserting both `fetcher.install(...)` and the `/admin/skills/fetch-github` route produce byte-identical frontmatter for the same `Skill` (parametrize over a name/description containing a newline to also lock in the BUG-2 fix).

---

## Notes / non-bugs deliberately excluded (to avoid padding)

- `GitHubSkillSource.list_skills` / `download_skill` (`fetcher.py:65-93`) have no SSRF guard, but the target host is hardcoded to `api.github.com` / `raw.githubusercontent.com` and httpx's top-level `httpx.get` defaults to `follow_redirects=False`, so there is no reachable redirect-SSRF here. **Not** filed as a bug.
- `RunHistoryRecorder` is an unbounded in-memory dict (never evicts) — a potential slow memory-growth concern for a long-lived daemon "flight recorder," but it is in-memory-by-design with no stated retention contract, so I list it as a **watch item**, not a confirmed bug. If a retention bound is intended, add an LRU cap on `_timeline`/`_artifacts` keyed by job_id.
- `issue_ingestor` work_type mapping (`issue_ingestor.py:46-52`) is last-label-wins when an issue carries both e.g. `bug` and `docs`; this is ambiguous but not clearly incorrect (no documented precedence), so it is **not** filed.
- The GitHub fetch sends no auth token (`issue_ingestor.py:74-79`), so private repos 404 and the path is rate-limited to 60 req/h. This is a capability gap, not a correctness/security defect; **not** filed.
