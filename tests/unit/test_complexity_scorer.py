"""Tests for CodeComplexityScorer — code complexity analysis and task type suggestion."""

from __future__ import annotations

import os
import tempfile

from general_ludd.code_intelligence.complexity_scorer import (
    CodeComplexityScorer,
    ComplexityScore,
    DirectoryComplexityReport,
)
from general_ludd.schemas.benchmark import TaskType

SIMPLE_CODE = '''
def add(a, b):
    return a + b

def greet(name):
    return f"hello {name}"
'''

COMPLEX_CODE = '''
import os
import sys

class DataProcessor:
    def __init__(self, config):
        self.config = config
        self._cache = {}

    def process(self, data):
        results = []
        for record in data:
            if self._validate(record):
                if record.get("type") == "A":
                    results.append(self._transform_a(record))
                elif record.get("type") == "B":
                    results.append(self._transform_b(record))
                else:
                    results.append(self._transform_default(record))
            else:
                if self._can_fix(record):
                    results.append(self._fix(record))
                else:
                    raise ValueError("bad record")
        return results

    def _validate(self, record):
        return isinstance(record, dict) and len(record) > 0

    def _transform_a(self, record):
        out = {}
        for k, v in record.items():
            if k.startswith("a_"):
                out[k] = v.upper()
            elif k.startswith("b_"):
                out[k] = v.lower()
            else:
                out[k] = str(v)
        return out

    def _transform_b(self, record):
        return {k: v for k, v in record.items() if v is not None}

    def _transform_default(self, record):
        return record

    def _can_fix(self, record):
        return "fixable" in record

    def _fix(self, record):
        return {"fixed": True, **record}


def helper(x):
    if x > 0:
        if x > 10:
            if x > 100:
                return "huge"
            return "big"
        return "small"
    return "zero"


def nested(a, b, c):
    if a:
        for i in range(b):
            while c > 0:
                if i > 5:
                    c -= 1
                else:
                    c += 1
    return c
'''

HIGH_NESTING_CODE = '''
def deeply_nested(data):
    if data:
        for item in data:
            if item:
                for key in item:
                    if key.startswith("x"):
                        while True:
                            if key.endswith("y"):
                                return key
                            break
    return None
'''

SECURITY_PATTERN_CODE = '''
def check_access(user, resource):
    if user.is_admin:
        if resource.public:
            return True
        elif resource.owner == user.id:
            return True
        elif user in resource.shared_with:
            return True
        else:
            if user.has_role("viewer"):
                if resource.allow_viewers:
                    return True
                else:
                    return False
            elif user.has_role("editor"):
                if resource.allow_editors:
                    return True
                else:
                    return False
            else:
                return False
    else:
        if resource.public:
            return True
        return False
'''


class TestComplexityScore:
    def test_create_complexity_score(self):
        score = ComplexityScore(
            path="test.py",
            cyclomatic_complexity=5,
            max_nesting_depth=3,
            function_count=4,
            class_count=1,
            loc=50,
        )
        assert score.path == "test.py"
        assert score.cyclomatic_complexity == 5
        assert score.max_nesting_depth == 3
        assert score.function_count == 4
        assert score.class_count == 1
        assert score.loc == 50

    def test_complexity_score_defaults(self):
        score = ComplexityScore(path="test.py")
        assert score.cyclomatic_complexity == 0
        assert score.max_nesting_depth == 0
        assert score.function_count == 0
        assert score.class_count == 0
        assert score.loc == 0


class TestDirectoryComplexityReport:
    def test_create_report(self):
        score = ComplexityScore(path="a.py", cyclomatic_complexity=3, loc=20)
        report = DirectoryComplexityReport(
            path="/some/dir",
            file_scores=[score],
            total_loc=20,
            avg_complexity=3.0,
            file_count=1,
        )
        assert report.path == "/some/dir"
        assert len(report.file_scores) == 1
        assert report.total_loc == 20
        assert report.file_count == 1

    def test_report_defaults(self):
        report = DirectoryComplexityReport(path="/dir")
        assert report.file_scores == []
        assert report.total_loc == 0
        assert report.avg_complexity == 0.0
        assert report.file_count == 0


class TestCodeComplexityScorer:
    def test_score_simple_code(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(SIMPLE_CODE)
            f.flush()
            path = f.name
        try:
            scorer = CodeComplexityScorer()
            score = scorer.score_file(path)
            assert score.loc > 0
            assert score.function_count >= 2
            assert score.cyclomatic_complexity >= 1
            assert score.max_nesting_depth >= 0
        finally:
            os.unlink(path)

    def test_score_complex_code(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(COMPLEX_CODE)
            f.flush()
            path = f.name
        try:
            scorer = CodeComplexityScorer()
            score = scorer.score_file(path)
            assert score.cyclomatic_complexity > 5
            assert score.function_count >= 5
            assert score.class_count >= 1
            assert score.loc > 20
        finally:
            os.unlink(path)

    def test_score_high_nesting(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(HIGH_NESTING_CODE)
            f.flush()
            path = f.name
        try:
            scorer = CodeComplexityScorer()
            score = scorer.score_file(path)
            assert score.max_nesting_depth >= 4
        finally:
            os.unlink(path)

    def test_score_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("")
            f.flush()
            path = f.name
        try:
            scorer = CodeComplexityScorer()
            score = scorer.score_file(path)
            assert score.loc == 0
            assert score.function_count == 0
            assert score.cyclomatic_complexity <= 1
        finally:
            os.unlink(path)

    def test_score_nonexistent_file(self):
        scorer = CodeComplexityScorer()
        score = scorer.score_file("/nonexistent/path.py")
        assert score.loc == 0

    def test_score_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "a.py"), "w") as f:
                f.write(SIMPLE_CODE)
            with open(os.path.join(tmpdir, "b.py"), "w") as f:
                f.write(COMPLEX_CODE)
            with open(os.path.join(tmpdir, "c.txt"), "w") as f:
                f.write("not python")

            scorer = CodeComplexityScorer()
            report = scorer.score_directory(tmpdir)
            assert report.file_count == 2
            assert report.total_loc > 0
            assert len(report.file_scores) == 2

    def test_score_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scorer = CodeComplexityScorer()
            report = scorer.score_directory(tmpdir)
            assert report.file_count == 0
            assert report.total_loc == 0
            assert report.avg_complexity == 0.0

    def test_score_directory_nonexistent(self):
        scorer = CodeComplexityScorer()
        report = scorer.score_directory("/nonexistent/dir")
        assert report.file_count == 0


class TestSuggestTaskType:
    def test_simple_code_suggests_feature(self):
        scorer = CodeComplexityScorer()
        score = ComplexityScore(
            path="simple.py",
            cyclomatic_complexity=2,
            max_nesting_depth=1,
            function_count=3,
            class_count=0,
            loc=10,
        )
        task_type = scorer.suggest_task_type(score)
        assert task_type == TaskType.FEATURE

    def test_complex_code_suggests_refactor(self):
        scorer = CodeComplexityScorer()
        score = ComplexityScore(
            path="complex.py",
            cyclomatic_complexity=15,
            max_nesting_depth=3,
            function_count=10,
            class_count=3,
            loc=200,
        )
        task_type = scorer.suggest_task_type(score)
        assert task_type == TaskType.REFACTOR

    def test_high_nesting_suggests_bug_fix(self):
        scorer = CodeComplexityScorer()
        score = ComplexityScore(
            path="nested.py",
            cyclomatic_complexity=5,
            max_nesting_depth=6,
            function_count=2,
            class_count=0,
            loc=30,
        )
        task_type = scorer.suggest_task_type(score)
        assert task_type == TaskType.BUG_FIX

    def test_many_branches_suggests_security_fix(self):
        scorer = CodeComplexityScorer()
        score = ComplexityScore(
            path="branches.py",
            cyclomatic_complexity=25,
            max_nesting_depth=4,
            function_count=5,
            class_count=1,
            loc=80,
        )
        task_type = scorer.suggest_task_type(score)
        assert task_type == TaskType.SECURITY_FIX

    def test_moderate_complexity_suggests_refactor(self):
        scorer = CodeComplexityScorer()
        score = ComplexityScore(
            path="moderate.py",
            cyclomatic_complexity=8,
            max_nesting_depth=3,
            function_count=6,
            class_count=1,
            loc=100,
        )
        task_type = scorer.suggest_task_type(score)
        assert task_type == TaskType.REFACTOR


class TestComplexityScorerWithRealFiles:
    def test_score_real_python_file(self):
        extractor_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "src",
            "general_ludd",
            "code_intelligence",
            "extractor.py",
        )
        if not os.path.exists(extractor_path):
            return
        scorer = CodeComplexityScorer()
        score = scorer.score_file(extractor_path)
        assert score.loc > 0
        assert score.function_count >= 1
        assert score.class_count >= 1
