from pathlib import Path

_TERMINAL_PASSED = "=== GATE: PASSED ==="
_TERMINAL_FAILED = "=== GATE: FAILED ==="


def is_gate_complete(gate_path: Path) -> bool:
    if not gate_path.exists():
        return False
    try:
        content = gate_path.read_text()
    except Exception:
        return False
    return _TERMINAL_PASSED in content or _TERMINAL_FAILED in content


def is_gate_passed(gate_path: Path) -> bool:
    return is_gate_complete(gate_path) and _TERMINAL_PASSED in gate_path.read_text()


if __name__ == "__main__":
    import sys

    gate_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".gate-status")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "is-complete":
        sys.exit(0 if is_gate_complete(gate_path) else 1)
    elif cmd == "is-passed":
        sys.exit(0 if is_gate_passed(gate_path) else 1)
    else:
        sys.exit(f"Usage: {sys.argv[0]} {{is-complete|is-passed}} [path]")
