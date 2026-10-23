"""Preserve the immutable triggering Git tag during draft-release recovery."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
CLEANUP_STEP = "Clean stale release (already_exists guard)"
GH_ADAPTER = r'''
import json
import os
import subprocess
import sys
from pathlib import Path

arguments = sys.argv[1:]
with Path(os.environ["GLUDD_DRAFT_GH_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(arguments) + "\n")
if arguments[:2] == ["release", "view"]:
    state = os.environ["GLUDD_DRAFT_RELEASE_STATE"]
    if state == "missing":
        raise SystemExit(1)
    print("true" if state == "draft" else "false")
elif arguments[:2] == ["release", "delete"]:
    status = int(os.environ["GLUDD_DRAFT_DELETE_STATUS"])
    if status:
        print("native draft deletion refused", file=sys.stderr)
        raise SystemExit(status)
    if "--cleanup-tag" in arguments:
        result = subprocess.run(
            [
                os.environ["GLUDD_DRAFT_GIT"], "-c", "core.hooksPath=/dev/null",
                "update-ref", "-d", "refs/tags/" + arguments[2],
            ],
            env=os.environ.copy(),
            check=False,
            timeout=5,
        )
        raise SystemExit(result.returncode)
else:
    raise SystemExit("unexpected gh fixture command: " + repr(arguments))
'''


@dataclass
class _OwnedRelease:
    root: Path
    git: str
    bash: str
    environment: dict[str, str]
    gh_log: Path

    def command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(arguments),
            cwd=self.root,
            env=self.environment.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def git_command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.command(
            self.git,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *arguments,
        )

    def git_text(self, *arguments: str) -> str:
        result = self.git_command(*arguments)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def calls(self) -> list[list[str]]:
        return [
            json.loads(line)
            for line in self.gh_log.read_text(encoding="utf-8").splitlines()
        ]


@pytest.fixture
def owned_release(tmp_path: Path) -> _OwnedRelease:
    git = shutil.which("git")
    bash = shutil.which("bash")
    assert git is not None and bash is not None
    root = tmp_path / "gludd-owned-release"
    root.mkdir()
    adapter_root = tmp_path / "gludd-harmless-gh"
    adapter_root.mkdir()
    adapter = adapter_root / "native_gh_adapter.py"
    adapter.write_text(GH_ADAPTER, encoding="utf-8")
    executable = adapter_root / "gh"
    executable.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(adapter))
        + ' "$@"\n',
        encoding="utf-8",
    )
    executable.chmod(0o700)
    environment = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_NAMESPACE",
    ):
        environment.pop(name, None)
    gh_log = tmp_path / "gludd-native-gh-calls.jsonl"
    environment.update(
        {
            "PATH": str(adapter_root) + os.pathsep + environment.get("PATH", ""),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Gludd release fixture",
            "GIT_AUTHOR_EMAIL": "release-fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Gludd release fixture",
            "GIT_COMMITTER_EMAIL": "release-fixture@example.invalid",
            "GLUDD_DRAFT_GH_LOG": str(gh_log),
            "GLUDD_DRAFT_GIT": git,
            "GLUDD_DRAFT_RELEASE_STATE": "draft",
            "GLUDD_DRAFT_DELETE_STATUS": "0",
        }
    )
    owned = _OwnedRelease(root, git, bash, environment, gh_log)
    owned.git_text("init", "--quiet", "--initial-branch=main", "--template=")
    owned.git_text("commit", "--quiet", "--allow-empty", "-m", "owned release candidate")
    return owned


def _workflow_cleanup() -> str:
    workflow = yaml.load(
        WORKFLOW.read_text(encoding="utf-8"),
        Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader),
    )
    matches = [
        step["run"]
        for step in workflow["jobs"]["release"]["steps"]
        if step.get("name") == CLEANUP_STEP
    ]
    assert len(matches) == 1 and isinstance(matches[0], str)
    return matches[0]


def _run_cleanup(owned: _OwnedRelease, tag: str) -> subprocess.CompletedProcess[str]:
    script = owned.root / "gludd-workflow-draft-cleanup.sh"
    script.write_text(
        _workflow_cleanup().replace("${{ github.ref_name }}", tag),
        encoding="utf-8",
    )
    # The workflow omits shell:, whose native Unix default is bash -e {0}.
    return owned.command(owned.bash, "-e", str(script))


def _annotated_tag(owned: _OwnedRelease, tag: str) -> tuple[str, str, str]:
    owned.git_text("tag", "--annotate", tag, "--message", "immutable reviewed source")
    return (
        owned.git_text("rev-parse", "refs/tags/" + tag),
        owned.git_text("rev-parse", "refs/tags/" + tag + "^{}"),
        owned.git_text("cat-file", "-p", "refs/tags/" + tag),
    )


@pytest.mark.parametrize("release_tag", ["v0.1.2", "v0.1.2-rc.1"])
def test_draft_cleanup_preserves_exact_triggering_tag_object(
    owned_release: _OwnedRelease, release_tag: str
) -> None:
    original = _annotated_tag(owned_release, release_tag)

    result = _run_cleanup(owned_release, release_tag)

    assert result.returncode == 0, result.stderr
    assert owned_release.git_text("rev-parse", "refs/tags/" + release_tag) == original[0]
    assert owned_release.git_text("rev-parse", "refs/tags/" + release_tag + "^{}") == original[1]
    assert owned_release.git_text("cat-file", "-p", "refs/tags/" + release_tag) == original[2]
    assert owned_release.calls() == [
        ["release", "view", release_tag, "--json", "isDraft", "-q", ".isDraft"],
        ["release", "delete", release_tag, "--yes"],
    ]


@pytest.mark.parametrize("release_state", ["published", "missing"])
def test_cleanup_preserves_non_draft_release_and_triggering_tag(
    owned_release: _OwnedRelease, release_state: str
) -> None:
    tag = "v0.1.2"
    original = _annotated_tag(owned_release, tag)
    owned_release.environment["GLUDD_DRAFT_RELEASE_STATE"] = release_state

    result = _run_cleanup(owned_release, tag)

    assert result.returncode == 0, result.stderr
    assert owned_release.git_text("rev-parse", "refs/tags/" + tag) == original[0]
    assert owned_release.calls() == [
        ["release", "view", tag, "--json", "isDraft", "-q", ".isDraft"]
    ]


def test_cleanup_retains_native_draft_delete_failure(owned_release: _OwnedRelease) -> None:
    tag = "v0.1.2"
    original = _annotated_tag(owned_release, tag)
    owned_release.environment["GLUDD_DRAFT_DELETE_STATUS"] = "17"

    result = _run_cleanup(owned_release, tag)

    assert result.returncode == 17
    assert "native draft deletion refused" in result.stderr
    assert owned_release.git_text("rev-parse", "refs/tags/" + tag) == original[0]


def test_native_cleanup_tag_witness_deletes_only_owned_tag(
    owned_release: _OwnedRelease,
) -> None:
    tag = "v0.1.2"
    _annotated_tag(owned_release, tag)
    other_tag = "v0.1.1"
    previous = _annotated_tag(owned_release, other_tag)

    result = owned_release.command("gh", "release", "delete", tag, "--yes", "--cleanup-tag")

    assert result.returncode == 0, result.stderr
    assert owned_release.git_command("show-ref", "--verify", "refs/tags/" + tag).returncode != 0
    assert owned_release.git_text("rev-parse", "refs/tags/" + other_tag) == previous[0]
    assert owned_release.calls() == [
        ["release", "delete", tag, "--yes", "--cleanup-tag"]
    ]
