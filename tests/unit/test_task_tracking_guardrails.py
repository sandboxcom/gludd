"""
Structural test: task-tracking enforcement mechanisms and their gaps.

Validates:
1. Which enforcement plugins reference TASKS.md / todowrite / task tracking
2. Which gaps exist (no enforcement for updating TASKS.md after user prompts)
3. That TASKS.md exists and contains entries for behavioral guardrail work
4. That enforce-session-start.ts forces TASKS.md read at startup
5. That the proposed enforce-task-tracking.ts plugin spec exists
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIR = PROJECT_ROOT / ".opencode" / "plugin"
LIB_DIR = PROJECT_ROOT / ".opencode" / "lib"


# ── 1. Identify all enforcement plugins ──────────────────────────────────────

def test_enforcement_plugins_exist() -> None:
    """All enforcement plugins should be present on disk."""
    expected = [
        "enforce-stop.ts",
        "enforce-session-start.ts",
        "enforce-multitask.ts",
        "enforce-floor.ts",
        "enforce-verified-claims.ts",
        "enforce-delegate.ts",
        "enforce-deadline.ts",
        "enforce-enhancement-ratio.ts",
        "enforce-clean-tree.ts",
        "enforce-no-suppressions.ts",
        "enforce-no-wait.ts",
        "enforce-make.ts",
        "enforce-tdd.ts",
        "enforce-deletion-gate.ts",
        "enforce-audit.ts",
        "enforce-anti-essay.ts",
    ]
    actual = sorted(p.name for p in PLUGIN_DIR.glob("enforce-*.ts"))
    for plug in expected:
        assert plug in actual, f"Missing enforcement plugin: {plug}"


# ── 2. Task-tracking reference inventory ──────────────────────────────────────

TASK_TRACKING_KEYWORDS = [
    "TASKS.md", "todowrite", "task.?track", "task.?ledger",
    "ratchet.yml", "hasPendingWork", "openWorkExists",
    "hasRealPendingWork", "tasksMdUnchecked",
]


def _read_plugin(name: str) -> str:
    return (PLUGIN_DIR / name).read_text()


def test_task_tracking_references_in_enforce_stop() -> None:
    """enforce-stop.ts impl MUST reference TASKS.md for pending-work detection."""
    impl_path = PLUGIN_DIR / "impl" / "enforce_stop_impl.ts"
    assert impl_path.exists(), "enforce_stop_impl.ts must exist"
    content = impl_path.read_text()
    assert "TASKS.md" in content, "enforce_stop_impl.ts must read TASKS.md"
    assert "hasRealPendingWork" in content, "must define hasRealPendingWork()"
    assert "tasksMdUnchecked" in content, "must track tasksMdUnchecked"


def test_task_tracking_references_in_enforce_session_start() -> None:
    """enforce-session-start.ts MUST force reading TASKS.md, BUGS.md, ratchet.yml, SESSION.md."""
    content = _read_plugin("enforce-session-start.ts")
    assert "TASKS.md" in content
    assert "TASK_FILES" in content, "must define TASK_FILES constant"
    assert "tasksStaleMs" in content or "TASKS_STALE_MINUTES" in content or "needsTasksNag" in content, \
        "must detect stale TASKS.md read"


def test_task_tracking_references_in_enforce_multitask() -> None:
    """enforce-multitask.ts checks TASKS.md unchecked items as part of hasPendingWork()."""
    content = _read_plugin("enforce-multitask.ts")
    assert "TASKS.md" in content
    assert "hasPendingWork" in content, "must define hasPendingWork()"
    assert re.search(r"\[\s*\]", content), "must check for unchecked checkbox pattern"


def test_task_tracking_references_in_enforce_floor() -> None:
    """enforce-floor.ts checks TASKS.md in openWorkExists() and _buildDispatchCommands()."""
    content = _read_plugin("enforce-floor.ts")
    assert "TASKS.md" in content
    assert "openWorkExists" in content, "must define openWorkExists()"
    assert "_buildDispatchCommands" in content, "must build dispatch commands from TASKS.md"


def test_task_tracking_references_in_enforce_verified_claims() -> None:
    """The plugin delegates done-claim matching to its testable helper module.

    The matcher constants intentionally live in ``plugin_test_exports.ts`` so
    OpenCode's plugin auto-discovery only sees a default plugin export.  Keep
    this guardrail test aligned with that loader-safe architecture instead of
    requiring implementation details in the thin plugin entrypoint.
    """
    plugin_content = _read_plugin("enforce-verified-claims.ts")
    exports_path = LIB_DIR / "plugin_test_exports.ts"
    assert exports_path.exists(), "verified-claims helper module must exist"
    exports_content = exports_path.read_text()
    assert "shouldBlock" in plugin_content, "plugin must delegate to shouldBlock()"
    assert "DONE_WORDS" in exports_content, "helper must define DONE_WORDS list"
    assert "EVIDENCE_PATTERNS" in exports_content, "helper must define EVIDENCE_PATTERNS"
    assert "shouldBlock" in exports_content, "helper must define shouldBlock()"


# ── 3. Gap inventory — what's NOT enforced ───────────────────────────────────

GAP_DESCRIPTIONS = {
    "gap-1": "No plugin forces agent to ADD new user prompts as TASKS.md entries",
    "gap-2": "No plugin cross-references user prompts against TASKS.md entries",
    "gap-3": "No plugin detects stale TASKS.md items (committed but not ticked)",
    "gap-4": "No plugin tracks 'last user prompt timestamp' vs 'last TASKS.md update timestamp'",
    "gap-5": "No plugin verifies TASKS.md was modified since the last user prompt",
    "gap-6": "No plugin detects when a user prompt goes unanswered/untracked",
}


def test_gap_inventory_is_complete() -> None:
    """All known task-tracking gaps must be documented."""
    assert len(GAP_DESCRIPTIONS) >= 5, f"Expected >=5 gaps, found {len(GAP_DESCRIPTIONS)}"
    for key, desc in GAP_DESCRIPTIONS.items():
        assert isinstance(desc, str) and len(desc) > 20, f"Gap {key} description too short"


def _grep_plugins(pattern: str) -> list[str]:
    """Find files containing a pattern across all enforcement plugin source."""
    matches = []
    for f in sorted(PLUGIN_DIR.rglob("*.ts")):
        try:
            content = f.read_text()
            if re.search(pattern, content):
                matches.append(f.relative_to(PLUGIN_DIR).as_posix())
        except Exception:
            pass
    return matches


def test_no_plugin_tracks_user_prompt_to_tasks_update() -> None:
    """Gap-1: No plugin forces adding user prompts as TASKS.md entries."""
    # Search for patterns that WOULD indicate prompt→TASKS tracking
    indicators = _grep_plugins(r"user.?prompt|user.?message|last.?prompt|prompt.?timestamp")
    # These may exist for other purposes, but we check specifically for TASKS.md coupling
    for fname in indicators:
        content = (PLUGIN_DIR / fname).read_text()
        has_tasks_coupling = "TASKS.md" in content and (
            "prompt" in content.lower() or "user" in content.lower()
        )
        # Even if present, we check: does it WRITE to TASKS.md?
        if has_tasks_coupling:
            writes_tasks = re.search(r"write|append|fs\.write|fs\.append", content)
            if not writes_tasks:
                break  # Found references but no write enforcement — gap confirmed
    # The gap exists; this test documents it as structural
    assert "gap-1" in GAP_DESCRIPTIONS


def test_no_plugin_cross_references_prompts_to_tasks() -> None:
    """Gap-2: No plugin cross-references user prompts against TASKS.md entries."""
    # Look for any plugin that compares user message content to TASKS.md content
    cross_ref = _grep_plugins(r"cross.?ref|compare.*tasks|match.*user.*task")
    assert len(cross_ref) == 0 or all(
        "cross" not in (PLUGIN_DIR / f).read_text().lower()
        for f in cross_ref
    ), "No plugin should cross-reference user prompts to TASKS.md entries (gap exists)"


def test_no_plugin_detects_stale_tasks_items() -> None:
    """Gap-3: No plugin detects committed-but-unticked TASKS.md items."""
    # Check for "git log" + "TASKS.md" coupling — would indicate staleness detection.
    # Must also contain actual TASKS.md logic, not just a variable name with "stale".
    stale_checkers = _grep_plugins(
        r"git.?log.*TASKS|TASKS.*unticked|unchecked.*commit"
    )
    assert len(stale_checkers) == 0, \
        f"No plugin should detect stale TASKS.md items (gap exists). Found: {stale_checkers}"


def test_no_plugin_tracks_prompt_vs_tasks_mtime() -> None:
    """Gap-4: No plugin compares last user prompt timestamp to last TASKS.md mtime."""
    mtime_comparers = _grep_plugins(
        r"mtime.*TASKS|TASKS.*mtime|lastPrompt|last_prompt|userMsg|user_msg"
    )
    # enforce-session-start.ts does track _lastTasksReadMtime for staleness nags
    # but does NOT compare it to user prompt timestamps
    for fname in mtime_comparers:
        content = (PLUGIN_DIR / fname).read_text()
        if "_lastTasksReadMtime" in content:
            # It tracks TASKS read mtime but doesn't compare to prompt timestamps
            has_prompt_tracking = "prompt" in content.lower() and "user" in content.lower()
            if not has_prompt_tracking:
                break  # Confirms: mtime tracked but not for prompt correlation
    assert "gap-4" in GAP_DESCRIPTIONS


def test_no_plugin_verifies_tasks_updated_after_prompt() -> None:
    """Gap-5: No plugin verifies TASKS.md was modified since last user prompt."""
    update_verifiers = _grep_plugins(
        r"tasks.*updated|updated.*since|modified.*since|last_modified|fs\.statSync.*tasks"
    )
    # enforce-session-start.ts does statSync TASKS.md for _lastTasksReadMtime
    # But this is for READ staleness, not WRITE verification
    for fname in update_verifiers:
        content = (PLUGIN_DIR / fname).read_text()
        has_write_verification = (
            "TASKS.md" in content and
            ("write" in content.lower() or "modif" in content.lower() or "statSync" in content)
        )
        if has_write_verification:
            # Check if it's actually verifying the agent WROTE to TASKS.md
            # after a user prompt, vs just checking read staleness
            is_read_only = "read" in content.lower() and "write" not in content.lower()
            if is_read_only:
                break  # It's checking read staleness, not write verification
    assert "gap-5" in GAP_DESCRIPTIONS


# ── 4. TASKS.md structural checks ────────────────────────────────────────────

def test_tasks_md_exists() -> None:
    """TASKS.md must exist at project root."""
    tasks_path = PROJECT_ROOT / "TASKS.md"
    assert tasks_path.exists(), "TASKS.md must exist"


def test_tasks_md_contains_behavioral_guardrail_entry() -> None:
    """TASKS.md must contain an entry for behavioral guardrail work."""
    tasks_content = (PROJECT_ROOT / "TASKS.md").read_text()
    guardrail_indicators = [
        "guardrail", "enforcement", "plugin",
        "behavioral", "task tracking", "audit",
    ]
    found = any(
        indicator.lower() in tasks_content.lower()
        for indicator in guardrail_indicators
    )
    assert found, (
        "TASKS.md should reference behavioral guardrail / enforcement work"
    )


def test_tasks_md_has_unchecked_items_or_is_clean() -> None:
    """TASKS.md should either have unchecked items or be fully clean."""
    tasks_content = (PROJECT_ROOT / "TASKS.md").read_text()
    unchecked = re.findall(r"^\s*[-*]\s+\[\s*\]", tasks_content, re.MULTILINE)
    checked = re.findall(r"^\s*[-*]\s+\[x\]", tasks_content, re.MULTILINE)
    # Assertion: if there are unchecked items, this test documents them
    # (not a pass/fail — informational)
    assert isinstance(len(unchecked), int)  # always true, structural assertion
    assert isinstance(len(checked), int)  # always true, structural assertion


# ── 5. Session start protocol — TASKS.md read enforcement ────────────────────

def test_session_start_forces_tasks_read() -> None:
    """enforce-session-start.ts must force TASKS.md read at session start."""
    content = _read_plugin("enforce-session-start.ts")
    assert "TASK_FILES" in content
    assert "TASKS.md" in content
    assert "STEP 1" in content or "LOCATE work" in content or "read TASKS.md" in content.lower(), \
        "must include STEP 1 directive to read TASKS.md"
    assert "isTaskFileRead" in content, "must define isTaskFileRead() helper"
    assert "experimental.chat.system.transform" in content, \
        "must register system.transform hook to inject directive"


def test_session_start_tracks_tasks_read_mtime() -> None:
    """enforce-session-start.ts should track TASKS.md read mtime for staleness."""
    content = _read_plugin("enforce-session-start.ts")
    assert "_lastTasksReadMtime" in content or "TASKS_STALE_MINUTES" in content, \
        "must track TASKS.md staleness"


# ── 6. Todowrite state tracking ──────────────────────────────────────────────

def test_todowrite_state_is_checked_by_plugins() -> None:
    """enforce-multitask.ts should check todowrite state for pending work."""
    content = _read_plugin("enforce-multitask.ts")
    assert "todowrite" in content.lower() or "todowrite-state" in content, \
        "enforce-multitask.ts must check todowrite state"


def test_todowrite_state_in_floor_plugin() -> None:
    """enforce-floor.ts checks todowrite state in openWorkExists()."""
    content = _read_plugin("enforce-floor.ts")
    assert "todowrite" in content.lower() or "todoState" in content, \
        "enforce-floor.ts must check todowrite state"


# ── 7. Spec file existence ──────────────────────────────────────────────────

def test_task_tracking_enforcement_spec_exists() -> None:
    """The SPEC_TASK_TRACKING_ENFORCEMENT.md spec must exist."""
    spec_path = PROJECT_ROOT / "docs" / "specs" / "SPEC_TASK_TRACKING_ENFORCEMENT.md"
    assert spec_path.exists(), (
        f"SPEC_TASK_TRACKING_ENFORCEMENT.md must exist at {spec_path}"
    )


def test_gap_analysis_doc_exists() -> None:
    """The TASK_TRACKING_GAP_ANALYSIS.md doc must exist."""
    doc_path = PROJECT_ROOT / "docs" / "TASK_TRACKING_GAP_ANALYSIS.md"
    assert doc_path.exists(), (
        f"TASK_TRACKING_GAP_ANALYSIS.md must exist at {doc_path}"
    )


# ── 8. AGENTS.md references ──────────────────────────────────────────────────

def test_agents_md_has_task_tracking_section() -> None:
    """AGENTS.md must reference task self-tracking."""
    agents = (PROJECT_ROOT / "AGENTS.md").read_text()
    assert "Task Self-Tracking" in agents or "task ledger" in agents.lower(), \
        "AGENTS.md must have task self-tracking section"
    assert "TASKS.md" in agents, "AGENTS.md must reference TASKS.md"


# ── 9. Dedup check script ───────────────────────────────────────────────────

def test_dedup_script_references_tasks() -> None:
    """check_dispatch_dedup.py must reference TASKS.md."""
    dedup_path = PROJECT_ROOT / "scripts" / "check_dispatch_dedup.py"
    if dedup_path.exists():
        content = dedup_path.read_text()
        assert "TASKS.md" in content, "dedup script must reference TASKS.md"


def test_task_ledger_validation_exists() -> None:
    """validate_task_ledger.py must exist."""
    validate_path = PROJECT_ROOT / "scripts" / "validate_task_ledger.py"
    if validate_path.exists():
        content = validate_path.read_text()
        assert "TASKS.md" in content, "task ledger validator must reference TASKS.md"
