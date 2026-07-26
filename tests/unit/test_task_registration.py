from __future__ import annotations

from scripts.check_task_registration import registration_issues, task_ids, unregistered_paths


def test_task_ids_only_accept_checkbox_entries() -> None:
    text = "- [ ] S53.38 — guard\nReference A.1 in prose\n"
    assert task_ids(text) == {"S53.38"}


def test_path_reference_registers_single_file() -> None:
    text = "- [ ] S53.38 — guard scripts/check_task_registration.py\n"
    assert unregistered_paths(text, ["scripts/check_task_registration.py"]) == []


def test_commit_task_id_registers_change_set() -> None:
    text = "- [ ] S53.38 — guard new_file.py\n"
    assert unregistered_paths(text, ["new_file.py"], ["S53.38: add registration guard"]) == []


def test_missing_registration_is_reported() -> None:
    text = "- [ ] S53.38 — guard\n"
    assert unregistered_paths(text, ["new_file.py"], ["miscellaneous change"]) == ["new_file.py"]


def test_generated_artifacts_are_ignored() -> None:
    assert unregistered_paths("", [".coverage.audit.123", ".pytest_cache/x"]) == []


def test_delegated_unknown_id_fails_closed() -> None:
    issues = registration_issues("- [ ] S53.38 — guard\n", [], delegated_task_ids=["GAME-1"])
    assert issues == ["delegated task ID is not declared: GAME-1"]


def test_commit_id_without_file_mapping_stays_unregistered() -> None:
    text = "- [ ] S53.38 — guard scripts/check_task_registration.py\n"
    issues = registration_issues(text, ["unrelated.py"], ["S53.38: mixed commit"])
    assert issues == ["unregistered path: unrelated.py"]
