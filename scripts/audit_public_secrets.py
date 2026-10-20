#!/usr/bin/env python3
"""Apply six explicit public-finding reviews through the native baseline owner.

Review authority is a fixed filename, detector and digest, independent of
current source contents. New findings require a separate explicit review.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if TYPE_CHECKING:
    import manage_secrets_baseline as baseline_owner
else:
    from scripts import manage_secrets_baseline as baseline_owner

PUBLIC_FINDINGS: Final[tuple[baseline_owner.FindingIdentity, ...]] = (
    (
        "tests/unit/test_ci_run_summary_target.py",
        "Base64 High Entropy String",
        "fab08bfa9e87d50cec5bba75f20269b8ab6827c6",
    ),
    (
        "tests/unit/test_item_progress.py",
        "Base64 High Entropy String",
        "7e9d0072485183cb48eb9ab08acac44f7293227e",
    ),
    (
        "tests/unit/test_item_progress.py",
        "Base64 High Entropy String",
        "025e5b8af0f109ad6158df7675c43319861ae61c",
    ),
    (
        "tests/unit/test_pipeline_status_ssh_transport.py",
        "Base64 High Entropy String",
        "7e9d0072485183cb48eb9ab08acac44f7293227e",
    ),
    (
        "tests/unit/test_secrets_baseline_compaction.py",
        "Base64 High Entropy String",
        "7e9d0072485183cb48eb9ab08acac44f7293227e",
    ),
    (
        "TASKS.md",
        "Secret Keyword",
        "3d87c12867a9e3a7e5aeeeaf9d820091df6cec8c",
    ),
)


def main(argv: Sequence[str] | None = None) -> int:
    """Publish only the explicit reviewed identities, or refuse without writes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path(".secrets.baseline"))
    parser.add_argument(
        "--policy", type=Path,
        default=Path("config/detect_secrets_baseline_policy.json"),
    )
    args = parser.parse_args(argv)
    try:
        evidence = baseline_owner.review_findings(
            args.baseline, baseline_owner.load_policy(args.policy), PUBLIC_FINDINGS,
        )
    except (baseline_owner.BaselineError, OSError) as exc:
        print(f"audit-public-secrets: {exc}", file=sys.stderr)
        return 2
    print(
        baseline_owner.canonical_text({
            "action": "review-public",
            "reviewed_findings": len(PUBLIC_FINDINGS),
            **evidence.as_dict(),
        }),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
