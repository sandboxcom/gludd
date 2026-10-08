# Gludd Core Module Utils Contract

Status: **codified** — all libraries below are landed and document their current API surface.
Delegation-wrapper libraries in §5 follow the delegation pattern defined in §3.4–§3.5.

## 1. Location and import path

Core module_utils live under:

```text
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

### 3.4 Module_utils delegate to core — never reimplement

**Every module_utils library MUST delegate to existing core Python code under
`src/general_ludd/`.** It must never reimplement logic that already exists in
the core package. A module_utils is an Ansible-runtime adapter — it wraps core
behaviour, it does not duplicate it.

**Before adding a module_utils**, check whether `src/general_ludd/` already has
the implementation:

- If yes → **wrap, don't rewrite.** Add a thin adapter in module_utils that
  imports and calls the core module.
- If no → implement in `src/general_ludd/` first (with full test coverage,
  type hints, and observability), then add the module_utils wrapper.

**Why this matters.** Duplicating core logic into an Ansible module_utils is a
regression: the duplicate diverges from the core under maintenance, tests
cover one path but not the other, and fixes applied to one copy silently miss
the other. A thin adapter keeps the single source of truth in `src/` and lets
the module_utils carry only the Ansible-specific glue (result-shaping,
stdlib-only deps, error translation).

### 3.5 Delegation map

Each module_utils library delegates to one or more core Python modules:

| Module_utils | Delegates to (core) | Path in `src/general_ludd/` |
|---|---|---|
| `model_client` | ModelGateway | `models/gateway.py` |
| `embeddings` | SkillEmbedder, HashEmbedder | `skills/embeddings.py` |
| `rag` | embeddings + model_client (→ ModelGateway) | `skills/embeddings.py` + `models/gateway.py` |
| `searxng` | SearXConnector | `connectors/searx.py` |
| `capability_router` | CapabilityRouter | `dispatch/router.py` |
| `gludd` | ModelGateway (lazy, for `local_model_call`) | `models/gateway.py` |
| `output_parser` | (greenfield — no core equivalent) | — |
| `document_loader` | (greenfield — no core equivalent) | — |
| `ansible_tools` | MCPClient, MCPToolRegistry (bridging) | `mcp/client.py`, `mcp/registry.py` |

Libraries marked **greenfield** in the delegation map have no corresponding core
implementation to delegate to. **Bridging** libraries adapt an existing subsystem
(here, Ansible module discovery/calling) to a different protocol path (MCP tool
interface) without duplicating the underlying logic.

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

### 4.7 `output_parser` — Structured-output parsers (greenfield)

**File:** `module_utils/output_parser.py` (planned)
**Kind:** greenfield — no core equivalent; this is a standalone stdlib-only parser library.

API surface:

| Symbol | Kind | Purpose |
|---|---|---|
| `JsonOutputParser` | class | Extracts a JSON object from arbitrary model output. Strips fences, handles partial JSON recovery (trailing commas, unclosed braces), validates against an optional schema. Method: `parse(text) -> tuple[dict | None, str | None]`. |
| `PydanticOutputParser` | class | Validates parsed JSON against a Pydantic model at runtime. Constructed with a target model class. Method: `parse(text) -> tuple[BaseModel | None, str | None]`. Falls back to `JsonOutputParser` for the JSON extraction step. |
| `MarkdownOutputParser` | class | Extracts a Markdown code block by language tag, strips fences, and returns the raw inner content. Method: `parse(text) -> tuple[str | None, str | None]`. Useful as a preprocessor before piping to `JsonOutputParser` or `PydanticOutputParser`. |

Design notes:
- `parse()` returns `(parsed, error)` tuples — never raises on malformed input.
- `PydanticOutputParser` imports `pydantic` lazily (stdlib-only constraint lifted only at parse time).
- All three parsers are independent and composeable: `MarkdownOutputParser` → `JsonOutputParser` → `PydanticOutputParser`.

---

### 4.8 `document_loader` — File/document ingestion (greenfield)

**File:** `module_utils/document_loader.py` (planned)
**Kind:** greenfield — no core equivalent; stdlib-only document loading from local paths.

API surface:

| Symbol | Kind | Purpose |
|---|---|---|
| `Document(text=..., metadata=... | None)` | dataclass | A single document chunk with content text and optional metadata dict (source path, page number, encoding). |
| `TextLoader(path, encoding="utf-8")` | class | Reads a plain-text file and returns one `Document`. Method: `load() -> Document`. |
| `HTMLLoader(path)` | class | Reads an HTML file, strips tags to plain text via `html.parser`, and returns one `Document`. Method: `load() -> Document`. No external deps (stdlib `html.parser` only). |
| `DirectoryLoader(path, glob_pattern="**/*.txt", loader_cls=TextLoader)` | class | Recursively walks a directory, matches `glob_pattern`, and instantiates `loader_cls` for each file. Method: `load() -> list[Document]`. |

Design notes:
- All loaders follow the same `load()` interface — `DirectoryLoader` can wrap any loader.
- File I/O errors raise `FileNotFoundError` or `PermissionError` natively; no custom exception class needed.
- `DirectoryLoader` excludes dotfiles and `.git/` by default.

---

## 5. Delegation-wrapper libraries (landed — see §3.5)

These libraries were formerly listed as planned; they are now built and follow the
delegation pattern described in §3.4–§3.5. Each wraps an existing core module under
`src/general_ludd/` with an Ansible-compatible (stdlib-only, urllib-based) adapter.

| Library | File | Lines | Core delegate | Status |
|---|---|---|---|---|
| `model_client` | `module_utils/model_client.py` | 252 | `models/gateway.py` (via daemon HTTP) | **landed** |
| `rag` | `module_utils/rag.py` | 243 | `skills/embeddings.py` + `models/gateway.py` | **landed** |
| `searxng` | `module_utils/searxng.py` | 532 | `connectors/searx.py` | **landed** |
| `capability_router` | `module_utils/capability_router.py` | 315 | `dispatch/router.py` | **landed** |
| `ansible_tools` | `module_utils/ansible_tools.py` | planned | `mcp/client.py`, `mcp/registry.py` (bridging) | **planned** |

### 5.1 `ansible_tools` — Ansible-to-MCP tool bridge (bridging)

**File:** `module_utils/ansible_tools.py` (planned)
**Kind:** bridging — adapts Ansible module discovery and invocation to the MCP tool-call path so model agents can use Ansible modules through the same interface as external MCP servers.

API surface:

| Symbol | Kind | Purpose |
|---|---|---|
| `AnsibleToolAdapter(module_name, server_id="ansible")` | class | Wraps one Ansible module as an MCP-compatible tool. Methods: `tool_spec() -> dict` (returns the MCP `Tool` JSON schema derived from the module's `DOCUMENTATION`), `execute(args) -> dict` (runs the module via `ansible-runner` and shape the result as an MCP `CallToolResult`). |
| `discover_tools(collection_path=None)` | function | Scans an Ansible collection for executable modules and returns a list of `AnsibleToolAdapter` instances, one per discoverable module. Filters to modules with `DOCUMENTATION` blocks. |
| `call_tool(tool_name, args, server_id="ansible")` | function | One-shot: discovers the named tool, instantiates the adapter, calls `execute(args)`, and returns the result. Convenience wrapper for stateless callers that do not need to cache adapter instances. |

**Delegation chain:**
- `execute()` delegates to the core `ansible-runner` integration (same path used by the daemon's Ansible runner).
- Tool specs (JSON Schema) are derived from Ansible module `DOCUMENTATION` strings at discovery time.
- The `mcp/registry.py` and `mcp/client.py` patterns define the `Tool` schema shape and `CallToolResult` structure this adapter conforms to.

**Why bridging, not greenfield:** Ansible modules already exist and are callable through the daemon's runner. This library does not reimplement module discovery or execution — it translates between the Ansible module interface (DOCUMENTATION, JSON args, JSON results) and the MCP tool interface (Tool schema, CallToolResult). The underlying execution path is the same core runner.

Used by: modules that expose Ansible capabilities through the MCP tool path (model-driven playbook dispatch).
