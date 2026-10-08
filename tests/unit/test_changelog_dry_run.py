"""Tests for changelog validation during a pre-tag release dry run."""

from scripts.check_changelog_accuracy import (
    candidate_documents_complete,
    comparison_refs,
)


def test_uncut_candidate_compares_latest_tag_to_head() -> None:
    tags = ["v0.1.0-beta.3", "v0.1.0-beta.2"]

    assert comparison_refs(tags, "v0.1.0-beta.4") == (
        "v0.1.0-beta.3",
        "HEAD",
    )


def test_existing_tag_compares_the_prior_tag_to_that_tag() -> None:
    tags = ["v0.1.0-beta.4", "v0.1.0-beta.3"]

    assert comparison_refs(tags, "v0.1.0-beta.4") == (
        "v0.1.0-beta.3",
        "v0.1.0-beta.4",
    )


def test_candidate_documents_require_changelog_entries_and_matching_notes() -> None:
    changelog = "## [0.1.0-beta.4]\n- release matrix\n\n## [0.1.0-beta.3]\n"
    notes = "# v0.1.0-beta.4 release notes\n"

    assert candidate_documents_complete(changelog, notes, "0.1.0-beta.4")
    assert not candidate_documents_complete(
        "## [0.1.0-beta.4]\n",
        notes,
        "0.1.0-beta.4",
    )
    assert not candidate_documents_complete(changelog, "# unrelated\n", "0.1.0-beta.4")
