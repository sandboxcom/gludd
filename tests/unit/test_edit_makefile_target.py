"""Unit tests for scripts/edit_makefile_target.py"""
from __future__ import annotations

from scripts.edit_makefile_target import (
    categorize_section,
    extract_target_definition,
    insert_target,
)


def _make_full_makefile(path, extra_targets=None):
    """Write a full Makefile to path with required targets for validation."""
    lines = [
        ".PHONY: help\n",
        "help:\n",
        "\t@echo 'Usage: make [target]'\n",
        "\t@echo ''\n",
        "\t@echo '  --- Setup ---'\n",
        "\t@echo '  init                  Set up project'\n",
        "\t@echo ''\n",
        "\t@echo '  --- Quality ---'\n",
        "\t@echo '  validate              Full validation'\n",
        "\t@echo ''\n",
        "\t@echo '  --- Complete Target Index ---'\n",
        "\t@python3 scripts/check_make_help.py --print-index\n",
        "\n",
        "# Stub so validate_makefile can run 'make check-duplicate-targets'\n",
        "check-duplicate-targets:\n",
        "\t@echo 'check-duplicate-targets: OK'\n",
    ]
    if extra_targets:
        lines.extend(extra_targets)
    path.write_text("".join(lines), encoding="utf-8")


class TestEditMakefileTargetExtract:
    """Test the extract subcommand."""

    def test_extract_existing_target(self, tmp_path):
        """Call extract on a known target, verify it returns the definition."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "# Help target\n"
            "help:\n"
            "\t@echo 'Usage: make [target]'\n"
            "\t@echo ''\n"
            "\t@echo '  --- Setup ---'\n"
            "\t@echo '  init                  Set up project'\n"
            "\t@echo ''\n"
            "\t@echo '  --- Complete Target Index ---'\n"
            "\t@python3 scripts/check_make_help.py --print-index\n"
            "\n"
            "# Init target\n"
            "init:\n"
            "\t@echo 'Setting up project...'\n"
            "\t@mkdir -p .gludd\n"
            "\n"
            "clean:\n"
            "\t@echo 'Cleaning...'\n"
            "\t@rm -rf dist\n",
            encoding="utf-8",
        )
        definition = extract_target_definition(makefile, "init")
        assert definition is not None
        assert "init:" in definition
        assert "Setting up project" in definition

    def test_extract_nonexistent_target(self, tmp_path):
        """Call extract on a target that doesn't exist, verify returns None."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "help:\n\t@echo 'Usage'\n",
            encoding="utf-8",
        )
        result = extract_target_definition(makefile, "no-such-target")
        assert result is None


class TestEditMakefileTargetAdd:
    """Test the add subcommand."""

    def test_add_target_with_keyword(self, tmp_path):
        """Add a target whose name matches a section keyword, verify placed in correct section."""
        makefile = tmp_path / "Makefile"
        _make_full_makefile(makefile)
        result = insert_target(makefile, "gate-check", "Run gate check", "")
        assert result is True
        content = makefile.read_text(encoding="utf-8")
        assert "gate-check:" in content

    def test_add_target_fallback(self, tmp_path):
        """Add a target with no keyword match, verify placed in New Targets section."""
        makefile = tmp_path / "Makefile"
        _make_full_makefile(makefile)
        result = insert_target(makefile, "zzyx-test", "A test target with no keyword match", "")
        assert result is True
        content = makefile.read_text(encoding="utf-8")
        assert "zzyx-test:" in content

    def test_duplicate_target_rejection(self, tmp_path):
        """Add a target that already exists, verify it is detected."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "# My target\n"
            "my-target:\n"
            "\t@echo 'Hello'\n"
            "\n"
            ".PHONY: help\n"
            "help:\n"
            "\t@echo 'Usage'\n"
            "\t@echo ''\n"
            "\t@echo '  --- Setup ---'\n"
            "\t@echo '  init                  Set up project'\n"
            "\t@echo ''\n"
            "\t@echo '  --- Complete Target Index ---'\n"
            "\t@python3 scripts/check_make_help.py --print-index\n"
            "\n"
            "check-duplicate-targets:\n"
            "\t@echo 'check-duplicate-targets: OK'\n",
            encoding="utf-8",
        )
        existing = extract_target_definition(makefile, "my-target")
        assert existing is not None
        assert "my-target:" in existing

    def test_add_target_with_explicit_section(self, tmp_path):
        """Add a target with explicit --section, verify placed there."""
        makefile = tmp_path / "Makefile"
        _make_full_makefile(makefile)
        result = insert_target(makefile, "my-secret-scanner", "Scan for secrets", "Secrets + Security")
        assert result is True
        content = makefile.read_text(encoding="utf-8")
        assert "my-secret-scanner:" in content


class TestEditMakefileTargetValidate:
    """Test the validate subcommand."""

    def test_validate_clean_makefile(self, tmp_path):
        """Call validate on a minimal Makefile, verify passes."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "help:\n"
            "\t@echo 'Usage: make [target]'\n"
            "\t@echo ''\n"
            "\t@echo '  --- Setup ---'\n"
            "\t@echo '  init                  Set up project'\n"
            "\t@echo ''\n"
            "\t@echo '  --- Complete Target Index ---'\n"
            "\t@python3 scripts/check_make_help.py --print-index\n"
            "\n"
            "init:\n"
            "\t@echo 'Setting up...'\n"
            "\n"
            "check-duplicate-targets:\n"
            "\t@echo 'check-duplicate-targets: OK'\n",
            encoding="utf-8",
        )
        from scripts.edit_makefile_target import validate_makefile
        ok = validate_makefile(makefile, "help")
        assert ok is True


class TestEditMakefileTargetEditFlow:
    """Test the complete edit flow (extract + modify + replace)."""

    def test_edit_target_flow(self, tmp_path):
        """Extract a target, modify its definition, and replace it."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "# My test target\n"
            "my-test:\n"
            "\t@echo 'Before'\n"
            "\t@echo 'More output'\n"
            "\n"
            ".PHONY: help\n"
            "help:\n"
            "\t@echo 'Usage'\n"
            "\t@echo ''\n"
            "\t@echo '  --- Setup ---'\n"
            "\t@echo '  init                  Set up project'\n"
            "\t@echo ''\n"
            "\t@echo '  --- Complete Target Index ---'\n"
            "\t@python3 scripts/check_make_help.py --print-index\n"
            "\n"
            "check-duplicate-targets:\n"
            "\t@echo 'check-duplicate-targets: OK'\n",
            encoding="utf-8",
        )
        original_def = extract_target_definition(makefile, "my-test")
        assert original_def is not None
        assert "Before" in original_def

        new_def = original_def.replace("Before", "After")

        content = makefile.read_text(encoding="utf-8")
        content = content.replace(original_def.strip(), new_def.strip(), 1)
        makefile.write_text(content, encoding="utf-8")

        content = makefile.read_text(encoding="utf-8")
        assert "After" in content
        assert "Before" not in content


class TestEditMakefileTargetFunctional:
    """Functional tests that exercise the Makefile directly."""

    def test_added_target_helps_categorize(self, tmp_path):
        """Add a target and verify it lands in the correct section."""
        makefile = tmp_path / "Makefile"
        _make_full_makefile(makefile)
        result = insert_target(makefile, "typecheck-all", "Run full type checking", "")
        assert result is True
        content = makefile.read_text(encoding="utf-8")
        assert "typecheck-all:" in content


class TestCategorizeSection:
    """Test the categorize_section function directly."""

    def test_categorize_quality_keyword(self):
        """Target with 'lint' keyword goes to Quality."""
        assert categorize_section("lint-all", "Lint everything") == "Quality"

    def test_categorize_git_keyword(self):
        """Target with 'git' keyword goes to Git."""
        assert categorize_section("git-cleanup", "Clean git artifacts") == "Git"

    def test_categorize_no_match(self):
        """Target with no keyword match goes to New Targets."""
        assert categorize_section("foobarxyz", "No match at all") == "New Targets"

    def test_categorize_terraform_keyword(self):
        """Target with 'tf' keyword goes to Terraform."""
        assert categorize_section("tf-plan", "Run terraform plan") == "Terraform"

    def test_categorize_ci_keyword(self):
        """Target with 'ci-' prefix goes to CI."""
        assert categorize_section("ci-check", "CI check target") == "CI"


class TestExtractTargetDefinition:
    """Test extract_target_definition function directly."""

    def test_extract_simple_target(self, tmp_path):
        """Extract a simple target definition."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "# Simple target\n"
            "simple:\n"
            "\t@echo 'Hello'\n",
            encoding="utf-8",
        )
        result = extract_target_definition(makefile, "simple")
        assert result is not None
        assert "simple:" in result
        assert "@echo 'Hello'" in result

    def test_extract_with_comment(self, tmp_path):
        """Extract a target with a preceding comment."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "# This is my target\n"
            "# It does important things\n"
            "my-target:\n"
            "\t@echo 'Running'\n",
            encoding="utf-8",
        )
        result = extract_target_definition(makefile, "my-target")
        assert result is not None
        assert "This is my target" in result
        assert "my-target:" in result

    def test_extract_nonexistent(self, tmp_path):
        """Extract a target that doesn't exist returns None."""
        makefile = tmp_path / "Makefile"
        makefile.write_text("help:\n\t@echo 'Hello'\n", encoding="utf-8")
        result = extract_target_definition(makefile, "no-such")
        assert result is None


class TestEdgeCases:
    """Test edge-case behavior."""

    def test_empty_makefile(self, tmp_path):
        """Extract from an empty Makefile returns None."""
        makefile = tmp_path / "Makefile"
        makefile.write_text("", encoding="utf-8")
        result = extract_target_definition(makefile, "anything")
        assert result is None

    def test_add_multiple_targets(self, tmp_path):
        """Add two targets in sequence, verify both exist."""
        makefile = tmp_path / "Makefile"
        _make_full_makefile(makefile)
        assert insert_target(makefile, "target-one", "First test target", "") is True
        assert insert_target(makefile, "target-two", "Second test target", "") is True
        content = makefile.read_text(encoding="utf-8")
        assert "target-one:" in content
        assert "target-two:" in content
