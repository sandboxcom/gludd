import json
from pathlib import Path

from scripts.codex_stop_hook import handle


def test_stop_hook_blocks_pending_work_with_fresh_challenge(tmp_path):
    (tmp_path / "TASKS.md").write_text("- [ ] pending\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "ratchet.yml").write_text("# empty\n", encoding="utf-8")
    response = handle({"cwd": str(tmp_path), "stop_hook_active": False})
    assert response["decision"] == "block"
    assert "STOP CHALLENGE: " in response["reason"]


def test_stop_hook_issues_different_tokens_on_repeated_attempts(tmp_path):
    (tmp_path / "TASKS.md").write_text("- [ ] pending\n", encoding="utf-8")
    first = handle({"cwd": str(tmp_path), "stop_hook_active": False})["reason"]
    second = handle({"cwd": str(tmp_path), "stop_hook_active": True})["reason"]
    assert first.split("STOP CHALLENGE: ", 1)[1].split()[0] != second.split("STOP CHALLENGE: ", 1)[1].split()[0]


def test_stop_hook_allows_clean_ledger(tmp_path):
    (tmp_path / "TASKS.md").write_text("- [x] done\n", encoding="utf-8")
    assert handle({"cwd": str(tmp_path), "stop_hook_active": False}) == {"continue": True}


def test_stop_hook_is_json_serializable():
    assert json.dumps(handle({"cwd": str(Path.cwd()), "stop_hook_active": False}))


def test_project_codex_config_registers_stop_hook():
    config = json.loads((Path(__file__).parents[2] / ".codex" / "hooks.json").read_text())
    handlers = config["hooks"]["Stop"][0]["hooks"]
    assert handlers[0]["type"] == "command"
    assert "stop_continue.py" in handlers[0]["command"]
