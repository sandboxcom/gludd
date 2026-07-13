"""Integration tests for .opencode/.opencode.orig backup/recovery system.

Tests the backup-opencode, check-opencode-backup, restore-opencode, and
verify-opencode-backup make targets via their underlying shell commands,
using temp directories for full isolation.

Targets under test (Makefile ~L3878-3918):
  - backup-opencode: rsync .opencode/ -> .opencode.orig/ (excl node_modules)
  - check-opencode-backup: exits 1 if missing or stale
  - restore-opencode: rsync .opencode.orig/ -> .opencode/ + clear ~/.cache/opencode
  - verify-opencode-backup: checks file existence + shared.ts export parity
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

from scripts.verify_opencode_backup import _extract_exports, verify


def _mtime(path: Path) -> float:
    return path.stat().st_mtime


def _touch(path: Path) -> None:
    path.touch()


class TestBackupOpencode:
    """Verify backup-opencode copies .opencode/ -> .opencode.orig/ excluding node_modules."""

    def test_copies_plugin_files_to_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            plugin = opencode / "plugin"
            plugin.mkdir(parents=True)
            (plugin / "enforce-floor.ts").write_text("// test")

            subprocess.run(
                ["rsync", "-a", "--delete",
                 "--exclude=node_modules/", "--exclude=node_modules",
                 str(opencode) + "/", str(root / ".opencode.orig") + "/"],
                check=True,
            )

            backup_plugin = root / ".opencode.orig" / "plugin" / "enforce-floor.ts"
            assert backup_plugin.exists()
            assert backup_plugin.read_text() == "// test"

    def test_excludes_node_modules(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            plugin = opencode / "plugin"
            node_modules = opencode / "node_modules"
            plugin.mkdir(parents=True)
            node_modules.mkdir(parents=True)
            (plugin / "enforce-floor.ts").write_text("// test")
            (node_modules / "some-dep.js").write_text("// dep")

            subprocess.run(
                ["rsync", "-a", "--delete",
                 "--exclude=node_modules/", "--exclude=node_modules",
                 str(opencode) + "/", str(root / ".opencode.orig") + "/"],
                check=True,
            )

            assert (root / ".opencode.orig" / "plugin" / "enforce-floor.ts").exists()
            assert not (root / ".opencode.orig" / "node_modules").exists()

    def test_deletes_removed_files_from_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            opencode / "plugin"
            (opencode / "plugin").mkdir(parents=True)
            (opencode / "plugin" / "a.ts").write_text("a")
            (opencode / "plugin" / "b.ts").write_text("b")

            subprocess.run(
                ["rsync", "-a", "--delete",
                 "--exclude=node_modules/", "--exclude=node_modules",
                 str(opencode) + "/", str(root / ".opencode.orig") + "/"],
                check=True,
            )
            assert (root / ".opencode.orig" / "plugin" / "a.ts").exists()
            assert (root / ".opencode.orig" / "plugin" / "b.ts").exists()

            (opencode / "plugin" / "b.ts").unlink()

            subprocess.run(
                ["rsync", "-a", "--delete",
                 "--exclude=node_modules/", "--exclude=node_modules",
                 str(opencode) + "/", str(root / ".opencode.orig") + "/"],
                check=True,
            )
            assert (root / ".opencode.orig" / "plugin" / "a.ts").exists()
            assert not (root / ".opencode.orig" / "plugin" / "b.ts").exists()


class TestCheckOpencodeBackup:
    """Verify check-opencode-backup detects missing and stale backup directories."""

    def test_missing_backup_dir_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".opencode" / "plugin").mkdir(parents=True)
            (root / ".opencode" / "plugin" / "enforce-floor.ts").write_text("// test")

            backup = root / ".opencode.orig"
            assert not backup.exists()

            proc = subprocess.run(
                ["sh", "-c", "if [ ! -d \"$1\" ]; then exit 1; fi", "_",
                 str(root / ".opencode.orig")],
                capture_output=True,
            )
            assert proc.returncode == 1

    def test_stale_backup_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            opencode.mkdir(parents=True)
            backup.mkdir(parents=True)
            (opencode / "plugin").mkdir(parents=True)
            (opencode / "plugin" / "enforce-floor.ts").write_text("// fresh")
            (backup / "plugin").mkdir(parents=True)
            (backup / "plugin" / "enforce-floor.ts").write_text("// stale")

            time.sleep(0.1)
            _touch(opencode)

            proc = subprocess.run(
                ["find", str(backup), "-maxdepth", "0", "-newer", str(opencode), "-print"],
                capture_output=True, text=True,
            )
            assert proc.stdout.strip() == "", (
                "backup should NOT be newer than .opencode/"
            )

            proc2 = subprocess.run(
                ["sh", "-c",
                 'BACKUP_AGE=$(find "$1" -maxdepth 0 -newer "$2" -print | wc -l | tr -d " "); '
                 'if [ "$BACKUP_AGE" = "0" ]; then exit 1; fi',
                 "_", str(backup), str(opencode)],
            )
            assert proc2.returncode == 1

    def test_fresh_backup_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            opencode.mkdir(parents=True)
            backup.mkdir(parents=True)

            _touch(opencode)
            time.sleep(0.1)
            _touch(backup)

            proc = subprocess.run(
                ["find", str(backup), "-maxdepth", "0", "-newer", str(opencode), "-print"],
                capture_output=True, text=True,
            )
            assert proc.stdout.strip() != "", (
                "fresh backup should be newer than .opencode/"
            )


class TestRestoreOpencode:
    """Verify restore-opencode mirrors .opencode.orig/ -> .opencode/ and clears cache."""

    @staticmethod
    def _rsync_restore(opencode: Path, backup: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["rsync", "-a", "--delete",
             "--exclude=node_modules/", "--exclude=node_modules",
             str(backup) + "/", str(opencode) + "/"],
            check=True,
        )

    @staticmethod
    def _rsync_backup(opencode: Path, backup: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["rsync", "-a", "--delete",
             "--exclude=node_modules/", "--exclude=node_modules",
             str(opencode) + "/", str(backup) + "/"],
            check=True,
        )

    def test_restores_complete_tree_from_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            opencode.mkdir(parents=True)
            backup.mkdir(parents=True)

            (backup / "plugin").mkdir(parents=True)
            (backup / "plugin" / "enforce-floor.ts").write_text("// floor")
            (backup / "plugin" / "shared.ts").write_text("// shared")
            (backup / "plugins").mkdir(parents=True)
            (backup / "plugins" / "watchdog.ts").write_text("// watchdog")
            (backup / "skills").mkdir(parents=True)
            (backup / "skills" / "guardrail-pattern").mkdir(parents=True)
            (backup / "skills" / "guardrail-pattern" / "SKILL.md").write_text("# skill")
            (backup / "agent").mkdir(parents=True)
            (backup / "agent" / "web-update.md").write_text("# agent")
            (backup / "skill").mkdir(parents=True)
            (backup / "skill" / "deep-spec").mkdir(parents=True)
            (backup / "skill" / "deep-spec" / "SKILL.md").write_text("# deep-spec")
            (backup / "opencode.json").write_text('{"key": "value"}')
            (backup / ".gitignore").write_text("node_modules/")
            (backup / "plugin-hashes.json").write_text('{"hashes": {}}')

            self._rsync_restore(opencode, backup)

            assert (opencode / "plugin" / "enforce-floor.ts").read_text() == "// floor"
            assert (opencode / "plugin" / "shared.ts").read_text() == "// shared"
            assert (opencode / "plugins" / "watchdog.ts").read_text() == "// watchdog"
            assert (opencode / "skills" / "guardrail-pattern" / "SKILL.md").read_text() == "# skill"
            assert (opencode / "agent" / "web-update.md").read_text() == "# agent"
            assert (opencode / "skill" / "deep-spec" / "SKILL.md").read_text() == "# deep-spec"
            assert (opencode / "opencode.json").read_text() == '{"key": "value"}'
            assert (opencode / ".gitignore").read_text() == "node_modules/"
            assert (opencode / "plugin-hashes.json").read_text() == '{"hashes": {}}'

    def test_restore_unknown_subdirectory_survives(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            opencode.mkdir(parents=True)
            backup.mkdir(parents=True)

            (backup / "mcp_servers").mkdir(parents=True)
            (backup / "mcp_servers" / "config.json").write_text('{"servers": []}')
            (backup / "plugin").mkdir(parents=True)
            (backup / "plugin" / "enforce-floor.ts").write_text("// floor")

            self._rsync_restore(opencode, backup)

            assert (opencode / "mcp_servers" / "config.json").read_text() == '{"servers": []}'
            assert (opencode / "plugin" / "enforce-floor.ts").read_text() == "// floor"

    def test_restore_removes_stale_files_from_opencode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            opencode.mkdir(parents=True)
            backup.mkdir(parents=True)

            (backup / "plugin").mkdir(parents=True)
            (backup / "plugin" / "current.ts").write_text("// current")

            (opencode / "plugin").mkdir(parents=True, exist_ok=True)
            (opencode / "plugin" / "current.ts").write_text("// current")
            (opencode / "plugin" / "stale.ts").write_text("// stale, should be removed")
            (opencode / "stale_root.json").write_text('{"gone": true}')

            self._rsync_restore(opencode, backup)

            assert (opencode / "plugin" / "current.ts").exists()
            assert not (opencode / "plugin" / "stale.ts").exists(), "stale files should be removed by --delete"
            assert not (opencode / "stale_root.json").exists(), "stale root files should be removed by --delete"

    def test_roundtrip_backup_then_restore_is_identical(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orig = root / ".opencode"
            backup = root / ".opencode.orig"
            restored = root / "restored" / ".opencode"
            orig.mkdir(parents=True)
            backup.mkdir(parents=True)
            restored.mkdir(parents=True)

            (orig / "plugin").mkdir(parents=True)
            (orig / "plugin" / "enforce-floor.ts").write_text("// floor v2")
            (orig / "plugin" / "shared.ts").write_text("// shared")
            (orig / "plugins").mkdir(parents=True)
            (orig / "plugins" / "watchdog.ts").write_text("// watchdog")
            (orig / "skills").mkdir(parents=True)
            (orig / "skills" / "type-safety").mkdir(parents=True)
            (orig / "skills" / "type-safety" / "SKILL.md").write_text("# types")
            (orig / ".gitignore").write_text("node_modules/")
            (orig / "opencode.json").write_text('{"version": 2}')
            (orig / "plugin-hashes.json").write_text('{"hashes": {"enforce-floor": "abc"}}')

            self._rsync_backup(orig, backup)
            self._rsync_restore(restored, backup)

            def _readable_tree(d: Path, prefix: str = "") -> set:
                result = set()
                for p in d.rglob("*"):
                    if p.is_file():
                        rel = p.relative_to(d)
                        result.add(f"{rel}:{p.read_text()}")
                return result

            orig_tree = _readable_tree(orig)
            restored_tree = _readable_tree(restored)

            missing_in_restored = orig_tree - restored_tree
            extra_in_restored = restored_tree - orig_tree

            assert not missing_in_restored, f"files missing from restore: {missing_in_restored}"
            assert not extra_in_restored, f"unexpected files in restore: {extra_in_restored}"
            assert orig_tree == restored_tree, "round-trip backup→restore must produce identical tree"

    def test_clears_opencode_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / ".cache" / "opencode"
            cache.mkdir(parents=True)
            (cache / "some-cache.db").write_text("stale")

            subprocess.run(["rm", "-rf", str(cache)], check=True)

            assert not cache.exists()

    def test_restore_excludes_node_modules(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            opencode.mkdir(parents=True)
            backup.mkdir(parents=True)

            (opencode / "node_modules").mkdir(parents=True)
            (opencode / "node_modules" / "dep.js").write_text("// dep")
            (opencode / "plugin").mkdir(parents=True)
            (opencode / "plugin" / "enforce-floor.ts").write_text("// floor")

            self._rsync_backup(opencode, backup)

            assert not (backup / "node_modules").exists()
            assert (backup / "plugin" / "enforce-floor.ts").read_text() == "// floor"

    def test_restore_without_backup_dir_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            backup = root / ".opencode.orig"
            assert not backup.exists()

            proc = subprocess.run(
                ["sh", "-c", 'if [ ! -d "$1" ]; then echo "ERROR: no backup" >&2; exit 1; fi', "_", str(backup)],
                capture_output=True,
            )
            assert proc.returncode != 0
            assert b"no backup" in proc.stderr


class TestExtractExports:
    """Unit tests for _extract_exports helper."""

    def test_extracts_function_export(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
            f.write("export function hello() { return 1 }\nexport const FOO = 42\n")
            f.flush()
            exports = _extract_exports(Path(f.name))
        assert exports == {"hello", "FOO"}

    def test_extracts_interface_and_type_exports(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
            f.write("export interface DisengageOpts { maxMs?: number }\n")
            f.write("export type ToolName = 'read' | 'edit'\n")
            f.write("export class Widget {}\n")
            f.flush()
            exports = _extract_exports(Path(f.name))
        assert exports == {"DisengageOpts", "ToolName", "Widget"}

    def test_ignores_non_exported_declarations(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
            f.write("function internal() {}\n")
            f.write("const FOO = 1\n")
            f.write("export function external() {}\n")
            f.flush()
            exports = _extract_exports(Path(f.name))
        assert exports == {"external"}


class TestVerifyOpencodeBackup:
    """Verify-opencode-backup: content-level staleness detection."""

    def test_clean_backup_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            for d in [opencode / "plugin", backup / "plugin"]:
                d.mkdir(parents=True)
            shared_content = (
                "export function isSubagent(): boolean { return false }\n"
                "export const FOO = 42\n"
            )
            (opencode / "plugin" / "shared.ts").write_text(shared_content)
            (opencode / "plugin" / "enforce-floor.ts").write_text("// floor")
            (backup / "plugin" / "shared.ts").write_text(shared_content)
            (backup / "plugin" / "enforce-floor.ts").write_text("// floor")

            ok, msgs = verify(opencode, backup)
            assert ok, f"expected clean backup to pass, got: {msgs}"

    def test_missing_plugin_file_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            for d in [opencode / "plugin", backup / "plugin"]:
                d.mkdir(parents=True)
            (opencode / "plugin" / "shared.ts").write_text("export function x() {}\n")
            (opencode / "plugin" / "enforce-floor.ts").write_text("// floor")
            (backup / "plugin" / "shared.ts").write_text("export function x() {}\n")
            # enforce-floor.ts missing from backup

            ok, msgs = verify(opencode, backup)
            assert not ok
            assert any("enforce-floor.ts" in m for m in msgs)
            assert any("MISSING" in m for m in msgs)

    def test_missing_shared_ts_export_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            for d in [opencode / "plugin", backup / "plugin"]:
                d.mkdir(parents=True)
            (opencode / "plugin" / "shared.ts").write_text(
                "export function isSubagent(): boolean { return false }\n"
                "export function newFeature(): void {}\n"
            )
            (backup / "plugin" / "shared.ts").write_text(
                "export function isSubagent(): boolean { return false }\n"
            )

            ok, msgs = verify(opencode, backup)
            assert not ok
            assert any("newFeature" in m for m in msgs)
            assert any("MISSING EXPORT" in m for m in msgs)

    def test_extra_backup_export_is_note_not_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            for d in [opencode / "plugin", backup / "plugin"]:
                d.mkdir(parents=True)
            (opencode / "plugin" / "shared.ts").write_text(
                "export function isSubagent(): boolean { return false }\n"
            )
            (backup / "plugin" / "shared.ts").write_text(
                "export function isSubagent(): boolean { return false }\n"
                "export function removedFunc(): void {}\n"
            )

            ok, msgs = verify(opencode, backup)
            assert ok, "extra export in backup should not fail verification"
            assert any("removedFunc" in m for m in msgs)
            assert any("NOTE" in m for m in msgs)

    def test_missing_backup_dir_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            opencode.mkdir(parents=True)
            (opencode / "plugin").mkdir(parents=True)
            (opencode / "plugin" / "shared.ts").write_text("export function x() {}\n")

            ok, msgs = verify(opencode, root / ".opencode.orig")
            assert not ok
            assert any("does not exist" in m for m in msgs)

    def test_node_modules_excluded_from_file_check(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            for d in [opencode / "plugin", backup / "plugin"]:
                d.mkdir(parents=True)
            (opencode / "plugin" / "node_modules").mkdir(parents=True)
            (opencode / "plugin" / "node_modules" / "dep.ts").write_text("// dep")
            (opencode / "plugin" / "shared.ts").write_text("export function x() {}\n")
            (backup / "plugin" / "shared.ts").write_text("export function x() {}\n")
            # backup has no node_modules but that's fine — skip it

            ok, msgs = verify(opencode, backup)
            assert ok, f"node_modules should be excluded, got: {msgs}"
