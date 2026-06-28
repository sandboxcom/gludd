# MCP Catalog/Loader OOM + Blocking-IO Fix Spec (apply-ready)

Status: **✅ APPLIED** — 2026-06-28. All four findings (C-1, C-2, C-3, C-4) are
implemented in `src/general_ludd/mcp/catalog.py` and `src/general_ludd/mcp/loader.py`
with tests in `tests/unit/test_mcp_catalog.py` and `tests/unit/test_mcp_loader.py`.
Source: read-only audit agent ac555. Files were disjoint from all other in-flight
streams (catalog.py / loader.py / test_mcp_catalog.py / test_mcp_loader.py) and were
applied in isolation.

## C-1 — `mcp/catalog.py:137-143` — Unbounded `resp.read()` (Smithery branch) [HIGH]

Add module-level constant (near line 14, after `logger = ...`):
```python
_REGISTRY_RESPONSE_MAX_BYTES = 2 * 1024 * 1024  # 2 MB hard cap per registry response
```
Replace the Smithery `urlopen` read:
```python
with urllib.request.urlopen(req, timeout=10) as resp:
    raw = resp.read(_REGISTRY_RESPONSE_MAX_BYTES + 1)
    if len(raw) > _REGISTRY_RESPONSE_MAX_BYTES:
        raise ValueError(
            f"Smithery registry response exceeded {_REGISTRY_RESPONSE_MAX_BYTES}-byte cap"
        )
    data = json.loads(raw.decode())
```
`resp.read(n)` bounds memory at n+1; the `ValueError` is caught by the existing
`except Exception` at ~line 61-62 → search degrades gracefully to empty.

## C-2 — `mcp/catalog.py:158-164` — Same unbounded read (MCP registry branch) [HIGH]

Identical fix, reusing `_REGISTRY_RESPONSE_MAX_BYTES`:
```python
with urllib.request.urlopen(req, timeout=10) as resp:
    raw = resp.read(_REGISTRY_RESPONSE_MAX_BYTES + 1)
    if len(raw) > _REGISTRY_RESPONSE_MAX_BYTES:
        raise ValueError(
            f"MCP registry response exceeded {_REGISTRY_RESPONSE_MAX_BYTES}-byte cap"
        )
    data = json.loads(raw.decode())
```

## C-3 — `mcp/catalog.py:99-143` — Blocking `urlopen` callable from async context [MEDIUM]

Do NOT convert sync `search()` (breaking; CLI/test callers rely on it). Add an async
variant after `search()` (after ~line 63):
```python
async def search_async(
    self, query: str = "", limit: int = 20, source: str | None = None,
) -> list[MCPCatalogEntry]:
    """Async search() — dispatches blocking network I/O to a thread pool.
    Use from any async caller (FastAPI route, event-loop task)."""
    import asyncio
    results: list[MCPCatalogEntry] = []
    for registry in self._registries:
        if source and source not in registry:
            continue
        try:
            entries = await asyncio.to_thread(self._query_registry, registry, query, limit)
            results.extend(entries)
        except Exception as exc:
            logger.debug("Registry %s query failed: %s", registry, exc)
    return results[:limit]
```
Add a one-line docstring to `search()`: "Synchronous registry search. Blocks the event
loop — use search_async() from async callers."

## C-4 — `mcp/loader.py:14-15` — No size cap before `yaml.safe_load` [LOW]

```python
_MCP_CONFIG_MAX_BYTES = 512 * 1024  # 512 KB — MCP configs are tiny; cap fails hard

def load_mcp_config(config_path: str) -> dict[str, MCPServerConfig]:
    path = Path(config_path)
    if not path.exists():
        return {}
    size = path.stat().st_size
    if size > _MCP_CONFIG_MAX_BYTES:
        raise ValueError(
            f"MCP config {config_path!r} is too large "
            f"({size} bytes > {_MCP_CONFIG_MAX_BYTES}); refusing to load."
        )
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    ...
```
Hard raise (not graceful) — a >512 KB config indicates a path mistake or injection.

## transport.py — No open defects (all prior findings fixed in-file). No action.

## REQUIRED test changes (land WITH the production fix)

**Breaking mock fix:** the three `read` lambda mocks in `tests/unit/test_mcp_catalog.py`
(lines ~82, ~101, ~120) are `lambda self:` with no `n` param → `resp.read(CAP+1)` will
`TypeError`. Change each to `lambda self, n=None: <payload> if n is None else <payload>[:n]`.

**New tests** (full bodies in agent ac555 transcript):
- `test_search_smithery_rejects_oversized_response` (C-1) — oversized → `results == []`
- `test_search_mcp_registry_rejects_oversized_response` (C-2)
- `test_search_async_empty_registries` + `test_search_async_dispatches_via_thread` (C-3, needs pytest-asyncio)
- NEW file `tests/unit/test_mcp_loader.py`: missing-file → `{}`, normal load, oversized → ValueError, at-cap passes.

## Severity table
| ID | File | Lines | Defect | Severity |
|----|------|-------|--------|----------|
| C-1 | catalog.py | 113-114 | unbounded read (Smithery) | HIGH |
| C-2 | catalog.py | 129-130 | unbounded read (MCP registry) | HIGH |
| C-3 | catalog.py | 99-143 | blocking urlopen from async | MEDIUM |
| C-4 | loader.py | 14-15 | no size cap before yaml load | LOW |
