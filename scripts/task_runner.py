import subprocess
import sys
import signal
import os
import shlex


def main():
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
        print(chr(109) + chr(97) + chr(107) + chr(101) + chr(32) + chr(116) + chr(97) + chr(115) + chr(107) + chr(32) + chr(111) + chr(110) + chr(108) + chr(121) + chr(32) + chr(97) + chr(99) + chr(99) + chr(101) + chr(112) + chr(116) + chr(115) + chr(32) + chr(110) + chr(101) + chr(115) + chr(116) + chr(101) + chr(100) + chr(32) + chr(109) + chr(97) + chr(107) + chr(101) + chr(32) + chr(99) + chr(111) + chr(109) + chr(109) + chr(97) + chr(110) + chr(100) + chr(115), file=sys.stderr)
        sys.exit(2)
    wrapper_targets = {chr(116) + chr(97) + chr(115) + chr(107), chr(114) + chr(117) + chr(110) + chr(45) + chr(119) + chr(97) + chr(116) + chr(99) + chr(104) + chr(101) + chr(100)}
    if len(argv) > 1 and argv[1] in wrapper_targets:
        print(chr(109) + chr(97) + chr(107) + chr(101) + chr(32) + chr(116) + chr(97) + chr(115) + chr(107) + chr(32) + chr(109) + chr(97) + chr(121) + chr(32) + chr(110) + chr(111) + chr(116) + chr(32) + chr(105) + chr(110) + chr(118) + chr(111) + chr(107) + chr(101) + chr(32) + chr(119) + chr(114) + chr(97) + chr(112) + chr(112) + chr(101) + chr(114) + chr(32) + chr(116) + chr(97) + chr(114) + chr(103) + chr(101) + chr(116) + chr(115), file=sys.stderr)
        sys.exit(2)
    blocked = {59, 38, 124, 96, 36, 60, 62}
    if any(any(ord(ch) in blocked for ch in part) for part in argv):
        print(chr(109) + chr(97) + chr(107) + chr(101) + chr(32) + chr(116) + chr(97) + chr(115) + chr(107) + chr(32) + chr(114) + chr(101) + chr(106) + chr(101) + chr(99) + chr(116) + chr(115) + chr(32) + chr(115) + chr(104) + chr(101) + chr(108) + chr(108) + chr(32) + chr(109) + chr(101) + chr(116) + chr(97) + chr(99) + chr(104) + chr(97) + chr(114) + chr(97) + chr(99) + chr(116) + chr(101) + chr(114) + chr(115), file=sys.stderr)
        sys.exit(2)

    p = subprocess.Popen(argv, shell=False)
    try:
        p.wait(timeout)
    except subprocess.TimeoutExpired:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
        print(f"TASK TIMEOUT: killed after {timeout}s", file=sys.stderr)
        sys.exit(124)

    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
