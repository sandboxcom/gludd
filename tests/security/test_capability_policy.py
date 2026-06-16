"""Adversarial tests for the per-role capability policy guard.

Covers the module_util at
``collections/ansible_collections/general_ludd/agent/plugins/module_utils/capability_policy.py``
and its wiring into the ``gludd_db`` module.

Design notes
------------
* The module_util is loaded by *path* via ``importlib`` (the same convention
  ``tests/security/test_fs_write_policy.py`` and
  ``tests/integration/test_playbook_registry.py`` use for collection code) so
  no collection install or live daemon is required.  ``capability_policy``
  imports its sibling ``fs_write_policy`` at import time, so the module_utils
  dir is placed on ``sys.path`` before the load.
* The policy is **default-DENY** in every dimension: an unknown role grants
  nothing, and every ``check_*`` method raises ``CapabilityError`` (fail-closed)
  on denial.  Each dimension is exercised on BOTH the allow and the deny path.
* ``collections``-self-modify is verified to be granted ONLY to the
  self-improvement / self-research roles, refused for everybody else (including
  an ordinary fs-write role).
* The wiring into ``gludd_db`` is verified by AST/text parsing of the module
  source (it imports the guard, calls ``check_db_op`` BEFORE constructing the
  daemon client / issuing any request) — no daemon needed.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

import pytest

# tests/security/test_capability_policy.py -> repo root is three parents up.
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
POLICY_PATH = MODULE_UTILS_DIR / "capability_policy.py"


def _load_capability_module():
    """Import the capability_policy module_util by path (no collection install).

    The util imports its sibling ``fs_write_policy`` at module scope, so the
    module_utils directory is added to ``sys.path`` first to satisfy the
    flat-layout import fallback.
    """
    assert POLICY_PATH.is_file(), f"missing module_util: {POLICY_PATH}"
    if str(MODULE_UTILS_DIR) not in sys.path:
        sys.path.insert(0, str(MODULE_UTILS_DIR))
    spec = importlib.util.spec_from_file_location(
        "capability_policy", str(POLICY_PATH)
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass field-type resolution (which looks the
    # module up in sys.modules via cls.__module__) works under Python 3.12+.
    sys.modules["capability_policy"] = mod
    spec.loader.exec_module(mod)
    return mod


cap_mod = _load_capability_module()
CapabilityPolicy = cap_mod.CapabilityPolicy
CapabilityError = cap_mod.CapabilityError
for_role = cap_mod.for_role
extract_host = cap_mod.extract_host


# ---------------------------------------------------------------------------
# Default-DENY posture: an unknown role grants nothing in any dimension
# ---------------------------------------------------------------------------

class TestUnknownRoleDefaultDeny:
    def test_unknown_role_returns_baseline(self):
        cap = for_role("no_such_role_xyz")
        assert isinstance(cap, CapabilityPolicy)
        assert cap.fs_write is False
        assert cap.collections_self_modify is False
        assert cap.db_ops == []
        assert cap.facts_prefixes == []
        assert cap.network_hosts == []
        assert cap.secret_prefixes == []

    @pytest.mark.parametrize("role", [None, "", "   ", "ghost"])
    def test_unknown_role_denies_every_dimension(self, role):
        cap = for_role(role)
        with pytest.raises(CapabilityError):
            cap.check_db_op("todo_get")
        with pytest.raises(CapabilityError):
            cap.check_fs_write("/tmp/anything.txt")
        with pytest.raises(CapabilityError):
            cap.check_collections_self_modify("/x/collections/foo.py")
        with pytest.raises(CapabilityError):
            cap.check_facts_access("ludd.todos")
        with pytest.raises(CapabilityError):
            cap.check_network_host("localhost")
        with pytest.raises(CapabilityError):
            cap.check_secret_access("deploy/token")

    def test_bare_policy_denies_everything(self):
        cap = CapabilityPolicy()
        with pytest.raises(CapabilityError):
            cap.check_db_op("todo_get")
        with pytest.raises(CapabilityError):
            cap.check_facts_access("ludd")


# ---------------------------------------------------------------------------
# Database dimension: allow + deny
# ---------------------------------------------------------------------------

class TestDatabaseDimension:
    def test_allowed_op_passes(self):
        cap = for_role("coder")
        assert cap.check_db_op("todo_create") == "todo_create"

    def test_disallowed_op_denied(self):
        # coder is NOT granted resource_preference.
        cap = for_role("coder")
        with pytest.raises(CapabilityError):
            cap.check_db_op("resource_preference")

    def test_operator_has_broad_db(self):
        cap = for_role("operator")
        for op in ("todo_get", "todo_create", "todo_update_status",
                   "resource_preference"):
            assert cap.check_db_op(op) == op

    @pytest.mark.parametrize("bad", ["", None, "drop_table"])
    def test_invalid_or_unknown_op_denied(self, bad):
        cap = for_role("coder")
        with pytest.raises(CapabilityError):
            cap.check_db_op(bad)


# ---------------------------------------------------------------------------
# Filesystem-write dimension: allow + deny (composed with WritePolicy)
# ---------------------------------------------------------------------------

class TestFilesystemDimension:
    def test_role_without_fs_write_denied(self):
        cap = for_role("report_status")  # no fs_write capability at all
        with pytest.raises(CapabilityError):
            cap.check_fs_write("/tmp/out.txt")

    def test_fs_write_role_allows_inside_roots(self, tmp_path):
        cap = CapabilityPolicy(
            role="t", fs_write=True, fs_write_roots=[str(tmp_path)]
        )
        resolved = cap.check_fs_write(str(tmp_path / "out.txt"))
        assert os.path.realpath(str(tmp_path)) in resolved

    def test_fs_write_denied_outside_roots(self, tmp_path):
        cap = CapabilityPolicy(
            role="t", fs_write=True, fs_write_roots=[str(tmp_path)]
        )
        with pytest.raises(CapabilityError):
            cap.check_fs_write("/etc/passwd")

    def test_fs_write_composes_sensitive_guard(self, tmp_path):
        # Even with fs_write + matching root, .ssh/secret targets are refused
        # because the composed WritePolicy still rejects sensitive names.
        cap = CapabilityPolicy(
            role="t", fs_write=True, fs_write_roots=[str(tmp_path)]
        )
        with pytest.raises(CapabilityError):
            cap.check_fs_write(str(tmp_path / ".ssh" / "id_rsa"))


# ---------------------------------------------------------------------------
# Collections self-modify: ONLY self-improvement / self-research roles
# ---------------------------------------------------------------------------

class TestCollectionsSelfModify:
    def test_self_improve_role_may_self_modify(self):
        cap = for_role("self_improve_agent")
        assert cap.collections_self_modify is True
        path = "/workspace/collections/ansible_collections/x/plugins/y.py"
        assert cap.check_collections_self_modify(path) == path

    def test_self_research_role_may_self_modify(self):
        cap = for_role("self_research_agent")
        assert cap.check_collections_self_modify("/x/collections/z.py")

    def test_ordinary_fs_write_role_may_not_self_modify(self):
        # coder has fs_write but NOT collections_self_modify.
        cap = for_role("coder")
        assert cap.collections_self_modify is False
        with pytest.raises(CapabilityError):
            cap.check_collections_self_modify("/x/collections/z.py")

    def test_unknown_role_may_not_self_modify(self):
        cap = for_role("ghost")
        with pytest.raises(CapabilityError):
            cap.check_collections_self_modify("/x/collections/z.py")

    def test_fs_write_into_collections_requires_self_modify(self, tmp_path):
        # A path that lands inside a collections/ tree triggers the self-modify
        # gate even on an fs_write-capable role that lacks the grant.
        coll = tmp_path / "collections" / "pkg"
        coll.mkdir(parents=True)
        cap = CapabilityPolicy(
            role="plain", fs_write=True, fs_write_roots=[str(tmp_path)]
        )
        with pytest.raises(CapabilityError):
            cap.check_fs_write(str(coll / "mod.py"))

    def test_fs_write_into_collections_allowed_when_granted(self, tmp_path):
        coll = tmp_path / "collections" / "pkg"
        coll.mkdir(parents=True)
        cap = CapabilityPolicy(
            role="si",
            fs_write=True,
            fs_write_roots=[str(tmp_path)],
            collections_self_modify=True,
        )
        resolved = cap.check_fs_write(str(coll / "mod.py"))
        assert "collections" in resolved


# ---------------------------------------------------------------------------
# Facts / vars dimension: allow + deny
# ---------------------------------------------------------------------------

class TestFactsDimension:
    def test_report_role_may_read_granted_prefix(self):
        cap = for_role("report_status")
        assert cap.check_facts_access("ludd") == "ludd"
        assert cap.check_facts_access("ludd.todos.count") == "ludd.todos.count"

    def test_report_role_may_not_write_fs(self):
        # report_* explicitly read facts but never write the filesystem.
        cap = for_role("report_status")
        with pytest.raises(CapabilityError):
            cap.check_fs_write("/tmp/report.txt")

    def test_facts_prefix_denies_sibling(self):
        cap = for_role("report_metrics")  # ludd.metrics, ludd.traces only
        assert cap.check_facts_access("ludd.metrics.cpu")
        with pytest.raises(CapabilityError):
            cap.check_facts_access("ludd.todos")  # not granted
        with pytest.raises(CapabilityError):
            cap.check_facts_access("ludd.metrics_evil")  # sibling, not child

    def test_empty_fact_path_denied(self):
        cap = for_role("report_status")
        with pytest.raises(CapabilityError):
            cap.check_facts_access("")


# ---------------------------------------------------------------------------
# Network dimension: host allowlist allow + deny
# ---------------------------------------------------------------------------

class TestNetworkDimension:
    def test_allowed_host_passes(self):
        cap = for_role("operator")
        assert cap.check_network_host("localhost") == "localhost"

    def test_subdomain_suffix_match(self):
        cap = CapabilityPolicy(role="t", network_hosts=["example.com"])
        assert cap.check_network_host("api.example.com") == "api.example.com"

    def test_disallowed_host_denied(self):
        cap = for_role("operator")
        with pytest.raises(CapabilityError):
            cap.check_network_host("evil.example.com")

    def test_role_without_network_denied(self):
        cap = for_role("report_status")
        with pytest.raises(CapabilityError):
            cap.check_network_host("localhost")

    def test_extract_host_from_url(self):
        assert extract_host("http://localhost:8000/api/todos") == "localhost"
        assert extract_host("https://api.example.com/x") == "api.example.com"
        assert extract_host("example.com:8000") == "example.com"
        assert extract_host("example.com") == "example.com"


# ---------------------------------------------------------------------------
# Secrets dimension: alias-prefix allow + deny
# ---------------------------------------------------------------------------

class TestSecretsDimension:
    def test_security_role_may_resolve_granted_prefix(self):
        cap = for_role("security_auditor")
        assert cap.check_secret_access("deploy/token") == "deploy/token"
        assert cap.check_secret_access("audit/log-key")

    def test_security_role_denied_other_prefix(self):
        cap = for_role("security_auditor")
        with pytest.raises(CapabilityError):
            cap.check_secret_access("prod/master")

    def test_non_security_role_denied_secrets(self):
        cap = for_role("coder")  # no secret_prefixes
        with pytest.raises(CapabilityError):
            cap.check_secret_access("deploy/token")

    def test_prefix_does_not_leak_to_sibling(self):
        cap = CapabilityPolicy(role="t", secret_prefixes=["deploy/"])
        with pytest.raises(CapabilityError):
            cap.check_secret_access("deploy-evil/token")


# ---------------------------------------------------------------------------
# Config-driven override loader
# ---------------------------------------------------------------------------

class TestConfigOverride:
    def test_config_entry_grants_capability(self):
        config = {"roles": {"custom": {"db_ops": ["todo_get"]}}}
        cap = for_role("custom", config)
        assert cap.check_db_op("todo_get") == "todo_get"
        with pytest.raises(CapabilityError):
            cap.check_db_op("todo_create")  # not granted

    def test_config_partial_entry_still_default_deny(self):
        # Only fs_write granted; every other dimension still denies.
        config = {"roles": {"custom": {"fs_write": True,
                                       "fs_write_roots": ["/tmp"]}}}
        cap = for_role("custom", config)
        with pytest.raises(CapabilityError):
            cap.check_db_op("todo_get")
        with pytest.raises(CapabilityError):
            cap.check_secret_access("deploy/token")

    def test_config_override_beats_builtin(self):
        # Narrow a built-in role via config.
        config = {"roles": {"coder": {"db_ops": []}}}
        cap = for_role("coder", config)
        with pytest.raises(CapabilityError):
            cap.check_db_op("todo_create")


# ---------------------------------------------------------------------------
# Wiring proof: gludd_db gates the op by capability BEFORE any daemon call
# ---------------------------------------------------------------------------

class TestModuleWiring:
    DB_MODULE = MODULES_DIR / "gludd_db.py"

    def _source(self) -> str:
        assert self.DB_MODULE.is_file(), f"missing {self.DB_MODULE}"
        return self.DB_MODULE.read_text()

    def test_module_imports_the_guard(self):
        src = self._source()
        assert "capability_policy" in src, "gludd_db must import the capability guard"
        assert "for_role" in src
        assert "CapabilityError" in src

    def test_module_declares_role_param(self):
        src = self._source()
        assert 'role=dict(' in src, "gludd_db must accept a role parameter"

    def test_module_calls_check_db_op(self):
        src = self._source()
        assert "check_db_op(" in src, "gludd_db must call check_db_op()"

    def test_module_still_parses(self):
        ast.parse(self._source())

    def test_capability_check_precedes_daemon_call(self):
        """check_db_op must run BEFORE the GluddClient is built / any request.

        Fail-closed ordering: a denied op must never reach the daemon.
        """
        src = self._source()
        check_idx = src.index("check_db_op(")
        # The client construction and every request verb come after the gate.
        for sink in ("GluddClient(", "client.get(", "client.post(",
                     "client.patch("):
            assert sink in src, f"expected sink {sink} in module"
            assert check_idx < src.index(sink), (
                f"check_db_op must precede {sink} (fail-closed ordering)"
            )

    def test_check_inside_main_function(self):
        """The check_db_op call lives in main() (the executed path), via AST."""
        tree = ast.parse(self._source())
        main_fn = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main"
            ),
            None,
        )
        assert main_fn is not None, "gludd_db must define main()"
        calls = [
            n
            for n in ast.walk(main_fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "check_db_op"
        ]
        assert calls, "check_db_op() must be called inside main()"
