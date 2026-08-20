#!/usr/bin/env python3
"""
exam_quiz -- ham/marine license exam Q&A loader, grader, and JSON verdict writer.

Usage:
    python exam_quiz.py --exam fcc_tech --count 5
    python exam_quiz.py --exam fcc_tech --answers '{"T1A01": 1, "T1A02": 2}'

Imports module_utils.radio_exam_data and wraps it as a standalone CLI so the
role's tasks/main.yml can invoke a file backend.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import dataclass, field
from typing import Any

from ansible_collections.general_ludd.radio.plugins.module_utils.radio_exam_data import (
    EXAM_QUESTIONS,
    get_questions,
    grade_exam,
)

VALID_EXAMS = ("fcc_tech", "fcc_general", "fcc_extra", "roc_m", "gmdss")
PASS_THRESHOLD_PCT = 70.0


@dataclass
class ExamQuizVerdict:
    exam: str
    count: int
    questions: list[dict[str, Any]] = field(default_factory=list)
    grade: dict[str, Any] | None = None
    total_available: int = 0
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "exam": self.exam,
            "exam_display": self.exam.replace("_", " ").title(),
            "count_requested": self.count,
            "count_returned": len(self.questions),
            "total_available": self.total_available,
            "questions": self.questions,
            "verdict": "graded" if self.grade is not None else "loaded",
        }
        if self.grade is not None:
            d["grade"] = self.grade
        if self.seed is not None:
            d["seed"] = self.seed
        return d


def load_questions(exam: str, count: int, seed: int | None = None) -> ExamQuizVerdict:
    if exam not in VALID_EXAMS:
        return ExamQuizVerdict(
            exam=exam,
            count=count,
            questions=[],
            total_available=0,
        )

    if seed is not None:
        random.seed(seed)

    questions = get_questions(exam, count)
    total = sum(1 for q in EXAM_QUESTIONS if q["exam"] == exam)

    return ExamQuizVerdict(
        exam=exam,
        count=count,
        questions=[
            {
                "id": q["id"],
                "section": q.get("section", ""),
                "text": q["text"],
                "choices": q["choices"],
            }
            for q in questions
        ],
        total_available=total,
        seed=seed,
    )


def grade_answers(exam: str, answers: dict[str, int], count: int = 0, seed: int | None = None) -> ExamQuizVerdict:
    verdict = load_questions(exam, count or len(answers), seed=seed)
    tuples = [(qid, idx) for qid, idx in answers.items()]
    verdict.grade = grade_exam(tuples)
    return verdict


def main() -> None:
    parser = argparse.ArgumentParser(description="Ham/marine exam Q&A loader and grader")
    parser.add_argument(
        "--exam",
        choices=VALID_EXAMS,
        required=True,
        help="Exam pool name",
    )
    parser.add_argument("--count", type=int, default=10, help="Number of questions to draw")
    parser.add_argument(
        "--answers",
        type=str,
        default=None,
        help='JSON map of {question_id: chosen_index}, e.g. \'{"T1A01": 1}\'',
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible draws")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="/tmp/gludd-exam-quiz",
        help="Output directory for JSON verdict",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text", "md"],
        default="json",
        help="Output format",
    )
    args = parser.parse_args()

    if args.answers:
        try:
            answers = json.loads(args.answers)
        except json.JSONDecodeError as exc:
            parser.error(f"--answers must be valid JSON: {exc}")
        verdict = grade_answers(args.exam, answers, count=args.count, seed=args.seed)
    else:
        verdict = load_questions(args.exam, args.count, seed=args.seed)

    os.makedirs(args.output_dir, exist_ok=True)
    data = verdict.to_dict()

    if args.format == "json":
        output_path = os.path.join(args.output_dir, "exam_quiz.json")
        payload = json.dumps(data, indent=2)
    elif args.format == "text":
        output_path = os.path.join(args.output_dir, "exam_quiz.txt")
        payload = _render_text(data)
    elif args.format == "md":
        output_path = os.path.join(args.output_dir, "exam_quiz.md")
        payload = _render_md(data)
    else:
        output_path = os.path.join(args.output_dir, "exam_quiz.json")
        payload = json.dumps(data, indent=2)

    try:
        with open(output_path, "w") as f:
            f.write(payload)
    except OSError:
        pass

    print(payload)


def _render_text(data: dict[str, Any]) -> str:
    lines = [f"{data['exam_display']} Exam", "=" * (len(data["exam_display"]) + 5)]
    if data.get("grade"):
        g = data["grade"]
        lines.append(f"Score: {g['correct']}/{g['total']} ({g['percentage']}%)")
        lines.append(f"Result: {'PASS' if g['passed'] else 'FAIL'}")
        lines.append("")
        for i, r in enumerate(g.get("results", []), 1):
            lines.append(f"Q{i}: {r.get('text', '')}")
            lines.append(f"  Your answer: {r.get('chosen_text', '')}")
            lines.append(f"  Correct: {r.get('correct_text', '')} [{'CORRECT' if r.get('is_correct') else 'WRONG'}]")
            lines.append(f"  Explanation: {r.get('explanation', '')}")
            lines.append("")
    else:
        lines.append(f"Questions loaded: {data['count_returned']}")
        lines.append("No answers submitted for grading.")
    return "\n".join(lines) + "\n"


def _render_md(data: dict[str, Any]) -> str:
    lines = [f"# {data['exam_display']} Exam"]
    if data.get("grade"):
        g = data["grade"]
        lines.append("")
        lines.append(f"**Score:** {g['correct']}/{g['total']} ({g['percentage']}%)")
        lines.append(f"**Result:** {'PASS' if g['passed'] else 'FAIL'}")
        lines.append("")
        lines.append("## Questions")
        for i, r in enumerate(g.get("results", []), 1):
            lines.append("")
            lines.append(f"### Q{i}: {r.get('text', '')}")
            answer_status = "CORRECT" if r.get("is_correct") else "WRONG"
            lines.append(
                f"- **Your answer:** {r.get('chosen_text', '')} `{answer_status}`"
            )
            lines.append(f"- **Correct:** {r.get('correct_text', '')}")
            lines.append(f"- **Explanation:** {r.get('explanation', '')}")
    else:
        lines.append("")
        lines.append(f"**Questions loaded:** {data['count_returned']}")
        lines.append("")
        lines.append("No answers submitted for grading.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
