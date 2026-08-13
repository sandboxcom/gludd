"""DC.3, DC.4, DC.5 — AGENTS.md sections + BUGS.md NSIS incident pin.

Verifies:
- DC.3 "Git Operations Are Not Grinding" section exists in AGENTS.md and
  references the GIT_SHIPPING_TARGETS allowlist (BP.1) that resets the
  streak counter for terminal git operations.
- DC.4 "Plugin Hook Invocation Validation" section exists in AGENTS.md and
  names `make check-plugin-hook-invoke` as mandatory before plugin commits.
- DC.5 BUGS.md incident log entry for the NSIS BUILDDIR path resolution
  bug (commit d99624cc) exists at the top of the BUGS.md incident log.

See AGENTS.md "Git Operations Are Not Grinding (DC.3)" and "Plugin Hook
Invocation Validation (Anti-ReferenceError Gate) (DC.4)" sections, and
BUGS.md incident dated 2026-07-25.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS_MD = ROOT / "AGENTS.md"
BUGS_MD = ROOT / "BUGS.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"{path} not found"
    return path.read_text(encoding="utf-8")


def _extract_section(content: str, heading_token: str) -> str | None:
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if heading_token in line and line.lstrip().startswith("#"):
            body: list[str] = [line]
            heading_level = len(line) - len(line.lstrip("#"))
            for follow in lines[idx + 1 :]:
                stripped = follow.lstrip()
                if stripped.startswith("#"):
                    follow_level = len(follow) - len(follow.lstrip("#"))
                    if follow_level <= heading_level:
                        break
                body.append(follow)
            return "\n".join(body)
    return None


# ---------- DC.3 ----------


def test_dc3_section_heading_present() -> None:
    """DC.3 'Git Operations Are Not Grinding' section heading must exist."""
    content = _read(AGENTS_MD)
    assert re.search(
        r"##\s+CRITICAL:\s+Git Operations Are Not Grinding\s*\(DC\.3\)", content
    ), "AGENTS.md must contain '## CRITICAL: Git Operations Are Not Grinding (DC.3)'"


def test_dc3_references_git_shipping_allowlist() -> None:
    """DC.3 must reference the GIT_SHIPPING_TARGETS allowlist (BP.1)."""
    content = _read(AGENTS_MD)
    block = _extract_section(content, "Git Operations Are Not Grinding (DC.3)")
    assert block is not None, "DC.3 section not found"
    assert "GIT_SHIPPING_TARGETS" in block, (
        "DC.3 must reference GIT_SHIPPING_TARGETS allowlist from enforce-delegate.ts (BP.1)."
    )
    assert "BP.1" in block, "DC.3 must reference BP.1."


def test_dc3_names_terminal_targets() -> None:
    """DC.3 must enumerate the canonical terminal git shipping targets."""
    content = _read(AGENTS_MD)
    block = _extract_section(content, "Git Operations Are Not Grinding (DC.3)")
    assert block is not None
    for target in ("git-commit", "git-push-sandboxcom", "batch-push", "ship-commit"):
        assert target in block, f"DC.3 must name terminal target '{target}'."


def test_dc3_resets_streak_counter() -> None:
    """DC.3 must state that git operations reset the streak counter."""
    content = _read(AGENTS_MD)
    block = _extract_section(content, "Git Operations Are Not Grinding (DC.3)")
    assert block is not None
    assert "RESETS the streak counter" in block or "reset the streak" in block.lower(), (
        "DC.3 must explain that GIT_SHIPPING_TARGETS resets the streak counter."
    )


# ---------- DC.4 ----------


def test_dc4_section_heading_present() -> None:
    """DC.4 'Plugin Hook Invocation Validation' section heading must exist."""
    content = _read(AGENTS_MD)
    assert re.search(
        r"##\s+CRITICAL:\s+Plugin Hook Invocation Validation.*\(DC\.4\)", content
    ), (
        "AGENTS.md must contain '## CRITICAL: Plugin Hook Invocation Validation ... (DC.4)'"
    )


def test_dc4_names_check_plugin_hook_invoke() -> None:
    """DC.4 must name `make check-plugin-hook-invoke` as mandatory."""
    content = _read(AGENTS_MD)
    block = _extract_section(content, "Plugin Hook Invocation Validation")
    assert block is not None, "DC.4 section not found"
    assert "check-plugin-hook-invoke" in block, (
        "DC.4 must name `make check-plugin-hook-invoke` as mandatory before plugin commits."
    )


def test_dc4_mandates_before_commit() -> None:
    """DC.4 must mark the hook-invoke check as required before plugin commits."""
    content = _read(AGENTS_MD)
    block = _extract_section(content, "Plugin Hook Invocation Validation")
    assert block is not None
    lowered = block.lower()
    assert "commit" in lowered and ("must" in lowered or "mandatory" in lowered), (
        "DC.4 must state that the hook-invoke check is mandatory before commits."
    )


# ---------- DC.5 (BUGS.md) ----------


def test_dc5_bugs_md_nsis_entry_present() -> None:
    """DC.5: BUGS.md must contain the NSIS BUILDDIR incident dated 2026-07-25."""
    content = _read(BUGS_MD)
    assert "2026-07-25" in content, "BUGS.md missing 2026-07-25 NSIS incident entry."
    assert "NSIS" in content, "BUGS.md NSIS entry must mention NSIS."


def test_dc5_bugs_md_names_root_cause() -> None:
    """DC.5: NSIS entry must document the OutFile/script-relative path root cause."""
    content = _read(BUGS_MD)
    # Find the NSIS block
    idx = content.find("NSIS installer build failed")
    assert idx >= 0, "NSIS entry not found in BUGS.md"
    # Capture up to the next incident header (### 2026-)
    tail = content[idx:]
    next_incident = tail.find("\n### 2026-", 1)
    block = tail if next_incident < 0 else tail[:next_incident]
    assert "OutFile" in block, "BUGS.md NSIS entry must reference OutFile path resolution."
    assert "script" in block.lower(), (
        "BUGS.md NSIS entry must explain that NSIS resolves paths relative to the script file."
    )


def test_dc5_bugs_md_names_fix_commit() -> None:
    """DC.5: NSIS entry must name the fix commit (d99624cc)."""
    content = _read(BUGS_MD)
    idx = content.find("NSIS installer build failed")
    tail = content[idx:]
    next_incident = tail.find("\n### 2026-", 1)
    block = tail if next_incident < 0 else tail[:next_incident]
    assert "d99624cc" in block, (
        "BUGS.md NSIS entry must name fix commit d99624cc."
    )


def test_dc5_bugs_md_entry_remains_in_log() -> None:
    """DC.5: the NSIS entry remains in the chronological incident log."""
    content = _read(BUGS_MD)
    log_idx = content.find("## Incident Log")
    assert log_idx >= 0, "BUGS.md must contain an '## Incident Log' section."
    entry_idx = content.find("NSIS installer build failed", log_idx)
    assert entry_idx > log_idx, (
        "BUGS.md NSIS incident must remain in the Incident Log."
    )
    preceding_header = content.rfind("### 2026-07-25", log_idx, entry_idx)
    assert preceding_header > log_idx, "NSIS incident must retain its 2026-07-25 date."
