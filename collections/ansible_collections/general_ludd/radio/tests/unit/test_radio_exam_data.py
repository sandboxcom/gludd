"""Tests for radio_exam_data module."""

from __future__ import annotations

from plugins.module_utils.radio_exam_data import (
    EXAM_QUESTIONS,
    questions_for,
    exam_sections,
    exam_list,
    get_questions,
    grade_exam,
)


def test_exam_questions_is_non_empty_list():
    assert isinstance(EXAM_QUESTIONS, list)
    assert len(EXAM_QUESTIONS) >= 35


def test_every_question_has_required_keys():
    required = {"id", "exam", "section", "text", "choices", "correct", "explanation"}
    for q in EXAM_QUESTIONS:
        assert required.issubset(set(q.keys())), f"Missing keys in {q.get('id', 'UNKNOWN')}"
        assert isinstance(q["choices"], list)
        assert len(q["choices"]) >= 3
        assert isinstance(q["correct"], int)
        assert 0 <= q["correct"] < len(q["choices"])
        assert q["explanation"].strip()


def test_every_question_id_is_unique():
    ids = [q["id"] for q in EXAM_QUESTIONS]
    assert len(ids) == len(set(ids))


def test_known_exams_present():
    exams = set(q["exam"] for q in EXAM_QUESTIONS)
    assert "fcc_tech" in exams
    assert "fcc_general" in exams
    assert "fcc_extra" in exams
    assert "roc_m" in exams


def test_fcc_tech_has_20_plus():
    tech = [q for q in EXAM_QUESTIONS if q["exam"] == "fcc_tech"]
    assert len(tech) >= 20


def test_fcc_general_has_10_plus():
    gen = [q for q in EXAM_QUESTIONS if q["exam"] == "fcc_general"]
    assert len(gen) >= 10


def test_fcc_extra_has_5_plus():
    extra = [q for q in EXAM_QUESTIONS if q["exam"] == "fcc_extra"]
    assert len(extra) >= 5


def test_roc_m_has_5_plus():
    roc = [q for q in EXAM_QUESTIONS if q["exam"] == "roc_m"]
    assert len(roc) >= 5


def test_questions_for_filters_by_exam():
    result = questions_for("fcc_tech")
    assert len(result) > 0
    for q in result:
        assert q["exam"] == "fcc_tech"


def test_questions_for_with_section():
    sections = exam_sections("fcc_tech")
    for section in sections:
        result = questions_for("fcc_tech", section=section)
        for q in result:
            assert q["section"] == section


def test_questions_for_unknown_exam_returns_empty():
    result = questions_for("nonexistent")
    assert result == []


def test_exam_sections_returns_sorted():
    sections = exam_sections("fcc_tech")
    assert sections == sorted(sections)
    assert len(sections) >= 2


def test_exam_list_returns_sorted():
    exams = exam_list()
    assert exams == sorted(exams)
    assert "fcc_tech" in exams


def test_get_questions_returns_requested_count():
    result = get_questions("fcc_tech", count=5)
    assert len(result) == 5
    for q in result:
        assert q["exam"] == "fcc_tech"


def test_get_questions_handles_count_larger_than_pool():
    all_extra = [q for q in EXAM_QUESTIONS if q["exam"] == "fcc_extra"]
    result = get_questions("fcc_extra", count=999)
    assert len(result) == len(all_extra)


def test_get_questions_default_exam_is_fcc_tech():
    result = get_questions(count=3)
    assert len(result) == 3
    for q in result:
        assert q["exam"] == "fcc_tech"


def test_grade_exam_all_correct():
    questions = get_questions("fcc_tech", count=5)
    answers = [(q["id"], q["correct"]) for q in questions]
    result = grade_exam(answers)
    assert result["correct"] == 5
    assert result["total"] == 5
    assert result["percentage"] == 100.0
    assert result["passed"] is True


def test_grade_exam_all_wrong():
    questions = get_questions("fcc_tech", count=5)
    answers = [(q["id"], (q["correct"] + 1) % len(q["choices"])) for q in questions]
    result = grade_exam(answers)
    assert result["correct"] == 0
    assert result["percentage"] == 0.0
    assert result["passed"] is False


def test_grade_exam_mixed():
    questions = get_questions("fcc_tech", count=4)
    answers = [
        (questions[0]["id"], questions[0]["correct"]),
        (questions[1]["id"], (questions[1]["correct"] + 1) % len(questions[1]["choices"])),
        (questions[2]["id"], questions[2]["correct"]),
        (questions[3]["id"], (questions[3]["correct"] + 1) % len(questions[3]["choices"])),
    ]
    result = grade_exam(answers)
    assert result["correct"] == 2
    assert result["total"] == 4
    assert result["percentage"] == 50.0
    assert result["passed"] is False


def test_grade_exam_passing_threshold():
    questions = get_questions("fcc_tech", count=10)
    answers = []
    for i, q in enumerate(questions):
        if i < 7:
            answers.append((q["id"], q["correct"]))
        else:
            answers.append((q["id"], (q["correct"] + 1) % len(q["choices"])))
    result = grade_exam(answers)
    assert result["correct"] == 7
    assert result["percentage"] == 70.0
    assert result["passed"] is True


def test_grade_exam_results_have_explanations():
    questions = get_questions("fcc_tech", count=2)
    answers = [(q["id"], q["correct"]) for q in questions]
    result = grade_exam(answers)
    assert len(result["results"]) == 2
    for r in result["results"]:
        assert "explanation" in r
        assert r["explanation"]
        assert r["is_correct"]


def test_grade_exam_unknown_id_handled():
    answers = [("NONEXIST", 0)]
    result = grade_exam(answers)
    assert result["correct"] == 0
    assert result["results"][0]["explanation"] == "Unknown question ID"
