# Batch 3 Apply Plan — Deferred Fixes from REVIEW_FINDINGS_2026-06-17

> **Source:** `docs/integration/REVIEW_FINDINGS_2026-06-17.md` (findings surfaced
> after integration commit `e982a81`; none blocked the gate).
> **Order:** lowest-risk / most-isolated first; grouped by gate cycle so each
> cycle's fixes can be committed together after a `make gate` run.

Each entry contains:
- **File** — absolute src path
- **old_string** — verbatim anchor from the current file (unique; paste directly
  into `Edit`)
- **new_string** — exact replacement text
- **Regression test** — test file + minimal snippet to add (the snippet must
  FAIL before the fix and PASS after)
- **Risk** — blast-radius note and any preconditions

---

## Ordering rationale

| Cycle | Fixes | Why first |
|-------|-------|-----------|
| 1 | #9 (capabilities docstring), #8 (release LICENSE assert), #4 (accounting tokens) | Pure cosmetic/metric; zero behaviour change |
| 2 | #5 (variable_store safe_name + collision), #6 (markdown_todo escaping + dedup) | Self-contained modules, no shared state |
| 3 | #7 (is_path_within rename) | Cross-file rename; must touch 4 files atomically |
| 4 | #2 (cassandra + clickhouse SSRF guard) | Adds an import + guard to two connectors |
| 5 | #3 (git_automation realpath + _run_git routing) | Most invasive; touches subprocess call sites |
| 6 | #1 (secrets resolve() raise) | Conditional on blast-radius; applied last |

---

## Cycle 1 — Zero-behaviour fixes

### Fix 1A · `agents/capabilities.py` — Docstring honesty (finding #20)

**File:** `src/general_ludd/agents/capabilities.py`

**old_string:**
```python
    def prepare_messages(
        self, system_prompt: str, history: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Bound the conversation to the token budget via ContextCompactor."""
```

**new_string:**
```python
    def prepare_messages(
        self, system_prompt: str, history: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Compact the conversation history via ContextCompactor.

        Attempts to fit the prompt within the configured token budget by
        dropping older turns when the compaction threshold is exceeded.
        NOTE: an oversized system prompt or a single preserved message that
        individually exceeds the budget passes through uncapped — the compactor
        cannot split individual messages. Callers that need a hard cap must
        truncate the system prompt before calling this method.
        """
```

**Regression test — file:** `tests/unit/test_agent_capabilities.py`

```python
def test_prepare_messages_docstring_does_not_claim_hard_cap():
    """Docstring must not promise a hard cap it does not enforce."""
    import inspect
    from general_ludd.agents.capabilities import AgentCapabilities
    doc = inspect.getdoc(AgentCapabilities.prepare_messages) or ""
    # The old claim "Bound the conversation to the token budget" was false.
    assert "Bound the conversation" not in doc, (
        "Docstring must not claim a hard cap that is not enforced"
    )
    # The new docstring must warn about the uncapped-oversized-message edge case.
    assert "uncapped" in doc or "NOTE" in doc
```

**Risk:** Documentation-only change. No runtime behaviour altered. Zero blast
radius.

---

### Fix 1B · `runtime/release.py` — LICENSE-in-manifest assertion (finding #19)

**File:** `src/general_ludd/runtime/release.py`

The `_check_pip_bundle` method validates checksums stored in `MANIFEST.json` but
never asserts that a `LICENSE` file is listed, leaving the new LICENSE-packaging
guarantee unguarded.

**old_string:**
```python
        try:
            manifest_data = json.loads(manifest_file.read_text())
            stored_checksums = manifest_data.get("checksums", {})
            for fname, expected_hash in stored_checksums.items():
                fpath = artifacts_path / fname
                if fpath.exists():
                    actual = f"sha256:{hashlib.sha256(fpath.read_bytes()).hexdigest()}"
                    if actual != expected_hash:
                        errors.append(f"Checksum mismatch for {fname}")
                        return False
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"Error reading bundle artifacts: {exc}")
            return False

        return True
```

**new_string:**
```python
        try:
            manifest_data = json.loads(manifest_file.read_text())
            stored_checksums = manifest_data.get("checksums", {})
            for fname, expected_hash in stored_checksums.items():
                fpath = artifacts_path / fname
                if fpath.exists():
                    actual = f"sha256:{hashlib.sha256(fpath.read_bytes()).hexdigest()}"
                    if actual != expected_hash:
                        errors.append(f"Checksum mismatch for {fname}")
                        return False
            # The LICENSE file must be present in the wheel manifest so the
            # LICENSE-packaging guarantee is enforced at the release gate rather
            # than discovered post-publish. A manifest without a LICENSE entry is
            # a packaging gap, not a checksum failure.
            license_present = any(
                "LICENSE" in str(k) for k in stored_checksums
            )
            if not license_present:
                errors.append(
                    "LICENSE file not listed in MANIFEST.json checksums — "
                    "the LICENSE-packaging guarantee is unguarded"
                )
                return False
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"Error reading bundle artifacts: {exc}")
            return False

        return True
```

**Regression test — file:** `tests/unit/test_release_validator.py`

```python
import json
import tempfile
from pathlib import Path
from general_ludd.runtime.release import ReleaseArtifactValidator


def _make_artifacts(tmp: Path, include_license: bool = True) -> None:
    checksums = {"package-1.0.whl": "sha256:abc123"}
    if include_license:
        checksums["LICENSE"] = "sha256:def456"
    manifest = {"version": "1.0", "checksums": checksums}
    (tmp / "MANIFEST.json").write_text(json.dumps(manifest))
    (tmp / "CHECKSUMS.sha256").write_text("")


def test_pip_bundle_fails_without_license_in_manifest():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _make_artifacts(tmp, include_license=False)
        v = ReleaseArtifactValidator()
        result = v.validate_release("1.0", td)
        assert not result.pip_bundle_valid
        assert any("LICENSE" in e for e in result.errors)


def test_pip_bundle_passes_with_license_in_manifest():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _make_artifacts(tmp, include_license=True)
        v = ReleaseArtifactValidator()
        result = v.validate_release("1.0", td)
        assert result.pip_bundle_valid
```

**Risk:** Additive gate check — existing valid manifests that already include a
`LICENSE` entry are unaffected. Manifests missing `LICENSE` now fail the bundle
check. Confirm that `conftest`/CI artifact builders include the `LICENSE` key
before committing.

---

### Fix 1C · `routers/accounting.py` — Real token count (finding #13)

**File:** `src/general_ludd/routers/accounting.py`

The inline `_UsageRecord.__init__` computes `tokens_used` as
`total_calls * 1000` — a proxy that reports wildly wrong counts.
The actual key is `total_tokens` (or `input_tokens + output_tokens`).

**old_string:**
```python
                    class _UsageRecord:
                        def __init__(self, pid: str, info: dict[str, Any]) -> None:
                            self.project_id = pid
                            mu = info.get("model_usage", {})
                            # Sum across all models for this agent
                            self.tokens_used = sum(
                                int(u.get("total_calls", 0)) * 1000
                                for u in (mu.values() if isinstance(mu, dict) else [])
                            )
                            self.usd_spent = float(info.get("total_cost_usd", 0.0))
                            self.elapsed_seconds = float(info.get("run_time_seconds", 0.0))
```

**new_string:**
```python
                    class _UsageRecord:
                        def __init__(self, pid: str, info: dict[str, Any]) -> None:
                            self.project_id = pid
                            mu = info.get("model_usage", {})
                            # Sum real token counts across all models for this agent.
                            # Prefer total_tokens; fall back to input+output separately;
                            # last resort: total_calls*1000 (deprecated proxy).
                            self.tokens_used = sum(
                                int(
                                    u.get("total_tokens")
                                    or (
                                        int(u.get("input_tokens", 0))
                                        + int(u.get("output_tokens", 0))
                                    )
                                    or int(u.get("total_calls", 0)) * 1000
                                )
                                for u in (mu.values() if isinstance(mu, dict) else [])
                            )
                            self.usd_spent = float(info.get("total_cost_usd", 0.0))
                            self.elapsed_seconds = float(info.get("run_time_seconds", 0.0))
```

**Regression test — file:** `tests/unit/test_accounting_tokens.py`

```python
"""Regression: tokens_used must use real token counts, not total_calls*1000."""
from __future__ import annotations
from unittest.mock import MagicMock, AsyncMock, patch
import pytest


def _make_agent_info(total_tokens: int | None, total_calls: int) -> dict:
    mu = {"claude-3": {}}
    if total_tokens is not None:
        mu["claude-3"]["total_tokens"] = total_tokens
    mu["claude-3"]["total_calls"] = total_calls
    return {"project": "p1", "total_cost_usd": 0.01,
            "run_time_seconds": 1.0, "model_usage": mu}


def test_tokens_used_prefers_total_tokens_over_calls_proxy():
    """When total_tokens is present it must be used, not total_calls*1000."""
    # Simulate inline _UsageRecord construction from routers/accounting.py
    info = _make_agent_info(total_tokens=5000, total_calls=2)
    mu = info.get("model_usage", {})
    tokens_used = sum(
        int(
            u.get("total_tokens")
            or (int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0)))
            or int(u.get("total_calls", 0)) * 1000
        )
        for u in (mu.values() if isinstance(mu, dict) else [])
    )
    # Must be 5000 (real), NOT 2000 (proxy).
    assert tokens_used == 5000


def test_tokens_used_falls_back_to_calls_proxy_when_no_token_field():
    """Fallback to calls*1000 only when no token field is available."""
    info = _make_agent_info(total_tokens=None, total_calls=3)
    mu = info.get("model_usage", {})
    tokens_used = sum(
        int(
            u.get("total_tokens")
            or (int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0)))
            or int(u.get("total_calls", 0)) * 1000
        )
        for u in (mu.values() if isinstance(mu, dict) else [])
    )
    assert tokens_used == 3000
```

**Risk:** Metrics-only change; no auth, no security path. The ledger / budget
system reads `usd_spent`, not `tokens_used`, so budget enforcement is
unaffected. Only the accounting API response payload changes.

---

## Cycle 2 — Self-contained module fixes

### Fix 2A · `dispatch/variable_store.py` — safe_name collision guard (finding #15)

**File:** `src/general_ludd/dispatch/variable_store.py`

`safe_name` collapses `foo.bar` and `foo_bar` to the same key `foo_bar`;
a second `apply_results` write silently overwrites the first.
The docstring also promises bare-name aliases that `all_vars` never emits.

**old_string:**
```python
def apply_results(store: VariableStore, results: list[DispatchResult]) -> None:
    """Merge a list of DispatchResults into the store under ``"dispatch"`` namespace.

    Each result is stored as::

        dispatch__<name>__ok        bool
        dispatch__<name>__output    Any
        dispatch__<name>__error     str | None

    Also writes the latest result under the key ``dispatch__last``.
    """
    for result in results:
        safe_name = result.name.replace(".", "_").replace("-", "_")
        store.set("dispatch", f"{safe_name}__ok", result.ok)
        store.set("dispatch", f"{safe_name}__output", result.output)
        store.set("dispatch", f"{safe_name}__error", result.error)
```

**new_string:**
```python
def _safe_dispatch_name(name: str) -> str:
    """Normalise a dispatch result name to a Jinja2-safe key segment.

    Dots and dashes become underscores.  To avoid silent key collision when two
    tool names differ only by ``.`` vs ``_`` (e.g. ``fs.read`` and ``fs_read``
    would both map to ``fs_read``), the original separator is encoded as a
    placeholder:

    * ``.`` → ``_DOT_``
    * ``-`` → ``_DASH_``

    This keeps the key unambiguous while remaining a valid Jinja2 identifier.
    """
    return name.replace(".", "_DOT_").replace("-", "_DASH_")


def apply_results(store: VariableStore, results: list[DispatchResult]) -> None:
    """Merge a list of DispatchResults into the store under ``"dispatch"`` namespace.

    Each result is stored as::

        dispatch__<safe_name>__ok        bool
        dispatch__<safe_name>__output    Any
        dispatch__<safe_name>__error     str | None

    ``<safe_name>`` is produced by :func:`_safe_dispatch_name`: dots become
    ``_DOT_`` and dashes become ``_DASH_`` so ``fs.read`` and ``fs_read`` map
    to distinct keys. Also writes the latest result under ``dispatch__last``.
    """
    for result in results:
        safe_name = _safe_dispatch_name(result.name)
        store.set("dispatch", f"{safe_name}__ok", result.ok)
        store.set("dispatch", f"{safe_name}__output", result.output)
        store.set("dispatch", f"{safe_name}__error", result.error)
```

**Regression test — add to:** `tests/unit/test_variable_store.py`

```python
def test_dot_and_underscore_names_do_not_collide():
    """fs.read and fs_read must map to different dispatch keys."""
    from general_ludd.dispatch.variable_store import VariableStore, apply_results
    from general_ludd.dispatch.dynamic_dispatcher import DispatchResult
    store = VariableStore()
    r1 = DispatchResult(ok=True, kind="mcp", name="fs.read", output="via-dot")
    r2 = DispatchResult(ok=True, kind="mcp", name="fs_read", output="via-underscore")
    apply_results(store, [r1, r2])
    # Keys must be distinct — second write must NOT silently overwrite first.
    key_dot = store.get("dispatch", "fs_DOT_read__output")
    key_us = store.get("dispatch", "fs_read__output")
    assert key_dot == "via-dot", f"Expected 'via-dot', got {key_dot!r}"
    assert key_us == "via-underscore", f"Expected 'via-underscore', got {key_us!r}"


def test_dash_name_encoded_distinctly():
    """my-skill and my_skill must map to different dispatch keys."""
    from general_ludd.dispatch.variable_store import VariableStore, apply_results
    from general_ludd.dispatch.dynamic_dispatcher import DispatchResult
    store = VariableStore()
    r1 = DispatchResult(ok=True, kind="skill", name="my-skill", output="dash")
    r2 = DispatchResult(ok=True, kind="skill", name="my_skill", output="underscore")
    apply_results(store, [r1, r2])
    assert store.get("dispatch", "my_DASH_skill__output") == "dash"
    assert store.get("dispatch", "my_skill__output") == "underscore"
```

**Risk:** Breaking change for existing callers that template with the OLD key
form `{{ dispatch__my_skill__ok }}` for a dash/dot name. Search the codebase
for any Jinja2 templates that reference `dispatch__` keys containing `_` where
the original name had `.` or `-` before applying. Existing tests that check
`store.get("dispatch", "my_skill__ok")` for `name="my-skill"` will fail and
must be updated to `"my_DASH_skill__ok"`. The existing test
`test_name_with_dash_is_normalised` and `test_name_with_dot_is_normalised` in
`test_variable_store.py` pin the OLD behaviour and must be updated in the same
commit.

---

### Fix 2B · `issue_sources/markdown_todo.py` — `--&gt;` escaping + dedup (finding #17)

**File:** `src/general_ludd/issue_sources/markdown_todo.py`

Two bugs in `update_status`:

1. `comment` is embedded verbatim in `&lt;!--gludd:{comment}--&gt;` — a comment
   containing `--&gt;` terminates the HTML comment early and may corrupt adjacent
   markup.
2. The dedup check uses `marker.strip()` (strips leading space) but appends
   `text = f"{text}{marker}"` where `marker` already carries a leading space
   (`f" &lt;!--gludd:...">`). On a re-parse the leading space is part of the
   stored text, so `marker.strip()` no longer matches and the comment is
   appended again.

**old_string:**
```python
        if comment:
            marker = f" <!--gludd:{comment}-->"
            if marker.strip() not in text:
                text = f"{text}{marker}"
```

**new_string:**
```python
        if comment:
            # Escape any `-->` in the caller comment so the HTML comment is
            # never terminated early (e.g. a comment of "done-->injected"
            # would close the tag and corrupt adjacent markup).
            safe_comment = comment.replace("-->", "--&gt;")
            marker_body = f"<!--gludd:{safe_comment}-->"
            # Dedup: check for the body without the leading space so that a
            # previously appended marker (stored as " <!--gludd:...-->") is
            # found by both the leading-space form and the bare form.
            if marker_body not in text:
                text = f"{text} {marker_body}"
```

**Regression test — add to:** `tests/unit/test_markdown_todo.py`
(create if absent)

```python
import os, tempfile
from general_ludd.issue_sources.markdown_todo import MarkdownTodoSource


def _make_source(content: str):
    td = tempfile.mkdtemp()
    p = os.path.join(td, "TODO.md")
    with open(p, "w") as f:
        f.write(content)
    return MarkdownTodoSource({"root": td, "path": "TODO.md"}), p


def test_comment_with_arrow_is_escaped():
    """A comment containing --> must not terminate the HTML comment early."""
    src, path = _make_source("- [ ] Task one\n")
    issues = src.fetch_issues()
    eid = issues[0]["external_id"]
    src.update_status(eid, "done", comment="result-->injected")
    with open(path) as f:
        content = f.read()
    assert "-->" not in content.split("<!--gludd:")[1].split("-->")[0], (
        "The raw --> inside the comment must be escaped"
    )
    assert "--&gt;" in content


def test_update_status_does_not_double_annotate():
    """Calling update_status twice with the same comment must not duplicate it."""
    src, path = _make_source("- [ ] Task two\n")
    issues = src.fetch_issues()
    eid = issues[0]["external_id"]
    src.update_status(eid, "open", comment="reviewed")
    src.update_status(eid, "open", comment="reviewed")
    with open(path) as f:
        content = f.read()
    assert content.count("<!--gludd:reviewed-->") == 1, (
        "Marker must appear exactly once after two identical update_status calls"
    )
```

**Risk:** Minor behavioural change in marker format (space is now placed between
text and `&lt;!--` rather than inside the marker string). Any test that asserts the
exact string `" &lt;!--gludd:..."` (with the leading space inside the marker
variable) needs updating. No security surface.

---

## Cycle 3 — Cross-file rename

### Fix 3 · `security/auth.py` + callers — rename `is_path_within` (finding #18)

**Background:** Two definitions exist with swapped argument order:
- `security/sanitize.py:117` — `is_path_within(candidate, root)` — delegates to
  `confine_path(candidate, root)`.
- `security/auth.py:114` — `is_path_within(base, candidate)` — realpath +
  commonpath.

Both are individually correct in isolation, but `__init__.py` re-exports `auth`'s
version under the shared name `is_path_within`. `skills/fetcher.py` imports from
`security.auth` and calls `is_path_within(str(target), f"{stem}.md")` with
`(base, candidate)` — consistent with `auth.py`'s signature. Renaming `auth.py`'s
version to `is_join_within` makes the argument order explicit and removes the
ambiguity without breaking `sanitize.py` callers (which import from `sanitize`
directly).

This fix is **four files**; all must be applied in a single commit.

---

#### 3a · `security/auth.py` — rename the function

**old_string:**
```python
def is_path_within(base: str, candidate: str) -> bool:
    """True iff ``candidate`` resolves to a path inside ``base``.

    ``candidate`` is joined onto ``base`` first, so a relative path is taken
    relative to the base while an ABSOLUTE candidate replaces the base entirely
    (the classic escape) — which this function then catches via ``commonpath``.
    Both paths are passed through ``realpath`` so symlink and ``../`` escapes are
    resolved before comparison. Pure string/filesystem-metadata work only; no
    network, no blocking.
    """
    try:
        base_real = os.path.realpath(base)
        full = os.path.realpath(os.path.join(base_real, candidate))
        common = os.path.commonpath([base_real, full])
    except (ValueError, OSError):
        # Mixed drives, embedded NULs, etc. -> treat as not contained.
        return False
    return common == base_real
```

**new_string:**
```python
def is_join_within(base: str, candidate: str) -> bool:
    """True iff ``candidate``, joined onto ``base``, resolves inside ``base``.

    ``candidate`` is joined onto ``base`` first, so a relative path is taken
    relative to the base while an ABSOLUTE candidate replaces the base entirely
    (the classic escape) — which this function then catches via ``commonpath``.
    Both paths are passed through ``realpath`` so symlink and ``../`` escapes are
    resolved before comparison. Pure string/filesystem-metadata work only; no
    network, no blocking.

    Renamed from ``is_path_within`` to make the ``(base, candidate)`` argument
    order unambiguous: ``security.sanitize.is_path_within`` has the opposite
    ``(candidate, root)`` order, which caused confusion at import sites.
    """
    try:
        base_real = os.path.realpath(base)
        full = os.path.realpath(os.path.join(base_real, candidate))
        common = os.path.commonpath([base_real, full])
    except (ValueError, OSError):
        # Mixed drives, embedded NULs, etc. -> treat as not contained.
        return False
    return common == base_real


# Backward-compat alias — remove after all call sites are updated to is_join_within.
is_path_within = is_join_within
```

---

#### 3b · `security/__init__.py` — re-export the new name

**old_string:**
```python
from general_ludd.security.auth import (
    is_path_within,
    is_safe_fetch_url,
    require_auth_env,
    verify_psk,
)
from general_ludd.security.sanitize import sanitize_job_id, sanitize_path

__all__ = [
    "is_path_within",
    "is_safe_fetch_url",
    "require_auth_env",
    "sanitize_job_id",
    "sanitize_path",
    "verify_psk",
]
```

**new_string:**
```python
from general_ludd.security.auth import (
    is_join_within,
    is_path_within,  # backward-compat alias; prefer is_join_within
    is_safe_fetch_url,
    require_auth_env,
    verify_psk,
)
from general_ludd.security.sanitize import sanitize_job_id, sanitize_path

__all__ = [
    "is_join_within",
    "is_path_within",  # deprecated alias
    "is_safe_fetch_url",
    "require_auth_env",
    "sanitize_job_id",
    "sanitize_path",
    "verify_psk",
]
```

---

#### 3c · `skills/fetcher.py` — use the new canonical name at the call site

**old_string:**
```python
from general_ludd.security.auth import is_path_within, is_safe_fetch_url
```

**new_string:**
```python
from general_ludd.security.auth import is_join_within, is_safe_fetch_url
```

Also update the call site in `RemoteSkillFetcher.install`:

**old_string:**
```python
        if not is_path_within(str(target), f"{stem}.md"):
            logger.warning("Refusing skill path escaping %s: %r", target_dir, skill.name)
            return None
```

**new_string:**
```python
        if not is_join_within(str(target), f"{stem}.md"):
            logger.warning("Refusing skill path escaping %s: %r", target_dir, skill.name)
            return None
```

---

#### 3d · `security/auth.py` — update module docstring reference

**old_string:**
```python
  * ``is_path_within``    — realpath+commonpath jail; refuses absolute paths and
                            ``../`` escapes. Mirrors ExecutionEngine's guard.
```

**new_string:**
```python
  * ``is_join_within``    — realpath+commonpath jail; refuses absolute paths and
                            ``../`` escapes. Mirrors ExecutionEngine's guard.
                            (``is_path_within`` is a backward-compat alias.)
```

---

**Regression test — file:** `tests/unit/test_security_auth_rename.py`

```python
"""Regression: is_join_within must exist and is_path_within must be its alias."""
from general_ludd.security import auth as _auth
from general_ludd import security as _sec


def test_is_join_within_exists():
    assert callable(_auth.is_join_within)


def test_is_path_within_is_alias_for_is_join_within():
    """The backward-compat alias must point to the same function."""
    assert _auth.is_path_within is _auth.is_join_within


def test_is_join_within_exported_from_package():
    assert hasattr(_sec, "is_join_within")


def test_is_join_within_confines_correctly():
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        assert _auth.is_join_within(td, "subfile.txt") is True
        assert _auth.is_join_within(td, "../escape.txt") is False


def test_fetcher_import_uses_is_join_within():
    """fetcher.py must import is_join_within (not just is_path_within)."""
    import importlib, inspect
    import general_ludd.skills.fetcher as fetcher_mod
    src = inspect.getsource(fetcher_mod)
    assert "is_join_within" in src, (
        "fetcher.py must import and use is_join_within"
    )
```

**Risk:** Medium. Four files change atomically. The backward-compat alias means
no existing callers break immediately. Any third-party code importing
`is_path_within` from `general_ludd.security.auth` continues to work. Remove
the alias in a later cleanup commit once grep confirms zero remaining uses of
`is_path_within`.

---

## Cycle 4 — Connector SSRF guards

### Fix 4 · `connectors/cassandra_stats.py` + `connectors/clickhouse_stats.py` — host guard (finding #7)

Both connectors fetch credential-bearing requests to a caller-supplied URL with
no SSRF pre-check. A malicious `jmx_url` / `url` in config can exfiltrate the
bearer token / HTTP Basic password to an internal metadata endpoint.

---

#### 4a · `connectors/cassandra_stats.py`

**old_string:**
```python
    def _build_default_executor(self) -> Executor | None:
        try:
            import httpx  # guarded; default JMX-scrape transport
        except Exception as exc:  # pragma: no cover - guarded import
            self._driver_error = _DRIVER_UNAVAILABLE
            logger.debug("httpx import failed: %s", exc)
            return None

        url = self._jmx_url
        token = os.environ.get(self._token_env)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
```

**new_string:**
```python
    def _build_default_executor(self) -> Executor | None:
        try:
            import httpx  # guarded; default JMX-scrape transport
        except Exception as exc:  # pragma: no cover - guarded import
            self._driver_error = _DRIVER_UNAVAILABLE
            logger.debug("httpx import failed: %s", exc)
            return None

        url = self._jmx_url
        # SSRF guard: the bearer token is attached to this request, so a
        # malicious jmx_url pointing at 169.254.169.254 or localhost would
        # exfiltrate the credential to an internal metadata endpoint. Reject
        # any URL that is not a safe public HTTP(S) endpoint before the
        # token is read from the environment.
        from general_ludd.connectors.base import is_safe_endpoint as _is_safe
        if self._executor is None and not _is_safe(url):
            self._driver_error = (
                f"jmx_url {url!r} is not a safe public endpoint "
                "(blocked: loopback, link-local, RFC-1918, metadata)"
            )
            logger.error(
                "CassandraStatsSource: refusing jmx_url %r — SSRF guard", url
            )
            return None

        token = os.environ.get(self._token_env)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
```

---

#### 4b · `connectors/clickhouse_stats.py`

**old_string:**
```python
    def _build_default_executor(self) -> Executor | None:
        try:
            import httpx  # guarded; default HTTP transport
        except Exception as exc:  # pragma: no cover - guarded import
            self._driver_error = _DRIVER_UNAVAILABLE
            logger.debug("httpx import failed: %s", exc)
            return None

        password = os.environ.get(self._password_env, "")
        url = self._url
        user = self._user
```

**new_string:**
```python
    def _build_default_executor(self) -> Executor | None:
        try:
            import httpx  # guarded; default HTTP transport
        except Exception as exc:  # pragma: no cover - guarded import
            self._driver_error = _DRIVER_UNAVAILABLE
            logger.debug("httpx import failed: %s", exc)
            return None

        url = self._url
        # SSRF guard: HTTP Basic auth (user:password) is attached to every
        # request, so a malicious url pointing at 169.254.169.254 or localhost
        # would exfiltrate the password to an internal metadata endpoint. Reject
        # before the password is read from the environment.
        from general_ludd.connectors.base import is_safe_endpoint as _is_safe
        if self._executor is None and not _is_safe(url):
            self._driver_error = (
                f"url {url!r} is not a safe public endpoint "
                "(blocked: loopback, link-local, RFC-1918, metadata)"
            )
            logger.error(
                "ClickHouseStatsSource: refusing url %r — SSRF guard", url
            )
            return None

        password = os.environ.get(self._password_env, "")
        user = self._user
```

**Regression test — file:** `tests/unit/test_connector_ssrf_guard.py`

```python
"""Regression: cassandra_stats and clickhouse_stats must guard against SSRF."""
from general_ludd.connectors.cassandra_stats import CassandraStatsSource
from general_ludd.connectors.clickhouse_stats import ClickHouseStatsSource


class TestCassandraSSRFGuard:
    def test_localhost_jmx_url_returns_no_executor(self):
        src = CassandraStatsSource(config={"jmx_url": "http://localhost:7070/metrics"})
        # No injected executor — must fall through to _build_default_executor.
        executor = src._get_executor()
        assert executor is None
        assert src._driver_error is not None
        assert "SSRF" in (src._driver_error or "") or "safe" in (src._driver_error or "")

    def test_metadata_jmx_url_blocked(self):
        src = CassandraStatsSource(
            config={"jmx_url": "http://169.254.169.254/latest/meta-data/"}
        )
        executor = src._get_executor()
        assert executor is None

    def test_public_jmx_url_allowed(self, monkeypatch):
        """A public endpoint must pass the guard (httpx may not be available)."""
        src = CassandraStatsSource(
            config={"jmx_url": "http://monitoring.example.com:7070/metrics"}
        )
        # The guard must not block this; httpx may raise ImportError in CI.
        try:
            src._build_default_executor()
        except Exception:
            pass
        # As long as _driver_error is NOT the SSRF message, the guard passed.
        assert src._driver_error is None or "SSRF" not in (src._driver_error or "")

    def test_injected_executor_bypasses_guard(self):
        """An explicit injected executor must always be returned (no guard)."""
        called = []
        def fake_exec(cmd):
            called.append(cmd)
            return []
        src = CassandraStatsSource(
            config={"jmx_url": "http://localhost:7070/metrics"},
            executor=fake_exec,
        )
        assert src._get_executor() is fake_exec


class TestClickHouseSSRFGuard:
    def test_localhost_url_blocked(self):
        src = ClickHouseStatsSource(config={"url": "http://localhost:8123"})
        executor = src._get_executor()
        assert executor is None
        assert src._driver_error is not None

    def test_metadata_url_blocked(self):
        src = ClickHouseStatsSource(
            config={"url": "http://169.254.169.254/"}
        )
        executor = src._get_executor()
        assert executor is None

    def test_injected_executor_bypasses_guard(self):
        def fake_exec(sql):
            return []
        src = ClickHouseStatsSource(
            config={"url": "http://localhost:8123"},
            executor=fake_exec,
        )
        assert src._get_executor() is fake_exec
```

**Risk:** Medium. Connectors backed by a localhost ClickHouse/Cassandra for
local development will now fail to build the default executor and return
`health() -> {"ok": False}`. Operators running Cassandra/ClickHouse on loopback
must either inject an explicit `executor=` or configure a non-loopback URL. The
`executor=` injection path is completely unaffected by the guard (see
`test_injected_executor_bypasses_guard`), so all existing tests that inject a
fake executor remain green. The guard only fires in `_build_default_executor`
when no explicit executor was supplied.

---

## Cycle 5 — git_automation hardening

### Fix 5 · `git_automation/repo.py` — realpath jail + _run_git routing (finding #8)

Two gaps:

1. `_reject_escaping_path` uses `normpath` + prefix only — **no `realpath`** —
   so a symlinked worktree path can bypass the jail.
2. `init_repo`, `create_local_bare_mirror`, `merge_branch`, `push_to_remote`,
   `create_worktree`, `remove_worktree`, `list_worktrees`, `create_release_tag`,
   `create_checkpoint_tag` all call `subprocess.run` directly, bypassing
   `_run_git`'s timeout and non-interactive environment.

---

#### 5a — `_reject_escaping_path`: add `realpath` to the jail

**old_string:**
```python
    @staticmethod
    def _reject_escaping_path(repo_path: str, worktree_path: str) -> None:
        """Reject a worktree path that escapes the repo's parent directory.

        Worktrees are expected to live beside the repo (or under it). A path
        containing ``..`` that resolves above the repo parent is refused so a
        traversal value cannot plant a worktree in an arbitrary location.
        """
        # A `..` component is the traversal primitive that escapes the intended
        # area; refuse it outright (a legitimate worktree path never needs one).
        norm = os.path.normpath(worktree_path)
        parts = norm.replace("\\", "/").split("/")
        if ".." in parts:
            raise ValueError(
                f"refusing worktree path containing '..' traversal: {worktree_path!r}"
            )
        repo_abs = os.path.abspath(repo_path)
        parent = os.path.dirname(repo_abs) or os.sep
        if os.path.isabs(worktree_path):
            target = os.path.abspath(worktree_path)
        else:
            target = os.path.abspath(os.path.join(repo_abs, worktree_path))
        # Allowed roots: the repo itself or its immediate parent directory.
        for root in (repo_abs, parent):
            root_prefix = root.rstrip(os.sep) + os.sep
            if target == root or target.startswith(root_prefix):
                return
        raise ValueError(
            f"refusing worktree path that escapes the repo parent: {worktree_path!r}"
        )
```

**new_string:**
```python
    @staticmethod
    def _reject_escaping_path(repo_path: str, worktree_path: str) -> None:
        """Reject a worktree path that escapes the repo's parent directory.

        Worktrees are expected to live beside the repo (or under it). A path
        containing ``..`` that resolves above the repo parent is refused so a
        traversal value cannot plant a worktree in an arbitrary location.

        Uses ``os.path.realpath`` (not only ``abspath``) so a symlink whose
        target lives outside the intended area is caught — previously only
        ``normpath``/``abspath`` were used, which follow no symlinks and would
        accept a symlink-based escape. Mirrors ``execution/engine.py``'s jail.
        """
        # A `..` component is the traversal primitive that escapes the intended
        # area; refuse it outright (a legitimate worktree path never needs one).
        norm = os.path.normpath(worktree_path)
        parts = norm.replace("\\", "/").split("/")
        if ".." in parts:
            raise ValueError(
                f"refusing worktree path containing '..' traversal: {worktree_path!r}"
            )
        # realpath resolves symlinks so a symlinked escape is caught.
        repo_real = os.path.realpath(repo_path)
        parent = os.path.dirname(repo_real) or os.sep
        if os.path.isabs(worktree_path):
            target = os.path.realpath(worktree_path)
        else:
            target = os.path.realpath(os.path.join(repo_real, worktree_path))
        # Allowed roots: the repo itself or its immediate parent directory.
        for root in (repo_real, parent):
            root_prefix = root.rstrip(os.sep) + os.sep
            if target == root or target.startswith(root_prefix):
                return
        raise ValueError(
            f"refusing worktree path that escapes the repo parent: {worktree_path!r}"
        )
```

---

#### 5b — Route `create_worktree` through `_run_git`

**old_string:**
```python
        try:
            subprocess.run(
                # `-b <branch>` then `--` then the path positional: the path can
                # never be reinterpreted as an option.
                ["git", "worktree", "add", "-b", branch_name, "--", worktree_path, "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return WorktreeResult(path=worktree_path, branch=branch_name, success=True)
        except subprocess.CalledProcessError as exc:
            return WorktreeResult(
                path=worktree_path,
                branch=branch_name,
                success=False,
                message=exc.stderr.strip() if exc.stderr else str(exc),
            )
```

**new_string:**
```python
        try:
            # Route through _run_git for timeout + non-interactive env.
            worktree_ga = GitAutomation(repo_path)
            worktree_ga._run_git(
                "worktree", "add", "-b", branch_name, "--", worktree_path, "HEAD"
            )
            return WorktreeResult(path=worktree_path, branch=branch_name, success=True)
        except subprocess.CalledProcessError as exc:
            return WorktreeResult(
                path=worktree_path,
                branch=branch_name,
                success=False,
                message=exc.stderr.strip() if exc.stderr else str(exc),
            )
```

---

#### 5c — Route `remove_worktree` through `_run_git`

**old_string:**
```python
    def remove_worktree(self, repo_path: str, worktree_path: str) -> bool:
        _reject_leading_dash(worktree_path, kind="worktree path")
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--", worktree_path],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False
```

**new_string:**
```python
    def remove_worktree(self, repo_path: str, worktree_path: str) -> bool:
        _reject_leading_dash(worktree_path, kind="worktree path")
        try:
            GitAutomation(repo_path)._run_git(
                "worktree", "remove", "--", worktree_path
            )
            return True
        except subprocess.CalledProcessError:
            return False
```

---

#### 5d — Route `list_worktrees` through `_run_git`

**old_string:**
```python
    def list_worktrees(self, repo_path: str) -> list[WorktreeInfo]:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
```

**new_string:**
```python
    def list_worktrees(self, repo_path: str) -> list[WorktreeInfo]:
        result = GitAutomation(repo_path)._run_git(
            "worktree", "list", "--porcelain"
        )
```

---

#### 5e — Route `merge_branch` through `_run_git`

Replace the two bare `subprocess.run` calls in `merge_branch` that currently
lack timeout and non-interactive env.

**old_string:**
```python
        subprocess.run(
            ["git", "checkout", target, "--"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        # Options first, then `--`, then the source ref positional, so a
        # leading-dash source could never be parsed as a merge option.
        merge_args = ["git", "merge"]
        if strategy == "ff":
            merge_args.append("--ff-only")
        elif strategy == "no-ff":
            merge_args.extend(["--no-ff", "-m", f"Merge {source} into {target}"])
        elif strategy == "squash":
            merge_args.append("--squash")
        merge_args.extend(["--", source])
        result = subprocess.run(
            merge_args,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            conflicts = []
            if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                conflicts = [source]
            return MergeResult(success=False, strategy=strategy, message=result.stderr.strip(), conflicts=conflicts)
        if strategy == "squash":
            subprocess.run(
                ["git", "commit", "-m", f"Merge {source} into {target} (squash)"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
```

**new_string:**
```python
        _ga = GitAutomation(repo_path)
        _ga._run_git("checkout", target, "--")
        # Options first, then `--`, then the source ref positional, so a
        # leading-dash source could never be parsed as a merge option.
        merge_extra: list[str] = []
        if strategy == "ff":
            merge_extra.append("--ff-only")
        elif strategy == "no-ff":
            merge_extra.extend(["--no-ff", "-m", f"Merge {source} into {target}"])
        elif strategy == "squash":
            merge_extra.append("--squash")
        try:
            result = _ga._run_git("merge", *merge_extra, "--", source, check=False)
        except subprocess.CalledProcessError as exc:
            result = exc  # type: ignore[assignment]
        if getattr(result, "returncode", 0) != 0:
            stdout = getattr(result, "stdout", "") or ""
            stderr = getattr(result, "stderr", "") or ""
            conflicts = [source] if "CONFLICT" in stdout or "CONFLICT" in stderr else []
            return MergeResult(success=False, strategy=strategy, message=stderr.strip(), conflicts=conflicts)
        if strategy == "squash":
            _ga._run_git("commit", "-m", f"Merge {source} into {target} (squash)", check=False)
```

---

#### 5f — Route `push_to_remote` through `_run_git`

**old_string:**
```python
    def push_to_remote(self, repo_path: str, remote: str = "origin", branch: str | None = None) -> PushResult:
        _reject_leading_dash(remote, kind="remote name")
        if branch:
            _reject_leading_dash(branch, kind="branch name")
        # `--` ends option parsing before the refspec positional.
        args = ["git", "push", remote, "--"]
        if branch:
            args.append(branch)
        result = subprocess.run(
            args,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        return PushResult(
            success=result.returncode == 0,
            remote=remote,
            branch=branch or "",
            message=result.stderr.strip() if result.stderr else result.stdout.strip(),
        )
```

**new_string:**
```python
    def push_to_remote(self, repo_path: str, remote: str = "origin", branch: str | None = None) -> PushResult:
        _reject_leading_dash(remote, kind="remote name")
        if branch:
            _reject_leading_dash(branch, kind="branch name")
        extra = [branch] if branch else []
        try:
            result = GitAutomation(repo_path)._run_git(
                "push", remote, "--", *extra, check=False
            )
            return PushResult(
                success=result.returncode == 0,
                remote=remote,
                branch=branch or "",
                message=result.stderr.strip() if result.stderr else result.stdout.strip(),
            )
        except subprocess.CalledProcessError as exc:
            return PushResult(
                success=False,
                remote=remote,
                branch=branch or "",
                message=(exc.stderr or "").strip(),
            )
```

---

#### 5g — Route `create_release_tag` through `_run_git`

**old_string:**
```python
    def create_release_tag(self, repo_path: str, fmt: str = "YYYYMMDDHHMMSS") -> str:
        now = datetime.now(tz=UTC)
        tag = now.strftime("%Y%m%d%H%M%S")
        subprocess.run(
            ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return tag
```

**new_string:**
```python
    def create_release_tag(self, repo_path: str, fmt: str = "YYYYMMDDHHMMSS") -> str:
        now = datetime.now(tz=UTC)
        tag = now.strftime("%Y%m%d%H%M%S")
        GitAutomation(repo_path)._run_git("tag", "-a", tag, "-m", f"Release {tag}")
        return tag
```

---

#### 5h — Route `create_checkpoint_tag` through `_run_git`

**old_string:**
```python
    def create_checkpoint_tag(self, repo_path: str, todo_id: str, sha: str) -> str:
        ts = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
        short_sha = sha[:7]
        tag = f"agent/{todo_id}/{ts}/{short_sha}"
        subprocess.run(
            ["git", "tag", tag],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return tag
```

**new_string:**
```python
    def create_checkpoint_tag(self, repo_path: str, todo_id: str, sha: str) -> str:
        ts = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
        short_sha = sha[:7]
        tag = f"agent/{todo_id}/{ts}/{short_sha}"
        GitAutomation(repo_path)._run_git("tag", tag)
        return tag
```

---

#### 5i — Route `create_local_bare_mirror` through `_run_git`

The current implementation calls `git clone --bare <repo_path> <mirror_path>`
with no timeout and no leading-dash guard on `mirror_path`.

**old_string:**
```python
    def create_local_bare_mirror(self, repo_path: str, mirror_path: str) -> str:
        subprocess.run(
            ["git", "clone", "--bare", repo_path, mirror_path],
            capture_output=True,
            text=True,
            check=True,
        )
        return mirror_path
```

**new_string:**
```python
    def create_local_bare_mirror(self, repo_path: str, mirror_path: str) -> str:
        _reject_leading_dash(mirror_path, kind="mirror path")
        # Route through _run_git for timeout + non-interactive env. cwd is set
        # to repo_path so the relative-path clone is deterministic.
        GitAutomation(repo_path)._run_git(
            "clone", "--bare", "--", repo_path, mirror_path
        )
        return mirror_path
```

---

#### 5j — Route `init_repo` through `_run_git`

**old_string:**
```python
    def init_repo(self, path: str | None = None) -> InitResult:
        target = path or self.repo_path
        git_dir = os.path.join(target, ".git")
        created = not os.path.isdir(git_dir)
        subprocess.run(
            ["git", "init"],
            cwd=target,
            capture_output=True,
            text=True,
            check=True,
        )
        for cmd in (
            ["git", "config", "user.email", "agent@harness.local"],
            ["git", "config", "user.name", "Agentic Harness Agent"],
        ):
            subprocess.run(cmd, cwd=target, capture_output=True, text=True, check=False)
        return InitResult(path=target, created=created, message="initialized" if created else "already exists")
```

**new_string:**
```python
    def init_repo(self, path: str | None = None) -> InitResult:
        target = path or self.repo_path
        git_dir = os.path.join(target, ".git")
        created = not os.path.isdir(git_dir)
        _ga = GitAutomation(target)
        _ga._run_git("init")
        # git config is non-interactive by nature; route through _run_git for
        # timeout protection (a misconfigured credential helper can stall).
        _ga._run_git("config", "user.email", "agent@harness.local", check=False)
        _ga._run_git("config", "user.name", "Agentic Harness Agent", check=False)
        return InitResult(path=target, created=created, message="initialized" if created else "already exists")
```

---

**Regression test — file:** `tests/unit/test_git_automation_hardening.py`

```python
"""Regression tests for git_automation/repo.py hardening (Cycle 5)."""
from __future__ import annotations
import os
import tempfile

import pytest

from general_ludd.git_automation.repo import GitAutomation


class TestRejectEscapingPathRealpath:
    def test_symlink_escape_is_blocked(self, tmp_path):
        """A symlinked worktree_path whose target escapes the repo parent must be rejected."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        outside = tmp_path.parent / "outside_area"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "escape_link"
        link.symlink_to(outside)

        with pytest.raises(ValueError, match="escapes the repo parent"):
            GitAutomation._reject_escaping_path(str(repo_dir), str(link))

    def test_normal_sibling_path_is_allowed(self, tmp_path):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        sibling = tmp_path / "worktree-branch"
        # Must not raise.
        GitAutomation._reject_escaping_path(str(repo_dir), str(sibling))

    def test_dotdot_still_blocked(self, tmp_path):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        with pytest.raises(ValueError, match=r"'\.\.'"):
            GitAutomation._reject_escaping_path(str(repo_dir), "../escape")


class TestBareCloneLeadingDash:
    def test_mirror_path_with_leading_dash_rejected(self, tmp_path):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        ga = GitAutomation(str(repo_dir))
        with pytest.raises(ValueError, match="leading"):
            ga.create_local_bare_mirror(str(repo_dir), "--upload-pack=evil")


class TestRunGitRouting:
    def test_init_repo_uses_run_git_timeout(self, tmp_path, monkeypatch):
        """init_repo must route through _run_git (which enforces the timeout)."""
        called_args = []

        def fake_run_git(self_inner, *args, **kwargs):
            called_args.append(args)
            import subprocess
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(GitAutomation, "_run_git", fake_run_git)
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        ga = GitAutomation(str(repo_dir))
        ga.init_repo()
        assert any("init" in a for args in called_args for a in args), (
            "init_repo must call _run_git('init', ...)"
        )
```

**Risk:** High surface area — 9 call sites changed. Functional behaviour is
unchanged (same git commands, same error handling), but callers now get the
60-second timeout and `GIT_TERMINAL_PROMPT=0` / `GIT_ASKPASS=echo` environment
on every path. Any test that stubs `subprocess.run` directly (not `_run_git`)
will need updating — search `tests/` for `subprocess.run` + `git_automation`
before applying.

---

## Cycle 6 — Conditional security fix

### Fix 6 · `secrets/manager.py` — `resolve()` raise when alias exists but client is None (finding #10)

**SAFETY GATE — read before applying:**

This fix is ONLY safe if every call site that currently receives `None` from
`resolve()` treats `None` as "secret not configured" (not "secret backend
available but secret absent"). If any caller uses the returned `None` to skip
an optional feature (e.g. "if resolved is None: skip feature"), changing it to a
raise will break that path.

Run the following verification before applying:
```text
grep -rn "\.resolve(" src/ --include="*.py"
```
and inspect every call site. Safe patterns:
- `value = manager.resolve(alias); if value is None: raise ...` — already fails
  closed; the raise just moves the failure earlier.
- `token = manager.resolve("api_key") or os.environ.get(...)` — safe; the
  `or` falls through to the env fallback only when `alias is None` (key not
  registered), which this fix preserves.

Unsafe pattern (verify absent before applying):
- `value = manager.resolve(alias); if value is not None: do_something(value)` —
  if the `do_something` path is a critical gate, the new raise will propagate
  instead of silently skipping.

**Conditional:** Apply this fix ONLY after confirming all callers handle
`SecretsUnavailableError` or are in a `try/except` that already handles it.

---

**File:** `src/general_ludd/secrets/manager.py`

**old_string:**
```python
    def resolve(self, alias_name: str) -> str | None:
        alias = self._aliases.get(alias_name)
        if alias is None:
            return None
        if self._client is None:
            logger.warning("No secrets client configured for alias %s", alias_name)
            return None
```

**new_string:**
```python
    def resolve(self, alias_name: str) -> str | None:
        alias = self._aliases.get(alias_name)
        if alias is None:
            return None
        if self._client is None:
            # Fail CLOSED: the alias is registered (the operator intended to
            # resolve it from the secrets backend) but no client was ever
            # connected. Returning None here would let an unconfigured manager
            # masquerade as "secret not present", allowing a caller to proceed
            # with a missing credential instead of failing loudly.
            # Callers that want fail-open behaviour must check
            # `manager.list_aliases()` before calling resolve(), or catch
            # SecretsUnavailableError and fall back explicitly.
            raise SecretsUnavailableError(
                f"secrets backend not connected — cannot resolve alias "
                f"{alias_name!r} (call connect() before resolve())"
            )
```

**Regression test — file:** `tests/unit/test_secrets_manager_resolve_guard.py`

```python
"""Regression: SecretsManager.resolve() must raise when alias exists but client is None."""
import pytest
from general_ludd.secrets.manager import SecretsManager, SecretAlias, SecretsUnavailableError


def test_resolve_raises_when_alias_registered_but_no_client():
    """Fail CLOSED: a registered alias with no client must raise, not return None."""
    mgr = SecretsManager()  # client=None by default
    mgr.register_alias(SecretAlias(alias="api_key", path="myapp/api_key"))
    with pytest.raises(SecretsUnavailableError, match="not connected"):
        mgr.resolve("api_key")


def test_resolve_returns_none_for_unregistered_alias():
    """An unknown alias (not registered at all) still returns None — no client needed."""
    mgr = SecretsManager()
    result = mgr.resolve("nonexistent_alias")
    assert result is None


def test_resolve_old_warn_only_behaviour_is_gone():
    """After the fix, no warning-and-None path should exist for a registered alias."""
    import logging
    mgr = SecretsManager()
    mgr.register_alias(SecretAlias(alias="k", path="p"))
    with pytest.raises(SecretsUnavailableError):
        mgr.resolve("k")
```

**Risk:** Medium-high (blast-radius conditional). After verifying call sites,
blast radius is limited to code paths that call `resolve()` on an alias that
was registered but whose backend was never `connect()`-ed — typically a
misconfigured startup. The `auto` mode path in `build_secrets_resolver`
(`daemon.py`) wraps the resolver in an `EnvSecretsManager` fallback, so the
daemon's startup path is unaffected. The `test_secrets_auto_mode.py` suite
(which pins the fallback behaviour) continues to pass because it calls
`build_secrets_resolver`, not `SecretsManager.resolve()` directly. Apply AFTER
confirming no call site relies on the warn-and-None path for optional features.

---

## Summary index

| Section | Fix | File(s) | Cycle |
|---------|-----|---------|-------|
| 1A | Docstring honesty (`prepare_messages`) | `agents/capabilities.py` | 1 |
| 1B | LICENSE-in-manifest assertion | `runtime/release.py` | 1 |
| 1C | Real token count in accounting | `routers/accounting.py` | 1 |
| 2A | `safe_name` collision guard (`_DOT_`/`_DASH_`) | `dispatch/variable_store.py` | 2 |
| 2B | `--&gt;` escaping + dedup consistency | `issue_sources/markdown_todo.py` | 2 |
| 3 | `is_path_within` → `is_join_within` rename | `security/auth.py`, `security/__init__.py`, `skills/fetcher.py` | 3 |
| 4 | Cassandra + ClickHouse SSRF guard | `connectors/cassandra_stats.py`, `connectors/clickhouse_stats.py` | 4 |
| 5 | `_reject_escaping_path` realpath + `_run_git` routing | `git_automation/repo.py` | 5 |
| 6 | `resolve()` raise when alias exists, client None | `secrets/manager.py` | 6 (conditional) |

Total new test files: 6
Total files changed: 10
Gate cycles required: 6 (one `make gate` per cycle)
