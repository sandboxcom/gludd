# Browser Automation — Human-Like Web Interaction Design (2026-07-09)

Status: **design-complete, not yet implemented.** Self-contained, line-anchored
spec any implementer (human or LLM) can execute against current `master`
(HEAD `7624be03`). Line numbers are current-tree at authoring time — re-confirm
with a Read before editing, they drift. Land as an additive feature: new
dependency + new Ansible modules + new role + new high-level API package; touches
`pyproject.toml`, adds `src/general_ludd/browser/`, adds five
`gludd_browser_*` modules + a `roles/browse_web/` role. No edits to existing
guardrails — the browser rides on top of the SSRF/write-jail/secrets/human-gate
primitives that already exist.

**Goal (verbatim intent):** a gludd agent with *minimal* HTTP/HTML/web-dev
knowledge drives a real browser like a human — reads and fills forms, navigates
captchas (detect→human handoff), reads all request/response headers, sees
SSL/TLS cert data, sees every page element (embedded JSON, tables, iframes and
their nested content, downloadable raw files) — through high-level "human-intent"
verbs, never CSS selectors or DOM APIs.

---

## 0 — Current state (survey, file:line)

There is **zero** real browser-automation code in gludd today. No `playwright`,
`selenium`, `pyppeteer`, or `webdriver` dependency or import exists in `src/`,
`tests/`, or `pyproject.toml`. Every "headless" grep hit is unrelated (CI/no-TTY
terminology or a "headless state machine" game pattern). The closest prior art is
forward-looking design only: `docs/presentation/DESIGN_a11y_visual_qa_skill.md:29-287`
already proposed introducing Playwright (sync API inside `asyncio.to_thread`,
`runner.py`, `make visual-qa-install` → `playwright install chromium`), gated
behind adding the dependency (`:199,247,266,276`) and calls it "the single hard
dependency" (`docs/presentation/DESIGN_revealjs_deck.md:103,133,239`). Reuse that
dependency-gating + skip-when-Chromium-absent precedent.

What exists to build **on top of** (do not reinvent):

- **HTTP fetch (no JS):** `retrieval/web.py` `WebRetriever.fetch_web_page` (`web.py:96-200`)
  — synchronous `urllib` GET, SSRF-guarded (`web.py:121-124`), 1 MB body cap
  (`_MAX_CONTENT_BYTES`, `web.py:21,175-178`), `_NoRedirectHandler` that raises on
  any redirect to stop 302→metadata bypass (`web.py:28-58`). This is the "before"
  state; the browser extends it for JS-heavy targets.
- **SSRF guard (canonical):** `security/ssrf.py` — `is_url_blocked(url, scheme_allowlist)`
  (`ssrf.py:145-170`), `host_is_blocked(host)` (`ssrf.py:92-142`, pure string, no
  DNS, handles NUL/trailing-dot/IPv6/`.localhost`), `BLOCKED_HOST_NAMES`
  (`ssrf.py:48-59`), `BLOCKED_METADATA_IPS = {169.254.169.254, 100.100.100.200}`
  (`ssrf.py:61-65`), `_ip_addr_is_blocked` (private/loopback/link-local, `ssrf.py:72-89`),
  DNS-resolving opt-in `resolved_host_is_blocked` (`ssrf.py:173-234`). Module
  docstring: "the SINGLE source of truth" — do not add a third variant.
- **Ansible module pattern:** `collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_git.py`,
  `gludd_worktree.py`; helpers `module_utils/gludd.py` (`ok_result`/`error_result`
  at `gludd.py:50-61`, `GluddClient` PSK HTTP shim `gludd.py:119-203`).
- **Process isolation:** `ansible/core_runner.py` — `run_playbook(...)` (`core_runner.py:236-249`),
  in-process env-scrub allowlist `_PLAYBOOK_ENV_ALLOWLIST` (`core_runner.py:521-568`),
  subprocess/container backend via `ansible_runner`+podman/bwrap+seccomp
  (`core_runner.py:570-684,390-406`), fork-child SIGKILL-the-whole-group timeout
  `_run_with_timeout`/`_terminate_tree` (`core_runner.py:356-516`), L7 static
  network pre-scan `scan_playbook_tasks` (`core_runner.py:260-280`).
- **Secrets resolver:** `secrets/manager.py` `SecretsManager.resolve(alias_name)`
  (`manager.py:286-317`), `SecretAlias` (`manager.py:132-162`), fail-closed
  `SecretsUnavailableError` (`manager.py:30-39`), auto-redaction of every seen
  value from exception text (`_redact`, `manager.py:203-240`). Payment precedent
  already wired to browsers: `SecurePaymentVault.get_processor_token/get_card_last4`
  (`payment_vault.py:203,250`) consumed by `auth/browser_login.py:435-457`.
- **Human-in-the-loop:** `execution/human_gate.py` `HumanGate.await_approval(...)`
  (`human_gate.py:128-191`, LangGraph `interrupt()`, non-blocking) / `.resume(...)`
  (`human_gate.py:193-223`); durable fallback = `gludd_human_todo` module
  (`plugins/modules/gludd_human_todo.py`, `category=blocker`,
  `parent_agent_todo_id` flips parent to `blocked_on_human`) →
  `TodoStatus.APPROVAL_REQUIRED` (`schemas/todo.py:23`).
- **Write jail:** `execution/engine.py` `_resolve_in_workspace` (`engine.py:808-831`,
  `realpath`+`commonpath` containment) and Ansible-side `fs_write_policy.py`
  `WritePolicy.check` / `default_policy(...)` (`fs_write_policy.py:184-263`,
  default-DENY, sensitive-name/suffix reject at `:59-91,164-180`).

**Note:** `auth/browser_login.py` (640 lines) is **not** browser automation — it
`subprocess.Popen(["open"/"xdg-open", url])` to launch the *human's* browser for
OAuth (`browser_login.py:364-373`) and listens for the redirect on a localhost
callback (`browser_login.py:304-358`). It never controls a page. This feature is
genuinely new capability, not an extension of that file.

---

## 1 — Library choice: Playwright (Python), sync API in a worker thread

**Decision: Playwright for Python** (`playwright>=1.44`). Justification against
the requirements, point by point:

| Requirement | Playwright mechanism |
|---|---|
| Human-like typing/mouse/timing | `locator.type(text, delay=…)` per-keystroke delay; `page.mouse.move(x,y,steps=N)` stepped motion; `page.wait_for_timeout` jitter |
| Read + fill forms by label | `page.get_by_label / get_by_placeholder / get_by_role` — semantic, not selector-based |
| ALL request+response headers | `page.on("request"/"response")` + `request.all_headers()` / `response.all_headers()`; `page.route("**/*")` to intercept every request |
| SSL/TLS cert chain/validity/cipher | `response.security_details()` → `{issuer, protocol, subjectName, validFrom, validTo}`; deeper chain via CDP `Network.getCertificate` / `Security` domain |
| Embedded JSON / raw file bytes | `response.json()`, `response.body()` (bytes), `download.save_as(path)` |
| Iframes + nested content | `page.frames` (flat list incl. nested), `frame_locator()` to read into a frame |
| Accessibility-tree read (minimal HTML knowledge) | `page.accessibility.snapshot()` — the human-visible semantic tree |
| Multi-engine | Chromium / Firefox / WebKit from one API |
| Deep network/security introspection | CDP session via `context.new_cdp_session(page)` (Network/Security/Page domains) |

**Why not Selenium:** Selenium/WebDriver exposes no first-class response-body or
per-request header interception (needs a separate proxy like mitmproxy or
BiDi/CDP bolt-ons), no built-in `security_details()`, weaker download API, and no
native accessibility snapshot. Its network/security introspection — a hard
requirement here (all headers + TLS + raw bytes) — is exactly Playwright's
strength. **Why not raw CDP/pyppeteer:** unmaintained, Chromium-only, no
role/label locator ergonomics. **Why not extend `retrieval/web.py`:** urllib
executes no JS — it cannot see client-rendered tables, iframes, or captchas.

**Execution model:** use the Playwright **sync** API driven inside
`asyncio.to_thread` (matching `DESIGN_a11y_visual_qa_skill.md:199`), so the
daemon event loop is never blocked and the existing `run_on(...)`/executor
pattern applies. One browser subprocess per session (see §4).

**Binary install (the one operational cost):** Playwright needs a browser binary
(`playwright install chromium`) — the wheel does not bundle it. Fit to gludd
bootstrap:
- Add a `make browser-install` target → `playwright install chromium` (mirror the
  already-designed `make visual-qa-install`, `DESIGN_a11y_visual_qa_skill.md:247`).
- Add to `make bootstrap` as an **opt-in** step gated on `GLUDD_ENABLE_BROWSER=1`
  (Chromium is ~150 MB; do not force it on every CI shard — the a11y design flags
  this cost at `:266`).
- Every browser test is **skip-marked when the binary is absent**
  (`pytest.importorskip("playwright")` + a `_chromium_present()` guard) so the
  suite stays green on shards without it.
- Pin the browser revision via `PLAYWRIGHT_BROWSERS_PATH` inside the workspace so
  the container backend (`core_runner.py:570-684`) can mount a pre-warmed binary
  cache rather than re-downloading per run.

---

## 2 — Human-intent API layer (`src/general_ludd/browser/`)

New package `src/general_ludd/browser/`. The agent never sees a CSS selector or
DOM node — only human verbs operating on **visible labels, roles, and text** and
the **accessibility tree**. Selector/DOM handling is entirely internal.

```text
browser/
  __init__.py
  session.py     # BrowserSession — owns Playwright ctx, one browser process
  intent.py      # HumanBrowser — the verb surface below (wraps a BrowserSession)
  reader.py      # page → StructuredPage (a11y tree + tables + forms + iframes)
  network.py     # request/response capture, header + TLS extraction
  captcha.py     # detection + classification (see §3)
  config.py      # BrowserConfig schema (see §5)
  errors.py      # BrowserBlockedError, CaptchaEncountered, DownloadTooLarge, ...
```

### 2a — `HumanBrowser` verb surface (`intent.py`)

All verbs are methods on `HumanBrowser(session, config)`. Targeting is
label/role/text first; a raw selector is never a public parameter.

```python
def open_page(url: str) -> "PageView"
    # SSRF-gated navigate (§4). Waits for network-idle + a11y tree ready.

def read_page_as_structured_data() -> StructuredPage
    # The workhorse: returns visible text, tables-as-JSON, forms-described,
    # links, iframes enumerated, embedded JSON blobs. See StructuredPage below.

def fill_form(values: dict[str, str | SecretRef]) -> FillReport
    # Keys are VISIBLE labels / placeholders / aria-labels, matched fuzzily via
    # the a11y tree — never selectors. Values may be a SecretRef(alias) resolved
    # via SecretsManager (§4) so passwords are never in plaintext args/logs.
    # Types with human-like per-keystroke delay + focus/blur events.

def submit(form_label: str | None = None) -> PageView
    # Clicks the form's submit control (by role=button within the form) or
    # presses Enter. If form_label omitted and exactly one form, uses it.

def click_text(label: str) -> PageView
    # get_by_role("button"/"link", name=label) then get_by_text fallback.

def extract_table(description: str) -> list[dict[str, str]]
    # Picks the table best matching `description` (caption/nearby heading/column
    # headers), returns rows as label→value dicts (header row = keys).

def download_file(link_label: str) -> DownloadedFile
    # Clicks a download link/button by visible label; captures the download,
    # size-caps it (§4), writes ONLY inside the workspace jail, returns path+meta.

def list_iframes() -> list[IframeInfo]           # name/title/src/url/index, nested
def read_iframe(index: int) -> StructuredPage     # same reader, scoped to a frame

def get_response_headers(url_substring: str | None = None) -> dict[str, dict]
    # request + response headers for the main doc (or the matching sub-request).

def get_tls_info() -> TlsInfo
    # issuer, subject, protocol (e.g. TLS 1.3), cipher, valid_from, valid_to,
    # cert chain (subjects+issuers), SAN list. Via security_details() + CDP.

def get_all_network_requests() -> list[NetworkRecord]
    # Every request the page made: method, url, status, request+response headers,
    # request body, response content-type, response size, timing.

def screenshot(label: str = "page") -> DownloadedFile   # jailed, redaction-aware
def wait_for_text(text: str, timeout_s: float = 15.0) -> bool
def go_back() / go_forward() / reload()
```

### 2b — `StructuredPage` (what "read the page" returns)

The single object that lets a caller with no HTML knowledge understand a page:

```python
@dataclass
class StructuredPage:
    url: str
    title: str | None
    visible_text: str                     # rendered, script/style stripped
    accessibility_tree: dict              # page.accessibility.snapshot()
    headings: list[str]
    links: list[Link]                     # {text, href, is_download}
    tables: list[Table]                   # {caption, columns, rows: list[dict]}
    forms: list[FormDescriptor]           # see below — described, not selectored
    iframes: list[IframeInfo]             # {index, name, title, src, url, nested}
    embedded_json: list[dict]             # <script type=application/*json> + JSON
                                          #   found in inline data islands
    captcha: CaptchaFinding | None        # populated by §3 detection
```

```python
@dataclass
class FormDescriptor:
    label: str | None                     # form aria-label / nearby heading
    fields: list[FieldDescriptor]         # {label, kind, required, placeholder,
                                          #   options (for select/radio), value}
    submit_labels: list[str]              # visible submit control names
```

`fill_form` consumes `FormDescriptor.fields[*].label` — the agent reads the form,
sees human labels, and fills by those same labels. No selector round-trips.

**Table extraction:** `reader.py` parses both native `<table>` and ARIA
`role="grid"/"table"` structures, plus common "data table" divs detected via the
a11y `role` (this is why the a11y tree, not the DOM, is the source of truth).
Embedded JSON: collect `<script type="application/json">`,
`type="application/ld+json"`, and `window.__DATA__`-style assignments captured via
`page.evaluate` of a small, fixed, read-only extractor (no arbitrary agent JS).

---

## 3 — Captcha handling: DETECT → NOTIFY human → RESUME (solver is opt-in)

**Stance (binding):** the default and only always-on path is **detect the
captcha and hand off to a human**; gludd is autonomous + notification-based, and
a human solves the challenge, then the run resumes. Automated solving is an
**explicit operator opt-in** (a pluggable solver service the operator enables and
supplies credentials for), never the default, and never framed as evasion/bypass.
We do not design auto-bypass, fingerprint spoofing, or anti-bot evasion as
default behavior.

### 3a — Detection + classification (`captcha.py`)

`detect_captcha(page) -> CaptchaFinding | None` runs as part of
`read_page_as_structured_data()`. Classifies common types by stable, visible
signals (never by defeating them):

| Type | Detection signal |
|---|---|
| reCAPTCHA v2 (checkbox/image) | iframe `src` host `google.com/recaptcha`, `g-recaptcha` role/label, `.grecaptcha-badge` |
| reCAPTCHA v3 (invisible/score) | `grecaptcha.execute` script + no visible widget → flagged as "score-based, may need action" |
| hCaptcha | iframe host `hcaptcha.com`, `h-captcha` marker |
| Cloudflare Turnstile / challenge | `challenges.cloudflare.com` iframe, interstitial title |
| Image-select / text / arithmetic | `<img>` + input near text "type the characters/letters", ARIA "captcha" label |

```python
@dataclass
class CaptchaFinding:
    kind: str            # recaptcha_v2 | recaptcha_v3 | hcaptcha | turnstile |
                         # image | text | unknown
    site_key: str | None # public site key when present (needed by solver path)
    frame_index: int | None
    page_url: str
    challenge_hint: str  # human-readable instruction lifted from the page
```

### 3b — Default path: notify + pause + resume

On a `CaptchaFinding`, `HumanBrowser` raises `CaptchaEncountered(finding)` which
the calling module (§4) turns into a human handoff **without failing the run**:

1. File a `gludd_human_todo` (`plugins/modules/gludd_human_todo.py`) with
   `category=blocker`, `priority=high`,
   `parent_agent_todo_id=<the browser task's todo>`, a description carrying
   `finding.kind` + `challenge_hint` + a screenshot artifact path (jailed,
   redaction-aware). This flips the parent todo to `blocked_on_human` /
   `TodoStatus.APPROVAL_REQUIRED` (`schemas/todo.py:23`) — the daemon will not
   re-dispatch it until resolved.
2. If the run is inside a LangGraph decision loop with `human_in_the_loop`
   enabled, **also** call `HumanGate.await_approval(thread_id, message, …)`
   (`human_gate.py:128-191`) for a synchronous, non-blocking in-process pause
   (the two are designed to compose — human_gate's own docstring names the
   todo-poll path its fallback).
3. **Session persistence across the pause:** the `BrowserSession` stays alive
   (cookies/context preserved, §4) keyed by a session handle stored on the todo,
   so when the human resolves — either by solving in a shared/remote view of the
   live browser, or by supplying a token the run injects — `resume()` continues on
   the *same* page state. The human resolution (`human_resolution` field,
   `gludd_human_todo.py:60-70`) releases `APPROVAL_REQUIRED → QUEUED`
   (`self_improve/approval.py:83`) and the browser task re-dispatches.

### 3c — Optional operator-enabled solver (opt-in only)

When — and only when — the operator sets `captcha.mode = "solver"` and configures
a solver credential alias (§5), a pluggable `CaptchaSolver` protocol is used for
authorized automation (e.g. a 2captcha-style API the operator is licensed to
use):

```python
class CaptchaSolver(Protocol):
    def solve(self, finding: CaptchaFinding, page_url: str) -> str | None: ...
    # returns a token to inject (e.g. g-recaptcha-response), or None → fall back
    # to the human handoff of §3b.
```

Solver credentials resolve via `SecretsManager.resolve(alias)` (never inline).
`mode="solver"` still **falls back to human handoff** on solver failure/timeout —
detect→notify→resume is always the floor. Modes: `detect_only` (report, don't
act), `human` (default — notify+pause), `solver` (opt-in, with human fallback).

---

## 4 — Ansible integration: five modules + `roles/browse_web/`

New modules under
`collections/ansible_collections/general_ludd/agent/plugins/modules/`, each
mirroring the `gludd_git.py`/`gludd_worktree.py` shape exactly: hand-rolled
`DOCUMENTATION/EXAMPLES/RETURN` docstring (`gludd_git.py:1-117`), the
`try: from ansible_collections… except ImportError: sys.path.insert…` fallback
import (`gludd_git.py:119-135`), `AnsibleModule(argument_spec=dict(...),
supports_check_mode=True, required_if=[...])`, `ok_result`/`error_result`
(`module_utils/gludd.py:50-61`), and **direct Python import** of the
`general_ludd.browser` package (same-venv, no RPC — matching
`from general_ludd.git_automation.repo import GitAutomation`, `gludd_git.py:177`).

Modules talk to a **persistent session** (see 4b) via a `session_handle` so a
role can chain open → fill → submit → extract → download across tasks while
cookies/auth/context persist.

### 4a — Module list + signatures

**`gludd_browser_open`** — start/reuse a session and navigate.
```text
argument_spec:
  session_handle (str, default None)   # None → create new session, return handle
  url            (str, required)
  engine         (str, choices=[chromium,firefox,webkit], default chromium)
  headless       (bool, default True)
  wait_for       (str, default None)   # optional visible text to wait on
returns: session_handle, url, status_code, title, redirected_from,
         captcha (CaptchaFinding|None)
```

**`gludd_browser_fill`** — fill + optionally submit a form by visible labels.
```text
argument_spec:
  session_handle (str, required)
  fields         (dict, required)      # {visible_label: value}
  secret_fields  (dict, default {})    # {visible_label: secret_alias} → resolve()
  submit         (bool, default False)
  form_label     (str, default None)
  human_timing   (bool, default True)  # per-keystroke delay + mouse motion
returns: filled (list of labels), submitted (bool), post_submit_title,
         captcha (CaptchaFinding|None)
no_log on secret_fields (mirror gludd_worktree/agent_task no_log pattern,
  roles/agent_task/tasks/main.yml:46,82)
```

**`gludd_browser_extract`** — read the page as structured data.
```text
argument_spec:
  session_handle (str, required)
  what           (str, choices=[page,table,iframe,text,forms,json],
                  default page)
  table_desc     (str, default None)   # when what=table
  iframe_index   (int, default None)   # when what=iframe
returns: page (StructuredPage dict) | table (rows) | iframe (StructuredPage) |
         forms | embedded_json
```

**`gludd_browser_download`** — download a file by visible link label, jailed.
```text
argument_spec:
  session_handle (str, required)
  link_label     (str, required)
  dest_dir       (str, required)       # must resolve inside the workspace jail
  max_bytes      (int, default 26214400)  # 25 MiB cap, operator-overridable
returns: path, filename, size_bytes, content_type, truncated (bool→fail if cap)
```

**`gludd_browser_inspect`** — headers, TLS, and full network log.
```text
argument_spec:
  session_handle (str, required)
  what           (str, choices=[headers,tls,network], default headers)
  url_substring  (str, default None)   # filter for headers/network
returns: headers ({request,response}) | tls (TlsInfo) | network (list[NetworkRecord])
```

A closing **`gludd_browser_close`** (or `state=absent` on `gludd_browser_open`)
tears the session down; the `browse_web` role always closes in an `always:` block
(mirror `roles/agent_task/tasks/main.yml:139-147`) so a browser subprocess is
never leaked even on failure — critical given the Chromium child process.

### 4b — Session model (persistent context across role steps)

A `BrowserSession` (`browser/session.py`) owns one Playwright `BrowserContext`
(cookies, localStorage, auth) and one browser subprocess. Sessions live in a
process-local `SessionRegistry` keyed by an opaque `session_handle` (a UUID
returned by `gludd_browser_open`). Because Ansible modules are short-lived
processes, the registry lives in the **long-running daemon**: modules reach it
via the `GluddClient` PSK HTTP shim (`module_utils/gludd.py:119-203`) to a
`/api/browser/*` daemon router that holds the actual sessions — OR, for
`connection=local` in-process runs, via a shared in-process singleton. The
`session_handle` is the only cross-task state; cookies/TLS session/open page all
persist server-side. Sessions carry an **idle TTL** and a **max lifetime**;
the daemon reaps expired sessions (and their Chromium subprocess via
`psutil`, already a dep, `pyproject.toml:35`).

### 4c — Role: `roles/browse_web/`

`collections/ansible_collections/general_ludd/agent/roles/browse_web/tasks/main.yml`
composes the modules declaratively, following `agent_task`'s
`assert → set_fact(_bw_*) → block/rescue/always` shape
(`roles/agent_task/tasks/main.yml:16-147`):

```yaml
- assert: that: ["target_url | length > 0"]
- block:
    - gludd_browser_open:   { url: "{{ target_url }}", headless: "{{ bw_headless | default(true) }}" }
      register: _bw_session
    - gludd_browser_extract: { session_handle: "{{ _bw_session.session_handle }}", what: page }
      register: _bw_page
    - gludd_browser_fill:
        session_handle: "{{ _bw_session.session_handle }}"
        fields: "{{ form_values }}"
        secret_fields: "{{ form_secrets | default({}) }}"
        submit: true
      register: _bw_fill
      no_log: "{{ (form_secrets | default({})) | length > 0 }}"
    # if _bw_fill.captcha is defined → file gludd_human_todo (category=blocker)
    - gludd_browser_inspect: { session_handle: "{{ _bw_session.session_handle }}", what: tls }
      register: _bw_tls
  rescue:
    - gludd_human_todo: { category: blocker, ... }   # on CaptchaEncountered / block
  always:
    - gludd_browser_close: { session_handle: "{{ _bw_session.session_handle | default('') }}" }
      failed_when: false
```

Register a `playbooks/browse_web.yml` (surfaces in `make playbook-list`).

---

## 5 — Config schema (`browser/config.py`, wired into user_config)

`BrowserConfig` (Pydantic, mirror `HumanInTheLoopConfig` at
`config/user_config.py:130`), read from `config["browser"]`:

```text
engine:            chromium | firefox | webkit          (default chromium)
headless:          bool                                  (default True)
proxy:             str | None   (http/https/socks proxy URL, None = direct)
allowlist:         list[str]    # operator URL/host allowlist — PRIMARY gate (§6)
allow_loopback:    bool         (default False; DAST-style targets set True)
nav_timeout_s:     float        (default 30)
max_download_bytes:int          (default 26214400 = 25 MiB)
captcha:
  mode:            detect_only | human | solver          (default human)
  solver_provider: str | None   (e.g. "twocaptcha")
  solver_cred_alias: str | None # SecretsManager alias — never an inline key
human_timing:      bool         (default True)  # realistic typing/mouse/jitter
screenshot_retention_days: int  (default 3)     # redaction/retention, §6
network_capture:   bool         (default True)  # record all requests
```

Wire into daemon config next to the human-gate keys
(`daemon.py:1370-1371`). `GLUDD_ENABLE_BROWSER=1` gates whether the browser
subsystem initializes at all (matches the opt-in bootstrap of §1).

---

## 6 — Security

The browser is a powerful new outbound + JS-executing surface. It runs entirely
inside the existing isolation and reuses the existing guards — no new trust
boundary is invented.

1. **Sandbox confinement.** The browser subprocess runs inside the same
   process-isolation as playbooks (`ansible/core_runner.py`): the
   subprocess/container backend (`core_runner.py:570-684`, podman/bwrap+seccomp)
   confines Chromium; the fork-child `os.setsid()` + `killpg` group-kill
   (`core_runner.py:390-411,489-516`) is what reaps a runaway/zombie Chromium and
   **all** its renderer children on timeout. Env is scrubbed to the allowlist
   (`_PLAYBOOK_ENV_ALLOWLIST`, `core_runner.py:521-568`) so `GLUDD_PSK`,
   `ZAI_API_KEY`, `AWS_*`, `DATABASE_URL` never reach a page's process. Add
   Chromium's own `--no-sandbox` guard: **do not** pass it — keep Chromium's
   internal sandbox on inside the container.

2. **URL gating — allowlist-primary + SSRF hard-deny.** Every navigation
   (`open_page`) and — critically — every **redirect target and sub-request**
   the browser tries to load is validated by `is_url_blocked(url)`
   (`ssrf.py:145-170`) via a `page.route("**/*")` interceptor that aborts blocked
   requests before they leave the process. Because a headless browser follows
   redirects internally (unlike `_NoRedirectHandler`, `web.py:28-58`), the check
   must live at the **network-interception layer**, per-request, not only at the
   first navigate. The `config.allowlist` is the **primary** gate (default-deny:
   only allowlisted hosts load); the SSRF `BLOCKED_*` metadata/RFC-1918 deny
   (`ssrf.py:48-89`) is the hard floor beneath it. Like DAST, loopback/RFC-1918
   may be a *legitimate* target — so `allow_loopback`/an explicit allowlist entry
   can permit a specific internal host, but the metadata IPs
   (`169.254.169.254`, `100.100.100.200`, `ssrf.py:61-65`) are **never**
   allowlistable.

3. **Download caps + write jail.** `download_file` streams to a bounded sink and
   **fails** past `max_download_bytes` (default 25 MiB) — no partial artifact
   kept. The destination is validated with `default_policy(workspace=<job_ws>).check(dest)`
   (`fs_write_policy.py:184-263`, default-DENY + sensitive-name reject) on the
   Ansible side, or `ExecutionEngine._resolve_in_workspace(dest)`
   (`engine.py:808-831`, realpath+commonpath) in-process — both pre-existing,
   tested primitives; write via the atomic mkstemp→fsync→`os.replace` pattern.

4. **Secrets never inline / never logged.** Form passwords are passed as
   `secret_fields: {label: alias}` and resolved at fill time via
   `SecretsManager.resolve(alias)` (`manager.py:286-317`); the plaintext value is
   never a module argument, never rendered into a fact, and the value is tracked
   for auto-redaction from any exception text (`_redact`, `manager.py:203-240`).
   Ansible tasks handling secrets set `no_log` (mirror
   `roles/agent_task/tasks/main.yml:46,82`). Solver API keys resolve the same way.

5. **Screenshots/headers may carry sensitive data.** A screenshot of a filled
   form, response headers (cookies, `Authorization`, `Set-Cookie`), and network
   bodies are potentially sensitive. Mitigations: (a) redact `Cookie` /
   `Authorization` / `Set-Cookie` / `Proxy-Authorization` header values in
   `get_response_headers`/`get_all_network_requests` output by default (raw
   available only under an explicit `include_sensitive_headers=True` operator
   flag); (b) screenshots and network dumps are jailed artifacts with a
   `screenshot_retention_days` TTL (§5) after which the daemon sweeper deletes
   them; (c) apply `SecretsManager`'s tracked-value redaction to all captured
   text so a resolved password can never surface in a screenshot's OCR-able
   fields log or a network body dump.

6. **No arbitrary agent JS.** `page.evaluate` is used only with fixed,
   repo-owned, read-only extractor snippets (table/JSON island reading). The
   agent's "verbs" never accept a raw JS string — this keeps the model from
   turning the browser into an arbitrary code-exec primitive against a target.

---

## 7 — Test plan

All browser tests skip-marked when Chromium absent (§1). Use a **local static
test page** (a `file://` or a localhost `http.server` fixture in
`tests/fixtures/browser/`) — no live-internet dependency; `allow_loopback=True` +
allowlist the fixture host.

- **Form fill (happy path):** serve a page with labeled `<input>`s + a `<select>`;
  `fill_form({"Email": ..., "Country": ...})` fills by label; `submit()`; assert
  the fixture's echo page shows the posted values. Includes a `secret_fields`
  case asserting the plaintext never appears in captured logs/facts.
- **Header extraction:** fixture returns custom request-echo + response headers;
  `get_response_headers()` returns both request and response maps; assert a custom
  header round-trips and that `Set-Cookie`/`Authorization` are redacted by default.
- **TLS extraction:** against a localhost HTTPS fixture with a self-signed cert;
  `get_tls_info()` returns issuer/subject/protocol/validFrom/validTo/cipher and a
  chain list; assert fields are populated (protocol string like `TLS 1.3`).
- **Iframe read:** fixture with a nested iframe; `list_iframes()` enumerates both
  outer+nested (correct index/src); `read_iframe(n)` returns the nested frame's
  `StructuredPage.visible_text`.
- **Table extraction:** fixture with a `<table>` + an ARIA `role="grid"`;
  `extract_table("quarterly revenue")` picks the right one; rows come back as
  header→value dicts; embedded `<script type="application/json">` surfaces in
  `StructuredPage.embedded_json`.
- **Captcha detect → notify:** fixture embedding a fake reCAPTCHA v2 iframe
  (`src` host `google.com/recaptcha`, no real challenge); `read_*` populates
  `CaptchaFinding(kind="recaptcha_v2", site_key=…)`; assert the module files a
  `gludd_human_todo(category=blocker, parent_agent_todo_id=…)` and the parent
  todo transitions to `APPROVAL_REQUIRED`; assert the session stays alive across
  the pause and `resume()` continues on the same page. Solver-mode test uses a
  stub `CaptchaSolver` and asserts fallback-to-human on solver `None`.
- **Download size cap:** fixture serves a body larger than `max_bytes`;
  `download_file` **fails** (`DownloadTooLarge`) and leaves **no** file on disk;
  a within-cap download writes exactly inside the workspace jail (assert
  `commonpath` containment).
- **SSRF/allowlist enforcement:** `open_page("http://169.254.169.254/…")` is
  rejected before any I/O (`is_url_blocked`); a page whose sub-resource/redirect
  targets a metadata IP has that request aborted at the `page.route` interceptor
  (assert it never loaded) even though the top URL was allowlisted; a non-
  allowlisted host is denied even when not SSRF-blocked (allowlist-primary).
- **Sandbox confinement:** assert a browser run inside `core_runner`'s
  subprocess/container backend cannot read a secret env var (env-scrub allowlist),
  and that a hung navigation is group-killed on timeout (no orphan Chromium —
  assert via `psutil` that the child pid is gone after `_terminate_tree`).
- **Session lifecycle:** open→extract→fill→close across separate module
  invocations sharing one `session_handle` preserves cookies; `always: close`
  reaps the subprocess; idle-TTL reaper removes a stale session.

---

## 8 — Implementation order

1. `pyproject.toml`: add `playwright>=1.44` (opt-in group) + `make browser-install`;
   `GLUDD_ENABLE_BROWSER` bootstrap gate; skip-marker helper.
2. `browser/session.py` + `SessionRegistry` + daemon `/api/browser/*` router
   (session model, §4b) — the spine everything else rides.
3. `browser/network.py` (route-interceptor SSRF gate, header/TLS capture) —
   land the security floor before any convenience verb.
4. `browser/reader.py` → `StructuredPage`; `browser/intent.py` verbs.
5. `browser/captcha.py` (detect + classify) + human-todo/human-gate handoff.
6. Five `gludd_browser_*` modules + `gludd_browser_close` + `roles/browse_web/` +
   `playbooks/browse_web.yml`.
7. Full test suite (§7); `make ansible-syntax`; `make gate-async` before claiming
   any item closed.

---

## Implementation corrections (2026-07-10 verification)

The spec above is implementable as designed, but the following 5 corrections
must be folded in before implementation starts.

1. **Module-pattern precedent is mis-cited (§4).** §4 says the
   `gludd_browser_*` modules mirror `gludd_git.py`/`gludd_worktree.py`'s
   **direct in-process import** shape (`from general_ludd... import`), but §4b
   correctly says the stateful browser **session** lives in the **daemon**,
   reached via the `GluddClient` HTTP shim — that is `gludd_human_todo.py`'s
   pattern (`GluddClient(base_url, psk, timeout)` + `Authorization: Bearer` +
   `X-PSK`, `no_log` on the psk — `module_utils/gludd.py:146-154`), **not** the
   git modules' pattern. A Playwright session can't be a same-process direct
   import from a short-lived Ansible module invocation — the browser subprocess
   must outlive the module process. **Correction:** cite `gludd_human_todo.py`
   (not `gludd_git.py`/`gludd_worktree.py`) as the precedent in §4's opening
   paragraph; the `DOCUMENTATION`/`EXAMPLES`/`RETURN` docstring shape and
   `ok_result`/`error_result` helpers still apply from the git modules, but the
   session-reach mechanism is the human-todo module's `GluddClient` HTTP shim.

2. **`locator.type(text, delay=…)` is deprecated (§1).** Playwright deprecated
   `locator.type()` around 1.28+ in favor of `locator.press_sequentially(text,
   delay=…)`, which is the current per-keystroke-delay API. **Correction:**
   update the §1 human-input verb table row ("Human-like typing/mouse/timing")
   to reference `locator.press_sequentially(text, delay=…)` instead of
   `locator.type(text, delay=…)`.

3. **CDP deep-TLS path is Chromium-only (§5/§2a).** `context.new_cdp_session()`
   — used for the deep cert-chain via the CDP Network/Security domains — is
   **unavailable** on Firefox and WebKit; CDP is a Chromium-specific protocol.
   Since §5 exposes `engine: chromium | firefox | webkit`, `get_tls_info()`'s
   deep-chain path is not portable across engines. **Correction:** state
   plainly that on non-Chromium engines, `get_tls_info()` degrades to
   `response.security_details()` (issuer/subject/protocol/validFrom/validTo
   only, no full chain) — or must explicitly fail/report "chain unavailable on
   this engine" rather than silently returning a partial `TlsInfo` that looks
   complete.

4. **Process-isolation is opt-in / off by default in prod (§6.1) —
   overstated confinement.** §6.1 reads as though sandbox confinement is
   guaranteed; it is not, by default. `core_runner.py:298` only routes to the
   podman/bwrap backend when `iso.enabled`, and `UserConfig.process_isolation`
   defaults to `{}` (`user_config.py:169`) — i.e. disabled unless an operator
   opts in. **Correction — state plainly:** by default, Chromium gets only
   fork-group-kill (`setsid`+`killpg` reap) and env-scrub, **not** real OS
   sandboxing. Two things genuinely ARE unconditional and can be relied on as
   a floor regardless of `iso.enabled`: (a) the `_PLAYBOOK_ENV_ALLOWLIST` env
   scrub at `core_runner.py:782-800` — `GLUDD_PSK`/API keys/`AWS_*`/
   `DATABASE_URL` are scrubbed from the child env even without isolation
   enabled; (b) the `setsid`+`killpg` reap that kills the whole process group
   on timeout. Real OS-level sandboxing (podman/bwrap/seccomp) requires the
   operator to set `process_isolation.enabled=True` — it is not the default
   posture, and §6.1 must say so explicitly rather than imply blanket
   containment.

5. **Missing operational requirements (§1/§6 bootstrap).** Headless Chromium
   in a container has two routine, currently-absent operational
   requirements: (a) `/dev/shm` must be sized up (or Chromium launched with
   `--disable-dev-shm-usage`) — the default container `/dev/shm` (64 MB) is
   too small for Chromium's shared-memory usage and causes crashes/renders
   failing under load; (b) font packages (e.g. `fonts-liberation` or
   equivalent) must be installed in the container image, or text renders as
   tofu/missing-glyph boxes, corrupting `visible_text`/screenshot output.
   **Correction:** add both to the §1 binary-install / §6 bootstrap
   requirements list.

**Also (minor, fold in alongside the above):**

- `read_iframe(index)` (§2a) should use `page.frames[index]` (a `Frame` object
  exposing `.content()`/`.locator()`), **not** `frame_locator()` — the latter
  takes a **CSS selector** that resolves *into* a frame element on the parent
  page, it does not index the flat frame list the way `list_iframes()`
  enumerates it. Fix the implementation note under §2a / the `read_iframe`
  signature comment in §2a's code block.
- Captcha handoff (§3b) must pass a **synthetic `confidence=0.0`** into
  `HumanGate.await_approval(...)` to force the interrupt — `await_approval`
  only interrupts when `confidence < threshold`; without an explicit
  below-threshold value, a captcha encounter could fail to trigger the
  LangGraph-side pause even though the `gludd_human_todo` filing (§3b step 1)
  still fires. Add this to §3b step 2's description.
