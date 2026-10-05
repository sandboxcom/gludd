"""Adversarial tests for the collection filesystem write-policy guard.

Covers the module_util at
``collections/ansible_collections/general_ludd/agent/plugins/module_utils/fs_write_policy.py``
and its wiring into the ``gludd_worktree`` write-capable module.

Design notes
------------
* The module_util is loaded by *path* via ``importlib`` (the same convention
  ``tests/integration/test_playbook_registry.py`` uses for collection
  modules) so no collection install or live daemon is required.
* Every denied vector is asserted FAIL-CLOSED: the policy must raise
  ``WritePolicyError`` (and ``is_allowed`` must return ``False``).  Allowed
  locations must succeed and return a resolved path inside an allowed root.
* Real temp dirs and real symlinks are used so symlink-escape rejection is
  exercised against the actual filesystem, not a mock.
* The wiring into ``gludd_worktree`` is verified by AST/text parsing of the
  module source (it imports the guard, builds a policy, and calls
  ``policy.check`` before any git mutation) — again, no daemon needed.
"""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path

import pytest

# tests/security/test_fs_write_policy.py -> repo root is three parents up.
ROOT = Path(__file__).parent.parent.parent
MODULE_UTILS_DIR = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "module_utils"
)
MODULES_DIR = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
)
POLICY_PATH = MODULE_UTILS_DIR / "fs_write_policy.py"


def _load_policy_module():
    """Import the fs_write_policy module_util by path (no collection install)."""
    assert POLICY_PATH.is_file(), f"missing module_util: {POLICY_PATH}"
    spec = importlib.util.spec_from_file_location("fs_write_policy", str(POLICY_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass field-type resolution (which looks the
    # module up in sys.modules via cls.__module__) works under Python 3.12+.
    import sys

    sys.modules["fs_write_policy"] = mod
    spec.loader.exec_module(mod)
    return mod


policy_mod = _load_policy_module()
WritePolicy = policy_mod.WritePolicy
WritePolicyError = policy_mod.WritePolicyError
default_policy = policy_mod.default_policy


@pytest.fixture()
def allowed_root(tmp_path: Path) -> Path:
    """A real allowed root with a nested writable subdir."""
    root = tmp_path / "workspace"
    (root / "sub").mkdir(parents=True)
    return root


@pytest.fixture()
def policy(allowed_root: Path) -> object:
    """Policy whose single allowed root is the realpath of ``allowed_root``."""
    return WritePolicy(allowed_roots=[str(allowed_root)])


# ---------------------------------------------------------------------------
# Allowed-location writes succeed
# ---------------------------------------------------------------------------

class TestAllowedWrites:
    def test_write_directly_in_root(self, policy, allowed_root):
        resolved = policy.check(str(allowed_root / "newfile.txt"))
        assert os.path.realpath(str(allowed_root)) in resolved
        assert policy.is_allowed(str(allowed_root / "newfile.txt"))

    def test_write_in_nested_subdir(self, policy, allowed_root):
        target = allowed_root / "sub" / "deep" / "file.txt"
        resolved = policy.check(str(target))
        assert resolved == os.path.realpath(str(target))

    def test_root_itself_is_allowed(self, policy, allowed_root):
        assert policy.is_allowed(str(allowed_root))

    def test_default_policy_allows_tmp_scratch(self, tmp_path):
        # tmp_path lives under the OS temp root, which default_policy allowlists.
        pol = default_policy(workspace=str(tmp_path), worktree_root=str(tmp_path))
        assert pol.is_allowed(str(tmp_path / "scratch" / "out.log"))


# ---------------------------------------------------------------------------
# Default-DENY posture
# ---------------------------------------------------------------------------

class TestDefaultDeny:
    def test_no_roots_denies_everything(self, tmp_path):
        pol = WritePolicy(allowed_roots=[])
        with pytest.raises(WritePolicyError):
            pol.check(str(tmp_path / "anything.txt"))
        assert pol.is_allowed(str(tmp_path / "anything.txt")) is False

    def test_empty_path_denied(self, policy):
        with pytest.raises(WritePolicyError):
            policy.check("")


# ---------------------------------------------------------------------------
# Denied vector: absolute path outside every allowed root
# ---------------------------------------------------------------------------

class TestOutsideRootAbsolute:
    @pytest.mark.parametrize(
        "bad",
        [
            "/etc/passwd",
            "/root/.bashrc",
            "/usr/local/bin/evil",
            "/var/lib/secret",
        ],
    )
    def test_absolute_outside_root_denied(self, policy, bad):
        with pytest.raises(WritePolicyError):
            policy.check(bad)
        assert policy.is_allowed(bad) is False

    def test_sibling_prefix_not_confused_for_root(self, tmp_path):
        # /…/workspace must NOT permit /…/workspace-evil via naive startswith.
        root = tmp_path / "workspace"
        root.mkdir()
        evil = tmp_path / "workspace-evil"
        evil.mkdir()
        pol = WritePolicy(allowed_roots=[str(root)])
        with pytest.raises(WritePolicyError):
            pol.check(str(evil / "file.txt"))


# ---------------------------------------------------------------------------
# Denied vector: ../ traversal
# ---------------------------------------------------------------------------

class TestTraversal:
    def test_dotdot_escapes_root_denied(self, policy, allowed_root):
        with pytest.raises(WritePolicyError):
            policy.check(str(allowed_root / ".." / ".." / "etc" / "passwd"))

    def test_literal_dotdot_component_denied_even_if_resolves_inside(
        self, policy, allowed_root
    ):
        # allowed_root/sub/../inside resolves back inside the root, but the
        # literal `..` component is refused outright (intent rejected).
        with pytest.raises(WritePolicyError):
            policy.check(str(allowed_root / "sub" / ".." / "inside.txt"))

    def test_relative_dotdot_denied(self, policy):
        with pytest.raises(WritePolicyError):
            policy.check("../../../etc/shadow")


# ---------------------------------------------------------------------------
# Denied vector: symlink escape to outside an allowed root
# ---------------------------------------------------------------------------

class TestSymlinkEscape:
    def test_symlink_leaf_points_outside(self, policy, allowed_root, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        link = allowed_root / "escape"  # lives inside root, points outside
        os.symlink(str(outside), str(link))
        # Writing "through" the symlink resolves outside the root -> denied.
        with pytest.raises(WritePolicyError):
            policy.check(str(link / "loot.txt"))
        assert policy.is_allowed(str(link / "loot.txt")) is False

    def test_symlinked_intermediate_component_escapes(
        self, policy, allowed_root, tmp_path
    ):
        outside = tmp_path / "outside2"
        (outside / "deep").mkdir(parents=True)
        link = allowed_root / "bridge"
        os.symlink(str(outside), str(link))
        # An intermediate symlink component must also be resolved + rejected.
        with pytest.raises(WritePolicyError):
            policy.check(str(link / "deep" / "file.txt"))

    def test_symlink_pointing_inside_is_allowed(self, policy, allowed_root):
        target = allowed_root / "sub"
        link = allowed_root / "innerlink"
        os.symlink(str(target), str(link))
        # Resolves to allowed_root/sub which is inside the root -> allowed.
        assert policy.is_allowed(str(link / "ok.txt"))


# ---------------------------------------------------------------------------
# Denied vector: encoded / NUL-byte path trickery
# ---------------------------------------------------------------------------

class TestEncodedPaths:
    def test_nul_byte_denied(self, policy, allowed_root):
        with pytest.raises(WritePolicyError):
            policy.check(str(allowed_root) + "/ok\x00/../../etc/passwd")

    def test_nul_byte_anywhere_denied(self, policy, allowed_root):
        with pytest.raises(WritePolicyError):
            policy.check(str(allowed_root / "file\x00.txt"))


# ---------------------------------------------------------------------------
# Denied vector: sensitive targets (.git / .ssh / keys / secrets) inside root
# ---------------------------------------------------------------------------

class TestSensitiveTargets:
    @pytest.mark.parametrize(
        "rel",
        [
            ".git/config",
            ".git/hooks/pre-commit",
            ".ssh/authorized_keys",
            ".ssh/id_rsa",
            ".env",
            "secrets/token",
            ".aws/credentials",
            "deploy.pem",
            "server.key",
            "backup_rsa",
        ],
    )
    def test_sensitive_target_denied_even_inside_root(self, policy, allowed_root, rel):
        target = allowed_root / rel
        with pytest.raises(WritePolicyError):
            policy.check(str(target))
        assert policy.is_allowed(str(target)) is False

    def test_git_internals_as_parent_denied(self, policy, allowed_root):
        with pytest.raises(WritePolicyError):
            policy.check(str(allowed_root / ".git" / "refs" / "heads" / "main"))

    def test_ordinary_file_named_like_prefix_allowed(self, policy, allowed_root):
        # A normal file that merely *resembles* a secret name but is not one.
        assert policy.is_allowed(str(allowed_root / "gitignore_notes.txt"))


# ---------------------------------------------------------------------------
# Wiring proof: gludd_worktree enforces the policy before any git mutation
# ---------------------------------------------------------------------------

class TestModuleWiring:
    WORKTREE_MODULE = MODULES_DIR / "gludd_worktree.py"

    def _source(self) -> str:
        assert self.WORKTREE_MODULE.is_file(), f"missing {self.WORKTREE_MODULE}"
        return self.WORKTREE_MODULE.read_text()

    def test_module_imports_the_guard(self):
        src = self._source()
        assert "fs_write_policy" in src, "gludd_worktree must import the write policy"
        assert "default_policy" in src
        assert "WritePolicyError" in src

    def test_module_calls_policy_check(self):
        src = self._source()
        assert "policy.check(" in src, "gludd_worktree must call policy.check()"

    def test_module_still_parses(self):
        # Guard against a syntax error introduced by the wiring edit.
        ast.parse(self._source())

    def test_policy_check_precedes_git_mutation(self):
        """policy.check must run before each authenticated mutation request.

        Fail-closed ordering: a denied path must never reach the git layer.
        """
        src = self._source()
        check_idx = src.index("policy.check(")
        check_line = src[:check_idx].count("\n")
        mutation_lines = [
            index
            for index, line in enumerate(src.splitlines())
            if "client.post(" in line
        ]

        assert mutation_lines, "gludd_worktree must invoke its authenticated API seam"
        assert all(check_line < mutation_line for mutation_line in mutation_lines)
        assert src.count('"/admin/git/operation"') == len(mutation_lines)
        assert '"op": "worktree_create"' in src
        assert '"op": "worktree_remove"' in src
        assert "general_ludd.git_automation" not in src

    def test_check_inside_main_function(self):
        """The policy.check call lives in main() (the executed path), via AST."""
        tree = ast.parse(self._source())
        main_fn = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main"
            ),
            None,
        )
        assert main_fn is not None, "gludd_worktree must define main()"
        calls = [
            n
            for n in ast.walk(main_fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "check"
        ]
        assert calls, "policy.check() must be called inside main()"
