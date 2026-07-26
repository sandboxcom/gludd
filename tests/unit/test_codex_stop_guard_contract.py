from pathlib import Path

from scripts.codex_stop_guard import confirm, run

ROOT = Path(__file__).resolve().parents[2]


def test_codex_stop_guard_script_exists_and_is_executable_contract():
    script = ROOT / "scripts" / "codex_stop_guard.py"
    assert script.exists(), "repository Codex stop guard must exist"
    text = script.read_text()
    assert "STOP CHALLENGE" in text
    assert "TASKS.md" in text
    assert "ratchet" in text


def test_codex_stop_guard_make_target_is_documented():
    makefile = (ROOT / "Makefile").read_text()
    assert "codex-stop-guard:" in makefile
    assert "codex-stop-guard" in makefile.split("help:", 1)[1]


def test_codex_stop_guard_declares_host_boundary():
    script = (ROOT / "scripts" / "codex_stop_guard.py").read_text()
    assert "Codex host" in script
    assert "cannot" in script.lower()


def test_codex_stop_guard_fails_closed_and_rotates_challenge(tmp_path, capsys):
    tasks = tmp_path / "TASKS.md"
    ratchet = tmp_path / "ratchet.yml"
    audit = tmp_path / "audit.jsonl"
    tasks.write_text("- [ ] pending\n- [x] done\n", encoding="utf-8")
    ratchet.write_text("known-failure: reason\n", encoding="utf-8")

    assert run(tasks, ratchet, audit) == 1
    first = capsys.readouterr().out.split("STOP CHALLENGE: ", 1)[1].splitlines()[0]
    assert run(tasks, ratchet, audit) == 1
    second = capsys.readouterr().out.split("STOP CHALLENGE: ", 1)[1].splitlines()[0]
    assert first != second
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 2


def test_codex_stop_guard_allows_clean_ledger(tmp_path, capsys):
    tasks = tmp_path / "TASKS.md"
    ratchet = tmp_path / "ratchet.yml"
    tasks.write_text("- [x] done\n", encoding="utf-8")
    ratchet.write_text("# no entries\n", encoding="utf-8")

    assert run(tasks, ratchet, tmp_path / "audit.jsonl") == 0
    assert "Codex host boundary" in capsys.readouterr().out


def test_codex_stop_guard_requires_exact_token_before_clean_stop(tmp_path, capsys):
    tasks = tmp_path / "TASKS.md"
    ratchet = tmp_path / "ratchet.yml"
    audit = tmp_path / "audit.jsonl"
    state = tmp_path / "state.json"
    tasks.write_text("- [x] done\n", encoding="utf-8")
    ratchet.write_text("# no entries\n", encoding="utf-8")

    assert run(tasks, ratchet, audit, state) == 0
    record = state.read_text(encoding="utf-8")
    token = __import__("json").loads(record)["challenge"]
    assert confirm("wrong", state, audit) == 1
    assert "rejected" in capsys.readouterr().out
    assert confirm(token, state, audit) == 0
    assert "accepted" in capsys.readouterr().out
    assert not state.exists()


def test_codex_stop_guard_rejects_corrupt_state_without_crashing(tmp_path, capsys):
    state = tmp_path / "state.json"
    audit = tmp_path / "audit.jsonl"
    state.write_text("{not-json", encoding="utf-8")

    assert confirm("candidate", state, audit) == 1
    assert "rejected" in capsys.readouterr().out
