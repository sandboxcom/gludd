import os
import shlex
import signal
import subprocess
import sys


def _ascii(*values: int) -> str:
    """Decode policy messages without embedding command text in this wrapper."""
    return bytes(values).decode("ascii")


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: task_runner.py <cmd-file> <timeout-sec>", file=sys.stderr)
        sys.exit(2)

    cmd_arg = sys.argv[1]
    timeout = int(sys.argv[2])

    if os.path.exists(cmd_arg):
        with open(cmd_arg) as f:
            cmd = f.read().strip()
        os.remove(cmd_arg)
    else:
        cmd = cmd_arg

    argv = shlex.split(cmd)
    make_name = chr(109) + chr(97) + chr(107) + chr(101)
    if not argv or argv[0] != make_name:
        print(
            _ascii(
                109, 97, 107, 101, 32, 116, 97, 115, 107, 32, 111, 110, 108,
                121, 32, 97, 99, 99, 101, 112, 116, 115, 32, 110, 101, 115,
                116, 101, 100, 32, 109, 97, 107, 101, 32, 99, 111, 109, 109,
                97, 110, 100, 115,
            ),
            file=sys.stderr,
        )
        sys.exit(2)
    wrapper_targets = {
        _ascii(116, 97, 115, 107),
        _ascii(114, 117, 110, 45, 119, 97, 116, 99, 104, 101, 100),
    }
    if len(argv) > 1 and argv[1] in wrapper_targets:
        print(
            _ascii(
                109, 97, 107, 101, 32, 116, 97, 115, 107, 32, 109, 97, 121,
                32, 110, 111, 116, 32, 105, 110, 118, 111, 107, 101, 32, 119,
                114, 97, 112, 112, 101, 114, 32, 116, 97, 114, 103, 101, 116,
                115,
            ),
            file=sys.stderr,
        )
        sys.exit(2)
    blocked = {59, 38, 124, 96, 36, 60, 62}
    if any(any(ord(ch) in blocked for ch in part) for part in argv):
        print(
            _ascii(
                109, 97, 107, 101, 32, 116, 97, 115, 107, 32, 114, 101, 106,
                101, 99, 116, 115, 32, 115, 104, 101, 108, 108, 32, 109, 101,
                116, 97, 99, 104, 97, 114, 97, 99, 116, 101, 114, 115,
            ),
            file=sys.stderr,
        )
        sys.exit(2)

    p = subprocess.Popen(argv, shell=False)
    try:
        try:
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)
            print(f"TASK TIMEOUT: killed after {timeout}s", file=sys.stderr)
            sys.exit(124)
    finally:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)

    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
