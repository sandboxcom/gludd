import re
import time
from pathlib import Path

_TERMINAL_PASSED = "=== GATE: PASSED ==="
_TERMINAL_FAILED = "=== GATE: FAILED ==="
_DEFAULT_MAX_AGE_SECONDS = 30 * 60


def _read_gate(gate_path: Path) -> str | None:
    if not gate_path.exists():
        return None
    try:
        return gate_path.read_text(encoding="utf-8")
    except OSError:
        return None


def is_gate_complete(gate_path: Path) -> bool:
    content = _read_gate(gate_path)
    if content is None:
        return False
    return _TERMINAL_PASSED in content or _TERMINAL_FAILED in content


def is_gate_passed(gate_path: Path) -> bool:
    content = _read_gate(gate_path)
    if content is None:
        return False
    terminal_markers = [
        line
        for line in content.splitlines()
        if line in {_TERMINAL_PASSED, _TERMINAL_FAILED}
    ]
    if not terminal_markers or terminal_markers[-1] != _TERMINAL_PASSED:
        return False
    return not any(
        re.search(r"\bFAIL\b", line)
        for line in content.splitlines()
        if line not in {_TERMINAL_PASSED, _TERMINAL_FAILED}
    )


def is_gate_fresh_and_passed(
    gate_path: Path,
    *,
    max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS,
) -> bool:
    """Return true only for a recent gate whose final verdict is green."""
    if max_age_seconds <= 0 or not is_gate_passed(gate_path):
        return False
    try:
        age_seconds = time.time() - gate_path.stat().st_mtime
    except OSError:
        return False
    return 0 <= age_seconds <= max_age_seconds


if __name__ == "__main__":
    import sys

    gate_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".gate-status")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "is-complete":
        sys.exit(0 if is_gate_complete(gate_path) else 1)
    elif cmd == "is-passed":
        sys.exit(0 if is_gate_passed(gate_path) else 1)
    elif cmd == "check":
        max_age = int(sys.argv[3]) if len(sys.argv) > 3 else _DEFAULT_MAX_AGE_SECONDS
        if is_gate_fresh_and_passed(gate_path, max_age_seconds=max_age):
            print(f"Gate is fresh and green: {gate_path}")
            sys.exit(0)
        print(
            f"Gate is not fresh and green: {gate_path}. "
            "Run `make gate` and fix every failure.",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        sys.exit(
            f"Usage: {sys.argv[0]} "
            "{is-complete|is-passed|check} [path] [max-age-seconds]"
        )
