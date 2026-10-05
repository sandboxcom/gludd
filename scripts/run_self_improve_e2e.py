#!/usr/bin/env python3
"""Compatibility CLI for the installed managed self-improvement runtime.

The package runtime owns ``--local-model-path``, ``--baseline-ref``, and
``--reference-ref`` processing. Repository operations remain Make-mediated via
``agent-worktree-base`` and ``git-patch-equivalence``.
"""

from __future__ import annotations

import sys

from general_ludd.self_improve import runtime as _runtime

if __name__ == "__main__":  # pragma: no cover - exercised through process tests
    raise SystemExit(_runtime.main())

# Preserve the historical import and monkeypatch surface without duplicating the
# package implementation. Importers receive the runtime module itself.
sys.modules[__name__] = _runtime
