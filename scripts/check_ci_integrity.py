import hashlib
import os
import sys

CI_CRITICAL = [
    "scripts/require_ci_green.py",
    "scripts/ci_push_guard.py",
    "scripts/ci_check_cooldown.py",
    "scripts/check_duplicate_targets.py",
]

def main():
    failed = False
    for f in CI_CRITICAL:
        if not os.path.exists(f):
            print(f"MISSING: {f}")
            failed = True
            continue
        with open(f, "rb") as fh:
            data = fh.read()
        h = hashlib.sha256(data).hexdigest()
        print(f"{f} [{h}]")
    if failed:
        sys.exit(1)
    print("OK: all CI-critical scripts present")

if __name__ == "__main__":
    sys.exit(main())
