"""#64 (defense-in-depth at the git layer): GitAutomation.clone() must refuse
dangerous clone-URL forms AT THE REPO WRAPPER, even if a higher layer validates.

The clone wrapper shells out to `git clone <url> <target>`. Several URL forms turn
that into a local-file-disclosure or arbitrary-command-execution primitive:

  * ``file://...``            -> local filesystem disclosure (clone any local repo)
  * ``ext::sh -c ...``        -> git's ext transport runs an ARBITRARY command
  * ``git::``/``fd::``        -> other transport helpers with the same exec class
  * ``ssh://...-oProxyCommand=`` / a url carrying ``-o``/``ProxyCommand`` -> ssh
                                 runs an arbitrary command via ProxyCommand
  * a url (or path) beginning with ``-`` -> option injection (parsed as a git flag)

These tests prove each dangerous form is rejected (``success=False``, no subprocess
launched) and that a normal ``https://`` clone still proceeds. The subprocess is
mocked so NO real network / git runs.
"""

from __future__ import annotations

from unittest import mock

import pytest

from general_ludd.git_automation.repo import GitAutomation


def _ok_completed(args, **_kwargs):
    """A stand-in subprocess result for an accepted clone (returncode 0)."""
    cp = mock.Mock()
    cp.returncode = 0
    cp.stdout = ""
    cp.stderr = ""
    return cp


class TestCloneUrlHardening:
    # These forms are arbitrary-command-execution / option-injection primitives
    # with NO legitimate clone use, so the wrapper refuses them UNCONDITIONALLY
    # (even at the permissive default allow_local=True).
    @pytest.mark.parametrize(
        "bad_url",
        [
            "ext::sh -c 'touch /tmp/pwned'",
            "git::https://evil.example/x",
            "fd::17/foo",
            "ssh://host/repo -oProxyCommand=touch${IFS}/tmp/pwned",
            "ssh://-oProxyCommand=calc/repo",
            "-oProxyCommand=touch /tmp/pwned",
            "--upload-pack=touch /tmp/pwned",
        ],
    )
    def test_dangerous_url_is_rejected_without_running_git(self, tmp_path, bad_url):
        git = GitAutomation()
        with mock.patch("subprocess.run", side_effect=AssertionError(
            "subprocess.run must NOT be called for a rejected dangerous url"
        )) as run:
            result = git.clone(bad_url, str(tmp_path / "out"))
        assert result.success is False, f"expected rejection for {bad_url!r}"
        run.assert_not_called()

    # file:// is local-filesystem disclosure. For an UNTRUSTED caller-supplied
    # url (allow_local=False) it must be refused before git ever runs.
    @pytest.mark.parametrize(
        "bad_url",
        [
            "file:///etc/passwd",
            "file://localhost/home/victim/secret-repo",
            "FILE:///etc/shadow",
        ],
    )
    def test_file_url_rejected_when_local_disallowed(self, tmp_path, bad_url):
        git = GitAutomation()
        with mock.patch("subprocess.run", side_effect=AssertionError(
            "subprocess.run must NOT be called for a rejected file:// url"
        )) as run:
            result = git.clone(bad_url, str(tmp_path / "out"), allow_local=False)
        assert result.success is False, f"expected rejection for {bad_url!r}"
        run.assert_not_called()

    def test_normal_https_url_is_accepted_and_runs_git(self, tmp_path):
        git = GitAutomation()
        target = tmp_path / "out"
        with mock.patch("subprocess.run", side_effect=_ok_completed) as run:
            result = git.clone("https://github.com/acme/widgets.git", str(target))
        assert result.success is True
        run.assert_called_once()
        # The actual argv that would be exec'd: a `--` end-of-options separator
        # must precede the positional url so a url can never be parsed as a flag.
        argv = run.call_args.args[0]
        assert argv[:2] == ["git", "clone"]
        assert "--" in argv, f"clone argv must insert '--' before positionals: {argv}"
        sep = argv.index("--")
        assert argv[sep + 1] == "https://github.com/acme/widgets.git"
        assert argv[sep + 2] == str(target.resolve()) or argv[sep + 2] == str(target)

    def test_normal_ssh_to_public_host_is_accepted(self, tmp_path):
        git = GitAutomation()
        with mock.patch("subprocess.run", side_effect=_ok_completed) as run:
            result = git.clone("git@github.com:acme/widgets.git", str(tmp_path / "out"))
        assert result.success is True
        run.assert_called_once()
