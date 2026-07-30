"""
W.22 — Harden .opencode integrity checker.

Tests cover three audit gaps:
  (A) Node v26 --experimental-strip-types scan integrated into checker
      (previously only `node --check` syntax validation ran).
  (B) Backup verification walks `.opencode/lib/` and `.opencode/plugins/`,
      not just `.opencode/plugin/`.
  (C) Cross-reference: opencode.json `plugin:` entries must match the .ts
      files that actually exist under .opencode/plugin/ and .opencode/plugins/
      (bidirectional — missing-on-disk AND orphan-on-disk are both flagged).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.check_opencode_integrity import (  # noqa: E402
    check_node_v26_compat,
    check_plugin_manifest_xref,
)
from scripts.verify_opencode_backup import _list_ts_files, verify  # noqa: E402


def _write_valid_opencode_json(root: Path, plugins: list[str] | None = None) -> None:
    if plugins is None:
        plugins = ["./.opencode/plugin/enforce-make.ts"]
    (root / "opencode.json").write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "permission": {
                    "bash": {"*": "deny", "make *": "allow"},
                    "doom_loop": "deny",
                },
                "plugin": plugins,
            }
        )
    )


# ── (A) Node v26 strip-types scan ────────────────────────────────────────


class TestNodeV26CompatScan:
    def test_clean_plugin_dir_passes(self, tmp_path: Path):
        (tmp_path / ".opencode" / "plugin").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugin" / "ok.ts").write_text("try { foo() } catch { bar() }\nexport default {}\n")
        errors = check_node_v26_compat(tmp_path)
        assert errors == [], f"expected no errors, got: {errors}"

    def test_nested_try_in_catch_flagged(self, tmp_path: Path):
        (tmp_path / ".opencode" / "plugin").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugin" / "bad.ts").write_text("try { a() } catch { try { b() } catch { c() } }\n")
        errors = check_node_v26_compat(tmp_path)
        assert len(errors) == 1
        assert "bad.ts" in errors[0]
        assert "nested try" in errors[0]

    def test_typed_catch_variable_flagged(self, tmp_path: Path):
        (tmp_path / ".opencode" / "plugin").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugin" / "typed.ts").write_text(
            "try { a() } catch (e: TypeError) { return null }\n"
        )
        errors = check_node_v26_compat(tmp_path)
        assert len(errors) == 1
        assert "typed catch" in errors[0]

    def test_enum_and_namespace_flagged(self, tmp_path: Path):
        (tmp_path / ".opencode" / "plugins").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugins" / "w.ts").write_text(
            "enum Color { Red }\nnamespace NS { export const x = 1 }\n"
        )
        errors = check_node_v26_compat(tmp_path)
        assert len(errors) >= 2
        joined = " ".join(errors)
        assert "enum" in joined
        assert "namespace" in joined


# ── (C) opencode.json ↔ .ts file cross-reference ─────────────────────────


class TestPluginManifestXref:
    def test_all_entries_exist_passes(self, tmp_path: Path):
        (tmp_path / ".opencode" / "plugin").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugin" / "enforce-make.ts").write_text("export default {}\n")
        _write_valid_opencode_json(tmp_path)
        errors = check_plugin_manifest_xref(tmp_path)
        assert errors == [], f"expected no errors, got: {errors}"

    def test_missing_entry_on_disk_flagged(self, tmp_path: Path):
        (tmp_path / ".opencode" / "plugin").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugin" / "enforce-make.ts").write_text("export default {}\n")
        _write_valid_opencode_json(
            tmp_path,
            ["./.opencode/plugin/enforce-make.ts", "./.opencode/plugin/does-not-exist.ts"],
        )
        errors = check_plugin_manifest_xref(tmp_path)
        assert len(errors) == 1
        assert "does-not-exist.ts" in errors[0]
        assert "MISSING" in errors[0]

    def test_orphan_ts_file_not_in_manifest_flagged(self, tmp_path: Path):
        (tmp_path / ".opencode" / "plugin").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugin" / "enforce-make.ts").write_text("export default {}\n")
        (tmp_path / ".opencode" / "plugin" / "orphan.ts").write_text("export default {}\n")
        _write_valid_opencode_json(tmp_path)
        errors = check_plugin_manifest_xref(tmp_path)
        assert len(errors) == 1
        assert "orphan.ts" in errors[0]
        assert "ORPHAN" in errors[0]

    def test_shared_and_hot_reload_not_flagged_as_orphans(self, tmp_path: Path):
        (tmp_path / ".opencode" / "plugin").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugin" / "shared.ts").write_text("// lib\n")
        (tmp_path / ".opencode" / "plugin" / "hot_reload.ts").write_text("// lib\n")
        (tmp_path / ".opencode" / "plugin" / "enforce-make.ts").write_text("export default {}\n")
        _write_valid_opencode_json(tmp_path)
        errors = check_plugin_manifest_xref(tmp_path)
        assert errors == [], f"shared/hot_reload should be allowlisted: {errors}"

    def test_plugins_dir_also_checked(self, tmp_path: Path):
        (tmp_path / ".opencode" / "plugin").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugins").mkdir(parents=True)
        (tmp_path / ".opencode" / "plugin" / "enforce-make.ts").write_text("export default {}\n")
        (tmp_path / ".opencode" / "plugins" / "watchdog.ts").write_text("export default {}\n")
        _write_valid_opencode_json(
            tmp_path,
            ["./.opencode/plugin/enforce-make.ts", "./.opencode/plugins/watchdog.ts"],
        )
        errors = check_plugin_manifest_xref(tmp_path)
        assert errors == [], f"expected no errors, got: {errors}"


# ── (B) Backup verification walks lib/ and plugins/ ──────────────────────


class TestBackupCoversLibAndPlugins:
    def test_missing_lib_file_in_backup_flagged(self, tmp_path: Path):
        opencode = tmp_path / ".opencode"
        backup = tmp_path / ".opencode.orig"
        for d in (opencode / "lib", opencode / "plugin", backup / "plugin"):
            d.mkdir(parents=True)
        (opencode / "lib" / "shared.ts").write_text("export function x() {}\n")
        (opencode / "plugin" / "enforce-floor.ts").write_text("// f")
        (backup / "plugin" / "enforce-floor.ts").write_text("// f")
        # backup has no lib/shared.ts

        ok, msgs = verify(opencode, backup)
        assert not ok
        assert any("lib" in m and "shared.ts" in m for m in msgs), msgs

    def test_missing_plugins_file_in_backup_flagged(self, tmp_path: Path):
        opencode = tmp_path / ".opencode"
        backup = tmp_path / ".opencode.orig"
        for d in (opencode / "plugins", opencode / "plugin", backup / "plugin"):
            d.mkdir(parents=True)
        (opencode / "plugins" / "watchdog.ts").write_text("// w")
        (opencode / "plugin" / "shared.ts").write_text("export function x() {}\n")
        (backup / "plugin" / "shared.ts").write_text("export function x() {}\n")
        # backup has no plugins/watchdog.ts

        ok, msgs = verify(opencode, backup)
        assert not ok
        assert any("watchdog.ts" in m for m in msgs), msgs

    def test_list_ts_files_includes_lib_and_plugins(self, tmp_path: Path):
        root = tmp_path / "tree"
        for sub in ("plugin", "plugins", "lib"):
            (root / sub).mkdir(parents=True)
        (root / "plugin" / "a.ts").write_text("a")
        (root / "plugins" / "b.ts").write_text("b")
        (root / "lib" / "c.ts").write_text("c")
        files = _list_ts_files(root)
        rel = {Path(f).name for f in files}
        assert {"a.ts", "b.ts", "c.ts"} <= rel, files

    def test_backup_with_all_three_dirs_passes(self, tmp_path: Path):
        opencode = tmp_path / ".opencode"
        backup = tmp_path / ".opencode.orig"
        for sub in ("plugin", "plugins", "lib"):
            (opencode / sub).mkdir(parents=True)
            (backup / sub).mkdir(parents=True)
        shared = "export function x() {}\n"
        (opencode / "lib" / "shared.ts").write_text(shared)
        (backup / "lib" / "shared.ts").write_text(shared)
        (opencode / "plugin" / "enforce-floor.ts").write_text("// f")
        (backup / "plugin" / "enforce-floor.ts").write_text("// f")
        (opencode / "plugins" / "watchdog.ts").write_text("// w")
        (backup / "plugins" / "watchdog.ts").write_text("// w")

        ok, msgs = verify(opencode, backup)
        assert ok, f"expected clean 3-dir backup to pass, got: {msgs}"
