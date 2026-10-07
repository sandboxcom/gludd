"""Git-mode contract for the OpenCode enforcement runtime surfaces."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _enforcement_runtime_paths() -> list[str]:
    """Return every tracked source surface that participates in enforcement."""
    config = json.loads((REPO_ROOT / "opencode.json").read_text(encoding="utf-8"))
    configured_plugins = {
        Path(path.removeprefix("./")).as_posix() for path in config["plugin"]
    }
    supporting_sources = {
        path.relative_to(REPO_ROOT).as_posix()
        for pattern in (
            ".opencode/lib/*.ts",
            ".opencode/plugin/impl/*.ts",
            ".opencode/plugin/*.test.node.mjs",
        )
        for path in REPO_ROOT.glob(pattern)
    }
    return sorted(configured_plugins | supporting_sources)


def _git_index_modes(paths: list[str]) -> dict[str, str]:
    """Read candidate-commit modes without trusting host filesystem metadata."""
    result = subprocess.run(
        ["git", "ls-files", "--stage", "-z", "--", *paths],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    modes: dict[str, str] = {}
    for raw_entry in result.stdout.split(b"\0"):
        if not raw_entry:
            continue
        metadata, raw_path = raw_entry.split(b"\t", maxsplit=1)
        mode, _object_id, stage = metadata.decode("ascii").split()
        assert stage == "0", f"unmerged enforcement source: {raw_path!r}"
        modes[raw_path.decode("utf-8")] = mode
    return modes


def test_enforcement_runtime_surfaces_have_executable_git_mode() -> None:
    """Every deployed guardrail source must retain Git mode 100755."""
    paths = _enforcement_runtime_paths()
    modes = _git_index_modes(paths)

    assert set(modes) == set(paths), "enforcement inventory contains untracked paths"
    wrong_modes = {path: modes[path] for path in paths if modes[path] != "100755"}
    assert not wrong_modes, f"non-executable enforcement sources: {wrong_modes}"
