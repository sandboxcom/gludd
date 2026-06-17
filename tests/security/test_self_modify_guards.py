"""Adversarial tests for gludd's self-modification guards (issue #58).

Three concrete defects are covered, each with an allow-path + a deny-path:

(a) ``collections_self_modify`` enforcement at the collections-WRITE site.
    The HotReloader rotates a leaf module by ``os.replace``-ing a candidate
    over the live file.  When the live file is inside a ``collections/`` tree
    that is *self-modification* and must require a role holding the
    ``collections_self_modify`` capability — a role lacking it is denied,
    BEFORE any byte is written.

(b) Protected-path deny-list in the HotReloader.  The reloader must REFUSE to
    swap guardrail / policy / permission files even if explicitly pointed at
    them, leaving the original bytes untouched.

(c) The dispatch path consults the per-role capability lattice.  A role that
    lacks the capability a tool-call kind requires is denied before the handler
    is ever invoked.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from general_ludd.reload.hot_reloader import HotReloader


def _install_live_module(
    root: Path, pkg_parts: list[str], name: str, body: str
) -> tuple[Path, str, object]:
    """Create + import a live module under ``root`` with the given package path.

    ``pkg_parts`` becomes nested packages (each gets an ``__init__.py``).  A
    per-call unique sys.path *root* keeps xdist workers from colliding while
    preserving the LITERAL package names (so a ``collections`` dir stays exactly
    ``collections`` on disk — the path segment the self-modify gate matches on).
    """
    uniq = uuid.uuid4().hex[:8]
    # Unique TOP package (so the fully-qualified module name never collides in
    # sys.modules across tests/xdist workers), with the caller's LITERAL package
    # parts nested beneath it — so e.g. a ``collections`` segment stays exactly
    # ``collections`` on disk, the path segment the self-modify gate matches on.
    top = f"smg_{uniq}"
    all_parts = [top, *pkg_parts]
    cur = root
    for part in all_parts:
        cur = cur / part
        cur.mkdir(parents=True, exist_ok=True)
        (cur / "__init__.py").write_text("")
    mod_path = cur / f"{name}.py"
    mod_path.write_text(textwrap.dedent(body))
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    fqmn = ".".join([*all_parts, name])
    mod = importlib.import_module(fqmn)
    return mod_path, fqmn, mod


# ---------------------------------------------------------------------------
# (a) collections_self_modify enforced at the HotReloader write site
# ---------------------------------------------------------------------------

class TestCollectionsSelfModifyEnforcedAtReloadSite:
    def test_role_without_self_modify_denied_collections_swap(
        self, tmp_path: Path
    ) -> None:
        # A live module that lives inside a collections/ tree.
        mod_path, fqmn, _mod = _install_live_module(
            tmp_path,
            ["collections", "pkg"],
            "leaf",
            "VERSION = 'original'\n",
        )
        original = mod_path.read_bytes()

        candidate = tmp_path / "cand.py"
        candidate.write_text("VERSION = 'attacker'\n")

        reloader = HotReloader(config_dir=str(tmp_path / "config"))
        # role 'coder' has fs_write but NOT collections_self_modify.
        result = reloader.reload_code_module(
            module_name=fqmn,
            candidate_source_path=str(candidate),
            health_check=lambda: True,
            role="coder",
        )

        assert result.success is False, (
            "a role lacking collections_self_modify must NOT be allowed to "
            f"rotate a module inside collections/. result={result}"
        )
        assert mod_path.read_bytes() == original, (
            "the live collections/ file was modified despite the role lacking "
            "collections_self_modify (write happened before the gate)"
        )

    def test_self_improve_role_allowed_collections_swap(self, tmp_path: Path) -> None:
        mod_path, fqmn, _mod = _install_live_module(
            tmp_path,
            ["collections", "pkg"],
            "leafok",
            "VERSION = 'original'\n",
        )
        candidate = tmp_path / "cand_ok.py"
        candidate.write_text("VERSION = 'improved'\n")

        reloader = HotReloader(config_dir=str(tmp_path / "config"))
        result = reloader.reload_code_module(
            module_name=fqmn,
            candidate_source_path=str(candidate),
            health_check=lambda: True,
            role="self_improve_agent",
        )

        assert result.success is True, (
            "self_improve_agent holds collections_self_modify and a healthy "
            f"swap should succeed. result={result}"
        )
        assert b"improved" in mod_path.read_bytes()

    def test_non_collections_swap_unaffected_by_role(self, tmp_path: Path) -> None:
        # A plain (non-collections) module: the self-modify gate must not fire,
        # so even a role without collections_self_modify can rotate it.
        mod_path, fqmn, _mod = _install_live_module(
            tmp_path,
            ["plainpkg"],
            "leaf",
            "VERSION = 'original'\n",
        )
        candidate = tmp_path / "cand_plain.py"
        candidate.write_text("VERSION = 'v2'\n")

        reloader = HotReloader(config_dir=str(tmp_path / "config"))
        result = reloader.reload_code_module(
            module_name=fqmn,
            candidate_source_path=str(candidate),
            health_check=lambda: True,
            role="coder",
        )
        assert result.success is True
        assert b"v2" in mod_path.read_bytes()


# ---------------------------------------------------------------------------
# (b) Protected-path deny-list: guardrail / policy / permission files
# ---------------------------------------------------------------------------

class TestProtectedPathDenyList:
    @pytest.mark.parametrize(
        "fname",
        [
            "guardrails.py",
            "capability_policy.py",
            "fs_write_policy.py",
            "permissions.py",
            "action_policy.py",
        ],
    )
    def test_reload_refuses_protected_file(self, tmp_path: Path, fname: str) -> None:
        stem = fname[:-3]
        mod_path, fqmn, _mod = _install_live_module(
            tmp_path,
            ["protpkg"],
            stem,
            "RULES = 'strict'\n",
        )
        original = mod_path.read_bytes()

        candidate = tmp_path / "cand_evil.py"
        candidate.write_text("RULES = 'disabled'  # guard removed\n")

        reloader = HotReloader(config_dir=str(tmp_path / "config"))
        result = reloader.reload_code_module(
            module_name=fqmn,
            candidate_source_path=str(candidate),
            health_check=lambda: True,
            # Even a self-improvement role may not overwrite a guard file.
            role="self_improve_agent",
        )

        assert result.success is False, (
            f"HotReloader must REFUSE to swap a protected guard file {fname!r}; "
            f"result={result}"
        )
        assert mod_path.read_bytes() == original, (
            f"protected guard file {fname!r} was overwritten despite the "
            "deny-list — the swap happened anyway"
        )
        assert "protected" in (result.error or "").lower()

    def test_ordinary_file_still_reloadable(self, tmp_path: Path) -> None:
        # A non-protected leaf still rotates fine (the deny-list is targeted).
        mod_path, fqmn, _mod = _install_live_module(
            tmp_path,
            ["okpkg"],
            "ordinary_leaf",
            "VALUE = 1\n",
        )
        candidate = tmp_path / "cand_ok2.py"
        candidate.write_text("VALUE = 2\n")
        reloader = HotReloader(config_dir=str(tmp_path / "config"))
        result = reloader.reload_code_module(
            module_name=fqmn,
            candidate_source_path=str(candidate),
            health_check=lambda: True,
        )
        assert result.success is True
        assert b"VALUE = 2" in mod_path.read_bytes()


# ---------------------------------------------------------------------------
# (c) Dispatch consults the per-role capability lattice
# ---------------------------------------------------------------------------

class TestDispatchConsultsCapabilityLattice:
    def test_dispatch_denies_capability_role_lacks(self) -> None:
        from general_ludd.dispatch.dynamic_dispatcher import (
            DynamicDispatcher,
            ToolCall,
        )

        invoked: list[str] = []

        def collection_handler(name: str, args: dict) -> str:
            invoked.append(name)
            return "ran"

        # 'coder' lacks the collection/self-modify capability the
        # 'collection' kind requires.
        dispatcher = DynamicDispatcher(
            collection_handler=collection_handler,
            role="coder",
        )
        result = dispatcher.dispatch(
            ToolCall(kind="collection", name="gludd_db", args={})
        )

        assert result.ok is False, (
            "dispatch must deny a capability the role lacks BEFORE invoking the "
            f"handler. result={result}"
        )
        assert invoked == [], (
            "the handler was invoked even though the role lacked the capability"
        )
        assert "capabilit" in (result.error or "").lower() or "denied" in (
            result.error or ""
        ).lower()

    def test_dispatch_allows_capability_role_holds(self) -> None:
        from general_ludd.dispatch.dynamic_dispatcher import (
            DynamicDispatcher,
            ToolCall,
        )

        invoked: list[str] = []

        def collection_handler(name: str, args: dict) -> str:
            invoked.append(name)
            return "ran"

        # self_improve_agent holds collections_self_modify.
        dispatcher = DynamicDispatcher(
            collection_handler=collection_handler,
            role="self_improve_agent",
        )
        result = dispatcher.dispatch(
            ToolCall(kind="collection", name="gludd_db", args={})
        )
        assert result.ok is True, (
            f"a role holding the capability must be allowed. result={result}"
        )
        assert invoked == ["gludd_db"]

    def test_dispatch_without_role_denies_privileged_kinds(self) -> None:
        # FAIL-CLOSED: an unbound (None) role must DENY every privileged kind,
        # BEFORE the handler runs. A missing role is no longer "unrestricted".
        from general_ludd.dispatch.dynamic_dispatcher import (
            PRIVILEGED_KINDS,
            DynamicDispatcher,
            ToolCall,
        )

        invoked: list[str] = []

        def _handler(name: str, args: dict) -> str:
            invoked.append(name)
            return "ok"

        # Register a handler for every privileged kind so the only thing that
        # can stop dispatch is the fail-closed None-role gate.
        dispatcher = DynamicDispatcher(
            role_handler=_handler,
            collection_handler=_handler,
            mcp_handler=_handler,
            skill_handler=_handler,
        )
        for kind in PRIVILEGED_KINDS:
            result = dispatcher.dispatch(ToolCall(kind=kind, name="x", args={}))
            assert result.ok is False, (
                f"unbound (None) role must DENY privileged kind {kind!r} "
                f"fail-closed. result={result}"
            )
            assert (
                "capabilit" in (result.error or "").lower()
                or "denied" in (result.error or "").lower()
            )
        assert invoked == [], (
            "a handler ran despite the None-role fail-closed gate denying every "
            f"privileged kind. invoked={invoked}"
        )

    def test_dispatch_unrestricted_role_sentinel_permits(self) -> None:
        # The explicit UNRESTRICTED_ROLE sentinel is the ONLY ungated path: it
        # permits every privileged kind (trusted in-process call sites opt in).
        from general_ludd.dispatch.dynamic_dispatcher import (
            PRIVILEGED_KINDS,
            UNRESTRICTED_ROLE,
            DynamicDispatcher,
            ToolCall,
        )

        invoked: list[str] = []

        def _handler(name: str, args: dict) -> str:
            invoked.append(name)
            return "ok"

        dispatcher = DynamicDispatcher(
            role_handler=_handler,
            collection_handler=_handler,
            mcp_handler=_handler,
            skill_handler=_handler,
            role=UNRESTRICTED_ROLE,
        )
        for kind in PRIVILEGED_KINDS:
            result = dispatcher.dispatch(ToolCall(kind=kind, name="x", args={}))
            assert result.ok is True, (
                f"UNRESTRICTED_ROLE must permit privileged kind {kind!r}. "
                f"result={result}"
            )
        assert len(invoked) == len(PRIVILEGED_KINDS), (
            "every privileged-kind handler should have run under "
            f"UNRESTRICTED_ROLE. invoked={invoked}"
        )
