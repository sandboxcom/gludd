"""Unit tests for AC release pipeline integrity scripts."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from check_asset_retention import (
    BINARY_PATTERNS,
    SBOM_PATTERNS,
    asset_matches_patterns,
    check_retention_for_releases,
)
from check_prerelease_flag import expected_prerelease
from check_release_audit_trail import (
    get_audit_dir,
    validate_audit_entry,
    validate_audit_file,
)
from check_rollback_procedure import check_rollback_section, required_rollback_fields
from check_sbom_freshness import get_tag_timestamp
from check_tag_immutability import ci_green_for_sha, tag_has_artifacts
from check_tag_signing import classify_result, verify_tag
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
