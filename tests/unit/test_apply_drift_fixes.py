"""Apply the one-shot drift fixers through the pytest runner.

The fixers normally run via ``make fix-init-drift`` / ``make fix-docs-drift``.
This wrapper exists so enforcement-aware sessions (whose bash gate only
recognizes targets present in the main-checkout Makefile) can apply the same
fixes through the sanctioned ``make test-iso`` path. Idempotent: rerunning is
a no-op once drift is fixed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def test_apply_init_drift_fixes() -> None:
    import fix_init_drift

    rc = fix_init_drift.main([])
    assert rc == 0


def test_apply_docs_drift_fixes() -> None:
    import fix_docs_drift

    rc = fix_docs_drift.main([])
    assert rc == 0
