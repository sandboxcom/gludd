#!/usr/bin/env bash
# PreToolUse — force-delegate guardrail — 2026-06-21
#
# GOAL: prevent main-thread grind (doing Edit/Write/commit inline on the main
# thread with no subagents running) by denying targeted mutations when the live
# subagent count is below CLAUDE_AGENT_FLOOR and consecutive targeted calls
# exceed a grace threshold.
#
# DEFAULT OFF / OPT-IN: only enforces when GLUDD_FORCE_DELEGATE=1.
# If the env var is absent or 0, this hook exits immediately (exit 0, no output).
#
# ALLOWLISTED (never denied, regardless):
#   Agent, Workflow — always allowed; also RESET the consecutive-targeted counter.
#   Read, Glob, Grep, ToolSearch, Skill — read-only query tools.
#   TodoWrite, TodoRead, ExitPlanMode, AskUserQuestion, SendMessage, TaskStop, NotebookEdit
#   Bash with a read-only make target (git-status/log/diff, ci-*, lint, typecheck, etc.)
#   Write/Edit whose file_path is under ~/.claude/projects/.../memory/
#
# TARGETED (deny candidates):
#   Edit, Write (non-memory path)
#   Bash with a mutating make target (commit/push/add/ship/merge/gate/etc.)
#
# GATE LOGIC:
#   Count consecutive targeted main-thread calls since last Agent/Workflow dispatch.
#   DENY when: GLUDD_FORCE_DELEGATE=1 AND live < CLAUDE_AGENT_FLOOR AND consecutive > GRACE.
#
# BOUNDED ESCAPE (anti-wedge):
#   After GLUDD_FORCE_DELEGATE_MAXBLOCK consecutive denials → FAIL OPEN (allow),
#   so unavoidable coordination steps are never permanently blocked.
#
# FAIL-OPEN: any parse error / python failure → exit 0 silently.
# NEVER exit non-zero (that would be a hook error).
#
# ENV VARS:
#   GLUDD_FORCE_DELEGATE          — set to "1" to enable (default: off)
#   GLUDD_FORCE_DELEGATE_GRACE    — consecutive targeted calls before first denial (default: 3)
#   GLUDD_FORCE_DELEGATE_MAXBLOCK — consecutive denials before fail-open (default: 4)
#   GLUDD_FORCE_DELEGATE_STATE    — state file path override (default: /tmp/gludd-force-delegate.json)
#   CLAUDE_AGENT_FLOOR            — live-subagent floor (shared with agent_floor hooks, default: 6)
#   FLOOR_LIVE_OVERRIDE           — test seam: forces live count to a fixed integer (see agent_liveness.py)

STATE_FILE="${GLUDD_FORCE_DELEGATE_STATE:-/tmp/gludd-force-delegate.json}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GRACE="${GLUDD_FORCE_DELEGATE_GRACE:-3}"
MAXBLOCK="${GLUDD_FORCE_DELEGATE_MAXBLOCK:-4}"
FLOOR="${CLAUDE_AGENT_FLOOR:-6}"

# Read stdin once into a variable; fail open if unavailable.
# We must capture here so the Python heredoc doesn't try to re-read an
# already-consumed stdin (heredoc + stdin-pipe compete for the same fd).
input="$(cat 2>/dev/null || echo '{}')"

# OPT-IN GATE — default off; exit immediately when not enforcing.
if [ "${GLUDD_FORCE_DELEGATE:-0}" != "1" ]; then
  exit 0
fi

# Pass captured stdin to Python via env var to avoid the heredoc/stdin conflict.
# All classification and gate logic in a single python3 call to avoid
# partial-state risk and keep the bash layer thin.
HOOK_INPUT="$input" python3 - "$STATE_FILE" "$REPO_ROOT" "$GRACE" "$MAXBLOCK" "$FLOOR" \
          "${FLOOR_LIVE_OVERRIDE:-}" <<'PYEOF' 2>/dev/null
import sys, json, os, re

state_file = sys.argv[1]
repo_root  = sys.argv[2]
grace      = int(sys.argv[3])
maxblock   = int(sys.argv[4])
floor      = int(sys.argv[5])
live_override = sys.argv[6]  # empty string if not set

try:
    payload = json.loads(os.environ.get("HOOK_INPUT", "{}"))
except Exception:
    sys.exit(0)  # fail open on parse error

tool_name  = (payload.get("tool_name") or "").strip()
tool_input = payload.get("tool_input") or {}
command    = (tool_input.get("command") or "").strip()
file_path  = (tool_input.get("file_path") or "").strip()

# ---------------------------------------------------------------------------
# CLASSIFICATION
# ---------------------------------------------------------------------------

# Read-only make target patterns (Bash tool)
READONLY_MAKE_RE = re.compile(
    r'^make\s+(git-(status|log|diff|staged|branch$|branch-files|ls-tracked|'
    r'revlist-count|is-ancestor|show$|show-diff|show-files|log-vs|files-vs|'
    r'remote-sandboxcom|fetch-sandboxcom|ls-remote-sandboxcom|remotes|tracked-keys|'
    r'ls-tracked|history-file)|'
    r'ci-(status|verdict|poll|greenness|head-compare|jobs-anon|status-anon|'
    r'run-detail|artifacts|log$|remotes|joblog-anon|checkrun-anno|annotations-anon|'
    r'wait-anon|watch$|watch-head|pyver-list|auth|ssh-test|faillog)|'
    r'disk$|disk-guard|floor-status|floor-plan|lint$|lint-all|typecheck$|typecheck-all|'
    r'test-count|test-unit|test-iso|test-xdist|collect-check|healthcheck|'
    r'ps-gludd|ps-pytest|gate-status|help$|branches-unmerged|'
    r'repo-status|repo-diff|repo-staged|repo-log|git-log|git-status|git-diff|git-staged|'
    r'git-branch$|git-revlist-count|git-branch-files|git-ls-tracked|git-log-vs|'
    r'git-files-vs|git-show$|git-show-diff|git-show-files|git-is-ancestor|'
    r'git-ls-remote-sandboxcom|git-tracked-keys|git-history-file|'
    r'verify-remote|ci-head-compare|ci-greenness|floor-plan|floor-status|'
    r'audit-messages|scan-secrets$|sast|sbom|pip-audit|security$|'
    r'test-no-wait-hook|test-model-ratio-hook|test-force-delegate-hook|'
    r'test-hooks|test-stop-hooks|test-guardrails|test-scripts|test-db|'
    r'test-live-zai|test-tui-daemon|test-liveness-workflow|'
    r'status-snapshot|deps-audit|plan|'
    r'collection-roles|collection-modules|molecule-scenarios|molecule-version|'
    r'release-view|verify-release-artifact|ci-diff-since-remote|git-divergence)'
)

# Mutating make target patterns (Bash tool)
MUTATING_MAKE_RE = re.compile(
    r'^make\s+(git-(commit|add$|add-all|merge$|push|tag|revert|rm$|reset$|cherry|'
    r'stash|cherry-pick|cherry-continue|cherry-abort|ff-only|merge-nc)|'
    r'commit$|commit-no-verify|commit-bootstrap|ship$|ship-ff|ship-async|'
    r'release-cut|feature-done|feature-start|'
    r'wt-sync|wt-apply|wt-import|wt-prune|wt-sync-all|'
    r'gate$|gate-async|test-and-commit|bootstrap$|install-hooks$|'
    r'clean$|clean-tmp|clean-hooks|clean-untracked|clean-worktree-venvs|'
    r'untrack$|git-push-sandboxcom|git-push-branch$|git-push-branch-nv|'
    r'git-pull-sandboxcom|git-stash-rebase-pop|git-add|'
    r'gated-merge|write-gate-safe-hook)'
)

def is_memory_path(path: str) -> bool:
    """Return True if path is under a .claude/projects/.../memory/ dir."""
    if not path:
        return False
    expanded = os.path.expanduser("~/.claude/projects/")
    norm = os.path.normpath(path)
    # Check two forms: absolute expanded path, or the ~ form
    if norm.startswith(os.path.normpath(expanded)) and "/memory/" in norm:
        return True
    # Also catch if path literally contains /.claude/projects/ and /memory/
    if "/.claude/projects/" in path and "/memory/" in path:
        return True
    return False

# Classify the tool call
AGENT_WORKFLOW = tool_name in ("Agent", "Workflow")
ALLOWLISTED = (
    tool_name in ("Read", "Glob", "Grep", "ToolSearch", "Skill",
                  "TodoWrite", "TodoRead", "ExitPlanMode", "AskUserQuestion",
                  "SendMessage", "TaskStop", "NotebookEdit", "WebFetch", "WebSearch")
    or (tool_name == "Bash" and bool(READONLY_MAKE_RE.match(command)))
    or (tool_name in ("Write", "Edit") and is_memory_path(file_path))
)
TARGETED = (
    (tool_name in ("Edit", "Write") and not is_memory_path(file_path))
    or (tool_name == "Bash" and bool(MUTATING_MAKE_RE.match(command)))
)

# ---------------------------------------------------------------------------
# STATE LOAD
# ---------------------------------------------------------------------------
def load_state():
    try:
        with open(state_file) as fh:
            s = json.load(fh)
        if not isinstance(s, dict):
            return {"consecutive_targeted": 0, "consecutive_denied": 0}
        return {
            "consecutive_targeted": int(s.get("consecutive_targeted", 0)),
            "consecutive_denied": int(s.get("consecutive_denied", 0)),
        }
    except Exception:
        return {"consecutive_targeted": 0, "consecutive_denied": 0}

def save_state(s):
    try:
        tmp = state_file + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(s, fh)
        os.replace(tmp, state_file)
    except Exception:
        pass  # fail open

def deny(consecutive_targeted, consecutive_denied, live):
    reason = (
        f"FORCE-DELEGATE: {consecutive_targeted} consecutive main-thread mutations "
        f"with only {live} subagent(s) live (floor={floor}). "
        f"Dispatch an Agent or Workflow to do this work on a subagent thread instead "
        f"of grinding inline on the main thread. "
        f"(Denied {consecutive_denied}/{maxblock} max; will fail-open after "
        f"{maxblock} consecutive denials to avoid wedging.)"
    )
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}
    print(json.dumps(out))

# ---------------------------------------------------------------------------
# DISPATCH
# ---------------------------------------------------------------------------

if AGENT_WORKFLOW:
    # Reset counter: a delegation happened, grind streak is broken.
    save_state({"consecutive_targeted": 0, "consecutive_denied": 0})
    sys.exit(0)

if ALLOWLISTED:
    # No state change; just allow.
    sys.exit(0)

if TARGETED:
    state = load_state()
    consecutive_targeted = state["consecutive_targeted"] + 1

    # Determine live agent count
    live = 0
    if live_override and live_override.isdigit():
        live = int(live_override)
    else:
        try:
            import subprocess
            result = subprocess.run(
                ["python3", "scripts/agent_liveness.py", "--count"],
                capture_output=True, text=True, timeout=10,
                cwd=repo_root
            )
            val = result.stdout.strip()
            if val.isdigit():
                live = int(val)
            # else fail open (live=0, below floor, may deny — but if we can't
            # determine count we default to 0 which is conservative)
        except Exception:
            live = 0

    if consecutive_targeted > grace and live < floor:
        # Potential deny — check maxblock
        consecutive_denied = state["consecutive_denied"] + 1
        if consecutive_denied > maxblock:
            # FAIL OPEN — bounded escape
            save_state({"consecutive_targeted": 0, "consecutive_denied": 0})
            sys.exit(0)
        else:
            save_state({
                "consecutive_targeted": consecutive_targeted,
                "consecutive_denied": consecutive_denied,
            })
            deny(consecutive_targeted, consecutive_denied, live)
            sys.exit(0)
    else:
        # Allow — update targeted count, preserve denied count
        save_state({
            "consecutive_targeted": consecutive_targeted,
            "consecutive_denied": state["consecutive_denied"],
        })
        sys.exit(0)

# Unknown/unclassified tool — allow
sys.exit(0)
PYEOF

# Fail-open: if python3 returned non-zero (unusual), still exit 0.
exit 0
