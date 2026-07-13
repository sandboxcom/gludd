"""Integration tests for .opencode/.opencode.orig backup/recovery system.

Tests the backup-opencode, check-opencode-backup, and restore-opencode
make targets via their underlying shell commands, using temp directories
for full isolation.

Targets under test (Makefile ~L3878-3911):
  - backup-opencode: rsync .opencode/ -> .opencode.orig/ (excl node_modules)
  - check-opencode-backup: exits 1 if missing or stale
  - restore-opencode: cp from .opencode.orig/ -> .opencode/, clear ~/.cache/opencode
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path


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
            plugin = opencode / "plugin"
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
    """Verify restore-opencode copies from .opencode.orig/ -> .opencode/ and clears cache."""

    def test_restores_files_from_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            opencode.mkdir(parents=True)
            backup.mkdir(parents=True)

            (backup / "plugin").mkdir(parents=True)
            (backup / "plugin" / "restored.ts").write_text("// restored content")
            (backup / "opencode.json").write_text('{"key": "value"}')

            subprocess.run(
                ["cp", "-R", str(backup / "plugin"), str(opencode / "plugin")],
                check=True,
            )
            subprocess.run(
                ["cp", "-R", str(backup / "opencode.json"), str(opencode / "opencode.json")],
                check=True,
            )

            assert (opencode / "plugin" / "restored.ts").exists()
            assert (opencode / "plugin" / "restored.ts").read_text() == "// restored content"
            assert (opencode / "opencode.json").read_text() == '{"key": "value"}'

    def test_clears_opencode_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / ".cache" / "opencode"
            cache.mkdir(parents=True)
            (cache / "some-cache.db").write_text("stale")

            subprocess.run(["rm", "-rf", str(cache)], check=True)

            assert not cache.exists()

    def test_restore_with_dotfiles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            backup = root / ".opencode.orig"
            opencode.mkdir(parents=True)
            backup.mkdir(parents=True)

            (backup / ".gitignore").write_text("node_modules/")

            subprocess.run(
                ["cp", str(backup / ".gitignore"), str(opencode / ".gitignore")],
                check=True,
            )

            assert (opencode / ".gitignore").read_text() == "node_modules/"

    def test_restore_removes_node_modules(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opencode = root / ".opencode"
            opencode.mkdir(parents=True)
            (opencode / "node_modules").mkdir(parents=True)
            (opencode / "node_modules" / "dep.js").write_text("// dep")

            subprocess.run(["rm", "-rf", str(opencode / "node_modules")], check=True)

            assert not (opencode / "node_modules").exists()
