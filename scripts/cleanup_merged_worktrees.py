#!/usr/bin/env python3
"""Retired compatibility entry for unsafe bulk worktree cleanup.

Use the existing selected native retirement owner after ownership reconciliation.
This entry never inventories, mutates, unregisters or starts another process.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Refuse legacy bulk cleanup and name the selected native planning owner."""
    print("Retired: worktree-cleanup-merged performs no cleanup.", file=sys.stderr)
    print(
        "AGENT_NEXT target=worktree-cleanup-merged outcome=refused native_status=2 "
        "gate_evidence=false release_ready=false next_owner=agent-cleanup; "
        "reconcile native exact BRANCH=, canonical WORKTREE=, full source SHA and "
        "released logical ownership. Preserve the configured registry; never pass "
        "an empty registry override or guess a registry file/path. Use the existing "
        "selected owner and its documented inputs with AGENT_CLEANUP_VALIDATE_ONLY=1. "
        "For genuinely missing registrations inspect the documented wt-prune-safe "
        "validate-only plan. Preserve branch refs, locks, "
        "checkpoints and gate proof; no force removal or implicit unregister.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
