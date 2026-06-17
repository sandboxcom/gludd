"""Red-team: project-workspace materialization must fail closed.

CONFIRMED CRITICAL (unauthenticated, reachable via POST /admin/projects and the
DB-restore path on daemon start):

  * repo_url RCE/SSRF — ``git clone`` of an attacker-supplied URL. ``ext::sh -c``
    runs arbitrary commands at clone time; ``file://`` reads local paths; an
    http(s)/git/ssh host of 169.254.169.254 / loopback / RFC-1918 is SSRF.
  * workspace_path traversal — an absolute or ``..`` workspace_path lets a project
    plant directories anywhere on disk (e.g. /root/.ssh) before ensure_dirs().

These tests prove every dangerous input is refused and NEVER reaches git.clone,
and that escaping workspace_paths create NO directory outside base_dir, while a
benign https url + in-base workspace_path still works.
"""

from __future__ import annotations

import os

import pytest

from general_ludd.git_automation.repo import (
    GitAutomation,
    reject_unsafe_repo_url,
)
from general_ludd.projects.manager import materialize_project_workspace
from general_ludd.projects.workspace import confine_workspace_path


class _CloneRecorder:
    """Stand-in for GitAutomation.clone that records every call (and never shells)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, target_dir: str, timeout: float = 120.0):
        self.calls.append((url, target_dir))
        from general_ludd.git_automation.types import CloneResult

        return CloneResult(path=target_dir, url=url, success=True)


@pytest.fixture
def clone_recorder(monkeypatch):
    rec = _CloneRecorder()
    monkeypatch.setattr(GitAutomation, "clone", rec)
    return rec


# --- repo_url RCE / SSRF -----------------------------------------------------

DANGEROUS_URLS = [
    "ext::sh -c 'touch /tmp/pwned'",          # git smart-transport RCE
    "transport::address",                      # any '::' smart transport
    "file:///etc/passwd",                      # local file exfiltration
    "file://localhost/etc/passwd",
    "http://169.254.169.254/latest/meta-data/",  # cloud-metadata SSRF
    "https://169.254.169.254/computeMetadata/",
    "http://metadata.google.internal/",        # GCP metadata DNS name
    "http://127.0.0.1/repo.git",               # loopback SSRF
    "https://localhost/repo.git",
    "git://10.0.0.5/internal.git",             # RFC-1918 private
    "ssh://git@192.168.1.10/repo.git",         # RFC-1918 via ssh
    "https://[::1]/repo.git",                  # IPv6 loopback
    "--upload-pack=touch /tmp/x",              # option injection (leading dash)
    "fakescheme://example.com/repo.git",       # disallowed scheme
    "",                                         # empty
    "   ",
]


@pytest.mark.parametrize("bad_url", DANGEROUS_URLS)
def test_dangerous_repo_url_never_reaches_clone(bad_url, clone_recorder, tmp_path):
    result = materialize_project_workspace(
        repo_url=bad_url,
        workspace_path="proj-x",
        base_dir=str(tmp_path / "base"),
    )
    assert result is None, f"expected refusal for {bad_url!r}"
    assert clone_recorder.calls == [], (
        f"git.clone was called for dangerous url {bad_url!r}: {clone_recorder.calls}"
    )


@pytest.mark.parametrize(
    "bad_url",
    [
        "ext::sh -c id",
        "transport::addr",
        "file:///etc/passwd",
        "http://169.254.169.254/",
        "https://127.0.0.1/x.git",
        "git://10.1.2.3/x.git",
        "--upload-pack=evil",
        "ftp://example.com/x.git",
        "",
    ],
)
def test_reject_unsafe_repo_url_raises(bad_url):
    with pytest.raises(ValueError):
        reject_unsafe_repo_url(bad_url)


def test_benign_public_urls_pass_url_guard():
    # Real public hosts: the guard must NOT reject ordinary remotes.
    for good in (
        "https://github.com/org/repo.git",
        "git@github.com:org/repo.git",
        "ssh://git@github.com/org/repo.git",
        "git://github.com/org/repo.git",
    ):
        assert reject_unsafe_repo_url(good) == good.strip()


# --- workspace_path traversal ------------------------------------------------

ESCAPING_PATHS = [
    "../escape",
    "../../etc",
    "../../../root/.ssh",
    "/root/.ssh",
    "/etc/cron.d",
    "/tmp/gludd-evil",
    "a/../../escape",
    "sub/../../../outside",
]


@pytest.mark.parametrize("bad_path", ESCAPING_PATHS)
def test_escaping_workspace_path_refused_and_creates_no_dirs(bad_path, clone_recorder, tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    # A real, resolvable sibling target that the traversal might try to create.
    sentinel_parent = tmp_path  # parent of base_dir; nothing here should appear

    before = set(os.listdir(tmp_path))
    result = materialize_project_workspace(
        repo_url="https://github.com/org/repo.git",
        workspace_path=bad_path,
        base_dir=str(base_dir),
    )
    after = set(os.listdir(sentinel_parent))

    assert result is None, f"expected refusal for workspace_path {bad_path!r}"
    assert clone_recorder.calls == [], "clone must not run for an escaping workspace_path"
    # No new directory was created outside base_dir.
    assert after == before, f"escaping path created dirs outside base_dir: {after - before}"
    # base_dir itself gained no escaping subtree.
    assert os.listdir(base_dir) == [], "no dirs should be created inside base_dir on refusal"


@pytest.mark.parametrize("bad_path", ESCAPING_PATHS)
def test_confine_workspace_path_raises(bad_path, tmp_path):
    with pytest.raises(ValueError):
        confine_workspace_path(str(tmp_path), bad_path)


def test_confine_workspace_path_accepts_in_base_name(tmp_path):
    confined = confine_workspace_path(str(tmp_path), "proj-abc")
    assert confined.startswith(str(tmp_path.resolve()))
    assert confined.endswith("proj-abc")


# --- benign happy path -------------------------------------------------------

def test_benign_url_and_inbase_path_materializes(clone_recorder, tmp_path):
    base_dir = tmp_path / "base"
    repo_dir = materialize_project_workspace(
        repo_url="https://github.com/org/repo.git",
        workspace_path="proj-good",
        base_dir=str(base_dir),
    )
    assert repo_dir is not None
    # The (recorded) clone happened exactly once with the safe url, into a dir
    # confined under base_dir.
    assert len(clone_recorder.calls) == 1
    url, target = clone_recorder.calls[0]
    assert url == "https://github.com/org/repo.git"
    assert target.startswith(str(base_dir.resolve()))
    assert repo_dir.startswith(str(base_dir.resolve()))
