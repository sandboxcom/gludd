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
