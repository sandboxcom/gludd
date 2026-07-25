"""Tests for git_automation/duplicate_targets.py — Makefile duplicate target scanner."""

from __future__ import annotations

import tempfile
from pathlib import Path

from general_ludd.git_automation.duplicate_targets import (
    check_duplicate_targets,
    extract_targets,
)

MAKEFILE_WITH_DUPLICATES = """\
check-duplicate-targets:
\t@echo "first declaration"

# this is a comment target: foo
.PHONY: check-duplicate-targets

gate: check-duplicate-targets lint

check-duplicate-targets:
\t@echo "second declaration — DUPLICATE"

clean:
\t@echo "clean target"

.PRECIOUS: %.o

lint:  # comment after target
\t@ruff check

SHELL := /bin/bash

check-duplicate-targets:
\t@echo "third declaration — TRIPLICATE"
"""

MAKEFILE_NO_DUPLICATES = """\
check-duplicate-targets:
\t@echo "only one"

gate: check-duplicate-targets lint

clean:
\t@echo "clean target"

lint:
\t@ruff check
"""

MAKEFILE_COMMENTED_TARGETS = """\
# check-duplicate-targets:
# \t@echo "commented out target"

real-target:
\t@echo "real only"

# another: commented
"""

MAKEFILE_WITH_DEPENDENCIES = """\
build: dep-a dep-b
\t@echo "building"

dep-a:
\t@echo "dep a"

dep-b:
\t@echo "dep b"
"""


class TestDuplicateTargetCheck:
    def test_finds_duplicate_top_level_targets(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(MAKEFILE_WITH_DUPLICATES)
            f.flush()
            path = Path(f.name)

        try:
            duplicates = check_duplicate_targets(path)
            dup_names = {d.target for d in duplicates}
            assert "check-duplicate-targets" in dup_names
        finally:
            path.unlink(missing_ok=True)

    def test_ignores_non_makefile_targets(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(MAKEFILE_WITH_DUPLICATES)
            f.flush()
            path = Path(f.name)

        try:
            duplicates = check_duplicate_targets(path)
            dup_names = {d.target for d in duplicates}
            assert ".PHONY" not in dup_names
            assert ".PRECIOUS" not in dup_names
            assert "SHELL" not in dup_names
        finally:
            path.unlink(missing_ok=True)

    def test_returns_list_of_duplicates(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(MAKEFILE_WITH_DUPLICATES)
            f.flush()
            path = Path(f.name)

        try:
            duplicates = check_duplicate_targets(path)
            assert isinstance(duplicates, list)
            assert len(duplicates) >= 1
            dup = next(d for d in duplicates if d.target == "check-duplicate-targets")
            assert isinstance(dup.target, str)
            assert isinstance(dup.count, int)
            assert isinstance(dup.lines, list)
            assert all(isinstance(ln, int) for ln in dup.lines)
        finally:
            path.unlink(missing_ok=True)

    def test_returns_empty_when_no_duplicates(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(MAKEFILE_NO_DUPLICATES)
            f.flush()
            path = Path(f.name)

        try:
            duplicates = check_duplicate_targets(path)
            assert duplicates == []
        finally:
            path.unlink(missing_ok=True)

    def test_counts_per_duplicate_target(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(MAKEFILE_WITH_DUPLICATES)
            f.flush()
            path = Path(f.name)

        try:
            duplicates = check_duplicate_targets(path)
            dup = next(d for d in duplicates if d.target == "check-duplicate-targets")
            assert dup.count == 3
            assert len(dup.lines) == 3
        finally:
            path.unlink(missing_ok=True)

    def test_handles_missing_makefile(self):
        path = Path("/tmp/gludd-nonexistent-makefile-xyz123.mk")
        try:
            duplicates = check_duplicate_targets(path)
            assert duplicates == []
        finally:
            path.unlink(missing_ok=True)

    def test_ignores_commented_targets(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(MAKEFILE_COMMENTED_TARGETS)
            f.flush()
            path = Path(f.name)

        try:
            duplicates = check_duplicate_targets(path)
            dup_names = {d.target for d in duplicates}
            assert "check-duplicate-targets" not in dup_names
            assert "another" not in dup_names
        finally:
            path.unlink(missing_ok=True)

    def test_finds_targets_with_dependencies(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(MAKEFILE_WITH_DEPENDENCIES)
            f.flush()
            path = Path(f.name)

        try:
            targets = extract_targets(path)
            target_names = {t[0] for t in targets}
            assert "build" in target_names
            assert "dep-a" in target_names
            assert "dep-b" in target_names
        finally:
            path.unlink(missing_ok=True)

    def test_extract_targets_returns_name_and_line(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mk", delete=False) as f:
            f.write(MAKEFILE_NO_DUPLICATES)
            f.flush()
            path = Path(f.name)

        try:
            targets = extract_targets(path)
            for name, line_no in targets:
                assert isinstance(name, str)
                assert isinstance(line_no, int)
                assert line_no >= 1
        finally:
            path.unlink(missing_ok=True)
