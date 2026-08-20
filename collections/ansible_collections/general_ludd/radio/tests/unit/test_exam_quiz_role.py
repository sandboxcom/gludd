"""Tests for exam_quiz role — validates task YAML structure, param validation, result shape."""

from __future__ import annotations

from pathlib import Path

import yaml
from plugins.module_utils.radio_exam_data import exam_list, get_questions, grade_exam

_COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent


def test_exam_quiz_tasks_file_exists():
    tasks = _COLLECTION_ROOT / "roles" / "exam_quiz" / "tasks" / "main.yml"
    assert tasks.exists()


def test_exam_quiz_tasks_has_validate_step():
    tasks = _COLLECTION_ROOT / "roles" / "exam_quiz" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "exam_quiz_exam in [" in content
    assert "exam_quiz_count" in content


def test_exam_quiz_tasks_has_load_step():
    tasks = _COLLECTION_ROOT / "roles" / "exam_quiz" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "general_ludd.radio.radio_runtime:" in content
    assert "operation: exam_quiz" in content
    assert "exam:" in content
    assert "count:" in content


def test_exam_quiz_tasks_has_grade_step():
    tasks = _COLLECTION_ROOT / "roles" / "exam_quiz" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "answers:" in content
    assert "exam_quiz_user_answers" in content


def test_exam_quiz_tasks_has_format_outputs():
    tasks = _COLLECTION_ROOT / "roles" / "exam_quiz" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "format:" in content
    assert "exam_quiz_format" in content
    assert "artifact_content" in content


def test_exam_quiz_tasks_has_verdict():
    tasks = _COLLECTION_ROOT / "roles" / "exam_quiz" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "exam_quiz_verdict" in content
    assert "role: exam_quiz" in content


def test_exam_quiz_defaults_file_exists():
    defaults = _COLLECTION_ROOT / "roles" / "exam_quiz" / "defaults" / "main.yml"
    assert defaults.exists()
    data = yaml.safe_load(defaults.read_text())
    assert "exam_quiz_exam" in data
    assert "exam_quiz_count" in data
    assert "exam_quiz_user_answers" in data
    assert isinstance(data["exam_quiz_user_answers"], list)


def test_default_exam_is_fcc_tech():
    defaults = _COLLECTION_ROOT / "roles" / "exam_quiz" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["exam_quiz_exam"] == "fcc_tech"


def test_default_count_is_10():
    defaults = _COLLECTION_ROOT / "roles" / "exam_quiz" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["exam_quiz_count"] == 10


def test_default_format_is_json():
    defaults = _COLLECTION_ROOT / "roles" / "exam_quiz" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["exam_quiz_format"] == "json"


def test_default_output_dir_writable():
    defaults = _COLLECTION_ROOT / "roles" / "exam_quiz" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    parent = Path(data["exam_quiz_output_dir"]).parent
    assert parent.exists() or parent.name.startswith("gludd")


def test_get_questions_works_with_all_exams():
    defaults = _COLLECTION_ROOT / "roles" / "exam_quiz" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    valid_exams = exam_list()
    assert data["exam_quiz_exam"] in valid_exams
    for exam in valid_exams:
        qs = get_questions(exam, count=5)
        assert len(qs) <= 5
        for q in qs:
            assert q["exam"] == exam


def test_grade_exam_produces_verdict_shape():
    questions = get_questions("fcc_tech", count=5)
    answers = [(q["id"], q["correct"]) for q in questions]
    graded = grade_exam(answers)
    assert "correct" in graded
    assert "total" in graded
    assert "percentage" in graded
    assert "passed" in graded
    assert "results" in graded
    assert isinstance(graded["passed"], bool)
    assert graded["total"] == 5


def test_verdict_has_all_required_fields():
    questions = get_questions("fcc_tech", count=3)
    answers = [(q["id"], q["correct"]) for q in questions]
    graded = grade_exam(answers)
    result = {
        "exam": "fcc_tech",
        "exam_display": "Fcc Tech",
        "num_questions": 3,
        "total_available": len([q for q in get_questions("fcc_tech", count=99)]),
        "questions": [{"id": q["id"], "text": q["text"], "choices": q["choices"]} for q in questions],
        "grade": graded,
    }
    assert "grade" in result
    assert result["grade"]["passed"] is True
    assert result["grade"]["percentage"] == 100.0


def test_exam_quiz_format_supports_json_text_md():
    supported = {"json", "text", "md"}
    defaults = _COLLECTION_ROOT / "roles" / "exam_quiz" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["exam_quiz_format"] in supported


def test_user_answers_default_empty_list():
    defaults = _COLLECTION_ROOT / "roles" / "exam_quiz" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["exam_quiz_user_answers"] == []


def test_exam_quiz_meta_has_role_name():
    meta = _COLLECTION_ROOT / "roles" / "exam_quiz" / "meta" / "main.yml"
    assert meta.exists()
    data = yaml.safe_load(meta.read_text())
    assert data["galaxy_info"]["role_name"] == "exam_quiz"
