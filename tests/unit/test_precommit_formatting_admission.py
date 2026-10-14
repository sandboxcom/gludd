"""Behavioral regression for exact frozen data and early formatter admission."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterator
from importlib import import_module
from pathlib import Path
from typing import cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / ".pre-commit-config.yaml"
SHIPPING_PATH = ROOT / "make/40-cross-version-and-worktrees.mk"
FIXTURE_PATH = "tests/fixtures/ansible_ee_searxng/limiter.toml.txt"


def _selected(hook_id: str, names: list[str]) -> list[str]:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    hook = next(
        hook
        for repo in config["repos"]
        for hook in repo["hooks"]
        if hook["id"] == hook_id
    )
    selector = cast(
        Callable[[list[str], str, str], Iterator[str]],
        import_module("pre_commit.commands.run").filter_by_include_exclude,
    )
    return list(selector(names, hook.get("files", ""), hook.get("exclude", "^$")))


@pytest.mark.parametrize("hook_id", ("trailing-whitespace", "end-of-file-fixer"))
def test_only_exact_frozen_fixture_is_excluded(hook_id: str) -> None:
    ordinary = [
        "src/general_ludd/example.py", "scripts/example.py", "tests/unit/test_example.py",
        "tests/fixtures/ansible_ee_searxng/other.txt", FIXTURE_PATH + ".py",
        "nested/" + FIXTURE_PATH, "docs/example.md", "config/example.json",
    ]
    assert _selected(hook_id, [FIXTURE_PATH, *ordinary]) == ordinary
    assert _selected(hook_id, ["docs/presentation/deck/vendor/reveal.js"]) == []
    if hook_id == "end-of-file-fixer":
        assert _selected(hook_id, ["config/self-improve/catalog-truth.json"]) == []



@pytest.mark.parametrize("failing_hook", (None, "trailing-whitespace", "end-of-file-fixer"))
@pytest.mark.parametrize("inherited_index", (None, "https://private.invalid/simple"))
def test_existing_guard_routes_formatter_failure_before_later_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_hook: str | None,
    inherited_index: str | None,
) -> None:
    if inherited_index is None:
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    else:
        monkeypatch.setenv("PIP_INDEX_URL", inherited_index)
    makefile = SHIPPING_PATH.read_text(encoding="utf-8")
    start = makefile.index("\n_pre-commit-secrets-guard:") + 1
    guard = makefile[start:makefile.index("\n\n", start)]
    trace = tmp_path / "trace.jsonl"
    stub = tmp_path / "native_tool_boundary.py"
    stub.write_text(
        "import json, os, pathlib, sys\n"
        f"trace = pathlib.Path({str(trace)!r})\n"
        "with trace.open('a') as stream:\n"
        "    stream.write(json.dumps(dict(argv=sys.argv[1:], index=os.environ.get('PIP_INDEX_URL'))) + '\\n')\n"
        f"raise SystemExit(1 if sys.argv[-1] == {failing_hook!r} else 0)\n",
        encoding="utf-8",
    )
    isolated = tmp_path / "Makefile"
    isolated.write_text(
        f"UV := {shlex.quote(sys.executable)} {shlex.quote(str(stub))}\n{guard}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(isolated), "_pre-commit-secrets-guard"],
        check=False, capture_output=True, text=True, timeout=10,
    )
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    hooks = ["trailing-whitespace", "end-of-file-fixer", "detect-secrets-readonly"]
    expected = hooks if failing_hook is None else hooks[:hooks.index(failing_hook) + 1]
    assert [call["argv"] for call in calls] == [["run", "pre-commit", "run", hook] for hook in expected]
    assert [call["index"] for call in calls] == ["https://pypi.org/simple"] * len(expected)
    assert (result.returncode == 0) is (failing_hook is None)
