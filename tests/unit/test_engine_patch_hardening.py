"""Patch-application hardening tests for ExecutionEngine.

The model (an untrusted LLM) supplies unified diffs that ExecutionEngine feeds
to ``patch -p1``. These tests prove the patch target is confined to the
workspace realpath jail: a diff whose ``+++`` *or* ``---`` path escapes the jail
(absolute path or ``../`` traversal) is rejected and ``patch`` is never invoked
with it, while a well-formed in-jail diff still applies.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from general_ludd.execution.engine import ExecutionEngine
from general_ludd.security.sandboxes.state import SandboxStateError


def _engine(workspace: str) -> ExecutionEngine:
    return ExecutionEngine(model_gateway=MagicMock(), workspace_path=workspace)


def _seed(workspace: str, rel: str, content: str) -> str:
    path = os.path.join(workspace, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)
    return path


class TestInJailPatchApplies:
    def test_in_jail_diff_applies_and_edits_file(self):
        workspace = tempfile.mkdtemp()
        _seed(workspace, "src/main.py", "print('hello')\n")
        engine = _engine(workspace)

        diff = "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-print('hello')\n+print('hello world')\n"
        changed = engine._apply_unified_diff(diff)

        assert "src/main.py" in changed
        with open(os.path.join(workspace, "src/main.py")) as fh:
            assert "hello world" in fh.read()


class TestOutOfJailRejected:
    def _victim(self) -> str:
        victim_dir = tempfile.mkdtemp()
        victim = os.path.join(victim_dir, "victim.txt")
        with open(victim, "w") as fh:
            fh.write("ORIGINAL\n")
        return victim

    def test_absolute_target_rejected_and_no_write(self):
        workspace = tempfile.mkdtemp()
        victim = self._victim()
        engine = _engine(workspace)

        # +++ points at an absolute out-of-jail path. After -p1 strips the
        # leading component the path is still absolute-ish traversal; the jail
        # must refuse it and never invoke patch.
        diff = f"--- a{victim}\n+++ b{victim}\n@@ -1 +1 @@\n-ORIGINAL\n+PWNED\n"
        changed = engine._apply_unified_diff(diff)

        assert changed == []
        with open(victim) as fh:
            assert fh.read() == "ORIGINAL\n"

    def test_dotdot_traversal_target_rejected_and_no_write(self):
        workspace = tempfile.mkdtemp()
        victim = self._victim()
        engine = _engine(workspace)

        # Build a ../-traversal from inside the workspace up to the victim.
        rel = os.path.relpath(victim, workspace)
        assert rel.startswith(".."), "victim must be outside workspace"

        diff = f"--- a/{rel}\n+++ b/{rel}\n@@ -1 +1 @@\n-ORIGINAL\n+PWNED\n"
        changed = engine._apply_unified_diff(diff)

        assert changed == []
        with open(victim) as fh:
            assert fh.read() == "ORIGINAL\n"

    def test_escaping_source_line_rejected_even_if_target_safe(self):
        """A benign +++ must not smuggle an escaping --- past the jail.

        ``patch`` reads both the ``---`` (source/old) and ``+++`` (target/new)
        paths and, with --force, can fall back to the source path. So an
        attacker could place a safe ``+++`` and an escaping ``---``. The jail
        must validate BOTH and refuse the whole diff.
        """
        workspace = tempfile.mkdtemp()
        victim = self._victim()
        rel = os.path.relpath(victim, workspace)
        engine = _engine(workspace)

        diff = f"--- a/{rel}\n+++ b/safe.txt\n@@ -1 +1 @@\n-ORIGINAL\n+PWNED\n"
        changed = engine._apply_unified_diff(diff)

        assert changed == []
        with open(victim) as fh:
            assert fh.read() == "ORIGINAL\n"
        assert not os.path.exists(os.path.join(workspace, "safe.txt"))


class TestArgvIsListForm:
    def test_patch_invoked_with_list_argv_and_realpath_dir(self, monkeypatch):
        """argv must be list-form (never a shell string) and -d must be the
        realpath jail base.

        Hardening moved upstream: ExecutionEngine.__init__ now runs
        secure_directory() on the workspace, which rejects symlink components
        outright (SandboxStateError). A symlinked workspace therefore can no
        longer exist to diverge from what the jail validates.
        """
        # Workspace reached via a symlink: engine construction must refuse it
        # before any patch invocation could occur.
        real_root = tempfile.mkdtemp()
        real_ws = os.path.join(real_root, "ws")
        os.makedirs(real_ws)
        _seed(real_ws, "src/main.py", "print('hello')\n")
        link_root = tempfile.mkdtemp()
        link_ws = os.path.join(link_root, "ws-link")
        os.symlink(real_ws, link_ws)
        with pytest.raises(SandboxStateError):
            _engine(link_ws)

        engine = _engine(real_ws)
        captured: dict[str, object] = {}

        real_run = __import__("subprocess").run

        def fake_run(argv, *args, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return real_run(argv, *args, **kwargs)

        monkeypatch.setattr("general_ludd.execution.engine.subprocess.run", fake_run)

        diff = "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-print('hello')\n+print('hello world')\n"
        engine._apply_unified_diff(diff)

        argv = captured.get("argv")
        assert isinstance(argv, list), "patch argv must be list-form, not a string"
        assert argv[0] == "patch"
        # -d directory must be the realpath jail base.
        assert "-d" in argv
        d_value = argv[argv.index("-d") + 1]
        assert os.path.realpath(d_value) == os.path.realpath(real_ws)
