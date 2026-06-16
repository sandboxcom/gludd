"""Adversarial tests for FIM-on-write audit composed over the write policy.

Covers the module_util at
``collections/ansible_collections/general_ludd/agent/plugins/module_utils/fs_write_audit.py``
and its wiring into the ``gludd_worktree`` write-capable module.

Design notes
------------
* The module_util is loaded by *path* via ``importlib`` (the same convention
  ``test_fs_write_policy.py`` / ``test_playbook_registry.py`` use) so no
  collection install or live daemon is required.  Both ``fs_write_policy`` and
  ``fs_write_audit`` are registered in ``sys.modules`` before exec so the
  audit module's ``from fs_write_policy import ...`` fallback resolves.
* Real temp dirs, real files, and real symlinks are used so symlink-escape and
  out-of-band tamper detection are exercised against the actual filesystem.
* Properties asserted:
    - allowed-location writes are RECORDED (manifest entry path/sha256/ts);
    - an out-of-band modification between two writes is DETECTED (fail-closed);
    - the manifest file itself cannot be parked outside the allowed roots;
    - injection / encoding / NUL / traversal / symlink vectors on the audited
      path are still rejected by the underlying WritePolicy (FIM never bypasses
      the policy).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

# tests/security/test_fs_write_audit.py -> repo root is three parents up.
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
AUDIT_PATH = MODULE_UTILS_DIR / "fs_write_audit.py"


def _load_by_path(name: str, path: Path):
    assert path.is_file(), f"missing module_util: {path}"
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # register before exec for intra-module imports
    spec.loader.exec_module(mod)
    return mod


# Load the policy first so the audit module's ``from fs_write_policy import ...``
# fallback resolves against the same in-memory module object.
policy_mod = _load_by_path("fs_write_policy", POLICY_PATH)
audit_mod = _load_by_path("fs_write_audit", AUDIT_PATH)

WritePolicy = policy_mod.WritePolicy
WritePolicyError = policy_mod.WritePolicyError
WriteAuditLog = audit_mod.WriteAuditLog
IntegrityViolation = audit_mod.IntegrityViolation
AuditEntry = audit_mod.AuditEntry
hash_bytes_on_disk = audit_mod.hash_bytes_on_disk


@pytest.fixture()
def allowed_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "sub").mkdir(parents=True)
    return root


@pytest.fixture()
def policy(allowed_root: Path) -> object:
    return WritePolicy(allowed_roots=[str(allowed_root)])


@pytest.fixture()
def manifest_path(allowed_root: Path) -> str:
    return str(allowed_root / "audit_manifest.json")


@pytest.fixture()
def audit(policy, manifest_path):
    return WriteAuditLog(policy=policy, manifest_path=manifest_path)


# ---------------------------------------------------------------------------
# Recording: allowed-location writes land in the manifest with path/hash/ts
# ---------------------------------------------------------------------------

class TestWritesAreRecorded:
    def test_audited_write_records_entry(self, audit, allowed_root):
        target = allowed_root / "out.txt"
        entry = audit.audited_write(str(target), b"hello fim")
        assert isinstance(entry, AuditEntry)
        assert entry.path == os.path.realpath(str(target))
        # Hash matches a fresh sha256 of the bytes that were written.
        import hashlib

        assert entry.sha256 == hashlib.sha256(b"hello fim").hexdigest()
        assert entry.recorded_at  # non-empty ISO timestamp
        assert os.path.realpath(str(target)) in audit.recorded_paths()

    def test_manifest_persisted_with_entry(self, audit, allowed_root, manifest_path):
        target = allowed_root / "sub" / "deep.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        audit.audited_write(str(target), b"data")
        assert os.path.isfile(manifest_path)
        raw = json.loads(Path(manifest_path).read_text())
        paths = [e["path"] for e in raw["entries"]]
        assert os.path.realpath(str(target)) in paths
        rec = next(e for e in raw["entries"] if e["path"] == os.path.realpath(str(target)))
        assert set(rec) == {"path", "sha256", "recorded_at"}

    def test_manifest_survives_new_auditlog_instance(self, policy, allowed_root, manifest_path):
        target = allowed_root / "persist.txt"
        a1 = WriteAuditLog(policy=policy, manifest_path=manifest_path)
        a1.audited_write(str(target), b"v1")
        # A fresh log over the same manifest must reload the recorded baseline.
        a2 = WriteAuditLog(policy=policy, manifest_path=manifest_path)
        assert a2.entry_for(str(target)) is not None
        assert a2.entry_for(str(target)).sha256 == hash_bytes_on_disk(str(target))

    def test_rewrite_updates_recorded_hash(self, audit, allowed_root):
        target = allowed_root / "evolving.txt"
        audit.audited_write(str(target), b"first")
        e2 = audit.audited_write(str(target), b"second")  # our own legit rewrite
        import hashlib

        assert e2.sha256 == hashlib.sha256(b"second").hexdigest()


# ---------------------------------------------------------------------------
# Tamper detection: out-of-band modification between writes is DETECTED
# ---------------------------------------------------------------------------

class TestTamperDetection:
    def test_out_of_band_modification_detected(self, audit, allowed_root):
        target = allowed_root / "guarded.txt"
        audit.audited_write(str(target), b"trusted contents")
        # Simulate an adversary editing the file out-of-band (not via the audit).
        Path(target).write_bytes(b"tampered contents")
        # The next audited write must fail-closed: pre-hash != recorded post-hash.
        with pytest.raises(IntegrityViolation):
            audit.pre_write_check(str(target))
        with pytest.raises(IntegrityViolation):
            audit.audited_write(str(target), b"would-be next write")

    def test_untampered_rewrite_allowed(self, audit, allowed_root):
        target = allowed_root / "clean.txt"
        audit.audited_write(str(target), b"v1")
        # No out-of-band change: our recorded hash still matches disk -> allowed.
        resolved = audit.pre_write_check(str(target))
        assert resolved == os.path.realpath(str(target))

    def test_first_write_to_unrecorded_path_passes_tamper_gate(self, audit, allowed_root):
        # No prior record => nothing to violate, even if a file already exists.
        target = allowed_root / "preexisting.txt"
        Path(target).write_bytes(b"already here")
        resolved = audit.pre_write_check(str(target))  # must not raise
        assert resolved == os.path.realpath(str(target))

    def test_tamper_detected_across_auditlog_instances(self, policy, allowed_root, manifest_path):
        target = allowed_root / "cross.txt"
        WriteAuditLog(policy=policy, manifest_path=manifest_path).audited_write(
            str(target), b"baseline"
        )
        Path(target).write_bytes(b"external edit between runs")
        reloaded = WriteAuditLog(policy=policy, manifest_path=manifest_path)
        with pytest.raises(IntegrityViolation):
            reloaded.pre_write_check(str(target))

    def test_deleted_then_recreated_file_does_not_false_violate(self, audit, allowed_root):
        # If the recorded file is gone, pre_write_check has no on-disk bytes to
        # compare; it must not raise (a fresh create is legitimate).
        target = allowed_root / "gone.txt"
        audit.audited_write(str(target), b"v1")
        os.remove(target)
        assert audit.pre_write_check(str(target)) == os.path.realpath(str(target))


# ---------------------------------------------------------------------------
# The manifest itself cannot be written outside the allowed roots
# ---------------------------------------------------------------------------

class TestManifestConfinement:
    def test_manifest_outside_root_rejected_on_write(self, policy, allowed_root, tmp_path):
        outside_manifest = str(tmp_path / "outside" / "manifest.json")
        os.makedirs(os.path.dirname(outside_manifest), exist_ok=True)
        # policy's only root is allowed_root; the manifest path is a sibling.
        log = WriteAuditLog(policy=policy, manifest_path=outside_manifest)
        target = allowed_root / "x.txt"
        with pytest.raises(WritePolicyError):
            log.audited_write(str(target), b"data")
        # And nothing was parked at the out-of-root manifest location.
        assert not os.path.exists(outside_manifest)

    def test_manifest_absolute_system_path_rejected(self, policy, allowed_root):
        log = WriteAuditLog(policy=policy, manifest_path="/etc/cron.d/gludd_manifest")
        target = allowed_root / "y.txt"
        with pytest.raises(WritePolicyError):
            log.audited_write(str(target), b"data")

    def test_manifest_traversal_path_rejected(self, policy, allowed_root):
        evil = str(allowed_root / ".." / ".." / "etc" / "manifest.json")
        log = WriteAuditLog(policy=policy, manifest_path=evil)
        target = allowed_root / "z.txt"
        with pytest.raises(WritePolicyError):
            log.audited_write(str(target), b"data")

    def test_manifest_symlink_escape_rejected(self, policy, allowed_root, tmp_path):
        outside = tmp_path / "exfil"
        outside.mkdir()
        link = allowed_root / "manifest_link"  # inside root, points outside
        os.symlink(str(outside), str(link))
        log = WriteAuditLog(policy=policy, manifest_path=str(link / "manifest.json"))
        target = allowed_root / "w.txt"
        with pytest.raises(WritePolicyError):
            log.audited_write(str(target), b"data")


# ---------------------------------------------------------------------------
# Underlying policy vectors still reject the AUDITED path (FIM never bypasses)
# ---------------------------------------------------------------------------

class TestAuditedPathStillPolicyGuarded:
    def test_audited_write_outside_root_denied(self, audit, tmp_path):
        with pytest.raises(WritePolicyError):
            audit.audited_write(str(tmp_path / "elsewhere.txt"), b"data")

    @pytest.mark.parametrize(
        "bad",
        ["/etc/passwd", "/root/.bashrc", "/usr/local/bin/evil"],
    )
    def test_absolute_outside_root_denied(self, audit, bad):
        with pytest.raises(WritePolicyError):
            audit.pre_write_check(bad)

    def test_nul_byte_in_audited_path_denied(self, audit, allowed_root):
        with pytest.raises(WritePolicyError):
            audit.pre_write_check(str(allowed_root / "ok\x00.txt"))

    def test_dotdot_traversal_in_audited_path_denied(self, audit, allowed_root):
        with pytest.raises(WritePolicyError):
            audit.pre_write_check(str(allowed_root / "sub" / ".." / ".." / "etc" / "x"))

    def test_symlink_escape_on_audited_path_denied(self, audit, allowed_root, tmp_path):
        outside = tmp_path / "loot"
        outside.mkdir()
        link = allowed_root / "escape"
        os.symlink(str(outside), str(link))
        with pytest.raises(WritePolicyError):
            audit.pre_write_check(str(link / "stolen.txt"))

    @pytest.mark.parametrize(
        "rel",
        [".git/config", ".ssh/id_rsa", ".env", "secrets/token", "deploy.pem", "server.key"],
    )
    def test_sensitive_audited_target_denied(self, audit, allowed_root, rel):
        with pytest.raises(WritePolicyError):
            audit.pre_write_check(str(allowed_root / rel))

    def test_no_bytes_written_when_pre_check_fails(self, audit, tmp_path):
        # audited_write must NOT create a file when the policy rejects the path.
        bad = tmp_path / "should_not_exist.txt"
        with pytest.raises(WritePolicyError):
            audit.audited_write(str(bad), b"data")
        assert not bad.exists()


# ---------------------------------------------------------------------------
# Wiring proof: gludd_worktree composes the audit log over the SAME policy
# ---------------------------------------------------------------------------

class TestModuleWiring:
    WORKTREE_MODULE = MODULES_DIR / "gludd_worktree.py"

    def _source(self) -> str:
        assert self.WORKTREE_MODULE.is_file(), f"missing {self.WORKTREE_MODULE}"
        return self.WORKTREE_MODULE.read_text()

    def test_module_imports_the_audit_util(self):
        src = self._source()
        assert "fs_write_audit" in src
        assert "WriteAuditLog" in src
        assert "IntegrityViolation" in src

    def test_module_constructs_auditlog_over_policy(self):
        src = self._source()
        assert "WriteAuditLog(" in src
        assert "policy=policy" in src, "audit must reuse the SAME policy instance"

    def test_module_records_an_audited_write(self):
        src = self._source()
        assert "audited_write(" in src

    def test_module_still_parses(self):
        ast.parse(self._source())

    def test_audit_used_inside_main(self):
        tree = ast.parse(self._source())
        main_fn = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main"
            ),
            None,
        )
        assert main_fn is not None
        names = {
            n.func.attr
            for n in ast.walk(main_fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "audited_write" in names

    def test_policy_check_precedes_audit_write(self):
        # Fail-closed ordering: the policy gate runs before any audited write.
        src = self._source()
        assert src.index("policy.check(") < src.index("audited_write(")
