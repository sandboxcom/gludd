"""Regression guards for root-directory hygiene and security documentation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_private_deploy_key_is_not_kept_at_repository_root() -> None:
    """Deploy credentials must live outside the checkout."""
    assert not (ROOT / "sandboxcom_github_rsa").exists()
    assert not (ROOT / "sandboxcom_github_rsa.pub").exists()


def test_coverage_audit_artifacts_are_not_kept_at_repository_root() -> None:
    """Per-run coverage databases belong in the audit scratch area."""
    leaked = sorted(ROOT.glob(".coverage.audit.*"))
    assert leaked == [], f"coverage audit artifacts leaked into repo root: {leaked}"


def test_directory_documentation_does_not_describe_credentials_as_root_files() -> None:
    """Directory docs must direct operators to external credential storage."""
    content = (ROOT / "docs" / "DIRECTORY_STRUCTURE.md").read_text(encoding="utf-8")
    assert "`sandboxcom_github_rsa`" not in content
    assert "outside the repository" in content
