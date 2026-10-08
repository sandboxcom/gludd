from types import SimpleNamespace

import general_ludd.git_automation.git_stats as git_stats


def test_git_command_helpers_parse_and_forward_file_filters(monkeypatch):
    commands = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        if "log" in cmd:
            return SimpleNamespace(stdout=chr(0).join(("abc", "Ada", "2026-01-01", "message")))
        return SimpleNamespace(stdout=" 1 file changed")

    monkeypatch.setattr(git_stats.subprocess, "run", run)
    assert git_stats.git_log("/repo", 1)[0]["hash"] == "abc"
    assert git_stats.git_show("/repo", "abc")["sha"] == "abc"
    assert git_stats.git_diff("/repo", ["a.py"])["output"] == "1 file changed"
    assert commands[-1][-2:] == ["--", "a.py"]


def test_git_helpers_fail_closed_on_empty_and_malformed_output(monkeypatch):
    outputs = iter(("\nmalformed\nabc\x00Ada\n", "", ""))

    def run(cmd, **kwargs):
        return SimpleNamespace(stdout=next(outputs))

    monkeypatch.setattr(git_stats.subprocess, "run", run)

    assert git_stats.git_log("/repo") == []
    assert git_stats.git_show("/repo") == {"sha": "HEAD", "output": "No diff"}
    assert git_stats.git_diff("/repo", "one.py") == {"output": "No diff"}


def test_git_log_skips_blank_output(monkeypatch):
    monkeypatch.setattr(
        git_stats.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="\n"),
    )

    assert git_stats.git_log("/repo") == []
