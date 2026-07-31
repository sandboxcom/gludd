#!/usr/bin/env python3
"""check_tag_signing.py — AC017: git-tag-signing.

Verifies all release tags are GPG-signed. Runs git verify-tag.
Blocks unsigned or invalidly-signed release tags.
"""

import os
import subprocess
import sys


def verify_tag(tag):
    result = subprocess.run(
        ["git", "verify-tag", tag],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stderr


def classify_result(tag, returncode, stderr):
    if returncode == 0:
        return {"status": "PASS", "message": f"AC017: PASS — tag {tag} is GPG-signed and verified", "exit_code": 0}

    stderr_lower = stderr.lower() if stderr else ""
    if "no signature" in stderr_lower or "unsigned" in stderr_lower:
        return {
            "status": "FAIL",
            "message": f"AC017: FAIL — tag {tag} is not GPG-signed",
            "hint": "AC017: Use: git tag -s -a <tag> -m '<message>'",
            "exit_code": 1,
        }

    if "cannot verify" in stderr_lower or "key expired" in stderr_lower or "gpg:" in stderr_lower:
        return {
            "status": "FAIL",
            "message": f"AC017: FAIL — tag {tag} signature cannot be verified: {stderr.strip()}",
            "exit_code": 1,
        }

    return {
        "status": "INCONCLUSIVE",
        "message": f"AC017: INCONCLUSIVE — cannot verify tag {tag}",
        "detail": stderr.strip() if stderr else "No stderr",
        "exit_code": 2,
    }


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC017: TAG required")
        sys.exit(2)

    returncode, stderr = verify_tag(tag)
    result = classify_result(tag, returncode, stderr)
    print(result["message"])
    if "hint" in result:
        print(result["hint"])
    if "detail" in result:
        print(result["detail"])
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
