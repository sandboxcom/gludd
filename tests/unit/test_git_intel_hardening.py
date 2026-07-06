"""Hardening tests for ``code_intelligence/git_intel.py``.

``GitIntelligence`` runs ``git -C <repo> ...`` with the repo path and a
ref/path that can come from caller input.  These tests prove:

* injection-y refs/paths (leading ``-`` / shell metachars) are rejected and
  never reach ``subprocess.run``;
* a normal intel query builds the expected argv (argv list-form, ``-C <repo>``,
  ``--`` end-of-options separator before the positional ref/path);
* the repo path is confined: a non-existent / non-directory path fails closed.
"""

from __future__ import annotations

import os
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.code_intelligence.git_intel import (
    GitIntelError,
    GitIntelligence,
    _validate_token,
)


def _completed(stdout: str = "", returncode: int = 0) -> MagicMock:
    cp = MagicMock()
    cp.stdout = stdout
    cp.returncode = returncode
    return cp


# --------------------------------------------------------------------------- #
# token validation
# --------------------------------------------------------------------------- #
class TestValidateToken:
    @pytest.mark.parametrize(
        "bad",
        [
            "-rf",
            "--output=/etc/passwd",
            "-",
            "--upload-pack=touch /tmp/pwn",
        ],
    )
    def test_leading_dash_rejected(self, bad: str):
        with pytest.raises(GitIntelError):
            _validate_token(bad)

    @pytest.mark.parametrize(
        "bad",
        [
            "foo; rm -rf /",
            "foo|cat",
            "foo&whoami",
            "$(id)",
            "`id`",
            "foo > /tmp/x",
            "foo\nbar",
            "a b",
        ],
    )
    def test_shell_metachars_rejected(self, bad: str):
        with pytest.raises(GitIntelError):
            _validate_token(bad)

    @pytest.mark.parametrize("bad", ["", None, 123])
    def test_empty_or_non_str_rejected(self, bad):
        with pytest.raises(GitIntelError):
            cast(Any, _validate_token)(bad)

    @pytest.mark.parametrize(
        "ok",
        ["README.md", "src/general_ludd/cli.py", "path/to/file.py", "v1.2.3"],
    )
    def test_normal_tokens_pass_through(self, ok: str):
        assert _validate_token(ok) == ok


# --------------------------------------------------------------------------- #
# repo path confinement
# --------------------------------------------------------------------------- #
class TestRepoConfinement:
    def test_nonexistent_repo_fails_closed(self, tmp_path):
        gi = GitIntelligence(str(tmp_path / "does-not-exist"))
        with patch("general_ludd.code_intelligence.git_intel.subprocess.run") as run:
            assert gi._run_git(["log"]) is None
            run.assert_not_called()

    def test_repo_path_must_be_dir_not_file(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        gi = GitIntelligence(str(f))
        with patch("general_ludd.code_intelligence.git_intel.subprocess.run") as run:
            assert gi._run_git(["log"]) is None
            run.assert_not_called()

    def test_repo_path_is_realpathed(self, tmp_path):
        gi = GitIntelligence(str(tmp_path) + "/.")
        with patch(
            "general_ludd.code_intelligence.git_intel.subprocess.run",
            return_value=_completed(),
        ) as run:
            gi._run_git(["log"])
        argv = run.call_args.args[0]
        # "-C <repo>" must carry the canonicalised (realpath'd) directory.
        assert argv[1] == "-C"
        assert argv[2] == os.path.realpath(str(tmp_path))


# --------------------------------------------------------------------------- #
# injection-y ref/path rejected end-to-end
# --------------------------------------------------------------------------- #
class TestInjectionRejected:
    def test_blame_with_dash_option_ref_is_rejected(self, tmp_path):
        gi = GitIntelligence(str(tmp_path))
        with patch("general_ludd.code_intelligence.git_intel.subprocess.run") as run:
            out = gi.blame_analysis("--output=/etc/passwd")
            # fails closed: empty result and subprocess never invoked
            assert out == {}
            run.assert_not_called()

    def test_blame_with_shell_metachar_ref_is_rejected(self, tmp_path):
        gi = GitIntelligence(str(tmp_path))
        with patch("general_ludd.code_intelligence.git_intel.subprocess.run") as run:
            out = gi.blame_analysis("foo.py; rm -rf /")
            assert out == {}
            run.assert_not_called()

    def test_run_git_unsafe_ref_returns_none(self, tmp_path):
        gi = GitIntelligence(str(tmp_path))
        with patch("general_ludd.code_intelligence.git_intel.subprocess.run") as run:
            assert gi._run_git(["blame"], refs=["-rf"]) is None
            run.assert_not_called()


# --------------------------------------------------------------------------- #
# normal query builds expected argv
# --------------------------------------------------------------------------- #
class TestExpectedArgv:
    def test_blame_builds_expected_argv_with_end_of_options(self, tmp_path):
        gi = GitIntelligence(str(tmp_path))
        with patch(
            "general_ludd.code_intelligence.git_intel.subprocess.run",
            return_value=_completed("author Alice\n\tline\n"),
        ) as run:
            gi.blame_analysis("src/general_ludd/cli.py")

        argv = run.call_args.args[0]
        repo = os.path.realpath(str(tmp_path))
        assert argv == [
            "git",
            "-C",
            repo,
            "blame",
            "--line-porcelain",
            "--",
            "src/general_ludd/cli.py",
        ]

    def test_run_git_is_argv_list_not_shell(self, tmp_path):
        gi = GitIntelligence(str(tmp_path))
        with patch(
            "general_ludd.code_intelligence.git_intel.subprocess.run",
            return_value=_completed(),
        ) as run:
            gi._run_git(["log", "-n", "5"], refs=["HEAD"])

        # positional argv (list-form), and shell= must NOT be enabled
        argv = run.call_args.args[0]
        assert isinstance(argv, list)
        assert argv[0] == "git"
        assert run.call_args.kwargs.get("shell", False) is False
        # end-of-options separator precedes the caller ref
        assert "--" in argv
        assert argv[argv.index("--") + 1] == "HEAD"

    def test_no_refs_means_no_separator(self, tmp_path):
        gi = GitIntelligence(str(tmp_path))
        with patch(
            "general_ludd.code_intelligence.git_intel.subprocess.run",
            return_value=_completed(),
        ) as run:
            gi.recent_commits(limit=3)
        argv = run.call_args.args[0]
        assert "--" not in argv
        repo = os.path.realpath(str(tmp_path))
        assert argv[:3] == ["git", "-C", repo]
