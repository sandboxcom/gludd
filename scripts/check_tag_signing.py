#!/usr/bin/env python3
"""check_tag_signing.py — AC017: git-tag-signing.

Verifies all release tags are GPG-signed. Runs git verify-tag.
Blocks unsigned or invalidly-signed release tags.
"""

import os
import subprocess
import sys


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC017: TAG required")
        sys.exit(2)

    result = subprocess.run(
        ["git", "verify-tag", tag],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(f"AC017: PASS — tag {tag} is GPG-signed and verified")
        sys.exit(0)

    stderr = result.stderr.lower()
    if "no signature" in stderr or "unsigned" in stderr:
        print(f"AC017: FAIL — tag {tag} is not GPG-signed")
        print("AC017: Use: git tag -s -a <tag> -m '<message>'")
        sys.exit(1)

    if "cannot verify" in stderr or "key expired" in stderr or "gpg:" in stderr:
        print(f"AC017: FAIL — tag {tag} signature cannot be verified: {result.stderr.strip()}")
        sys.exit(1)

    print(f"AC017: INCONCLUSIVE — cannot verify tag {tag}")
    print(result.stderr.strip() if result.stderr else "No stderr")
    sys.exit(2)


if __name__ == "__main__":
    main()
