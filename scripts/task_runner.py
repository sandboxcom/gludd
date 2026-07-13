import subprocess
import sys
import signal
import os


def main():
    if len(sys.argv) < 3:
        print("Usage: task_runner.py <cmd-file> <timeout-sec>", file=sys.stderr)
        sys.exit(2)

    cmd_file = sys.argv[1]
    timeout = int(sys.argv[2])

    with open(cmd_file) as f:
        cmd = f.read().strip()
    os.remove(cmd_file)

    p = subprocess.Popen(cmd, shell=True)
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
