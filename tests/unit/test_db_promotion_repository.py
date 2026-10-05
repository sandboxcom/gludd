"""Public-type compatibility for the split promotion repository."""

from __future__ import annotations

from pathlib import Path

from general_ludd.db.promotion_repository import ManagedPromotionIdentity


def test_promotion_identity_remains_owned_by_public_repository_module(
    tmp_path: Path,
) -> None:
    """Splitting private helpers must not move the durable public value type."""
    identity = ManagedPromotionIdentity(
        artifact_digest="a" * 64,
        plan_identity_digest="b" * 64,
        attempt_identity_digest="c" * 64,
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        return_id="RETURN-PROMOTION",
        repo_root=str(tmp_path),
    )

    assert type(identity).__module__ == "general_ludd.db.promotion_repository"
    assert identity.repo_root == str(tmp_path.resolve())
