#!/usr/bin/env python3
"""Refuse the retired bulk integration path without inspecting or mutating Git.

Use the existing selected-source integration and native retirement owners.
A cleanup plan is not cleanup, and a passing plan supplies no release proof.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Retain a discoverable compatibility entrypoint with an explicit refusal."""
    print(
        "RETIRED: worktree-merge-all cannot safely select, integrate or retire parallel worktrees.",
        file=sys.stderr,
    )
    print(
        "AGENT_NEXT target=worktree-merge-all outcome=retired native_status=2 "
        "gate_evidence=false release_ready=false; preserve source, index, refs, locks and checkpoints; "
        "inspect make worktree-state and make active-work-status; independently review one selected "
        "SOURCE and use make development-merge-forward SOURCE=<reviewed-ref> MODE=content APPLY=0 "
        "from clean development, then follow its guarded apply and proof requirements; "
        "retirement uses make agent-cleanup BRANCH=<exact-branch> WORKTREE=<canonical-worktree> "
        "AGENT_CLEANUP_VALIDATE_ONLY=1 before explicit mode0 with valid ownership release; "
        "a zero plan has merged or cleaned nothing; never autocommit foreign work or force cleanup",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
