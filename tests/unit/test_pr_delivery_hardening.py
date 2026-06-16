"""Hardening tests for PRDelivery argv construction (option/shell injection).

`PRDelivery.push_and_create_pr` shells `git push` and `gh pr create` with a
branch name, remote, title and body that originate from caller / LLM input.
These tests prove that:

  * a branch or remote that begins with '-' (would be parsed as a git/gh
    OPTION, e.g. --upload-pack=...) is REJECTED without ever invoking
    subprocess;
  * a branch or remote carrying shell metacharacters (`;`, `|`, `$`, ``` ` ```,
    spaces, newlines, ...) is REJECTED — even though we never go through a
    shell, such a value can never be a valid git ref / remote and is a clear
    injection attempt;
  * a normal push/PR builds the EXACT argv we expect — argv is a list (never a
    shell string), `--` separates option parsing from the positional
    branch/remote refs, and the PR title/body are passed as discrete argv
    values rather than interpolated into a command line.
"""

from __future__ import annotations

from unittest import mock

import pytest

from general_ludd.git_automation.pr_delivery import PRDelivery


def _ok_completed(stdout: str = "", returncode: int = 0):
    """A successful subprocess.CompletedProcess-like stub."""
    cp = mock.Mock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = ""
    return cp


class TestInjectionRejected:
    @pytest.mark.parametrize(
        "branch",
        [
            "--upload-pack=touch /tmp/pwn",
            "-oProxyCommand=evil",
            "--exec=rm -rf /",
        ],
    )
    def test_dash_leading_branch_rejected_without_subprocess(self, branch):
        d = PRDelivery()
        with mock.patch(
            "general_ludd.git_automation.pr_delivery.subprocess.run"
        ) as run:
            result = d.push_and_create_pr(
                repo_path="/repo",
                branch_name=branch,
                todo_id="T-1",
                title="x",
                description="y",
            )
        run.assert_not_called()
        assert result["pr_url"] is None
        assert result["error"]

    @pytest.mark.parametrize(
        "branch",
        [
            "feature; rm -rf /",
            "feat`whoami`",
            "feat$(touch pwn)",
            "feat|cat /etc/passwd",
            "feat with space",
            "feat\nrm -rf /",
            "feat&background",
            "feat>redirect",
        ],
    )
    def test_metachar_branch_rejected_without_subprocess(self, branch):
        d = PRDelivery()
        with mock.patch(
            "general_ludd.git_automation.pr_delivery.subprocess.run"
        ) as run:
            result = d.push_and_create_pr(
                repo_path="/repo",
                branch_name=branch,
                todo_id="T-1",
                title="x",
            )
        run.assert_not_called()
        assert result["pr_url"] is None
        assert result["error"]

    @pytest.mark.parametrize(
        "remote",
        [
            "--upload-pack=evil",
            "origin; rm -rf /",
            "ori`whoami`gin",
            "ori gin",
        ],
    )
    def test_injection_remote_rejected_without_subprocess(self, remote):
        d = PRDelivery()
        with mock.patch(
            "general_ludd.git_automation.pr_delivery.subprocess.run"
        ) as run:
            result = d.push_and_create_pr(
                repo_path="/repo",
                branch_name="feature/ok",
                todo_id="T-1",
                title="x",
                remote=remote,
            )
        run.assert_not_called()
        assert result["pr_url"] is None
        assert result["error"]


class TestExpectedArgv:
    def test_normal_push_and_pr_build_expected_argv(self):
        d = PRDelivery(base_branch="main")
        calls = {}

        def fake_run(argv, **kwargs):
            # gh --version availability probe vs the real push/create calls.
            if argv[:2] == ["gh", "--version"]:
                return _ok_completed()
            if argv[:2] == ["git", "push"]:
                calls["push"] = (argv, kwargs)
                return _ok_completed()
            if argv[:3] == ["gh", "pr", "create"]:
                calls["pr"] = (argv, kwargs)
                return _ok_completed(stdout="https://example.test/pr/1\n")
            raise AssertionError(f"unexpected argv: {argv!r}")

        with mock.patch(
            "general_ludd.git_automation.pr_delivery.subprocess.run",
            side_effect=fake_run,
        ):
            result = d.push_and_create_pr(
                repo_path="/repo",
                branch_name="feature/login",
                todo_id="T-42",
                title="Add login; really",
                description="A body with $pecial & chars\nmultiline",
            )

        assert result["error"] is None
        assert result["pr_url"] == "https://example.test/pr/1"

        # --- push argv: list, with `--` before the positional refs ---
        push_argv, push_kwargs = calls["push"]
        assert isinstance(push_argv, list)
        assert push_argv == [
            "git", "push", "origin", "--", "feature/login",
        ]
        assert push_kwargs["cwd"] == "/repo"

        # --- gh pr create argv: list, title/body passed as discrete values ---
        pr_argv, _ = calls["pr"]
        assert isinstance(pr_argv, list)
        assert pr_argv[:3] == ["gh", "pr", "create"]
        # title is a single argv element immediately after --title (not split,
        # not interpolated into a command string)
        ti = pr_argv.index("--title")
        assert pr_argv[ti + 1] == "[gludd] T-42: Add login; really"
        bi = pr_argv.index("--body")
        assert pr_argv[bi + 1] == "A body with $pecial & chars\nmultiline"
        assert "--head" in pr_argv
        assert pr_argv[pr_argv.index("--head") + 1] == "feature/login"
        assert "--base" in pr_argv
        assert pr_argv[pr_argv.index("--base") + 1] == "main"

    def test_custom_remote_passed_through_when_valid(self):
        d = PRDelivery()
        seen = {}

        def fake_run(argv, **kwargs):
            if argv[:2] == ["gh", "--version"]:
                return _ok_completed()
            if argv[:2] == ["git", "push"]:
                seen["push"] = argv
                return _ok_completed()
            if argv[:3] == ["gh", "pr", "create"]:
                return _ok_completed(stdout="url\n")
            raise AssertionError(f"unexpected argv: {argv!r}")

        with mock.patch(
            "general_ludd.git_automation.pr_delivery.subprocess.run",
            side_effect=fake_run,
        ):
            d.push_and_create_pr(
                repo_path="/repo",
                branch_name="feature/ok",
                todo_id="T-1",
                title="t",
                remote="upstream",
            )

        assert seen["push"] == [
            "git", "push", "upstream", "--", "feature/ok",
        ]
