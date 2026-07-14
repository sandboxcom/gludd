#!/usr/bin/env python3
"""Check brace/paren/bracket balance in a TS file."""
import sys

def check_balance(path):
    with open(path) as f:
        lines = f.readlines()

    depth = 0
    stack = []
    pairs = {"{": "}", "(": ")", "[": "]"}
    closing = {"}", ")", "]"}

    for lineno, line in enumerate(lines, 1):
        for col, ch in enumerate(line):
            if ch in pairs:
                stack.append((ch, lineno, col))
                depth += 1
            elif ch in closing:
                if not stack:
                    print(f"Extra closing '{ch}' at {path}:{lineno}:{col}")
                    return 1
                opener, oline, ocol = stack.pop()
                expected = pairs[opener]
                if ch != expected:
                    print(f"Mismatch: '{opener}' at {oline}:{ocol} closed by '{ch}' at {lineno}:{col}")
                    return 1
                depth -= 1

    if stack:
        for opener, lineno, col in stack:
            print(f"Unclosed '{opener}' at {path}:{lineno}:{col}")
        return 1

    print(f"BALANCED: {len(lines)} lines, {len(stack)} unclosed")
    return 0

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ".opencode/plugin/enforce-stop.ts"
    sys.exit(check_balance(path))
