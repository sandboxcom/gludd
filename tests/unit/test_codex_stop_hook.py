import json
import subprocess
import sys
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts import codex_stop_hook
from scripts.codex_stop_hook import handle


def test_stop_hook_names_the_native_selected_primary(tmp_path: Path) -> None:
    if TYPE_CHECKING:
        from item_progress import focus_item
    else:
        from scripts.item_progress import focus_item

    (tmp_path / "TASKS.md").write_text("- [ ] F12.1 — primary | status: pending\n", encoding="utf-8")
    focus_item(tmp_path, "F12.1")
    response = handle({"cwd": str(tmp_path), "stop_hook_active": True})
    assert response["decision"] == "block"
    assert "Selected primary: F12.1" in response["reason"]
    assert "deployment acceptance" in response["reason"]


@pytest.mark.parametrize("continuation", (False, True))
@pytest.mark.parametrize("selected_focus", (False, True))
def test_stop_hook_requires_bounded_external_input_wait(
    tmp_path: Path, continuation: bool, selected_focus: bool
) -> None:
    if TYPE_CHECKING:
        from item_progress import focus_item
    else:
        from scripts.item_progress import focus_item

    (tmp_path / "TASKS.md").write_text(
        "- [ ] F12.1 - release proof pending\n", encoding="utf-8"
    )
    if selected_focus:
        focus_item(tmp_path, "F12.1")

    response = handle({"cwd": str(tmp_path), "stop_hook_active": continuation})

    assert response["decision"] == "block"
    reason = response["reason"]
    assert "STOP CHALLENGE: " in reason
    assert "required external input" in reason
    assert "keep the item open" in reason
    assert "ask once" in reason
    assert "interruptible" in reason
    assert "60 seconds" in reason
    assert "do not finalize" in reason
    assert "do not repeat unchanged failures" in reason
    assert "source or receipt-only changes" in reason
    assert "new challenge token is not a changed prerequisite" in reason


@pytest.mark.parametrize(
    ("inventory_scope", "message"),
    (
        (None, "did not return a scope"),
        (
            {"open_count": "1", "backlog_open_count": 0, "label": ""},
            "invalid counts",
        ),
        (
            {"open_count": 1, "backlog_open_count": "0", "label": ""},
            "invalid counts",
        ),
        (
            {"open_count": 1, "backlog_open_count": 0, "label": 42},
            "invalid label",
        ),
    ),
)
def test_stop_hook_emits_blocking_json_for_invalid_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    inventory_scope: object,
    message: str,
) -> None:
    (tmp_path / "TASKS.md").write_text("- [ ] pending\n", encoding="utf-8")

    def invalid_inventory(_text: str) -> dict[str, object]:
        return {"task_scope": inventory_scope}

    monkeypatch.setattr(codex_stop_hook, "task_inventory", invalid_inventory)
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({"cwd": str(tmp_path)})))

    assert codex_stop_hook.main() == 0
    response = json.loads(capsys.readouterr().out)
    assert response["decision"] == "block"
    assert "Codex stop hook error" in response["reason"]
    assert message in response["reason"]


def test_stop_hook_keeps_ratchet_pending_without_task_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "ratchet.yml").write_text(
        "known: failure\n", encoding="utf-8"
    )
    native_exists = Path.exists

    def exists_without_task_ledger(path: Path) -> bool:
        if path.name == "TASKS.md":
            return False
        return native_exists(path)

    monkeypatch.setattr(Path, "exists", exists_without_task_ledger)

    response = handle({"cwd": str(tmp_path), "stop_hook_active": True})

    assert response["decision"] == "block"
    assert "0 TASKS.md item(s)" in response["reason"]
    assert "1 ratchet entry(ies)" in response["reason"]
    assert "required external input" in response["reason"]


def test_stop_hook_keeps_active_milestone_without_excluded_backlog(
    tmp_path: Path,
) -> None:
    (tmp_path / "TASKS.md").write_text(
        "# Tasks\n\n"
        "The fail-closed v0.1.1 milestone is the exact task set "
        "S83.157-S83.168.\n\n"
        "- [ ] S83.157 - required release proof\n",
        encoding="utf-8",
    )

    response = handle({"cwd": str(tmp_path), "stop_hook_active": True})

    assert response["decision"] == "block"
    assert "1 active v0.1.1 TASKS.md item(s)" in response["reason"]
    assert "backlog item(s) excluded" not in response["reason"]
    assert "required external input" in response["reason"]


def test_stop_hook_module_entrypoint_retains_pending_protocol(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text("- [ ] pending\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.codex_stop_hook"],
        input=json.dumps({"cwd": str(tmp_path), "stop_hook_active": True}),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    response = json.loads(result.stdout)
    assert response["decision"] == "block"
    assert "STOP CHALLENGE: " in response["reason"]
    assert "required external input" in response["reason"]


def test_stop_hook_rejects_corrupt_administrative_focus(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text("- [ ] F12.1 — primary | status: pending\n", encoding="utf-8")
    (tmp_path / ".gludd").mkdir()
    (tmp_path / ".gludd/item-progress.json").write_text("{}", encoding="utf-8")
    response = handle({"cwd": str(tmp_path), "stop_hook_active": True})
    assert response["decision"] == "block"
    assert "item progress error" in response["reason"].lower()


def test_stop_hook_blocks_pending_work_with_fresh_challenge(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text("- [ ] pending\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "ratchet.yml").write_text("# empty\n", encoding="utf-8")
    response = handle({"cwd": str(tmp_path), "stop_hook_active": False})
    assert response["decision"] == "block"
    assert "STOP CHALLENGE: " in response["reason"]


def test_stop_hook_issues_different_tokens_on_repeated_attempts(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text("- [ ] pending\n", encoding="utf-8")
    first = handle({"cwd": str(tmp_path), "stop_hook_active": False})["reason"]
    second = handle({"cwd": str(tmp_path), "stop_hook_active": True})["reason"]
    assert first.split("STOP CHALLENGE: ", 1)[1].split()[0] != second.split("STOP CHALLENGE: ", 1)[1].split()[0]


def test_stop_hook_allows_clean_ledger(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text("- [x] done\n", encoding="utf-8")
    assert handle({"cwd": str(tmp_path), "stop_hook_active": False}) == {"continue": True}


def test_stop_hook_is_json_serializable() -> None:
    assert json.dumps(handle({"cwd": str(Path.cwd()), "stop_hook_active": False}))


def test_project_codex_config_registers_stop_hook() -> None:
    config = json.loads((Path(__file__).parents[2] / ".codex" / "hooks.json").read_text())
    handlers = config["hooks"]["Stop"][0]["hooks"]
    assert handlers[0]["type"] == "command"
    assert "stop_continue.py" in handlers[0]["command"]


def test_stop_hook_finds_task_ledger_from_nested_cwd(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text("- [ ] pending\n", encoding="utf-8")
    nested = tmp_path / "work" / "nested"
    nested.mkdir(parents=True)
    response = handle({"cwd": str(nested), "stop_hook_active": False})
    assert response["decision"] == "block"
    assert "1 TASKS.md item(s)" in response["reason"]


def test_stop_hook_blocks_ratchet_entries_even_when_tasks_are_complete(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text("- [x] done\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "ratchet.yml").write_text("known: failure\n", encoding="utf-8")
    response = handle({"cwd": str(tmp_path), "stop_hook_active": False})
    assert response["decision"] == "block"
    assert "1 ratchet entry(ies)" in response["reason"]


def test_stop_hook_counts_only_declared_milestone_work(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text(
        """# Tasks

The fail-closed v0.1.1 milestone is the exact task set S83.157\u2013S83.168.

- [ ] S83.157 - active release work
- [x] S83.158 - completed release work
- [ ] S81.11 - future backlog work
""",
        encoding="utf-8",
    )

    response = handle({"cwd": str(tmp_path), "stop_hook_active": False})

    assert response["decision"] == "block"
    assert "1 active v0.1.1 TASKS.md item(s)" in response["reason"]
    assert "1 backlog item(s) excluded from the stop gate" in response["reason"]


def test_stop_hook_allows_stop_when_only_backlog_remains(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text(
        """# Tasks

The fail-closed v0.1.1 milestone is the exact task set S83.157-S83.168.

- [x] S83.157 - completed release work
- [ ] S81.11 - future backlog work
""",
        encoding="utf-8",
    )

    assert handle({"cwd": str(tmp_path), "stop_hook_active": True}) == {
        "continue": True
    }


def test_stop_hook_entrypoint_returns_codex_json_for_invalid_input() -> None:
    entrypoint = Path(__file__).parents[2] / ".codex" / "hooks" / "stop_continue.py"
    result = subprocess.run(
        [sys.executable, str(entrypoint)],
        input="not-json\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "Codex stop hook error" in payload["reason"]


def test_stop_hook_entrypoint_emits_protocol_json_for_pending_work(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text("- [ ] pending\n", encoding="utf-8")
    entrypoint = Path(__file__).parents[2] / ".codex" / "hooks" / "stop_continue.py"
    result = subprocess.run(
        [sys.executable, str(entrypoint)],
        input=json.dumps({"cwd": str(tmp_path), "stop_hook_active": True}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "STOP CHALLENGE: " in payload["reason"]
