"""Unit tests for AC release pipeline integrity scripts."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import check_dependency_pinning as dependency_pinning
from check_asset_retention import (
    BINARY_PATTERNS,
    SBOM_PATTERNS,
    asset_matches_patterns,
    check_retention_for_releases,
)
from check_changelog_accuracy import (
    crossref_changelog_against_commits,
    find_missing_commits,
    find_phantom_entries,
    find_version_section,
    parse_changelog_entries,
)
from check_dependency_pinning import (
    check_dependency_pinning,
    check_lockfile_staleness,
    find_unpinned_deps,
    parse_lockfile_deps,
)
from check_multiplatform_consistency import (
    PLATFORMS,
    check_binary_size_consistency,
    check_checksum_entries,
    check_platform_coverage,
)
from check_prerelease_flag import expected_prerelease
from check_provenance_attestation import (
    check_provenance_attestation,
    extract_builder_id,
    parse_provenance_file,
    verify_provenance_digest,
)
from check_release_audit_trail import (
    get_audit_dir,
    validate_audit_entry,
    validate_audit_file,
)
from check_rollback_procedure import check_rollback_section, required_rollback_fields
from check_runbook_currency import (
    check_targets_exist,
    check_version_in_runbook,
    extract_make_targets,
    find_missing_targets,
    parse_runbook_date,
)
from check_sbom_freshness import get_tag_timestamp
from check_tag_immutability import ci_green_for_sha, tag_has_artifacts
from check_tag_signing import classify_result, verify_tag
from check_version_bump_atomicity import (
    check_atomicity,
    extract_version_from_changelog,
    extract_version_from_init,
    extract_version_from_readme,
    extract_version_from_toml,
    extract_versions,
)
from generate_release_notes import COMMIT_CATEGORIES, categorize_commits, find_prev_tag, format_notes
from validate_release_checksums import parse_checksums
from verify_container_push import try_crane, try_docker, try_skopeo


class TestCheckTagImmutability:
    """AC003: Tag immutability — CI green guard."""

    def test_ci_green_for_sha_no_gh(self, monkeypatch):
        """When gh CLI is unavailable, returns False (fail-closed)."""

        def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("gh not found")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        assert ci_green_for_sha("abc123") is False

    def test_ci_green_for_sha_timeout(self, monkeypatch):
        """When gh times out, returns False (fail-closed)."""

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("gh", 30)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        assert ci_green_for_sha("abc123") is False

    def test_ci_green_for_sha_success(self, monkeypatch):
        """Returns True for GREEN CI verdict."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout='[{"conclusion": "success"}]',
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert ci_green_for_sha("abc123") is True

    def test_ci_green_for_sha_failure(self, monkeypatch):
        """Returns False for RED CI verdict."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout='[{"conclusion": "failure"}]',
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert ci_green_for_sha("abc123") is False

    def test_ci_green_for_sha_empty(self, monkeypatch):
        """Returns False when no runs found."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="[]",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert ci_green_for_sha("abc123") is False

    def test_ci_green_for_sha_gh_error(self, monkeypatch):
        """Returns False when gh exits non-zero."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=1,
                stdout="",
                stderr="error",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert ci_green_for_sha("abc123") is False

    def test_ci_green_for_sha_bad_json(self, monkeypatch):
        """Returns False when gh output is not valid JSON."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="not json",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert ci_green_for_sha("abc123") is False

    def test_tag_has_artifacts_no_gh(self, monkeypatch):
        """Returns False when gh CLI unavailable."""

        def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("gh not found")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        assert tag_has_artifacts("v1.0.0") is False

    def test_tag_has_artifacts_timeout(self, monkeypatch):
        """Returns False on timeout."""

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("gh", 30)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        assert tag_has_artifacts("v1.0.0") is False

    def test_tag_has_artifacts_with_assets(self, monkeypatch):
        """Returns True when release has assets and is not draft."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="false\n3\n",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert tag_has_artifacts("v1.0.0") is True

    def test_tag_has_artifacts_draft(self, monkeypatch):
        """Returns False when release is a draft."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="true\n3\n",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert tag_has_artifacts("v1.0.0") is False

    def test_tag_has_artifacts_zero_assets(self, monkeypatch):
        """Returns False when release has 0 assets."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="false\n0\n",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert tag_has_artifacts("v1.0.0") is False

    def test_tag_has_artifacts_gh_error(self, monkeypatch):
        """Returns False when gh exits non-zero."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=1,
                stdout="",
                stderr="error",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert tag_has_artifacts("v1.0.0") is False


class TestCheckPrereleaseFlag:
    """AC005: prerelease-flag-vs-tag-shape."""

    def test_expected_prerelease_alpha(self):
        assert expected_prerelease("v0.1.0-alpha.1") is True

    def test_expected_prerelease_beta(self):
        assert expected_prerelease("v0.1.0-beta.3") is True

    def test_expected_prerelease_rc(self):
        assert expected_prerelease("v2.0.0-rc.1") is True

    def test_expected_prerelease_dev(self):
        assert expected_prerelease("v0.1.0-dev") is True

    def test_expected_prerelease_pre(self):
        assert expected_prerelease("v0.1.0-pre") is True

    def test_expected_prerelease_stable(self):
        assert expected_prerelease("v1.2.3") is False

    def test_expected_prerelease_stable_v_prefix(self):
        assert expected_prerelease("v5.0.0") is False

    def test_expected_prerelease_unknown_conservative(self):
        assert expected_prerelease("v1.2.3-snapshot") is True

    def test_expected_prerelease_bare_semver(self):
        assert expected_prerelease("1.0.0") is True


class TestCheckSbomFreshness:
    """AC007: sbom-freshness."""

    def test_get_tag_timestamp_valid(self, monkeypatch):
        def mock_run(args, **kwargs):
            result = subprocess.CompletedProcess(args, returncode=0, stdout="1720000000", stderr="")
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert get_tag_timestamp("v1.0.0") == 1720000000

    def test_get_tag_timestamp_empty(self, monkeypatch):
        def mock_run(args, **kwargs):
            result = subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert get_tag_timestamp("v1.0.0") == 0

    def test_get_tag_timestamp_non_integer(self, monkeypatch):
        def mock_run(args, **kwargs):
            result = subprocess.CompletedProcess(args, returncode=0, stdout="not-a-number", stderr="")
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert get_tag_timestamp("v1.0.0") == 0

    def test_get_tag_timestamp_git_error(self, monkeypatch):
        def mock_run(args, **kwargs):
            result = subprocess.CompletedProcess(args, returncode=128, stdout="", stderr="fatal: no tag")
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert get_tag_timestamp("nonexistent") == 0


class TestVerifyContainerPush:
    """AC008: container-push-verification."""

    def test_try_skopeo_unavailable(self, monkeypatch):
        def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("skopeo not found")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        ok, output = try_skopeo("registry.example.com/image:tag")
        assert ok is False
        assert "unavailable" in output

    def test_try_skopeo_timeout(self, monkeypatch):
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("skopeo", 30)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        ok, _output = try_skopeo("registry.example.com/image:tag")
        assert ok is False

    def test_try_skopeo_success(self, monkeypatch):
        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(args, returncode=0, stdout='{"Digest":"sha256:abc"}')
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        ok, output = try_skopeo("registry.example.com/image:tag")
        assert ok is True
        assert "sha256:abc" in output

    def test_try_skopeo_failure(self, monkeypatch):
        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="not found")
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        ok, _ = try_skopeo("registry.example.com/image:tag")
        assert ok is False

    def test_try_crane_unavailable(self, monkeypatch):
        def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("crane not found")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        ok, output = try_crane("registry.example.com/image:tag")
        assert ok is False
        assert "unavailable" in output

    def test_try_crane_success(self, monkeypatch):
        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args, returncode=0, stdout='{"mediaType":"application/vnd.docker.distribution.manifest.v2+json"}'
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        ok, _output = try_crane("registry.example.com/image:tag")
        assert ok is True

    def test_try_docker_unavailable(self, monkeypatch):
        def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("docker not found")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        ok, output = try_docker("registry.example.com/image:tag")
        assert ok is False
        assert "unavailable" in output

    def test_try_docker_success(self, monkeypatch):
        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(args, returncode=0, stdout='{"schemaVersion":2}')
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        ok, _output = try_docker("registry.example.com/image:tag")
        assert ok is True


class TestCheckRollbackProcedure:
    """AC009: release-rollback."""

    def test_rollback_section_found_with_fields(self):
        content = (
            "## Rollback\nTo roll back to a target, use the container pin and binary.\nVerify config compatibility.\n"
        )
        errors, _ = check_rollback_section(content)
        assert len(errors) == 0

    def test_rollback_section_found_with_version(self):
        content = (
            "## Rollback\n"
            "Target: v0.1.0-beta.2\n"
            "Container: registry/gludd:v0.1.0-beta.2\n"
            "Binary: https://github.com/.../gludd\n"
            "Config: compatible\n"
            "Compatible with 0.1.0-beta.3\n"
        )
        errors, _ = check_rollback_section(content, tag="v0.1.0-beta.3")
        assert len(errors) == 0

    def test_rollback_section_missing_version(self):
        content = (
            "## Rollback\n"
            "Target: v0.1.0-beta.2\n"
            "Container: registry/gludd:v0.1.0-beta.2\n"
            "Binary: https://github.com/.../gludd\n"
            "Config: compatible\n"
        )
        errors, _ = check_rollback_section(content, tag="v0.1.0-beta.3")
        assert len(errors) == 1
        assert "version" in errors[0].lower()

    def test_rollback_section_missing_header(self):
        content = "No rollback section here."
        errors, _ = check_rollback_section(content)
        assert len(errors) == 1
        assert "no '## rollback'" in errors[0].lower()

    def test_rollback_section_missing_target_field(self):
        content = "## Rollback\nOnly mentions container and binary and config."
        errors, _ = check_rollback_section(content)
        assert len(errors) == 1
        assert "target" in errors[0].lower()

    def test_rollback_section_missing_multiple_fields(self):
        content = "## Rollback\nThis section has none of the required items."
        errors, _ = check_rollback_section(content)
        assert len(errors) == 1
        assert any(f in errors[0] for f in required_rollback_fields)

    def test_rollback_empty_content(self):
        errors, _ = check_rollback_section("")
        assert len(errors) == 1
        assert "no '## rollback'" in errors[0].lower()


class TestValidateReleaseChecksums:
    """AC006: Checksum validation."""

    def test_parse_checksums_empty(self):
        assert parse_checksums("") == {}

    def test_parse_checksums_comments_only(self):
        assert parse_checksums("# comment\n# another") == {}

    def test_parse_checksums_standard_format(self):
        content = (
            "abc123def4567890abc123def4567890abc123def4567890abc123def4567890  file1.tar.gz\n"
            "fed456cba7890abc123def4567890abcfed456cba7890abc123def4567890abc  file2.exe\n"
        )
        entries = parse_checksums(content)
        assert entries == {
            "file1.tar.gz": "abc123def4567890abc123def4567890abc123def4567890abc123def4567890",
            "file2.exe": "fed456cba7890abc123def4567890abcfed456cba7890abc123def4567890abc",
        }

    def test_parse_checksums_binary_prefix(self):
        content = "abc123def4567890abc123def4567890abc123def4567890abc123def4567890 *binary.tar.gz\n"
        entries = parse_checksums(content)
        assert entries == {"binary.tar.gz": "abc123def4567890abc123def4567890abc123def4567890abc123def4567890"}

    def test_parse_checksums_skips_short_entries(self):
        """Short checksums (non-SHA256) are skipped."""
        content = "abcd1234 short.hash\n"
        assert parse_checksums(content) == {}

    def test_parse_checksums_skips_non_hex(self):
        """Non-hex 'checksums' are skipped."""
        content = "zzzz1234zzzz5678zzzz1234zzzz5678zzzz1234zzzz5678zzzz1234zzzz5678  bad.bin\n"
        assert parse_checksums(content) == {}

    def test_parse_checksums_multiline_with_blanks(self):
        content = (
            "\n"
            "abc123def4567890abc123def4567890abc123def4567890abc123def4567890  a.bin\n"
            "\n"
            "# ignore this\n"
            "fed456cba7890abc123def4567890abcfed456cba7890abc123def4567890abc  b.bin\n"
            "\n"
        )
        entries = parse_checksums(content)
        assert entries == {
            "a.bin": "abc123def4567890abc123def4567890abc123def4567890abc123def4567890",
            "b.bin": "fed456cba7890abc123def4567890abcfed456cba7890abc123def4567890abc",
        }


class TestCheckTagSigning:
    """AC017: git-tag-signing — GPG signature verification."""

    def test_verify_tag_signed(self, monkeypatch):
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, returncode=0, stdout="Good signature", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_run)
        returncode, stderr = verify_tag("v1.0.0")
        assert returncode == 0
        assert stderr == ""

    def test_verify_tag_unsigned(self, monkeypatch):
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="error: no signature found")

        monkeypatch.setattr(subprocess, "run", mock_run)
        returncode, stderr = verify_tag("v1.0.0")
        assert returncode == 1
        assert "no signature" in stderr

    def test_verify_tag_expired_key(self, monkeypatch):
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="gpg: key expired")

        monkeypatch.setattr(subprocess, "run", mock_run)
        returncode, stderr = verify_tag("v1.0.0")
        assert returncode == 1
        assert "gpg:" in stderr.lower()

    def test_classify_pass(self):
        result = classify_result("v1.0.0", 0, "")
        assert result["status"] == "PASS"
        assert result["exit_code"] == 0

    def test_classify_fail_unsigned(self):
        result = classify_result("v1.0.0", 1, "error: no signature found")
        assert result["status"] == "FAIL"
        assert result["exit_code"] == 1
        assert "not GPG-signed" in result["message"]

    def test_classify_fail_key_expired(self):
        result = classify_result("v1.0.0", 1, "gpg: key expired and cannot verify")
        assert result["status"] == "FAIL"
        assert result["exit_code"] == 1
        assert "cannot be verified" in result["message"]

    def test_classify_inconclusive(self):
        result = classify_result("v1.0.0", 1, "some unknown git error")
        assert result["status"] == "INCONCLUSIVE"
        assert result["exit_code"] == 2


class TestGenerateReleaseNotes:
    """AC018: release-notes-automation — conventional-commit categorization."""

    def test_find_prev_tag_has_previous(self):
        assert find_prev_tag(["v2.0.0", "v1.0.0", "v0.1.0"], "v2.0.0") == "v1.0.0"

    def test_find_prev_tag_first_release(self):
        assert find_prev_tag(["v1.0.0", "v0.1.0"], "v1.0.0") == "v0.1.0"

    def test_find_prev_tag_only_tag(self):
        assert find_prev_tag(["v1.0.0"], "v1.0.0") is None

    def test_find_prev_tag_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            find_prev_tag(["v2.0.0", "v1.0.0"], "v3.0.0")

    def test_categorize_commits_feat_and_fix(self):
        commits = ["abc123 feat: add login page", "def456 fix: crash on null pointer"]
        categories = categorize_commits(commits)
        assert len(categories["feat"]) == 1
        assert len(categories["fix"]) == 1
        assert len(categories["other"]) == 0

    def test_categorize_commits_other_non_conventional(self):
        commits = ["abc123 Merge pull request", "def456 random text without colon"]
        categories = categorize_commits(commits)
        assert len(categories["other"]) == 2
        assert sum(len(categories[k]) for k in categories if k != "other") == 0

    def test_categorize_commits_scoped(self):
        commits = ["abc123 feat(api)!: breaking change to endpoint"]
        categories = categorize_commits(commits)
        assert len(categories["feat"]) == 1
        assert categories["feat"][0] == "breaking change to endpoint"

    def test_format_notes_output(self):
        categories = {k: [] for k in COMMIT_CATEGORIES}
        categories["feat"] = ["add login"]
        output = format_notes(categories, "1.0.0", "v0.1.0", contributors="Alice <alice@example.com>")
        assert "## What's Changed in 1.0.0" in output
        assert "### Features" in output
        assert "- add login" in output
        assert "**Full Changelog**" in output


class TestCheckAssetRetention:
    """AC019: asset-retention-policy — tiered asset pruning."""

    def test_asset_matches_patterns_match_binary(self):
        assert asset_matches_patterns("linux-amd64.tar.gz", BINARY_PATTERNS) is True

    def test_asset_matches_patterns_no_match(self):
        assert asset_matches_patterns("README.md", BINARY_PATTERNS) is False

    def test_asset_matches_patterns_sbom(self):
        assert asset_matches_patterns("myproject-sbom.json", SBOM_PATTERNS) is True

    def test_asset_matches_patterns_case_insensitive(self):
        assert asset_matches_patterns("gludd-Windows.exe", BINARY_PATTERNS) is True

    def test_retention_keep_all_first_3(self):
        releases = [
            {"tagName": "v3.0.0"},
            {"tagName": "v2.0.0"},
            {"tagName": "v1.0.0"},
        ]

        def get_assets(_tag):
            return [{"name": "extra-file.txt"}, {"name": "notes.md"}]

        violations = check_retention_for_releases(releases, get_assets)
        assert len(violations) == 0

    def test_retention_binaries_only_idx_3(self):
        releases = [
            {"tagName": "v4.0.0"},
            {"tagName": "v3.0.0"},
            {"tagName": "v2.0.0"},
            {"tagName": "v1.0.0"},
        ]

        def get_assets(tag):
            if tag == "v1.0.0":
                return [{"name": "extra-config.json"}]
            return [{"name": "linux-amd64.tar.gz"}]

        violations = check_retention_for_releases(releases, get_assets)
        assert len(violations) == 1
        assert "v1.0.0" in violations[0]

    def test_retention_sbom_only_after_10(self):
        releases = [{"tagName": f"v{i}.0.0"} for i in range(12, 0, -1)]

        def get_assets(tag):
            if tag == "v1.0.0":
                return [{"name": "linux-amd64.tar.gz"}]
            return [{"name": "sbom.json"}]

        violations = check_retention_for_releases(releases, get_assets)
        assert len(violations) == 1
        assert "v1.0.0" in violations[0]

    def test_retention_all_pass(self):
        releases = [{"tagName": f"v{i}.0.0"} for i in range(8, 0, -1)]

        def get_assets(_tag):
            return [{"name": "linux-amd64.tar.gz"}, {"name": "sbom.json"}]

        violations = check_retention_for_releases(releases, get_assets)
        assert len(violations) == 0


class TestCheckReleaseAuditTrail:
    """AC020: release-audit-trail — audit file completeness."""

    def test_validate_audit_entry_complete(self):
        data = {
            "tag": "v1.0.0",
            "tag_sha": "abc123",
            "ci_run_id": "456",
            "ci_conclusion": "success",
            "artifacts": ["a.tar.gz"],
            "release_cut_timestamp": "2024-01-01T00:00:00Z",
            "gate_status": "PASS",
            "changelog_range": "v0.9.0..v1.0.0",
            "signing_key_fingerprint": "ABCD1234",
            "operator": "ci-bot",
        }
        assert validate_audit_entry(data) == []

    def test_validate_audit_entry_missing_field(self):
        data = {"tag": "v1.0.0", "tag_sha": "abc123"}
        missing = validate_audit_entry(data)
        assert "tag_sha" not in missing
        assert "ci_run_id" in missing

    def test_validate_audit_entry_null_field(self):
        data = {"tag": None, "tag_sha": "abc123"}
        missing = validate_audit_entry(data)
        assert "tag" in missing

    def test_validate_audit_entry_multiple_missing(self):
        data = {"tag": "v1.0.0"}
        missing = validate_audit_entry(data)
        assert len(missing) > 1
        assert "tag_sha" in missing

    def test_get_audit_dir_path(self):
        result = get_audit_dir(script_root="/foo")
        assert result == Path("/foo/docs/releases")

    def test_validate_audit_file_bad_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json")
            f.flush()
            ok, reason = validate_audit_file(f.name)
        os.unlink(f.name)
        assert ok is False
        assert reason is not None

    def test_validate_audit_file_missing_fields(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"tag": "v1.0.0"}, f)
            f.flush()
            ok, reason = validate_audit_file(f.name)
        os.unlink(f.name)
        assert ok is False
        assert "missing fields" in reason


class TestCheckMultiplatformConsistency:
    """AC010: multi-platform-consistency — binaries for all platforms, similar sizes."""

    def test_all_platforms_present_valid_sizes(self):
        assets = [
            {"name": "gludd-linux-amd64.tar.gz", "size": 5000},
            {"name": "gludd-linux-arm64.tar.gz", "size": 4800},
            {"name": "gludd-macos-amd64.tar.gz", "size": 5200},
            {"name": "gludd-macos-arm64.tar.gz", "size": 4900},
        ]
        passed, found, missing, _issues = check_platform_coverage(assets)
        assert passed is True
        assert len(found) == 4
        assert len(missing) == 0

    def test_missing_platform_reported(self):
        assets = [
            {"name": "gludd-linux-amd64.tar.gz", "size": 5000},
            {"name": "gludd-macos-amd64.tar.gz", "size": 5000},
        ]
        passed, _found, missing, _issues = check_platform_coverage(assets)
        assert passed is False
        assert "linux-arm64" in missing

    def test_oversized_binary_warns_but_passes(self):
        assets = [
            {"name": "gludd-linux-amd64.tar.gz", "size": 5000},
            {"name": "gludd-linux-arm64.tar.gz", "size": 5000},
            {"name": "gludd-macos-amd64.tar.gz", "size": 20000},
            {"name": "gludd-macos-arm64.tar.gz", "size": 5000},
        ]
        passed, _found, _missing, issues = check_platform_coverage(assets)
        assert passed is True
        assert any("deviates from mean" in i for i in issues)

    def test_min_platforms_default_four(self):
        assets = [
            {"name": "gludd-linux-amd64.tar.gz", "size": 5000},
        ]
        passed, _found, _missing, issues = check_platform_coverage(assets)
        assert passed is False
        assert any("1/4 required" in i for i in issues)

    def test_empty_assets_fails(self):
        passed, found, missing, _issues = check_platform_coverage([])
        assert passed is False
        assert len(found) == 0
        assert len(missing) == len(PLATFORMS)

    def test_binary_size_consistency_all_similar(self):
        sizes = {"linux-amd64": 5000, "linux-arm64": 4800, "macos-amd64": 5200, "macos-arm64": 5100}
        passed, issues = check_binary_size_consistency(sizes)
        assert passed is True
        assert len(issues) == 0

    def test_binary_size_one_deviant(self):
        sizes = {"linux-amd64": 5000, "linux-arm64": 5000, "macos-amd64": 20000, "macos-arm64": 5000}
        passed, issues = check_binary_size_consistency(sizes)
        assert passed is True
        assert len(issues) == 1
        assert "deviates from mean" in issues[0]

    def test_checksum_entries_all_covered(self):
        assets = [{"name": "a.tar.gz"}, {"name": "b.exe"}]
        content = (
            "abc123def4567890abc123def4567890abc123def4567890abc123def4567890  a.tar.gz\n"
            "fed456cba7890abc123def4567890abcfed456cba7890abc123def4567890abc  b.exe\n"
        )
        passed, issues = check_checksum_entries(assets, content)
        assert passed is True
        assert len(issues) == 0

    def test_checksum_entry_missing(self):
        assets = [{"name": "a.tar.gz"}, {"name": "b.exe"}]
        content = "abc123def4567890abc123def4567890abc123def4567890abc123def4567890  a.tar.gz\n"
        passed, issues = check_checksum_entries(assets, content)
        assert passed is False
        assert any("b.exe" in i for i in issues)


class TestCheckProvenanceAttestation:
    """AC011: provenance-attestation — SLSA provenance for release artifacts."""

    def test_valid_provenance_found(self):
        assets = [
            {"name": "gludd-linux-amd64"},
            {"name": "gludd-linux-amd64.build.provenance"},
        ]
        passed, prov_count, _bin_count = check_provenance_attestation(assets)
        assert passed is True
        assert prov_count == 1

    def test_no_provenance_fails(self):
        assets = [
            {"name": "gludd-linux-amd64"},
            {"name": "gludd-macos-amd64"},
        ]
        passed, prov_count, _bin_count = check_provenance_attestation(assets)
        assert passed is False
        assert prov_count == 0

    def test_multiple_provenance_files(self):
        assets = [
            {"name": "gludd-linux-amd64"},
            {"name": "gludd-linux-amd64.build.provenance"},
            {"name": "gludd-macos-amd64.attestation.json"},
        ]
        passed, prov_count, _bin_count = check_provenance_attestation(assets)
        assert passed is True
        assert prov_count == 2

    def test_parse_valid_provenance_json(self):
        content = json.dumps({"subject": [{"name": "gludd", "digest": {"sha256": "abc"}}]})
        result = parse_provenance_file(content)
        assert isinstance(result, dict)
        assert "subject" in result

    def test_parse_invalid_json_returns_none(self):
        assert parse_provenance_file("not json") is None

    def test_verify_digest_match(self):
        prov = {"subject": [{"name": "gludd", "digest": {"sha256": "abc123"}}]}
        assert verify_provenance_digest(prov, "abc123") is True

    def test_verify_digest_mismatch(self):
        prov = {"subject": [{"name": "gludd", "digest": {"sha256": "abc123"}}]}
        assert verify_provenance_digest(prov, "wrong") is False

    def test_extract_builder_id_present(self):
        prov = {
            "builder": {
                "id": "https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_container_slsa3.yml@refs/tags/v1.9.0"
            }
        }
        assert (
            extract_builder_id(prov)
            == "https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_container_slsa3.yml@refs/tags/v1.9.0"
        )

    def test_extract_builder_id_missing(self):
        assert extract_builder_id({}) is None
        assert extract_builder_id({"subject": []}) is None


class TestCheckDependencyPinning:
    """AC012: dependency-pinning — exact lock resolution and stale-lock detection."""

    def test_all_pinned_passes(self):
        content = '[project]\ndependencies = [\n  "requests==2.31.0",\n  "click==8.1.7",\n]\n'
        passed, violations = check_dependency_pinning(content)
        assert passed is True
        assert len(violations) == 0

    def test_range_dep_passes_when_lockfile_satisfies_it(self):
        content = '[project]\ndependencies = [\n  "requests>=2.31.0",\n  "click==8.1.7",\n]\n'
        lockfile_deps = {"requests": "2.32.0", "click": "8.1.7"}
        violations = find_unpinned_deps(content, lockfile_deps)
        assert violations == []

    def test_empty_pyproject_passes(self):
        passed, _violations = check_dependency_pinning("")
        assert passed is True

    def test_non_dependencies_section_skipped(self):
        content = (
            "[project]\n"
            'dependencies = [\n  "requests==2.31.0",\n]\n\n'
            "[tool.uv]\n"
            'dev-dependencies = [\n  "pytest>=7.0",\n]\n'
        )
        passed, _violations = check_dependency_pinning(content)
        assert passed is True

    def test_parse_lockfile_deps_valid(self):
        content = (
            '[[package]]\nname = "requests"\nversion = "2.31.0"\n\n[[package]]\nname = "click"\nversion = "8.1.7"\n'
        )
        deps = parse_lockfile_deps(content)
        assert deps == {"requests": ("2.31.0",), "click": ("8.1.7",)}

    def test_parse_lockfile_preserves_marker_split_versions(self):
        content = (
            '[[package]]\nname = "ansible-core"\nversion = "2.19.11"\n\n'
            '[[package]]\nname = "ansible-core"\nversion = "2.21.3"\n'
        )
        assert parse_lockfile_deps(content) == {
            "ansible-core": ("2.19.11", "2.21.3"),
        }

    def test_marker_split_requirements_accept_matching_locked_versions(self):
        content = (
            "[project]\n"
            "dependencies = [\n"
            '  "ansible-core>=2.19.11,<2.20; python_version < \'3.12\'",\n'
            '  "ansible-core>=2.21.2,<2.22; python_version >= \'3.12\'",\n'
            "]\n"
        )
        violations = find_unpinned_deps(
            content,
            {"ansible-core": ("2.19.11", "2.21.3")},
        )
        assert violations == []

    def test_parse_lockfile_deps_empty(self):
        assert parse_lockfile_deps("") == {}

    def test_find_unpinned_deps_range_in_prod(self):
        content = '[project]\ndependencies = [\n  "requests~=2.31",\n  "click==8.1.7",\n]\n'
        lockfile_deps = {"requests": "2.31.0", "click": "8.1.7"}
        violations = find_unpinned_deps(content, lockfile_deps)
        assert violations == []

    def test_find_unpinned_deps_rejects_incompatible_lock_version(self):
        content = '[project]\ndependencies = [\n  "requests>=2.31.0",\n]\n'
        violations = find_unpinned_deps(content, {"requests": "2.30.0"})
        assert violations == ["requests>=2.31.0: locked 2.30.0 does not satisfy >=2.31.0"]

    def test_find_unpinned_deps_rejects_missing_lock_entry(self):
        content = '[project]\ndependencies = [\n  "requests>=2.31.0",\n]\n'
        violations = find_unpinned_deps(content, {})
        assert violations == ["requests: not in lockfile"]

    def test_find_unpinned_deps_all_cross_referenced(self):
        content = '[project]\ndependencies = [\n  "requests==2.31.0",\n  "click==8.1.7",\n]\n'
        lockfile_deps = {"requests": "2.31.0", "click": "8.1.7"}
        violations = find_unpinned_deps(content, lockfile_deps)
        assert len(violations) == 0

    def test_check_lockfile_staleness_lockfile_newer(self, tmp_path):
        lockfile = tmp_path / "uv.lock"
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("")
        lockfile.write_text("")
        import time

        time.sleep(0.01)
        lockfile.touch()
        assert check_lockfile_staleness(lockfile, pyproject) is False

    def test_check_lockfile_staleness_lockfile_older(self, tmp_path):
        lockfile = tmp_path / "uv.lock"
        pyproject = tmp_path / "pyproject.toml"
        lockfile.write_text("")
        import time

        time.sleep(0.01)
        pyproject.write_text("")
        assert check_lockfile_staleness(lockfile, pyproject) is True

    def test_legacy_range_scanner_reports_production_ranges(self):
        content = '[project]\ndependencies = [\n  "requests>=2.31.0",\n]\n'
        passed, violations = check_dependency_pinning(content)
        assert passed is False
        assert violations == ['"requests>=2.31.0",']

    def test_parse_lockfile_rejects_invalid_toml_and_malformed_packages(self):
        assert parse_lockfile_deps("[[package]") == {}
        content = (
            'package = ["not-a-table", '
            '{ name = 1, version = "1.0" }, '
            '{ name = "missing-version" }, '
            '{ name = "ok", version = "1.0" }, '
            '{ name = "ok", version = "1.0" }]'
        )
        assert parse_lockfile_deps(content) == {"ok": ("1.0",)}

    def test_optional_runtime_requirements_are_checked(self):
        content = (
            "[project]\n"
            'dependencies = ["base>=1", 42]\n'
            "[project.optional-dependencies]\n"
            'feature = ["extra>=2"]\n'
            'metadata = "ignored"\n'
        )
        violations = find_unpinned_deps(
            content,
            {"base": "1.0", "extra": "2.0"},
        )
        assert violations == []

    def test_malformed_project_toml_fails_without_false_violations(self):
        assert find_unpinned_deps("[project", {}) == []
        assert find_unpinned_deps('project = "not-a-table"', {}) == []

    def test_invalid_requirement_and_locked_version_are_rejected(self):
        invalid_requirement = (
            '[project]\ndependencies = ["not valid !!!"]\n'
        )
        assert find_unpinned_deps(invalid_requirement, {}) == [
            "not valid !!!: invalid requirement"
        ]

        invalid_version = '[project]\ndependencies = ["requests>=2"]\n'
        assert find_unpinned_deps(
            invalid_version,
            {"requests": ("not-a-version",)},
        ) == ["requests: invalid locked version not-a-version"]

    def test_all_incompatible_marker_versions_are_reported(self):
        content = '[project]\ndependencies = ["ansible-core>=2.22"]\n'
        assert find_unpinned_deps(
            content,
            {"ansible-core": ("2.19.11", "2.21.3")},
        ) == [
            "ansible-core>=2.22: locked versions 2.19.11, 2.21.3 "
            "do not satisfy >=2.22"
        ]

    def test_staleness_check_fails_closed_for_missing_files(self, tmp_path):
        assert check_lockfile_staleness(
            tmp_path / "missing.lock",
            tmp_path / "missing.toml",
        ) is True

    def test_main_fails_closed_when_lockfile_is_missing(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        script_path = tmp_path / "scripts" / "check_dependency_pinning.py"
        monkeypatch.setattr(dependency_pinning, "__file__", str(script_path))

        with pytest.raises(SystemExit) as raised:
            dependency_pinning.main()

        assert raised.value.code == 1
        assert "uv.lock not found" in capsys.readouterr().out

    def test_main_fails_closed_when_pyproject_is_missing(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        script_path = tmp_path / "scripts" / "check_dependency_pinning.py"
        monkeypatch.setattr(dependency_pinning, "__file__", str(script_path))
        (tmp_path / "uv.lock").write_text("", encoding="utf-8")

        with pytest.raises(SystemExit) as raised:
            dependency_pinning.main()

        assert raised.value.code == 1
        assert "pyproject.toml not found" in capsys.readouterr().out

    def test_main_reports_requirement_violations(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        script_path = tmp_path / "scripts" / "check_dependency_pinning.py"
        monkeypatch.setattr(dependency_pinning, "__file__", str(script_path))
        (tmp_path / "uv.lock").write_text(
            '[[package]]\nname = "click"\nversion = "8.1.7"\n',
            encoding="utf-8",
        )
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["requests>=2"]\n',
            encoding="utf-8",
        )

        with pytest.raises(SystemExit) as raised:
            dependency_pinning.main()

        output = capsys.readouterr().out
        assert raised.value.code == 1
        assert "requests: not in lockfile" in output
        assert "not reproducibly resolved" in output

    def test_main_reports_exact_locked_version_count(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        script_path = tmp_path / "scripts" / "check_dependency_pinning.py"
        monkeypatch.setattr(dependency_pinning, "__file__", str(script_path))
        (tmp_path / "uv.lock").write_text(
            '[[package]]\nname = "ansible-core"\nversion = "2.19.11"\n\n'
            '[[package]]\nname = "ansible-core"\nversion = "2.21.3"\n',
            encoding="utf-8",
        )
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            "dependencies = [\n"
            '  "ansible-core>=2.19,<2.20; python_version < \'3.12\'",\n'
            '  "ansible-core>=2.21,<2.22; python_version >= \'3.12\'",\n'
            "]\n",
            encoding="utf-8",
        )

        with pytest.raises(SystemExit) as raised:
            dependency_pinning.main()

        assert raised.value.code == 0
        assert "2 exact locked package versions" in capsys.readouterr().out


class TestCheckRunbookCurrency:
    """AC013: release-runbook-currency."""

    def test_parse_runbook_date_with_date(self):
        content = "Last updated: 2024-06-15\nOther content here."
        assert parse_runbook_date(content) == "2024-06-15"

    def test_parse_runbook_date_with_colon_space(self):
        content = "Last Updated:  2024-01-20  "
        assert parse_runbook_date(content) == "2024-01-20"

    def test_parse_runbook_date_missing(self):
        content = "Some content without a date line."
        assert parse_runbook_date(content) is None

    def test_parse_runbook_date_empty_string(self):
        assert parse_runbook_date("") is None

    def test_check_version_in_runbook_found(self):
        assert check_version_in_runbook("version 1.2.3 included", "1.2.3") is True

    def test_check_version_in_runbook_not_found(self):
        assert check_version_in_runbook("different content here", "1.2.3") is False

    def test_check_version_in_runbook_empty_version(self):
        assert check_version_in_runbook("some content", "") is True

    def test_check_targets_exist_all_found(self):
        makefile = "build:\n\t@echo build\nrelease-cut:\n\t@echo cut\n"
        assert check_targets_exist(["build", "release-cut"], makefile) == []

    def test_check_targets_exist_nonexistent(self):
        makefile = "build:\n\t@echo build\n"
        missing = check_targets_exist(["build", "deploy"], makefile)
        assert missing == ["deploy"]

    def test_check_targets_exist_empty_list(self):
        assert check_targets_exist([], "anything:\n\t@echo x") == []

    def test_extract_make_targets_from_runbook(self):
        content = "Run `make release-cut` then `make verify-release-completeness`."
        targets = extract_make_targets(content)
        assert targets == {"release-cut", "verify-release-completeness"}

    def test_extract_make_targets_hyphenated(self):
        content = "Use `make check-runbook-currency` and `make check-changelog-accuracy`."
        targets = extract_make_targets(content)
        assert "check-runbook-currency" in targets
        assert "check-changelog-accuracy" in targets

    def test_find_missing_targets_all_present(self):
        makefile = "target-a:\n\t@echo a\ntarget-b:\n\t@echo b\n"
        assert find_missing_targets({"target-a", "target-b"}, makefile) == []

    def test_find_missing_targets_some_absent(self):
        makefile = "target-a:\n\t@echo a\n"
        missing = find_missing_targets({"target-a", "target-c"}, makefile)
        assert missing == ["target-c"]


class TestReleaseDryRunGuard:
    """AC014: dry-run-releases — target dependency verification."""

    DRY_RUN_GUARD_TARGETS: tuple[str, ...] = (
        "check-runbook-currency",
        "check-changelog-accuracy",
        "check-version-bump-atomicity",
        "check-prerelease-flag",
    )

    def test_dry_run_guard_targets_exist_in_makefile(self):
        makefile_path = Path(__file__).resolve().parent.parent.parent / "Makefile"
        makefile_content = makefile_path.read_text()
        for target in self.DRY_RUN_GUARD_TARGETS:
            assert re.search(rf"^{target}:", makefile_content, re.MULTILINE), (
                f"Guard target '{target}' not found in Makefile"
            )

    def test_release_dry_run_target_exists(self):
        makefile_path = Path(__file__).resolve().parent.parent.parent / "Makefile"
        makefile_content = makefile_path.read_text()
        assert re.search(r"^release-dry-run:", makefile_content, re.MULTILINE), (
            "release-dry-run target not found in Makefile"
        )

    def test_dry_run_guard_calls_all_checks(self):
        makefile_path = Path(__file__).resolve().parent.parent.parent / "Makefile"
        makefile_content = makefile_path.read_text()
        start = makefile_content.index("_release-dry-run-guard:")
        end = makefile_content.index("\n\n", start) if "\n\n" in makefile_content[start:] else len(makefile_content)
        guard_block = makefile_content[start:end]
        for script in [
            "check_runbook_currency.py",
            "check_changelog_accuracy.py",
            "check_version_bump_atomicity.py",
            "check_prerelease_flag.py",
        ]:
            assert script in guard_block, f"Guard block missing {script}"

    def test_dry_run_target_does_not_push_tag(self):
        makefile_path = Path(__file__).resolve().parent.parent.parent / "Makefile"
        makefile_content = makefile_path.read_text()
        start = makefile_content.index("release-dry-run:")
        next_target = re.search(r"\n[^\t\n#][a-zA-Z_-]+:", makefile_content[start + 1 :])
        end_offset = next_target.start() if next_target else len(makefile_content[start:])
        dry_run_block = makefile_content[start : start + end_offset]
        recipe_lines = [line for line in dry_run_block.split("\n") if line.startswith("\t")]
        recipe_text = "\n".join(recipe_lines)
        assert "git-tag-push" not in recipe_text
        assert "git-push-sandboxcom" not in recipe_text

    def test_dry_run_guard_fail_closed(self):
        makefile_path = Path(__file__).resolve().parent.parent.parent / "Makefile"
        makefile_content = makefile_path.read_text()
        start = makefile_content.index("_release-dry-run-guard:")
        next_target = re.search(r"\n[a-zA-Z_-]+:", makefile_content[start + 1 :])
        end_offset = next_target.start() if next_target else len(makefile_content[start:])
        guard_block = makefile_content[start : start + end_offset]
        assert "||" not in guard_block, "Guard uses || (fail-open instead of fail-closed)"


class TestCheckChangelogAccuracy:
    """AC015: changelog-accuracy."""

    def test_find_version_section_with_brackets(self):
        content = "## [1.0.0]\n- feat: something\n\n## [0.9.0]\n- old stuff\n"
        section = find_version_section(content, "1.0.0")
        assert "## [1.0.0]" in section
        assert "feat: something" in section

    def test_find_version_section_without_brackets(self):
        content = "## 0.1.0-beta.1\n- initial release\n\n## Other\n"
        section = find_version_section(content, "0.1.0-beta.1")
        assert section is not None
        assert "initial release" in section

    def test_find_version_section_not_found(self):
        content = "## [1.0.0]\n- release notes\n"
        assert find_version_section(content, "2.0.0") is None

    def test_parse_changelog_entries_basic(self):
        content = "## [1.0.0]\n- feat: add login\n- fix: crash on null\n\n## [0.9.0]"
        entries = parse_changelog_entries(content, "1.0.0")
        assert entries == ["feat: add login", "fix: crash on null"]

    def test_parse_changelog_entries_missing_version(self):
        entries = parse_changelog_entries("## [1.0.0]\n- something\n", "2.0.0")
        assert entries == []

    def test_parse_changelog_entries_empty_changelog(self):
        assert parse_changelog_entries("", "1.0.0") == []

    def test_crossref_changelog_against_commits_all_covered(self):
        section_text = "abc123 feat: add login feature\ndef456 fix: crash on null pointer"
        commits = ["abc123 feat: add login feature", "def456 fix: crash on null pointer"]
        result = crossref_changelog_against_commits(section_text, commits)
        assert result["missing_in_changelog"] == []
        assert result["phantom_entries"] == []

    def test_crossref_changelog_against_commits_missing(self):
        section_text = "abc123 feat: add login feature"
        commits = ["abc123 feat: add login feature", "def456 fix: crash on null pointer"]
        result = crossref_changelog_against_commits(section_text, commits)
        assert len(result["missing_in_changelog"]) == 1
        assert "def456" in result["missing_in_changelog"][0]

    def test_find_phantom_entries_no_phantom(self):
        section_text = "abc123 feat: add login feature"
        commits = ["abc123 feat: add login feature"]
        assert find_phantom_entries(section_text, commits) == []

    def test_find_phantom_entries_detected(self):
        section_text = "abc123 feat: add login feature\nReferences commit deadbeef for something"
        commits = ["abc123 feat: add login feature"]
        phantom = find_phantom_entries(section_text, commits)
        assert "deadbeef" in phantom

    def test_find_missing_commits_all_found(self):
        section = "abc123 def456 feat: add login"
        commits = ["abc123 feat: add login feature"]
        assert find_missing_commits(commits, section) == []

    def test_find_missing_commits_not_found(self):
        section = "Some changelog text"
        commits = ["abc123 feat: add login"]
        missing = find_missing_commits(commits, section)
        assert len(missing) == 1
        assert "abc123" in missing[0]


class TestCheckVersionBumpAtomicity:
    """AC016: version-bump-atomicity."""

    def test_extract_version_from_toml(self):
        content = '[project]\nname = "gludd"\nversion = "0.1.0"\n'
        assert extract_version_from_toml(content) == "0.1.0"

    def test_extract_version_from_toml_prerelease(self):
        content = '[project]\nversion = "0.1.0-beta.3"\n'
        assert extract_version_from_toml(content) == "0.1.0-beta.3"

    def test_extract_version_from_toml_not_found(self):
        assert extract_version_from_toml('[project]\nname = "gludd"\n') is None

    def test_extract_version_from_init(self):
        content = '__version__ = "1.2.3"\n'
        assert extract_version_from_init(content) == "1.2.3"

    def test_extract_version_from_init_not_found(self):
        assert extract_version_from_init("x = 5") is None

    def test_extract_version_from_changelog(self):
        content = "## [0.1.0]\n- first release\n"
        assert extract_version_from_changelog(content) == "0.1.0"

    def test_extract_version_from_changelog_prerelease(self):
        content = "## 0.1.0-beta.1\n- beta release\n"
        assert extract_version_from_changelog(content) == "0.1.0-beta.1"

    def test_extract_version_from_changelog_not_found(self):
        assert extract_version_from_changelog("# No version here\n") is None

    def test_extract_version_from_readme(self):
        content = "**Status as of v0.1.0-beta.1**\n"
        assert extract_version_from_readme(content) == "0.1.0-beta.1"

    def test_extract_version_from_readme_no_v_prefix(self):
        content = "**Status as of 1.0.0**\n"
        assert extract_version_from_readme(content) == "1.0.0"

    def test_extract_version_from_readme_not_found(self):
        assert extract_version_from_readme("# README") is None

    def test_extract_versions_preserves_dotted_prerelease(self, tmp_path):
        (tmp_path / "src/general_ludd").mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0-beta.3"\n')
        (tmp_path / "src/general_ludd/__init__.py").write_text('__version__ = "0.1.0-beta.3"\n')
        (tmp_path / "CHANGELOG.md").write_text("## [0.1.0-beta.3] — release\n")
        (tmp_path / "README.md").write_text("**Status as of v0.1.0-beta.3 — today**\n")

        assert set(extract_versions(tmp_path).values()) == {"0.1.0-beta.3"}

    def test_check_atomicity_all_match(self):
        versions = {
            "pyproject.toml": "0.1.0",
            "src/general_ludd/__init__.py": "0.1.0",
            "CHANGELOG.md": "0.1.0",
            "README.md": "0.1.0",
        }
        ok, info = check_atomicity(versions)
        assert ok is True
        assert "0.1.0" in info

    def test_check_atomicity_pyproject_mismatch(self):
        versions = {
            "pyproject.toml": "0.2.0",
            "src/general_ludd/__init__.py": "0.1.0",
            "CHANGELOG.md": "0.1.0",
            "README.md": "0.1.0",
        }
        ok, info = check_atomicity(versions)
        assert ok is False
        assert len(info) > 0

    def test_check_atomicity_init_mismatch(self):
        versions = {
            "pyproject.toml": "0.1.0",
            "src/general_ludd/__init__.py": "0.2.0",
            "CHANGELOG.md": "0.1.0",
            "README.md": "0.1.0",
        }
        ok, _info = check_atomicity(versions)
        assert ok is False

    def test_check_atomicity_changelog_mismatch(self):
        versions = {
            "pyproject.toml": "0.1.0",
            "src/general_ludd/__init__.py": "0.1.0",
            "CHANGELOG.md": "0.2.0",
            "README.md": "0.1.0",
        }
        ok, _info = check_atomicity(versions)
        assert ok is False

    def test_check_atomicity_readme_mismatch(self):
        versions = {
            "pyproject.toml": "0.1.0",
            "src/general_ludd/__init__.py": "0.1.0",
            "CHANGELOG.md": "0.1.0",
            "README.md": "0.2.0",
        }
        ok, _info = check_atomicity(versions)
        assert ok is False

    def test_check_atomicity_empty_versions(self):
        ok, info = check_atomicity({})
        assert ok is False
        assert "No versions" in info[0]

    def test_check_atomicity_all_none(self):
        versions = {
            "pyproject.toml": None,
            "src/general_ludd/__init__.py": None,
        }
        ok, info = check_atomicity(versions)
        assert ok is False
        assert "No versions" in info[0]
