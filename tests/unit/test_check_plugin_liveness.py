"""Tests for plugin-liveness structural checks (impl/ split awareness)."""

from __future__ import annotations

from pathlib import Path

import scripts.check_plugin_liveness as liveness


def _write_layout(root: Path) -> None:
    plugin_dir = root / ".opencode" / "plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "enforce-stop.ts").write_text(
        'import type { Plugin } from "@opencode-ai/plugin";\n'
        "export default function enforceStop(): Plugin { return {}; }\n",
        encoding="utf-8",
    )
    impl_dir = plugin_dir / "impl"
    impl_dir.mkdir()
    (impl_dir / "enforce_stop_impl.ts").write_text(
        "const STOP_ENFORCE = true;\n"
        "function responseLooksTerminal() {}\n"
        "function tasksMdHasUnchecked() {}\n"
        "function ratchetHasEntries() {}\n"
        "function gateStatusIsRed() {}\n"
        "function repoHasPendingWork() {}\n"
        "function ciIsPendingOrRed() {}\n"
        "function computeHealthScore() {}\n"
        'registerHook("session.idle");\n'
        'registerHook("experimental.text.complete");\n'
        'registerHook("tool.execute.before");\n'
        "throw new Error('block');\n"
        "const COMPLETION_VERBATIM = /done/;\n"
        "const STOP_LIKE_TARGETS_RE = /ship/;\n"
        "const QUESTION_DENY_REASON = 'no';\n"
        "const HARD_STOP = 'HARD STOP';\n",
        encoding="utf-8",
    )


def test_read_plugin_src_includes_impl_files(monkeypatch, tmp_path: Path) -> None:
    _write_layout(tmp_path)
    monkeypatch.setattr(liveness, "PLUGIN_DIR", tmp_path / ".opencode" / "plugin")
    monkeypatch.setattr(liveness, "PLUGIN_FILE", tmp_path / ".opencode" / "plugin" / "enforce-stop.ts")
    src = liveness._read_plugin_src()
    assert "const STOP_ENFORCE = true;" in src
    assert 'registerHook("session.idle");' in src
    assert "function gateStatusIsRed() {}" in src


def test_structural_checks_pass_when_impl_is_present(monkeypatch, tmp_path: Path) -> None:
    _write_layout(tmp_path)
    monkeypatch.setattr(liveness, "PLUGIN_DIR", tmp_path / ".opencode" / "plugin")
    monkeypatch.setattr(liveness, "PLUGIN_FILE", tmp_path / ".opencode" / "plugin" / "enforce-stop.ts")
    src = liveness._read_plugin_src()
    checks = liveness._run_structural_checks(src)
    statuses = {c["name"]: c["status"] for c in checks}
    for name in (
        "session.idle",
        "experimental.text.complete",
        "tool.execute.before",
        "responseLooksTerminal",
        "tasksMdHasUnchecked",
        "ratchetHasEntries",
        "gateStatusIsRed",
        "repoHasPendingWork",
        "ciIsPendingOrRed",
        "computeHealthScore",
        "STOP_ENFORCE",
        "COMPLETION_VERBATIM",
        "STOP_LIKE_TARGETS_RE",
        "QUESTION_DENY_REASON",
        "HARD STOP",
    ):
        assert statuses.get(name) == "OK", f"{name} status={statuses.get(name)}"
