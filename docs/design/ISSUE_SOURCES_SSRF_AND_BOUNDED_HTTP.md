# Issue-source SSRF consolidation + bounded-HTTP / zip-bomb guard (2026-07-10)

Turnkey design for two adjacent hardening items on the issue-source adapters,
with adversarial corrections already folded in. Same landing discipline as the
Wave C docs: single-writer per file, targeted `make test-iso` per adapter,
CI-green per batch. Line numbers are current-tree (verified 2026-07-10); re-Read
before editing. Each item: verdict + exact locus + fix + tests.

Two parts, independent — Part A can land per-adapter; Part B is a new shared
module plus call-site edits. **Recommended order: land Part A first** (small,
self-contained, no new module), then Part B.

---

## Part A — Metadata-name SSRF consolidation (4 + 1 straggler adapters)

### Verdict
Four issue-source adapters ship a weaker *local* `_BLOCKED_HOST_LITERALS`
(loopback names only) instead of delegating to the canonical
`host_is_blocked` (`security/ssrf.py:92-142`). A fifth adapter (ServiceNow) has
its own independent `_host_is_blocked` with the same missing-metadata-names gap.
All five let `http://metadata.google.internal/` (and `metadata.goog`,
`instance-data`) through — a live SSRF-to-cloud-metadata hole at adapter
construction time.

The canonical guard's superiority (why delegation closes the hole):
`BLOCKED_HOST_NAMES` (`ssrf.py:48-59`) includes `metadata`,
`metadata.google.internal`, `metadata.goog`, `instance-data`, `ip6-localhost`,
`ip6-loopback`; `BLOCKED_METADATA_IPS` (`ssrf.py:65`) adds `169.254.169.254` and
Alibaba's `100.100.100.200`; plus the `.localhost` RFC-6761 suffix
(`ssrf.py:130`), NUL-byte handling (`ssrf.py:110`), trailing-dot / IPv6-bracket
normalization (`ssrf.py:119-124`), and — critically — the
`not ip.is_global` catch (`ssrf.py:88`) that denies TEST-NET / documentation /
CGNAT ranges the per-adapter `ipaddress` flag lists miss.

### Root cause of the metadata bypass (all 5)
Each local guard checks a tiny name-literal set, then does
`ipaddress.ip_address(host)` inside a `try/except ValueError` that **swallows the
ValueError and returns/falls-through = allowed**. `metadata.google.internal` is
not in the tiny literal set AND is not an IP literal, so `ip_address()` raises
`ValueError`, which is caught → the host is permitted. Example, `linear.py:83-97`:

```python
if host.lower() in _BLOCKED_HOST_LITERALS:      # tiny set: no metadata.* names
    raise ValueError(...)
try:
    ip = ipaddress.ip_address(host)
except ValueError:
    return                                       # <-- metadata.google.internal lands here → ALLOWED
```

### Affected adapters (FIX these 5)
| Adapter | Local guard | Blocklist locus | Guard body |
|---|---|---|---|
| `linear.py` | `_reject_internal_base_url` | `_BLOCKED_HOST_LITERALS` 29-36 | 76-97 |
| `trello.py` | `_reject_internal_base_url` | `_BLOCKED_HOST_LITERALS` 34-41 | 44-74 |
| `azure_boards.py` | `_reject_internal_base_url` | `_BLOCKED_HOST_LITERALS` 31-38 | 41-62 |
| `asana.py` | `_reject_internal_base_url` | `_BLOCKED_HOST_LITERALS` 29-36 | 45-66 |
| `servicenow.py` | `_host_is_blocked` (+`_require_instance_url`) | `_BLOCKED_HOSTNAMES` 54-61 | 76-110 |

### Adapters already correct (do NOT touch — reference for the fix)
`jira.py:97-111`, `clickup.py:89-113`, `monday.py:98-123`,
`bitbucket_issues.py:91-115`, `gitlab_issues.py:82-94`, `base.py:551-560`
(`_is_internal_host`) — all delegate to `host_is_blocked`. `clickup.py`'s
`_is_blocked_host` is the reference pattern: it calls `host_is_blocked(host)`
first, then ADDITIVELY applies its own stricter `.local`/`.internal`/`.localhost`
/`.intranet` + no-dot rules.

### FIX (each of the 5)
Delete the local name-literal set + the bespoke `ipaddress` flag block; import
and delegate to the canonical guard. It stays pure-literal / no-DNS (`ssrf.py:26-31`
hang-safety contract), so every adapter's "no DNS resolution" docstring stays
accurate — **no docstring change needed** beyond optionally noting the delegation.

**linear / trello / azure_boards / asana** — replace the guard body with a
delegation and drop the now-unused `import ipaddress`:

```python
from general_ludd.security.ssrf import host_is_blocked

def _reject_internal_base_url(base_url: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme for base_url: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise ValueError("base_url has no host")
    if host_is_blocked(host):
        raise ValueError(f"internal/blocked host in base_url: {host!r}")
```

**servicenow.py** — `_host_is_blocked` (76-110) currently mixes the canonical-
gap name set with a `_BLOCKED_SUFFIXES` (`.local`/`.internal`/`.localdomain`/
`.cluster.local`, 64-69) stricter-than-canonical rule. Preserve that stricter
behavior (mirror clickup): delegate to `host_is_blocked` first, then keep the
suffix check. Drop the now-redundant `_BLOCKED_HOSTNAMES` set and the local
`ipaddress` flag block:

```python
from general_ludd.security.ssrf import host_is_blocked

def _host_is_blocked(host: str) -> bool:
    host = host.strip().lower().rstrip(".")
    if not host:
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host_is_blocked(host):
        return True
    return any(host.endswith(suffix) for suffix in _BLOCKED_SUFFIXES)
```

Keep `SSRFError(ValueError)` and `_require_instance_url` as-is (they already
raise a `ValueError` subclass, so the tests below still catch `ValueError`).

Note ServiceNow's constructor takes `(config, transport, *, env=...)` and reads
`instance_url` (not `base_url`) — the test must construct it accordingly.

### Purity / no-DNS invariant (unchanged)
`host_is_blocked` performs zero DNS/network I/O (`ssrf.py:26-31`). Delegation
does not weaken the "config-time literal check, no resolver-side SSRF" property
any adapter documents. Do NOT reach for `resolved_host_is_blocked` — these are
general fetch paths, not the opt-in DNS-accepting connectors.

### TESTS (per adapter — mirror `tests/unit/test_issue_source_clickup.py:133-141`)
Add a parametrized test asserting the three metadata aliases raise `ValueError`
at construction. The clickup reference:

```python
@pytest.mark.parametrize(
    "bad_url",
    ["http://metadata.google.internal/", "http://metadata.goog/", "http://instance-data/"],
)
def test_metadata_alias_names_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError):
        ClickUpIssueSource({"base_url": bad_url}, transport=RecordingTransport([]), env={})
```

Per-adapter construction shapes (drop `env=`/`transport=` where the ctor differs):
- `LinearIssueSource({"base_url": bad_url})` — transport kwarg optional
- `TrelloIssueSource({"base_url": bad_url})`
- `AzureBoardsIssueSource({"base_url": bad_url})`
- `AsanaIssueSource({"base_url": bad_url})`
- `ServiceNowIssueSource({"instance_url": bad_url})` — **uses `instance_url`**, and
  raises `SSRFError` (a `ValueError` subclass) — `pytest.raises(ValueError)` still passes.

Also add the CGNAT / Alibaba-metadata-IP regression already present in the
clickup suite (`test_alibaba_metadata_ip_rejected`,
`test_cgnat_address_rejected`, 144-160) to at least one migrated adapter to pin
the `not is_global` catch that the old per-adapter flag lists missed:
`http://100.100.100.200/` and a `100.64.0.1` CGNAT host both raise `ValueError`.

### Acceptance
Each of the 5 adapters rejects `http://metadata.google.internal/`,
`http://metadata.goog/`, `http://instance-data/` at construction; the 6
already-correct adapters and `base.py` are untouched; no adapter performs DNS.

---

## Part B — Bounded HTTP / zip-bomb guard (new `src/general_ludd/security/bounded_http.py`)

### Goal
Cap unbounded `resp.json()` / `.read()` and openpyxl zip-bomb exposure across the
issue-source adapters + `csv_excel.py`. **`bounded_http.py` does not exist today**
(verified) — this is a new module. `openpyxl` is an optional guarded import (not
in `pyproject.toml`); the module must import it lazily so nothing hard-depends on it.

### Adversarial corrections (the first-draft design had 3 soundness holes)

#### CORRECTION 1 — zip-bomb heuristic on `ZipInfo` metadata is UNSOUND
`ZipInfo.file_size` / `.compress_size` come from the ZIP **central-directory**
record, which is fully attacker-controlled and is NOT validated against the
actual DEFLATE stream before decompression. A crafted entry can declare a tiny
`file_size`/`compress_size` (passing any declared-ratio check) yet decompress to
gigabytes. Central-directory-metadata-only checks are a known-insufficient
zip-bomb defense.

**Correct design:** `reject_oversized_xlsx` must do **incremental bounded
decompression** — open each entry with `zf.open(info)` and read in fixed-size
chunks, accumulating a running decompressed-byte counter, and abort with
`ValueError` the moment the cumulative output crosses `max_uncompressed_bytes`.
Never trust declared sizes for the authoritative decision. Keep the cheap
`os.path.getsize` precheck on the *compressed* file as a fast first gate (reject
absurdly large archives before opening), but the real guard is the streaming
read cap. This is a net improvement regardless: `csv_excel.py:118-135` (and the
write path at 227-254) today call `openpyxl.load_workbook` with ZERO zip-bomb
protection.

#### CORRECTION 2 — a post-hoc `capped_json` over non-streaming httpx does NOT bound memory
All the httpx/requests adapters call `client.get()` / `client.post()` (or
`httpx.request(...)`) **without** `stream=True`, so httpx eagerly reads the
entire body into `resp.content` during `send()` — *before* any post-hoc
`len(resp.content)` check could run. A bolt-on cap can therefore only **reject an
already-fully-downloaded oversized body**, not prevent the memory blowup that
already happened.

**Correct design — document BOTH options, recommend the pairing:**
- **(a) Minimum viable** — `capped_json(resp, max_bytes)` inspects the
  already-buffered `resp.content`, raises `ValueError` if it exceeds `max_bytes`,
  else parses. This is a **partial mitigation**: it protects the downstream
  parse/normalize/store path (and bounds JSON-decode blowup) but does NOT bound
  the network read itself. Cheap, no call-site restructuring — swap `resp.json()`
  → `capped_json(resp)`.
- **(b) Full fix (sound)** — migrate the hot fetch call sites to
  `client.stream(...)` and enforce the cap while iterating `resp.iter_bytes()`,
  aborting early once the running byte count crosses the cap. This is the only
  option that truly bounds memory (never buffers the whole hostile body).

**Recommendation:** ship (a) everywhere as the immediate, low-churn mitigation,
and schedule (b) for the hot read paths (`fetch_issues` — the only unbounded
list responses; write-back responses are small). State plainly in the doc that
(a) is partial and (b) is the sound fix so the tradeoff is not silently
rediscovered later.

#### CORRECTION 3 — the urllib error-body path is ALSO unbounded
The stdlib-`urllib` adapters (clickup / monday / bitbucket) read the error body
via `body = exc.read()` in their `except urllib.error.HTTPError` branch
(`clickup.py:135`, `monday.py:146`, `bitbucket_issues.py:137`) with no cap — a
hostile server can return a 4xx/5xx with a gigabyte body. Cap it too via
`capped_read_bytes(exc)`. Unlike httpx, `http.client.HTTPResponse.read(n)` (which
backs both the success `resp.read()` and the `HTTPError.read()`) IS a genuine
bounded read: `capped_read_bytes(resp, max_bytes)` doing `resp.read(max_bytes+1)`
and rejecting if `len > max_bytes` is **sound for the success path** (bounds the
actual socket read), and equally applicable to the error branch.

### Module API (`security/bounded_http.py`)

```python
"""Bounded HTTP body + zip-bomb read guards — SINGLE source of truth.

Cap the two unbounded-read classes across the issue-source adapters and the
CSV/Excel source:

  * capped_json / capped_read_bytes — cap an HTTP response body before it is
    parsed or stored (httpx buffered bodies; stdlib http.client responses).
  * reject_oversized_xlsx — INCREMENTAL bounded decompression of an .xlsx
    (a ZIP) so a zip-bomb is caught by real decompressed-byte accounting,
    never by attacker-controlled central-directory metadata.

Hang-safety / purity: no network I/O here; callers own the socket. The xlsx
guard reads from a local file only.
"""

DEFAULT_MAX_JSON_BYTES = 25 * 1024 * 1024          # 25 MiB buffered-body cap
DEFAULT_MAX_XLSX_COMPRESSED_BYTES = 50 * 1024 * 1024   # fast precheck on the file
DEFAULT_MAX_XLSX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024  # authoritative streaming cap
_XLSX_READ_CHUNK = 1024 * 1024                     # 1 MiB incremental chunk


class BoundedReadError(ValueError):
    """Raised when a response/entry exceeds its configured byte cap."""


def capped_json(resp: Any, max_bytes: int = DEFAULT_MAX_JSON_BYTES) -> Any:
    """Parse ``resp`` JSON only if its already-buffered body is <= max_bytes.

    PARTIAL mitigation (see design CORRECTION 2): for a non-streaming httpx
    response the body is ALREADY in resp.content, so this bounds the
    parse/store path, not the network read. Raises BoundedReadError if the
    buffered body exceeds the cap.
    """
    body = getattr(resp, "content", None)
    if body is not None and len(body) > max_bytes:
        raise BoundedReadError(f"response body {len(body)}B exceeds cap {max_bytes}B")
    return resp.json()


def capped_read_bytes(resp: Any, max_bytes: int = DEFAULT_MAX_JSON_BYTES) -> bytes:
    """Bounded read of an http.client.HTTPResponse / urllib HTTPError body.

    SOUND: resp.read(max_bytes + 1) bounds the actual socket read; if the
    result exceeds max_bytes we raise rather than return the oversized body.
    Used for the stdlib-urllib adapters' success AND HTTPError branches.
    """
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BoundedReadError(f"response body exceeds cap {max_bytes}B")
    return data


def reject_oversized_xlsx(
    path: str,
    *,
    max_compressed_bytes: int = DEFAULT_MAX_XLSX_COMPRESSED_BYTES,
    max_uncompressed_bytes: int = DEFAULT_MAX_XLSX_UNCOMPRESSED_BYTES,
) -> None:
    """Raise BoundedReadError if the .xlsx at ``path`` is a zip-bomb.

    1. Fast precheck: os.path.getsize(path) > max_compressed_bytes -> reject.
    2. AUTHORITATIVE (see CORRECTION 1): open each ZIP entry with zf.open(info)
       and read in _XLSX_READ_CHUNK chunks, accumulating a GLOBAL decompressed
       byte counter across ALL entries. The instant the running total crosses
       max_uncompressed_bytes, abort with BoundedReadError. Declared
       ZipInfo.file_size / .compress_size are NEVER trusted for the decision.
    Call this BEFORE openpyxl.load_workbook.
    """
    import zipfile

    compressed = os.path.getsize(path)
    if compressed > max_compressed_bytes:
        raise BoundedReadError(
            f"xlsx compressed size {compressed}B exceeds cap {max_compressed_bytes}B"
        )
    total = 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            with zf.open(info) as entry:
                while True:
                    chunk = entry.read(_XLSX_READ_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_uncompressed_bytes:
                        raise BoundedReadError(
                            f"xlsx decompressed output exceeds cap "
                            f"{max_uncompressed_bytes}B (zip-bomb)"
                        )
```

`BoundedReadError` subclasses `ValueError` so existing adapter/health try/except
`Exception` handlers (which turn errors into `{"ok": False, ...}`) keep working,
and tests can assert `pytest.raises(ValueError)`.

### Call-site inventory

**~21 `resp.json()` sites → `capped_json(resp)` (option (a), minimum viable):**

| File:line | Function | Call |
|---|---|---|
| `linear.py:144` | `_graphql` | `resp.json()` |
| `azure_boards.py:122` | `_fetch_work_items` | `resp.json().get("value", [])` |
| `azure_boards.py:171` | `fetch_issues` | `wiql_resp.json().get("workItems", [])` |
| `azure_boards.py:190` | `update_status` | `patch.json()` |
| `azure_boards.py:209` | `add_comment` | `resp.json()` |
| `asana.py:153` | `fetch_issues` | `resp.json().get("data", [])` |
| `asana.py:173` | `update_status` | `resp.json().get("data", {})` |
| `asana.py:180` | `update_status` | `section_resp.json().get("data", {})` |
| `asana.py:198` | `add_comment` | `resp.json().get("data", {})` |
| `trello.py:124` | `_list_names` | `resp.json()` |
| `trello.py:170` | `fetch_issues` | `resp.json()` |
| `trello.py:189` | `update_status` | `move.json()` |
| `trello.py:207` | `add_comment` | `resp.json()` |
| `gitlab_issues.py:279` | `fetch_issues` | `resp.json()` |
| `gitlab_issues.py:328` | `add_comment` | `resp.json() if 2xx` |
| `servicenow.py:260` | `fetch_issues` | `resp.json()` |
| `servicenow.py:328` | `_result_dict` | `resp.json()` |
| `jira.py:228` | `fetch_issues` | `resp.json() or {}` |
| `jira.py:267` | `update_status` | `resp.json() or {}` |
| `github_issues.py:132` | `_call` default transport | `resp.status_code, resp.json()` |
| `redmine.py:365` | `fetch_issues` | `self._extract_issues(resp.json())` |

Notes for the migration:
- `gitlab_issues`, `jira`, `redmine` use an injectable transport whose response
  is a `Protocol` exposing `status_code` + `json()` but NOT necessarily
  `.content`. `capped_json` guards with `getattr(resp, "content", None)` and
  no-ops the size check when `.content` is absent (mock transports, `requests`
  Response has `.content`, httpx `Response` has `.content`) — it still bounds
  the parse for real httpx/requests, and is safe for mocks. Do NOT require
  `.content`.
- `servicenow.py:328` (`_result_dict`) is wrapped in a `try/except Exception`
  that returns `{}` — a `BoundedReadError` there degrades to `{}`, acceptable.
- `github_issues.py:132` is inside the default httpx transport only; the raw
  injectable transport path (`_raw_transport`, line 120) is caller-owned.
- **Hot read paths eligible for option (b)** (`client.stream()` + `iter_bytes`
  cap): the `fetch_issues` list responses — `linear.py:189-191`,
  `azure_boards.py:117-123/164-172`, `asana.py:147-153`, `trello.py:163-170`,
  `gitlab_issues.py:274-279`, `jira.py:227-228`, `servicenow.py:259-260`,
  `redmine.py:361-365`. Write-back responses are small; leave them on (a).

**3 stdlib-urllib `.read()` sites → `capped_read_bytes` (success + HTTPError branches):**

| File | Success read | HTTPError-branch read |
|---|---|---|
| `clickup.py` | `:132` `body = resp.read()` | `:135` `body = exc.read()` |
| `monday.py` | `:143` `body = resp.read()` | `:146` `body = exc.read()` |
| `bitbucket_issues.py` | `:134` `body = resp.read()` | `:137` `body = exc.read()` |

Replace both branches per file, e.g. clickup:

```python
from general_ludd.security.bounded_http import capped_read_bytes
...
try:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = int(getattr(resp, "status", 0) or resp.getcode() or 0)
        body = capped_read_bytes(resp)
except urllib.error.HTTPError as exc:
    status = int(exc.code)
    body = capped_read_bytes(exc) if hasattr(exc, "read") else b""
```

`http.client.HTTPResponse.read(n)` and `HTTPError.read(n)` both honor the byte
argument, so this is the sound bounded read (CORRECTION 3). `monday.py` uses a
`_NoRedirectHandler` opener but the response type is identical.

**2 `csv_excel.py` `load_workbook` sites → `reject_oversized_xlsx(path)` before load:**

| File:line | Function | Load |
|---|---|---|
| `csv_excel.py:128` | `_load_xlsx_rows` (read) | `openpyxl.load_workbook(self.path, read_only=True, data_only=True)` |
| `csv_excel.py:238` | `_write_back_xlsx` (write) | `openpyxl.load_workbook(self.path)` |

Insert `reject_oversized_xlsx(self.path)` immediately before each
`load_workbook` (after the guarded `openpyxl` import, and for the write path
after the existing `os.path.exists` check at `:236`). This is a *file*-based
source (no network); the guard reads the local file only.

### TESTS (new `tests/unit/test_security_bounded_http.py`)

No `test_security_bounded_http*` or `test_security_ssrf*` file exists today —
this is new. Test list:

- `test_capped_json_rejects_oversized_buffered_body` — a fake response with
  `content=b"x" * (cap + 1)` raises `BoundedReadError`; assert `.json()` is
  NEVER called (spy) so the cap fires before parse.
- `test_capped_json_passes_small_body` — `content` under cap → returns the
  parsed object.
- `test_capped_json_noops_without_content_attr` — a mock response lacking
  `.content` (Protocol-only transport) still parses (size check skipped), proving
  the injectable-transport adapters don't break.
- `test_capped_read_bytes_bounds_socket_read` — a fake `http.client`-style object
  whose `read(n)` returns `min(n, huge)` bytes; assert it is called with
  `max_bytes + 1` and raises when the returned length exceeds the cap (the SOUND
  path — verify it does not read the full hostile body).
- `test_capped_read_bytes_small_ok` — under-cap body returned intact.
- `test_reject_oversized_xlsx_compressed_precheck` — a file larger than
  `max_compressed_bytes` (monkeypatch `os.path.getsize` or write a big temp file
  with a tiny cap) raises before any ZIP open.
- **`test_reject_oversized_xlsx_zip_bomb_incremental`** (the load-bearing one) —
  build a real zip whose single entry is a highly-compressible payload, e.g.
  `zf.writestr("sheet.bin", b"0" * 50_000_000)` (50 MB of `"0"` deflates to a
  few KB on disk). Call `reject_oversized_xlsx(path, max_uncompressed_bytes=1_000_000)`
  and assert it raises `BoundedReadError`. Crucially, assert it aborts via the
  **incremental read** (small `max_uncompressed_bytes`, tiny compressed file that
  PASSES the compressed precheck) — proving the decision comes from real
  decompressed-byte accounting, not declared `ZipInfo.file_size`. Optionally
  monkeypatch/inspect that not all 50 MB is materialized (chunked reads stop early).
- `test_reject_oversized_xlsx_normal_workbook_ok` — a small legitimately-built
  `.xlsx` (via openpyxl if available, else a small hand-built zip) passes.
- `test_bounded_read_error_is_value_error` — `issubclass(BoundedReadError, ValueError)`
  so adapter health/except paths and `pytest.raises(ValueError)` keep working.

Adapter-level integration (add 1-2 to existing adapter test files, optional):
- clickup/monday/bitbucket: a mock transport returning an oversized body in the
  HTTPError branch → the adapter surfaces a bounded error, not an OOM.
- csv_excel: `_load_xlsx_rows` on a zip-bomb `.xlsx` raises before `load_workbook`.

### Acceptance
`bounded_http.py` exists with the three primitives; the ~21 `.json()` sites use
`capped_json`, the 6 urllib `.read()` sites (success + HTTPError) use
`capped_read_bytes`, and both `csv_excel.py` `load_workbook` sites are preceded by
`reject_oversized_xlsx`; the zip-bomb test proves incremental-read abort without
full decompression; option (a) is documented as partial and (b) (`client.stream`
+ `iter_bytes` cap on the `fetch_issues` hot paths) as the sound follow-on.

---

## Landing plan
1. **Part A** (per-adapter, 5 commits or one batch): delete local blocklist +
   delegate to `host_is_blocked`; add the metadata-alias parametrized test.
   `make test-iso TESTFILE=tests/unit/test_issue_source_<adapter>.py` each.
2. **Part B step 1**: add `security/bounded_http.py` + `test_security_bounded_http.py`
   (including the real zip-bomb test). Land standalone (no call-site churn) —
   verifiable in isolation.
3. **Part B step 2**: wire `capped_json` / `capped_read_bytes` /
   `reject_oversized_xlsx` into the inventory above (option (a)); re-run each
   adapter's test file.
4. **Part B step 3 (follow-on)**: migrate the `fetch_issues` hot paths to
   `client.stream()` + `iter_bytes` cap (option (b), the sound memory bound).
5. CI-green per batch before the next.
