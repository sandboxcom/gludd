#!/usr/bin/env python3
"""Enforce clean-tree push admission through the canonical worktree owner."""

from worktree_state_guard import main

raise SystemExit(main(["--assert-clean"]))
