# Gludd Core Module Utils Contract

Status: **codified** — all existing libraries below document the current API surface.
Planned libraries (not yet landed) are marked **planned**.

## 1. Location and import path

Core module_utils live under:

```
collections/ansible_collections/general_ludd/agent/plugins/module_utils/
```

Any collection imports them via the fully-qualified Ansible collection path:

```python
from ansible_collections.general_ludd.agent.plugins.module_utils.<lib> import <symbol>
```

There is no alternative short-name import path.  Every module in a collection
that depends on core utilities MUST use the FQCN above.

## 2. Universal contract

Every library in this directory must satisfy:

| Requirement | Detail |
|---|---|
| **Documented API** | A module-level docstring listing every public symbol, its parameters, return type, and purpose. |
| **Type hints** | All public functions and class methods carry complete type annotations (`__future__ import annotations`). |
| **Tests** | A corresponding test file under `tests/unit/` exercises the public API. |
| **Timeout handling** | Any blocking I/O (HTTP, subprocess, model calls) accepts a `timeout` parameter and raises on expiry. |
| **Error propagation** | Errors are surfaced as exceptions (`WritePolicyError`, `CapabilityError`, `IntegrityViolation`) or structured return tuples (`(obj, reason)`); they are **never** silently swallowed. |
| **No third-party deps** | Stdlib only — Ansible module execution runs on managed nodes that may not have `requests`, `numpy`, etc. available. |

## 3. Cardinal rules

### 3.1 Collections MUST NOT reimplement these

If a module_utils library already provides the capability your module needs, use it.
If it is missing the specific function or parameter you need, **add it to the core
library** — do not copy/paste it into your module or a collection-local helper.

### 3.2 New module_utils require spec + tests

Before a new library lands in this directory:

1. A one-page spec (`docs/design/<name>_SPEC.md`) describing the problem, API surface,
   dependency model, and failure modes.
2. A test file under `tests/unit/` with **passing tests** before the implementation PR.
3. All five universal-contract requirements above must be met at merge time.

### 3.3 Shared ownership, breaking-change discipline

All collection modules depend on these libraries.  A breaking change (removed function,
changed return shape, renamed parameter) must be treated as a release-level event —
coordinated across all callers, documented in CHANGELOG.md, and gated behind a
deprecation cycle where possible.

## 4. Available libraries

### 4.1 `gludd` — Daemon client + structured-output helpers

**File:** `module_utils/gludd.py` (239 lines)  
**Test:** `tests/unit/test_module_utils_structured.py`

API surface:

| Symbol | Kind | Purpose |
|---|---|---|
| `GluddClient` | class | HTTP transport to the Gludd daemon (PSK auth, urllib-only, GET/POST/PATCH). Params: `base_url`, `psk`, `timeout`. |
| `ok_result(data, changed=False)` | function | Wrap a dict as a successful Ansible-style result (`{"failed": False, ...}`). |
| `error_result(msg, **extra)` | function | Wrap a dict as a failed Ansible-style result (`{"failed": True, "msg": ...}`). |
| `strip_code_fences(text)` | function | Remove surrounding Markdown code fences (e.g., ` ```json ... ``` ` → inner JSON). Never raises. |
| `parse_structured(text, schema=None)` | function | Fence-strip then `json.loads`; returns `(obj, None)` on success or `(None, reason)` on failure. Never raises. |
| `local_model_call(prompt, ...)` | function | Direct in-process model call (imports `ModelGateway` lazily); falls back with `error_result` if the daemon is not importable. |

Used by: every module that talks to the daemon (30+ modules).

---

### 4.2 `embeddings` — Embedding client + in-memory vector store

**File:** `module_utils/embeddings.py` (212 lines)  
**Test:** pending (spec exists, implementation complete; test gap tracked in TASKS.md)

API surface:

| Symbol | Kind | Purpose |
|---|---|---|
| `EmbeddingClient(model_profile, timeout=60)` | class | Lazy model-gateway adapter. Methods: `embed_text(text) -> list[float]`, `embed_batch(texts) -> list[list[float]]`, `cosine_similarity(a, b) -> float` (static). |
| `VectorStore` | class | In-memory brute-force cosine-similarity vector index. Methods: `add(id, vec)`, `remove(id)`, `clear()`, `search(query, k) -> list[(id, score)]`, `similarity(query, id) -> float`, `get(id) -> list[float]`, `list_ids() -> list[str]`. Supports `__len__` and `__contains__`. |

---

### 4.3 `capability_policy` — Per-role authorisation

**File:** `module_utils/capability_policy.py` (481 lines)  

API surface:

| Symbol | Kind | Purpose |
|---|---|---|
| `CapabilityPolicy(role=..., fs_write=False, ...)` | dataclass | Per-role capability grant set. Methods: `check_fs_write(path)`, `check_collections_self_modify(path)`, `check_facts_access(path)`, `check_network_host(host)`, `check_secret_access(alias)`, `check_db_op(op)`, `write_policy()`. All default-DENY. |
| `CapabilityError` | exception | Raised (fail-closed) when a role lacks a capability. |
| `for_role(role_name, config=None)` | function | Resolve a `CapabilityPolicy` from built-in table or caller-supplied config. |
| `extract_host(url_or_host)` | function | Strip scheme/port/userinfo from a URL; return bare hostname. |

Used by: `gludd_db` (the only module that performs capability-gated operations).

---

### 4.4 `fs_write_policy` — Filesystem-write allow/deny guard

**File:** `module_utils/fs_write_policy.py` (263 lines)  

API surface:

| Symbol | Kind | Purpose |
|---|---|---|
| `WritePolicy(allowed_roots=..., sensitive_names=..., sensitive_suffixes=...)` | dataclass | Default-DENY path allowlist. Method: `check(path) -> resolved_path` (raises `WritePolicyError` on violation); `is_allowed(path) -> bool` (never raises). |
| `WritePolicyError` | exception | Raised when a write target violates the policy. |
| `default_policy(workspace=..., worktree_root=..., extra_roots=..., include_tmp=True)` | function | Build a standard policy with workspace, worktree root, and `/tmp`. |
| `SENSITIVE_NAMES` | frozenset | Component names (`.git`, `.ssh`, `.aws`, ...) refused even inside allowed roots. |
| `SENSITIVE_SUFFIXES` | tuple | Filename suffixes (`.pem`, `.key`, ...) refused even inside allowed roots. |

Used by: `gludd_worktree`, `fs_write_audit`.

---

### 4.5 `fs_write_audit` — FIM-on-write audit log

**File:** `module_utils/fs_write_audit.py` (269 lines)  

API surface:

| Symbol | Kind | Purpose |
|---|---|---|
| `WriteAuditLog(policy, manifest_path="")` | dataclass | Wraps a `WritePolicy` with tamper-detection. Methods: `pre_write_check(path)`, `record_write(path)`, `audited_write(path, data)` (pre-check + write + record in one call), `entry_for(path)`, `recorded_paths()`. |
| `AuditEntry(path, sha256, recorded_at)` | dataclass | One recorded write entry. Methods: `to_dict()`, `from_dict(data)`. |
| `IntegrityViolation` | exception | Raised when on-disk bytes don't match the previously recorded hash. |
| `hash_bytes_on_disk(path)` | function | Return the sha256 hex digest of a file's current contents. |

Used by: `gludd_worktree`.

---

### 4.6 `gludd_stream_buffer` — Rolling byte buffer

**File:** `module_utils/gludd_stream_buffer.py` (147 lines)  
**Test:** `tests/unit/test_gludd_stream_buffer.py`

API surface:

| Symbol | Kind | Purpose |
|---|---|---|
| `RollingBuffer(max_bytes=1048576)` | class | Bounded byte sink. Methods: `push(data)`, `peek() -> bytes`, `peek_head(n) -> bytes`, `peek_tail(n) -> bytes`, `find_key(key) -> int | None`, `split_at(offset) -> (head, tail)`, `drain() -> bytes`. Supports `__len__` and `size` property. |

Used by: `gludd_stream`.

---

## 5. Planned libraries (not yet landed)

| Library | Purpose | Status |
|---|---|---|
| `model_client` | Unified model-call interface abstracting model provider differences | **planned** — no spec yet |
| `rag` | Higher-level retrieval-augmented generation pipeline (chunking, retrieval, prompt assembly) | **planned** — depends on `embeddings` landing first |
| `searxng` | SearXNG client for web search as a tool capability | **planned** — no spec yet |
| `capability_router` | Dynamic capability routing (which model/role handles which task type) | **planned** — no spec yet |
