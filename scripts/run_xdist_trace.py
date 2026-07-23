from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import pytest

DEFAULT_TRACE_LOG = "/tmp/gludd-xdist-progress.log"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pytest with durable xdist progress tracing.")
    parser.add_argument("--log", default=DEFAULT_TRACE_LOG)
    parser.add_argument("--basetemp", default="/tmp/gludd-xdist-trace")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def prepare_environment(log_path: str) -> None:
    root = str(repo_root())
    while root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)

    existing = os.environ.get("PYTHONPATH", "")
    parts = [part for part in existing.split(os.pathsep) if part and part != root]
    os.environ["PYTHONPATH"] = os.pathsep.join([root, *parts])
    os.environ["GLUDD_XDIST_TRACE_LOG"] = log_path
    os.environ["GLUDD_XDIST_TRACE_TRUNCATE"] = "1"


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(list(sys.argv[1:] if argv is None else argv))
    pytest_args = list(ns.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]

    prepare_environment(ns.log)
    basetemp = Path(ns.basetemp)
    shutil.rmtree(basetemp, ignore_errors=True)
    pytest_args.extend(["--basetemp", str(basetemp)])

    try:
        return int(pytest.main(pytest_args))
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
